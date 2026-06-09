"""
Reproductor de macros — ejecuta una secuencia de eventos grabados.
"""

import time
import threading

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # Mover a esquina superior izq = abortar
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False


# ── Mapeo de nombres de teclas para pyautogui ──
KEY_MAP = {
    "enter": "enter",
    "space": "space",
    "tab": "tab",
    "backspace": "backspace",
    "delete": "delete",
    "esc": "esc",
    "escape": "esc",
    "shift": "shift",
    "shift_r": "shiftright",
    "shift_l": "shiftleft",
    "ctrl": "ctrl",
    "ctrl_r": "ctrlright",
    "ctrl_l": "ctrlleft",
    "alt": "alt",
    "alt_r": "altright",
    "alt_l": "altleft",
    "cmd": "win",
    "cmd_r": "winright",
    "cmd_l": "winleft",
    "win": "win",
    "caps_lock": "capslock",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "page_up": "pageup",
    "page_down": "pagedown",
    "insert": "insert",
    "print_screen": "printscreen",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
    "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
    "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    "pause": "pause",
}


class MacroPlayer:
    """Reproduce una secuencia de eventos macro."""

    def __init__(self, events, callbacks=None):
        """
        events: lista de eventos (formato MacroRecorder)
        callbacks: {"on_start": fn, "on_event": fn(idx, event), "on_finish": fn, "on_stop": fn}
        """
        self.events = events
        self.callbacks = callbacks or {}
        self._stop_event = threading.Event()
        self._thread = None

    def play(self, block=False):
        """Reproduce la macro en un thread separado."""
        if not HAS_PYAUTOGUI:
            raise RuntimeError("pyautogui no está instalado. Ejecuta: pip install pyautogui")
        if not self.events:
            self._callback("on_finish")
            return

        self._stop_event.clear()
        if self.callbacks.get("on_start"):
            self.callbacks["on_start"]()

        if block:
            self._execute()
        else:
            self._thread = threading.Thread(target=self._execute, daemon=True)
            self._thread.start()

    def stop(self):
        """Detiene la reproducción."""
        self._stop_event.set()
        if self.callbacks.get("on_stop"):
            self.callbacks["on_stop"]()

    def wait(self, timeout=None):
        """Espera a que termine la reproducción (si se lanzó no bloqueante)."""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def is_playing(self):
        return self._thread is not None and self._thread.is_alive()

    def _callback(self, name, *args):
        fn = self.callbacks.get(name)
        if fn:
            fn(*args)

    def _execute(self):
        try:
            for i, ev in enumerate(self.events):
                if self._stop_event.is_set():
                    break

                self._callback("on_event", i, ev)
                self._play_event(ev)

            self._callback("on_finish")
        except pyautogui.FailSafeException:
            self._callback("on_stop")
        except Exception as e:
            self._callback("on_error", str(e))

    def _play_event(self, ev):
        t = ev["type"]

        if t == "key_press":
            k = KEY_MAP.get(ev["key"], ev["key"])
            try:
                pyautogui.keyDown(k)
            except (ValueError, TypeError):
                pass  # Tecla no soportada

        elif t == "key_release":
            k = KEY_MAP.get(ev["key"], ev["key"])
            try:
                pyautogui.keyUp(k)
            except (ValueError, TypeError):
                pass

        elif t == "mouse_move":
            x, y = ev.get("x", 0), ev.get("y", 0)
            # Movimiento rápido y fluido con easing
            pyautogui.moveTo(x, y, duration=0.03, tween=pyautogui.easeOutQuad)

        elif t == "mouse_click":
            btn = ev.get("button", "left")
            x, y = ev.get("x"), ev.get("y")
            if btn in ("left", "right", "middle"):
                # Mover primero al punto del click (fluido)
                pyautogui.moveTo(x, y, duration=0.03, tween=pyautogui.easeOutQuad)
                pyautogui.mouseDown(button=btn)

        elif t == "mouse_release":
            btn = ev.get("button", "left")
            if btn in ("left", "right", "middle"):
                pyautogui.mouseUp(button=btn)

        elif t == "scroll":
            pyautogui.scroll(ev.get("dy", 0))


