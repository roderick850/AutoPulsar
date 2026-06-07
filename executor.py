import subprocess
import threading
import time
import os

try:
    from icon_detector import check_conditions, check_icon
    HAS_ICON_DETECTOR = True
except ImportError:
    HAS_ICON_DETECTOR = False


def _normalize_conditions(item):
    """Convierte formato antiguo (repeat_until_*) a formato unificado.

    Retorna (conditions_dict, is_repeat_until).
    conditions_dict siempre tiene la forma unificada:
    {
        "action": "require" | "repeat_until",
        "mode": "and" | "or",
        "items": [...],
        "repeat": {...},   # solo si action == "repeat_until"
        "retry": {...},
        "fallback": {...},
    }
    """
    conditions = item.get("conditions", {}) or {}

    # ── Migrar formato antiguo repeat_until_* ──
    if item.get("repeat_until_enabled") and item.get("repeat_until_icon"):
        icon = item["repeat_until_icon"]
        ru_mode = item.get("repeat_until_mode", "match")
        # Crear condición unificada (siempre "require" — stop_when
        # maneja la diferencia entre match y no_match)
        cond_item = {
            "type": "require",
            "icon_path": icon,
            "label": os.path.basename(icon),
            "threshold": item.get("repeat_until_threshold", 0.08),
        }
        conditions = {
            "action": "repeat_until",
            "mode": "and",
            "items": [cond_item],
            "repeat": {
                "stop_when": ru_mode,
                "max_iterations": item.get("repeat_until_max_iterations", 0),
                "check_interval": item.get("repeat_until_check_interval", 0.5),
            },
            "retry": {"enabled": False, "count": 3, "delay": 5},
            "fallback": {"enabled": False, "threshold": 3,
                         "script": "", "delay_after": 0},
        }
        return conditions, True

    # ── Si ya tiene el formato unificado ──
    if conditions.get("action"):
        return conditions, (conditions.get("action") == "repeat_until")

    # ── Condiciones "legacy" sin action → asumir "require" ──
    if conditions.get("items"):
        conditions["action"] = "require"
        return conditions, False

    # Sin condiciones
    return conditions, False


def _check_icon_with_threshold(cond):
    """Verifica un icono usando el threshold definido en la condición."""
    threshold = cond.get("threshold", 0.08)
    return check_icon(cond.get("icon_path", ""), None, threshold)


def _evaluate_items(items, mode):
    """Evalúa una lista de condiciones (require/block) con modo AND/OR.

    Retorna (passed, reasons).
    """
    if not items:
        return True, None

    results = []
    reasons = []

    for cond in items:
        icon_path = cond.get("icon_path", "")
        ctype = cond.get("type", "require")

        if not icon_path:
            passed = True if ctype == "block" else False
            results.append(passed)
            if not passed:
                reasons.append("Requiere icono (sin ruta)")
            continue

        found, error = _check_icon_with_threshold(cond)

        if ctype == "require":
            results.append(found)
            if not found:
                tag = "falta icono" if error == "missing" else "icono no visible"
                reasons.append(
                    f"Requerir: {tag} ({os.path.basename(icon_path)})")
        elif ctype == "block":
            blocked = not found
            results.append(blocked)
            if not blocked:
                reasons.append(
                    f"Bloquear: icono visible "
                    f"({os.path.basename(icon_path)})")
        else:
            results.append(found)

    if mode == "or":
        passed = any(results)
    else:
        passed = all(results)

    if passed:
        return True, None
    return False, "; ".join(reasons) if reasons else "condiciones no cumplidas"


