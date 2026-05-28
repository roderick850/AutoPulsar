"""
Global hotkey listener using native Windows RegisterHotKey API.

No external dependencies — pure ctypes + threading.
"""

import ctypes
from ctypes import wintypes
import threading
import uuid

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_NULL = 0x0000
WM_DESTROY = 0x0002
MOD_NOREPEAT = 0x4000

_VK_MAP = {f"f{i}": 0x6F + i for i in range(1, 13)}  # f1=0x70 ... f12=0x7B


class HotkeyListener:
    """Registers a single global hotkey and invokes a callback when pressed.

    Usage::

        listener = HotkeyListener()
        listener.start("f10", my_callback)   # may raise RuntimeError
        ...
        listener.stop()
    """

    def __init__(self):
        self._hotkey_id = 1
        self._hwnd = None
        self._thread = None
        self._stop_evt = threading.Event()
        self._ready_evt = threading.Event()
        self._error_evt = threading.Event()
        self._error_msg = None
        self._callback = None

    # ── public ──────────────────────────────────────────────────────

    def start(self, hotkey: str, callback):
        """Register *hotkey* (e.g. ``"f10"``).  *callback* is called (no
        arguments) on the main thread when the hotkey is pressed.

        Raises ``RuntimeError`` if registration fails."""
        vk = _VK_MAP.get(hotkey.lower())
        if vk is None:
            raise RuntimeError(f"Tecla no válida: {hotkey}")

        self._callback = callback
        self._stop_evt.clear()
        self._ready_evt.clear()
        self._error_evt.clear()
        self._error_msg = None

        self._thread = threading.Thread(
            target=self._run, args=(vk,), daemon=True, name="hotkey"
        )
        self._thread.start()

        # Wait for either ready or error
        while not self._ready_evt.is_set() and not self._error_evt.is_set():
            self._ready_evt.wait(0.1)

        if self._error_evt.is_set() or not self._ready_evt.is_set():
            self._cleanup_thread()
            msg = self._error_msg or f"No se pudo registrar la hotkey {hotkey.upper()}"
            raise RuntimeError(msg)

    def stop(self):
        """Unregister and stop the listener thread."""
        self._stop_evt.set()
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    # ── internal ────────────────────────────────────────────────────

    def _cleanup_thread(self):
        self._stop_evt.set()
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)

    def _run(self, vk: int):
        try:
            hinst = kernel32.GetModuleHandleW(None)

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM,
            )
            self._wndproc = WNDPROC(self._on_msg)

            # Unique class name to avoid collisions
            cls_name = f"TinyTaskHK_{uuid.uuid4().hex[:8]}"
            wc = wintypes.WNDCLASSW()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.lpszClassName = cls_name

            atom = user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                self._error_msg = "No se pudo crear la clase de ventana"
                self._error_evt.set()
                self._ready_evt.set()
                return

            # Message-only window (HWND_MESSAGE = -3)
            self._hwnd = user32.CreateWindowExW(
                0, atom, None, 0, 0, 0, 0, 0,
                wintypes.HWND(-3), None, hinst, None,
            )
            if not self._hwnd:
                user32.UnregisterClassW(atom, hinst)
                self._error_msg = "No se pudo crear la ventana del listener"
                self._error_evt.set()
                self._ready_evt.set()
                return

            # Register the hotkey
            ok = user32.RegisterHotKey(
                self._hwnd, self._hotkey_id, MOD_NOREPEAT, vk
            )
            if not ok:
                # Try without MOD_NOREPEAT
                ok = user32.RegisterHotKey(
                    self._hwnd, self._hotkey_id, 0, vk
                )

            if not ok:
                user32.DestroyWindow(self._hwnd)
                user32.UnregisterClassW(atom, hinst)
                self._hwnd = None
                self._error_msg = f"La tecla ya está en uso por otro programa"
                self._error_evt.set()
                self._ready_evt.set()
                return

            self._ready_evt.set()

            # Message pump
            msg = wintypes.MSG()
            while not self._stop_evt.is_set():
                if user32.PeekMessageW(ctypes.byref(msg), self._hwnd, 0, 0, 1):
                    if msg.message == WM_DESTROY:
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                else:
                    self._stop_evt.wait(0.05)

            # Cleanup
            if self._hwnd:
                user32.UnregisterHotKey(self._hwnd, self._hotkey_id)
                user32.DestroyWindow(self._hwnd)
            user32.UnregisterClassW(atom, hinst)
            self._hwnd = None

        except Exception as e:
            self._error_msg = str(e)
            self._error_evt.set()
            self._ready_evt.set()

    def _on_msg(self, hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY and wparam == self._hotkey_id:
            if self._callback:
                try:
                    self._callback()
                except Exception:
                    pass
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