def events_to_actions(events):
    """
    Convierte una lista de eventos crudos a una lista de acciones amigables
    para mostrar en la UI de edición.

    Cada acción tiene:
      - action: "press" | "click" | "move" | "scroll" | "wait"
      - key: str (para press)
      - button: str (para click)
      - x, y: int (para move/click)
      - press_duration: float (para press — tiempo entre press y release)
      - wait_before: float (tiempo desde la acción anterior)
    """
    actions = []
    i = 0
    prev_time = 0.0

    while i < len(events):
        ev = events[i]
        wait = round(ev["time"] - prev_time, 4)

        if ev["type"] == "key_press":
            # Buscar el release correspondiente
            press_ev = ev
            release_time = ev["time"]
            j = i + 1
            while j < len(events):
                nxt = events[j]
                if nxt["type"] == "key_release" and nxt["key"] == press_ev["key"]:
                    release_time = nxt["time"]
                    i = j  # Saltar hasta el release
                    break
                # Si hay otro evento intermedio del mismo tipo (otra key_press diferente),
                # asumimos que este press no tuvo release explícito
                if nxt["type"] == "key_press":
                    break
                j += 1

            duration = round(release_time - press_ev["time"], 4)
            actions.append({
                "action": "press",
                "key": press_ev["key"],
                "press_duration": duration if duration > 0 else 0.05,
                "wait_before": wait,
            })
            prev_time = release_time

        elif ev["type"] in ("key_release",):
            # Release sin press previo → ignorar o tratar como press breve
            actions.append({
                "action": "press",
                "key": ev["key"],
                "press_duration": 0.05,
                "wait_before": wait,
            })
            prev_time = ev["time"]

        elif ev["type"] == "mouse_click":
            # Buscar el release
            press_ev = ev
            release_time = ev["time"]
            j = i + 1
            while j < len(events):
                nxt = events[j]
                if nxt["type"] == "mouse_release" and nxt["button"] == press_ev["button"]:
                    release_time = nxt["time"]
                    i = j
                    break
                j += 1

            duration = round(release_time - press_ev["time"], 4)
            actions.append({
                "action": "click",
                "button": press_ev["button"],
                "x": press_ev.get("x", 0),
                "y": press_ev.get("y", 0),
                "press_duration": duration if duration > 0 else 0.05,
                "wait_before": wait,
            })
            prev_time = release_time

        elif ev["type"] in ("mouse_release",):
            # Release sin click previo
            actions.append({
                "action": "click",
                "button": ev["button"],
                "x": ev.get("x", 0),
                "y": ev.get("y", 0),
                "press_duration": 0.05,
                "wait_before": wait,
            })
            prev_time = ev["time"]

        elif ev["type"] == "scroll":
            actions.append({
                "action": "scroll",
                "dx": ev.get("dx", 0),
                "dy": ev.get("dy", 0),
                "wait_before": wait,
                "press_duration": 0,
            })
            prev_time = ev["time"]

        i += 1

    return actions


def actions_to_events(actions):
    """
    Convierte una lista de acciones editables a eventos crudos para reproducción.
    """
    events = []
    t = 0.0

    for act in actions:
        t += act.get("wait_before", 0)

        if act["action"] == "press":
            key = act["key"]
            events.append({"type": "key_press", "key": key, "time": round(t, 4)})
            t += act.get("press_duration", 0.05)
            events.append({"type": "key_release", "key": key, "time": round(t, 4)})

        elif act["action"] == "click":
            btn = act.get("button", "left")
            x = act.get("x", 0)
            y = act.get("y", 0)
            events.append({"type": "mouse_move", "x": x, "y": y, "time": round(t, 4)})
            events.append({"type": "mouse_click", "button": btn, "x": x, "y": y, "time": round(t, 4)})
            t += act.get("press_duration", 0.05)
            events.append({"type": "mouse_release", "button": btn, "x": x, "y": y, "time": round(t, 4)})

        elif act["action"] == "move":
            events.append({"type": "mouse_move", "x": act.get("x", 0), "y": act.get("y", 0), "time": round(t, 4)})

        elif act["action"] == "scroll":
            events.append({"type": "scroll", "dx": act.get("dx", 0), "dy": act.get("dy", 0), "x": 0, "y": 0, "time": round(t, 4)})

    return events