class Executor(threading.Thread):
    def __init__(self, playlist, settings, callbacks,
                 stop_event, launch_event):
        super().__init__(daemon=True)
        self.playlist = playlist
        self.settings = settings
        self.callbacks = callbacks
        self.stop_event = stop_event
        self.launch_event = launch_event

    def run(self):
        if len(self.playlist) == 0:
            self._safe_callback("on_finish", "Lista vacía", 0, 0, 0, 0)
            return

        loop_mode = self.settings.get("loop_mode", "once")
        loop_count = self.settings.get("loop_count", 1)
        loop_delay = self.settings.get("loop_delay", 0)

        if loop_mode == "infinite":
            max_loops = None
        elif loop_mode == "fixed":
            max_loops = loop_count
        else:
            max_loops = 1

        total_reps_per_loop = sum(
            item["repetitions"] for item in self.playlist)

        once_reps = sum(item["repetitions"] for item in self.playlist
                        if item.get("first_loop_only", False))
        repeat_reps = total_reps_per_loop - once_reps

        if max_loops is None:
            total_global_reps = None
        else:
            total_global_reps = once_reps + repeat_reps * max_loops

        current_loop = 0
        completed_reps_total = 0

        time.sleep(1)

        self._safe_callback("on_start_run", total_global_reps,
                            total_reps_per_loop, max_loops)

        while True:
            if self.stop_event.is_set():
                break

            if max_loops is not None and current_loop >= max_loops:
                break

            current_loop += 1

            self._safe_callback("on_start_loop", current_loop,
                                max_loops, total_global_reps)

            for idx, item in enumerate(self.playlist):
                if self.stop_event.is_set():
                    break

                if current_loop > 1 and item.get("first_loop_only", False):
                    continue

                name = os.path.basename(item["path"])
                reps = item["repetitions"]
                duration = item["duration"]
                pause = item["pause"]

                # ── Normalizar condiciones (formato unificado) ──
                conditions, is_repeat_until = _normalize_conditions(item)
                has_conditions = bool(
                    conditions.get("items") and HAS_ICON_DETECTOR)

                if is_repeat_until:
                    mode_label = "(hasta condición)"
                else:
                    mode_label = ""

                self._safe_callback("on_start_item", idx, name,
                    None if is_repeat_until else reps,
                    mode_label)

                if is_repeat_until and HAS_ICON_DETECTOR:
                    # ── REPEAT UNTIL (unificado) ──
                    repeat_cfg = conditions.get("repeat", {})
                    stop_when = repeat_cfg.get("stop_when", "match")
                    ru_max = repeat_cfg.get("max_iterations", 0)
                    ru_interval = repeat_cfg.get("check_interval", 0.5)
                    ru_unlimited = (ru_max == 0)

                    # ── Verificar ANTES de la primera ejecución ──
                    ok, reason = _evaluate_items(
                        conditions.get("items", []),
                        conditions.get("mode", "and"))
                    if stop_when == "match":
                        condition_already_met = ok
                    else:  # no_match
                        condition_already_met = not ok

                    if condition_already_met:
                        # La condición ya se cumple → detener todo
                        self._safe_callback(
                            "on_repeat_until_done",
                            idx, name, 0)
                        self.stop_event.set()
                        break

                    iteration = 0
                    while ((ru_unlimited or iteration < ru_max)
                           and not self.stop_event.is_set()):
                        iteration += 1

                        # ── Intervalo entre iteraciones ──
                        if ru_interval > 0 and iteration > 1:
                            slept = 0.0
                            while (slept < ru_interval
                                   and not self.stop_event.is_set()):
                                time.sleep(0.1)
                                slept += 0.1

                        if self.stop_event.is_set():
                            break

                        self._safe_callback(
                            "on_repeat",
                            completed_reps_total + 1,
                            total_global_reps,
                            total_reps_per_loop,
                            name,
                            iteration,
                            ru_max,
                            current_loop,
                            max_loops,
                        )

                        # ── Lanzar script ──
                        try:
                            self.launch_event.clear()
                            self._safe_callback(
                                "on_launch", item["path"])
                            if not self.launch_event.wait(timeout=10):
                                raise TimeoutError(
                                    "El hilo principal no pudo "
                                    "lanzar el .exe")
                            time.sleep(2.0)
                        except Exception as e:
                            self._safe_callback("on_error", str(e))
                            break

                        completed_reps_total += 1

                        # ── Esperar duración completa ──
                        slept = 0.0
                        while (slept < duration
                               and not self.stop_event.is_set()):
                            time.sleep(0.1)
                            slept += 0.1

                        if self.stop_event.is_set():
                            break

                        # ── Verificar condición al final ──
                        ok2, _ = _evaluate_items(
                            conditions.get("items", []),
                            conditions.get("mode", "and"))
                        if stop_when == "match":
                            condition_met = ok2
                        else:
                            condition_met = not ok2

                        self._safe_callback(
                            "on_repeat_until_check", idx, name,
                            iteration, ru_max,
                            ok2, condition_met)

                        if condition_met:
                            self._safe_callback(
                                "on_repeat_until_done",
                                idx, name, iteration)
                            self.stop_event.set()
                            break

                    if (not ru_unlimited and iteration >= ru_max
                            and not self.stop_event.is_set()):
                        self._safe_callback(
                            "on_repeat_until_max",
                            idx, name, ru_max)

                else:
                    # ── FIXED REPETITIONS ──
                    for r in range(reps):
                        if self.stop_event.is_set():
                            break

                        # ── Verificar condiciones ANTES ──
                        if has_conditions:
                            ok = self._check_conditions_with_retry(
                                conditions, idx, item)
                            if not ok:
                                break  # saltar reps restantes

                        self._safe_callback(
                            "on_repeat",
                            completed_reps_total + 1,
                            total_global_reps,
                            total_reps_per_loop,
                            name,
                            r + 1,
                            reps,
                            current_loop,
                            max_loops,
                        )

                        try:
                            self.launch_event.clear()
                            self._safe_callback(
                                "on_launch", item["path"])
                            if not self.launch_event.wait(timeout=10):
                                raise TimeoutError(
                                    "El hilo principal no pudo "
                                    "lanzar el .exe")
                            time.sleep(2.0)
                        except Exception as e:
                            self._safe_callback("on_error", str(e))
                            break

                        completed_reps_total += 1

                        slept = 0.0
                        while (slept < duration
                               and not self.stop_event.is_set()):
                            time.sleep(0.1)
                            slept += 0.1

                        if self.stop_event.is_set():
                            break

                        if r < reps - 1 and pause > 0:
                            slept = 0.0
                            while (slept < pause
                                   and not self.stop_event.is_set()):
                                time.sleep(0.1)
                                slept += 0.1

                        if self.stop_event.is_set():
                            break

            # Delay entre loops
            if ((max_loops is None or current_loop < max_loops)
                    and loop_delay > 0):
                self._safe_callback(
                    "on_loop_delay", current_loop,
                    loop_delay, total_global_reps)
                slept = 0.0
                while (slept < loop_delay
                       and not self.stop_event.is_set()):
                    time.sleep(0.1)
                    slept += 0.1

            if self.stop_event.is_set():
                break

        self._safe_callback(
            "on_finish",
            "Detenido" if self.stop_event.is_set()
            else "Completado",
            completed_reps_total,
            total_global_reps,
            total_reps_per_loop,
            current_loop,
            max_loops,
        )

    def _check_conditions_with_retry(self, conditions, idx, item):
        """Verifica condiciones con reintentos + fallback.

        Retorna True si las condiciones se cumplen (script debe ejecutarse).
        Retorna False si fallan (saltar esta repetición/script).
        Usa check_conditions() original de icon_detector (probado en batalla).
        """
        retry_cfg = conditions.get("retry", {})
        retry_enabled = retry_cfg.get("enabled", False)
        retry_count = retry_cfg.get("count", 1) if retry_enabled else 1
        retry_delay = retry_cfg.get("delay", 3) if retry_enabled else 0

        cond_met = False
        for attempt in range(retry_count):
            if self.stop_event.is_set():
                return False
            ok, reason = check_conditions(conditions)
            if ok:
                cond_met = True
                break
            if attempt < retry_count - 1 and retry_delay > 0:
                self._safe_callback(
                    "on_retry_wait", idx,
                    os.path.basename(item["path"]),
                    attempt + 1, retry_count)
                slept = 0.0
                while (slept < retry_delay
                       and not self.stop_event.is_set()):
                    time.sleep(0.1)
                    slept += 0.1

        if not cond_met:
            item["_consecutive_failures"] = \
                item.get("_consecutive_failures", 0) + 1

            fallback_cfg = conditions.get("fallback", {})
            if fallback_cfg.get("enabled", False):
                threshold = fallback_cfg.get("threshold", 3)
                fallback_script = fallback_cfg.get("script", "")
                if (item["_consecutive_failures"] >= threshold
                        and fallback_script):
                    self._safe_callback(
                        "on_fallback_trigger", idx,
                        os.path.basename(item["path"]),
                        os.path.basename(fallback_script))
                    try:
                        subprocess.Popen(fallback_script, shell=True)
                    except Exception as e:
                        self._safe_callback(
                            "on_fallback_error",
                            os.path.basename(fallback_script),
                            str(e))
                    item["_consecutive_failures"] = 0

                    fb_delay = fallback_cfg.get("delay_after", 0)
                    if fb_delay > 0:
                        self._safe_callback(
                            "on_fallback_wait", idx,
                            os.path.basename(item["path"]),
                            fb_delay)
                        slept = 0.0
                        while (slept < fb_delay
                               and not self.stop_event.is_set()):
                            time.sleep(0.1)
                            slept += 0.1

            self._safe_callback(
                "on_skip_icon", idx,
                os.path.basename(item["path"]))
            return False

        return True

    def _safe_callback(self, name, *args):
        cb = self.callbacks.get(name)
        if cb:
            try:
                cb(*args)
            except Exception as e:
                print(f"Callback error {name}: {e}")
