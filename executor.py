import subprocess
import threading
import time
import os

try:
    from icon_detector import check_conditions, check_icon
    HAS_ICON_DETECTOR = True
except ImportError:
    HAS_ICON_DETECTOR = False


class Executor(threading.Thread):
    def __init__(self, playlist, settings, callbacks, stop_event, launch_event):
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

        total_reps_per_loop = sum(item["repetitions"] for item in self.playlist)

        # first_loop_only items only count once overall
        once_reps = sum(item["repetitions"] for item in self.playlist
                        if item.get("first_loop_only", False))
        repeat_reps = total_reps_per_loop - once_reps

        # Total global real (None si es infinito)
        if max_loops is None:
            total_global_reps = None
        else:
            total_global_reps = once_reps + repeat_reps * max_loops

        current_loop = 0
        completed_reps_total = 0

        # Allow Windows to fully clean up the previous process context
        time.sleep(1)

        self._safe_callback("on_start_run", total_global_reps, total_reps_per_loop, max_loops)

        while True:
            if self.stop_event.is_set():
                break

            if max_loops is not None and current_loop >= max_loops:
                break

            current_loop += 1

            self._safe_callback("on_start_loop", current_loop, max_loops, total_global_reps)

            for idx, item in enumerate(self.playlist):
                if self.stop_event.is_set():
                    break

                # Skip first_loop_only items after the first loop
                if current_loop > 1 and item.get("first_loop_only", False):
                    continue

                name = os.path.basename(item["path"])
                reps = item["repetitions"]
                duration = item["duration"]
                pause = item["pause"]
                conditions = item.get("conditions", {})
                has_conditions = bool(conditions and conditions.get("items") and HAS_ICON_DETECTOR)

                # ── Repeat-until condition mode ──
                repeat_until = item.get("repeat_until_enabled", False)
                ru_icon = item.get("repeat_until_icon", "")
                ru_mode = item.get("repeat_until_mode", "match")
                ru_threshold = item.get("repeat_until_threshold", 0.05)
                ru_max = item.get("repeat_until_max_iterations", 99999)
                ru_interval = item.get("repeat_until_check_interval", 0.5)
                # 0 = unlimited (no max)
                ru_unlimited = (ru_max == 0)

                if repeat_until and HAS_ICON_DETECTOR and ru_icon:
                    mode_label = f"(hasta {'encontrar' if ru_mode == 'match' else 'desaparecer'} icono)"
                else:
                    mode_label = ""

                self._safe_callback("on_start_item", idx, name,
                    None if repeat_until else reps,
                    mode_label)

                if repeat_until and HAS_ICON_DETECTOR and ru_icon:
                    # ── LOOP UNTIL CONDITION ──
                    iteration = 0
                    while (ru_unlimited or iteration < ru_max) and not self.stop_event.is_set():
                        iteration += 1

                        if ru_interval > 0 and iteration > 1:
                            slept = 0.0
                            while slept < ru_interval and not self.stop_event.is_set():
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
                            self.launch_event.clear()
                            self._safe_callback("on_launch", item["path"])
                            if not self.launch_event.wait(timeout=10):
                                raise TimeoutError("El hilo principal no pudo lanzar el .exe")
                            time.sleep(2.0)
                        except Exception as e:
                            self._safe_callback("on_error", str(e))
                            break

                        completed_reps_total += 1

                        slept = 0.0
                        while slept < duration and not self.stop_event.is_set():
                            time.sleep(0.1)
                            slept += 0.1

                        if self.stop_event.is_set():
                            break

                        # ── Check the repeat-until condition ──
                        found = check_icon(ru_icon, None, ru_threshold)
                        condition_met = (ru_mode == "match" and found) or (ru_mode == "no_match" and not found)

                        self._safe_callback("on_repeat_until_check", idx, name,
                            iteration, ru_max, found, condition_met)

                        if condition_met:
                            self._safe_callback("on_repeat_until_done", idx, name, iteration)
                            break

                    if not ru_unlimited and iteration >= ru_max and not self.stop_event.is_set():
                        self._safe_callback("on_repeat_until_max", idx, name, ru_max)

                else:
                    # ── FIXED REPETITIONS ──
                    for r in range(reps):
                        if self.stop_event.is_set():
                            break

                        # ── Verificar condiciones ANTES de cada repetición ──
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
                            self.launch_event.clear()
                            self._safe_callback("on_launch", item["path"])
                            if not self.launch_event.wait(timeout=10):
                                raise TimeoutError("El hilo principal no pudo lanzar el .exe")
                            time.sleep(2.0)
                        except Exception as e:
                            self._safe_callback("on_error", str(e))
                            break

                        completed_reps_total += 1

                        slept = 0.0
                        while slept < duration and not self.stop_event.is_set():
                            time.sleep(0.1)
                            slept += 0.1

                        if self.stop_event.is_set():
                            break

                        if r < reps - 1 and pause > 0:
                            slept = 0.0
                            while slept < pause and not self.stop_event.is_set():
                                time.sleep(0.1)
                                slept += 0.1

                        if self.stop_event.is_set():
                            break

            # Delay between full loops (except after the last loop)
            if (max_loops is None or current_loop < max_loops) and loop_delay > 0:
                self._safe_callback("on_loop_delay", current_loop, loop_delay, total_global_reps)
                slept = 0.0
                while slept < loop_delay and not self.stop_event.is_set():
                    time.sleep(0.1)
                    slept += 0.1

            if self.stop_event.is_set():
                break

        self._safe_callback(
            "on_finish",
            "Detenido" if self.stop_event.is_set() else "Completado",
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
                self._safe_callback("on_retry_wait", idx,
                    os.path.basename(item["path"]), attempt + 1, retry_count)
                slept = 0.0
                while slept < retry_delay and not self.stop_event.is_set():
                    time.sleep(0.1)
                    slept += 0.1

        if not cond_met:
            # Track consecutive failures for fallback
            item["_consecutive_failures"] = item.get("_consecutive_failures", 0) + 1

            fallback_cfg = conditions.get("fallback", {})
            if fallback_cfg.get("enabled", False):
                threshold = fallback_cfg.get("threshold", 3)
                fallback_script = fallback_cfg.get("script", "")
                if item["_consecutive_failures"] >= threshold and fallback_script:
                    self._safe_callback("on_fallback_trigger", idx,
                        os.path.basename(item["path"]),
                        os.path.basename(fallback_script))
                    try:
                        # Lanzar en background — no esperar a que termine
                        subprocess.Popen(fallback_script, shell=True)
                    except Exception as e:
                        self._safe_callback("on_fallback_error",
                            os.path.basename(fallback_script), str(e))
                    item["_consecutive_failures"] = 0  # reset after fallback

                    # Delay post-fallback antes de continuar
                    fb_delay = fallback_cfg.get("delay_after", 0)
                    if fb_delay > 0:
                        self._safe_callback("on_fallback_wait", idx,
                            os.path.basename(item["path"]), fb_delay)
                        slept = 0.0
                        while slept < fb_delay and not self.stop_event.is_set():
                            time.sleep(0.1)
                            slept += 0.1

            self._safe_callback("on_skip_icon", idx,
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
