"""
Grabador de macros — captura eventos de teclado y mouse con timestamps.
"""

import time
import threading

try:
    from pynput import keyboard, mouse
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False


class MacroRecorder:
    """Graba una secuencia de eventos de teclado y mouse con tiempos relativos."""

    def __init__(self, on_event=None):
        """
        on_event(event_dict): callback llamado cada vez que se captura un evento.
        event_dict = {"type": "key_press"|"key_release"|"mouse_move"|"mouse_click"|"mouse_release"|"scroll",
                      "key": str, "button": str, "x": int, "y": int, "time": float}
        """
        self.on_event = on_event
        self.events = []
        self.recording = False
        self._start_time = 0
        self._kb_listener = None
        self._ms_listener = None
        self._last_mouse_time = 0

    @property
    def is_recording(self):
        return self.recording

    def start(self):
        """Inicia la grabación."""
        if not HAS_PYNPUT:
            raise RuntimeError("pynput no está instalado. Ejecuta: pip install pynput")

        self.events = []
        self.recording = True
        self._start_time = time.time()
        self._last_mouse_time = 0

        self._kb_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._ms_listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._kb_listener.start()
        self._ms_listener.start()

    def stop(self):
        """Detiene la grabación y devuelve la lista de eventos."""
        self.recording = False
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._ms_listener:
            self._ms_listener.stop()
            self._ms_listener = None
        return self.get_events()

    def get_events(self):
        """Devuelve la secuencia de eventos limpia (sin movimientos de mouse redundantes)."""
        # Filtrar eventos de mouse: solo guardar movimientos significativos
        filtered = []
        last_mouse_event = None

        for ev in self.events:
            if ev["type"] in ("mouse_move",):
                # Solo guardar movimientos cada ~100ms o si hay cambio significativo
                if last_mouse_event and last_mouse_event["type"] == "mouse_move":
                    t_diff = ev["time"] - last_mouse_event["time"]
                    dx = abs(ev.get("x", 0) - last_mouse_event.get("x", 0))
                    dy = abs(ev.get("y", 0) - last_mouse_event.get("y", 0))
                    if t_diff < 0.08 and dx < 5 and dy < 5:
                        # Reemplazar el último
                        filtered[-1] = ev
                        last_mouse_event = ev
                        continue
                filtered.append(ev)
                last_mouse_event = ev
            else:
                filtered.append(ev)
                last_mouse_event = None if ev["type"] not in ("mouse_move",) else ev
        return filtered

    def _elapsed(self):
        return round(time.time() - self._start_time, 4)

    def _add_event(self, event_dict):
        event_dict["time"] = self._elapsed()
        self.events.append(event_dict)
        if self.on_event:
            self.on_event(event_dict)

    # ── Keyboard callbacks ──

    def _on_press(self, key):
        try:
            k = key.char
        except AttributeError:
            k = str(key).replace("Key.", "")
        self._add_event({"type": "key_press", "key": k})

    def _on_release(self, key):
        try:
            k = key.char
        except AttributeError:
            k = str(key).replace("Key.", "")
        self._add_event({"type": "key_release", "key": k})

    # ── Mouse callbacks ──

    def _on_move(self, x, y):
        self._add_event({"type": "mouse_move", "x": int(x), "y": int(y)})

    def _on_click(self, x, y, button, pressed):
        btn = str(button).replace("Button.", "")
        if pressed:
            self._add_event({"type": "mouse_click", "button": btn, "x": int(x), "y": int(y)})
        else:
            self._add_event({"type": "mouse_release", "button": btn, "x": int(x), "y": int(y)})

    def _on_scroll(self, x, y, dx, dy):
        self._add_event({"type": "scroll", "dx": dx, "dy": dy, "x": int(x), "y": int(y)})
