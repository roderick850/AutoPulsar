import os
import time
import subprocess
import threading
import itertools

try:
    from icon_detector import check_conditions, check_icon, check_icon_multi
    HAS_ICON_DETECTOR = True
except ImportError:
    HAS_ICON_DETECTOR = False

try:
    from macro_player import MacroPlayer, actions_to_events
    HAS_MACRO_PLAYER = True
except ImportError:
    HAS_MACRO_PLAYER = False


def _normalize_conditions(item):
    """Normaliza condiciones de un item (formato unificado o legacy)."""
    conditions = item.get("conditions", {}) or {}

    # ── Migrar formato antiguo repeat_until_* ──
    if item.get("repeat_until_enabled") and item.get("repeat_until_icon"):
        icon = item["repeat_until_icon"]
        ru_mode = item.get("repeat_until_mode", "match")
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
    """Verifica un icono usando el threshold, región y multi-muestreo
    definidos en la condición."""
    threshold = cond.get("threshold", 0.08)
    region = cond.get("region")  # [x, y, w, h] o None
    samples = cond.get("samples", 1)
    confidence = cond.get("confidence", 1.0)
    return check_icon_multi(
        cond.get("icon_path", ""), region, threshold,
        samples=samples, confidence=confidence)


def _evaluate_items(items, mode):
    """Evalúa una lista de condiciones (require/block) con modo AND/OR.
    Retorna (passed, reasons)."""
    reasons = []
    for cond in items:
        ctype = cond.get("type", "require")
        found, err = _check_icon_with_threshold(cond)
        if ctype == "require":
            ok = found
        else:  # block
            ok = not found
        reasons.append((cond.get("label", "?"), ctype, ok, found, err))
        if mode == "and" and not ok:
            return False, reasons
        if mode == "or" and ok:
            return True, reasons
    if mode == "and":
        return True, reasons
    return False, reasons


class Executor(threading.Thread):
    """Ejecuta la playlist en un hilo separado."""

    def __init__(self, playlist, settings, callbacks,
                 stop_event, launch_event):
        super().__init__(daemon=True)
        self.playlist = playlist
        self.settings = settings
        self.callbacks = callbacks
        self.stop_event = stop_event
        self.launch_event = launch_event

    def run(self):
        """Ejecuta la playlist completa (loop + ítems)."""
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
            item.get("repetitions", 1) for item in self.playlist)

        once_reps = sum(item.get("repetitions", 1) for item in self.playlist
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

                name = os.path.basename(item.get("path", "?"))
                reps = item.get("repetitions", 1)
                duration = item.get("duration", 1)
                pause = item.get("pause", 0)

                conditions, is_repeat_until = _normalize_conditions(item)
                has_conditions = bool(conditions.get("items"))

                mode_label = ""
                if has_conditions and not is_repeat_until:
                    mode_label = "⚙️"

                self._safe_callback("on_start_item", idx, name,
                    None if is_repeat_until else reps,
                    mode_label)

                if is_repeat_until and HAS_ICON_DETECTOR:
                    # ── REPEAT UNTIL ──
                    repeat_cfg = conditions.get("repeat", {})
                    stop_when = repeat_cfg.get("stop_when", "match")
                    ru_max = repeat_cfg.get("max_iterations", 0)
                    ru_interval = repeat_cfg.get("check_interval", 0.5)
                    ru_unlimited = (ru_max == 0)

                    ok, reason = _evaluate_items(
                        conditions.get("items", []),
                        conditions.get("mode", "and"))
                    if stop_when == "match":
                        condition_already_met = ok
                    else:
                        condition_already_met = not ok

                    if condition_already_met:
                        self._safe_callback(
                            "on_repeat_until_done", idx, name, 0)
                        self.stop_event.set()
                        break

                    iteration = 0
                    while ((ru_unlimited or iteration < ru_max)
                           and not self.stop_event.is_set()):
                        iteration += 1

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

                        try:
                            self._launch_item(item)
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

                        ok2, _ = _evaluate_items(
                            conditions.get("items", []),
                            conditions.get("mode", "and"))
                        if stop_when == "match":
                            condition_met = ok2
                        else:
                            condition_met = not ok2

                        self._safe_callback(
                            "on_repeat_until_check", idx, name,
                            iteration, ru_max, ok2, condition_met)

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

                        if has_conditions:
                            ok = self._check_conditions_with_retry(
                                conditions, idx, item)
                            if not ok:
                                break

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
                            self._launch_item(item)
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

    def _launch_item(self, item):
        """Lanza un script (.exe) o reproduce una macro, según el tipo."""
        if item.get("type") == "macro":
            self._play_macro(item)
        else:
            self.launch_event.clear()
            self._safe_callback("on_launch", item["path"])
            if not self.launch_event.wait(timeout=10):
                raise TimeoutError(
                    "El hilo principal no pudo lanzar el .exe")
            time.sleep(2.0)

    def _check_conditions_with_retry(self, conditions, idx, item):
        """Verifica condiciones con reintentos + fallback."""
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
            # Pequeña pausa para que el usuario vea el estado amarillo
            time.sleep(0.4)
            return False

        return True

    def _play_macro(self, item):
        """Ejecuta una macro grabada."""
        if not HAS_MACRO_PLAYER:
            self._safe_callback("on_error", "MacroPlayer no disponible")
            return
        macro_data = item.get("macro_data", {})
        actions = macro_data.get("actions", [])
        events = actions_to_events(actions)

        self._safe_callback("on_launch", f"Macro: {macro_data.get('name', 'sin nombre')}")
        player = MacroPlayer(events)
        player.play(block=True)

    def _safe_callback(self, name, *args):
        cb = self.callbacks.get(name)
        if cb:
            try:
                cb(*args)
            except Exception as e:
                print(f"Callback error {name}: {e}")
