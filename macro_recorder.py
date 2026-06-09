"""
Grabador de macros — captura eventos de teclado y mouse con timestamps.
No registra movimiento del mouse (solo clicks con posición).
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
        self.on_event = on_event
        self.events = []
        self.recording = False
        self._start_time = 0
        self._kb_listener = None
        self._ms_listener = None

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

        self._kb_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._ms_listener = mouse.Listener(
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
        """Devuelve la secuencia de eventos grabada."""
        return self.events

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
    def _on_click(self, x, y, button, pressed):
        btn = str(button).replace("Button.", "")
        if pressed:
            self._add_event({"type": "mouse_click", "button": btn, "x": int(x), "y": int(y)})
        else:
            self._add_event({"type": "mouse_release", "button": btn, "x": int(x), "y": int(y)})

    def _on_scroll(self, x, y, dx, dy):
        self._add_event({"type": "scroll", "dx": dx, "dy": dy, "x": int(x), "y": int(y)})
