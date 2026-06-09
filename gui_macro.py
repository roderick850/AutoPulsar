"""
Editor visual de macros — grabar, editar y reproducir secuencias de teclado/mouse.
Estilo HyperX/Logitech con timeline visual de teclas.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import json
import os
import threading

from macro_recorder import MacroRecorder
from macro_player import MacroPlayer, events_to_actions, actions_to_events

# ── Colores (coinciden con gui.py DARK_COLORS) ──
C = {
    "bg": "#0d0d0d",
    "surface": "#1a1a1a",
    "surface2": "#212121",
    "border": "#3a3a3a",
    "text": "#e0e0e0",
    "text2": "#808080",
    "accent": "#1f538d",
    "accent2": "#14375e",
    "green": "#2e8b57",
    "red": "#c44545",
    "yellow": "#c4a43d",
    "blue": "#3a7ebf",
    "purple": "#7c5cbf",
    "orange": "#d98a3a",
}

# ── Colores para teclas por tipo ──
KEY_COLORS = {
    "default": ("#2a2a3a", "#e0e0e0"),
    "modifier": ("#3a2a1a", "#f0c040"),    # shift/ctrl/alt/win
    "special": ("#2a3a2a", "#60d060"),      # enter/space/tab/esc
    "function": ("#1a2a3a", "#60a0f0"),     # F1-F12
    "arrow": ("#2a1a3a", "#c060f0"),        # flechas
    "mouse": ("#3a1a2a", "#f06060"),        # clicks
    "move": ("#1a1a2a", "#9090c0"),         # movimiento
}

KEY_TYPES = {
    "shift": "modifier", "shift_r": "modifier", "shift_l": "modifier",
    "ctrl": "modifier", "ctrl_r": "modifier", "ctrl_l": "modifier",
    "alt": "modifier", "alt_r": "modifier", "alt_l": "modifier",
    "cmd": "modifier", "win": "modifier",
    "enter": "special", "space": "special", "tab": "special",
    "backspace": "special", "delete": "special", "esc": "special", "escape": "special",
    "up": "arrow", "down": "arrow", "left": "arrow", "right": "arrow",
}
for i in range(1, 13):
    KEY_TYPES[f"f{i}"] = "function"


def key_color(key):
    ktype = KEY_TYPES.get(key.lower(), "default")
    return KEY_COLORS.get(ktype, KEY_COLORS["default"])


def key_display(key):
    """Nombre legible de tecla."""
    names = {
        "space": "␣", "enter": "↵", "tab": "↹", "backspace": "⌫",
        "delete": "⌦", "esc": "Esc", "escape": "Esc",
        "up": "↑", "down": "↓", "left": "←", "right": "→",
        "shift": "⇧", "ctrl": "Ctrl", "alt": "Alt", "cmd": "Win", "win": "Win",
        "shift_r": "⇧R", "shift_l": "⇧L", "ctrl_r": "CtR", "ctrl_l": "CtL",
        "alt_r": "AR", "alt_l": "AL",
        "caps_lock": "Caps", "num_lock": "NumLk",
        "home": "Home", "end": "End", "page_up": "PgUp", "page_down": "PgDn",
        "insert": "Ins", "print_screen": "PrtSc",
    }
    if key in names:
        return names[key]
    if key.startswith("f") and len(key) <= 3:
        return key.upper()
    if len(key) == 1:
        return key.upper()
    return key


def format_s(seconds):
    if seconds < 1:
        return f"{int(seconds*1000)}ms"
    return f"{seconds:.2f}s"


class MacroEditorWindow(ctk.CTkToplevel):
    """Ventana principal del editor de macros."""

    def __init__(self, parent, on_save=None, initial_actions=None, initial_name=""):
        super().__init__(parent, fg_color=C["bg"])
        self.title("Editor de Macros — TinyTask Orchestrator")
        self.geometry("950x650")
        self.minsize(700, 400)
        self.transient(parent)
        self.grab_set()

        self.on_save = on_save
        self.actions = initial_actions or []
        self.macro_name = tk.StringVar(value=initial_name)
        self.status_text = tk.StringVar(value="Listo")

        # Grabador
        self.recorder = MacroRecorder(on_event=self._on_record_event)
        self._recorded_actions = []
        self._recording = False

        # Jugador
        self._player = None
        self._playing = False

        # Built
        self._build_ui()

        if self.actions:
            self._refresh_list()

    # ── UI Build ──────────────────────────────────────────

    def _build_ui(self):
        # ── Top bar: nombre + grabar ──
        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Nombre:", style="Compact.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(top, textvariable=self.macro_name, width=25).pack(side=tk.LEFT, padx=(0, 10))

        self._btn_rec = ttk.Button(top, text="⏺️ Grabar", command=self._toggle_record, style="Compact.TButton")
        self._btn_rec.pack(side=tk.LEFT, padx=3)

        self._btn_play = ttk.Button(top, text="▶️ Probar", command=self._play_macro, style="Compact.TButton")
        self._btn_play.pack(side=tk.LEFT, padx=3)

        ttk.Button(top, text="➕ Añadir Tecla", command=self._add_key, style="Compact.TButton").pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="➕ Añadir Click", command=self._add_click, style="Compact.TButton").pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="➕ Añadir Espera", command=self._add_wait, style="Compact.TButton").pack(side=tk.LEFT, padx=3)

        # ── Status ──
        status_frame = ttk.Frame(self, padding=(8, 0, 8, 4))
        status_frame.pack(fill=tk.X)
        self._status_label = ttk.Label(status_frame, textvariable=self.status_text, style="Dim.TLabel")
        self._status_label.pack(side=tk.LEFT)

        self._counter_label = ttk.Label(status_frame, text="", style="Dim.TLabel")
        self._counter_label.pack(side=tk.RIGHT)

        # ── Timeline (Canvas + Scrollbar) ──
        canvas_frame = ttk.Frame(self, padding=(8, 0, 8, 0))
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(
            canvas_frame,
            bg=C["surface"],
            highlightthickness=0,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Frame dentro del canvas
        self._inner = ttk.Frame(self._canvas, style="Surface.TFrame")
        self._win_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw", tags="inner")

        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # ── Botón guardar ──
        btn_frame = ttk.Frame(self, padding=8)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="💾 Guardar Macro y Cerrar", command=self._save_and_close, style="Accent.TButton").pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy, style="Compact.TButton").pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="🗑️ Limpiar Todo", command=self._clear_all, style="Danger.TButton").pack(side=tk.LEFT, padx=5)

    # ── Recording ─────────────────────────────────────────

    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        self._recorded_actions = []
        self._recording = True
        self._btn_rec.config(text="⏹️ Detener")
        self.status_text.set("GRABANDO — presiona teclas, haz clicks...")
        self._status_label.config(foreground=C["red"])

        try:
            self.recorder.start()
        except RuntimeError as e:
            self.status_text.set(f"Error: {e}")
            self._recording = False
            self._btn_rec.config(text="⏺️ Grabar")

    def _stop_recording(self):
        self._recording = False
        events = self.recorder.stop()
        self._btn_rec.config(text="⏺️ Grabar")
        self.status_text.set("Listo")
        self._status_label.config(foreground=C["text2"])

        if events:
            self._recorded_actions = events_to_actions(events)
            self.actions.extend(self._recorded_actions)
            self._refresh_list()
            self.status_text.set(f"Grabados {len(self._recorded_actions)} eventos")

    def _on_record_event(self, event):
        """Callback en vivo durante grabación."""
        if event["type"] in ("key_press", "mouse_click", "mouse_move"):
            key = event.get("key") or event.get("button") or ""
            self.status_text.set(f"GRABANDO: {key_display(key)}")

    # ── Playback ──────────────────────────────────────────

    def _play_macro(self):
        if self._playing:
            return
        if not self.actions:
            self.status_text.set("No hay acciones para reproducir")
            return

        events = actions_to_events(self.actions)
        self._playing = True
        self._btn_play.config(text="⏸️ Reproduciendo...", state="disabled")

        def on_event(idx, ev):
            key = ev.get("key") or ev.get("button") or ""
            self.status_text.set(f"Reproduciendo [{idx+1}/{len(events)}]: {key_display(key)}")
            # Resaltar item actual
            if self._row_widgets and idx < len(self._row_widgets):
                self._canvas.yview_moveto(self._row_widgets[idx].winfo_y() / self._inner.winfo_height())

        def on_finish():
            self._playing = False
            self._btn_play.config(text="▶️ Probar", state="normal")
            self.status_text.set("Reproducción completada")

        self._player = MacroPlayer(events, callbacks={"on_event": on_event, "on_finish": on_finish})
        self._player.play(block=False)

    # ── CRUD Acciones ─────────────────────────────────────

    def _add_key(self):
        dlg = _InputDialog(self, "Añadir Tecla", "Tecla:", "a", "Duración (s):", "0.15", "Espera antes (s):", "0.5")
        if dlg.result:
            self.actions.append({"action": "press", "key": dlg.result[0], "press_duration": float(dlg.result[1]), "wait_before": float(dlg.result[2])})
            self._refresh_list()

    def _add_click(self):
        dlg = _InputDialog(self, "Añadir Click", "Botón (left/right/middle):", "left", "X:", "500", "Y:", "300")
        if dlg.result:
            self.actions.append({"action": "click", "button": dlg.result[0], "x": int(dlg.result[1]), "y": int(dlg.result[2]), "press_duration": 0.05, "wait_before": 0.5})
            self._refresh_list()

    def _add_wait(self):
        dlg = _SimpleInput(self, "Añadir Espera", "Segundos:", "0.5")
        if dlg.result:
            self.actions.append({"action": "press", "key": "__wait__", "press_duration": 0, "wait_before": float(dlg.result)})
            self._refresh_list()

    def _remove_action(self, idx):
        if 0 <= idx < len(self.actions):
            del self.actions[idx]
            self._refresh_list()

    def _move_up(self, idx):
        """Mueve una acción una posición hacia arriba."""
        if 1 <= idx < len(self.actions):
            self.actions[idx], self.actions[idx - 1] = self.actions[idx - 1], self.actions[idx]
            self._refresh_list()

    def _move_down(self, idx):
        """Mueve una acción una posición hacia abajo."""
        if 0 <= idx < len(self.actions) - 1:
            self.actions[idx], self.actions[idx + 1] = self.actions[idx + 1], self.actions[idx]
            self._refresh_list()

    def _edit_action(self, idx):
        """Doble click en una acción → editar."""
        if idx < 0 or idx >= len(self.actions):
            return
        act = self.actions[idx]

        if act["action"] == "press" and act["key"] == "__wait__":
            # Es una espera
            dlg = _SimpleInput(self, "Editar Espera", "Segundos:", str(act.get("wait_before", 0.5)))
            if dlg.result:
                act["wait_before"] = float(dlg.result)
                self._refresh_list()
            return

        if act["action"] == "press":
            dlg = _InputDialog(self, "Editar Tecla",
                "Tecla:", act.get("key", "a"),
                "Duración (s):", str(act.get("press_duration", 0.15)),
                "Espera antes (s):", str(act.get("wait_before", 0.5)))
            if dlg.result:
                act["key"] = dlg.result[0]
                act["press_duration"] = float(dlg.result[1])
                act["wait_before"] = float(dlg.result[2])
                self._refresh_list()

        elif act["action"] == "click":
            dlg = _InputDialog(self, "Editar Click",
                "Botón (left/right/middle):", act.get("button", "left"),
                "X:", str(act.get("x", 0)),
                "Y:", str(act.get("y", 0)),
                "Duración (s):", str(act.get("press_duration", 0.05)),
                "Espera antes (s):", str(act.get("wait_before", 0.5)))
            if dlg.result:
                act["button"] = dlg.result[0]
                act["x"] = int(dlg.result[1])
                act["y"] = int(dlg.result[2])
                act["press_duration"] = float(dlg.result[3])
                act["wait_before"] = float(dlg.result[4])
                self._refresh_list()

    def _clear_all(self):
        if messagebox.askyesno("Limpiar", "¿Borrar todas las acciones?", parent=self):
            self.actions = []
            self._refresh_list()

    # ── Render ────────────────────────────────────────────

    def _refresh_list(self):
        for w in self._inner.winfo_children():
            w.destroy()
        self._row_widgets = []

        self._counter_label.config(text=f"{len(self.actions)} acciones")

        if not self.actions:
            ttk.Label(self._inner, text="(sin acciones — graba o añade manualmente)", style="Dim.TLabel", padding=20).pack()
            return

        for i, act in enumerate(self.actions):
            row = self._render_row(i, act)
            row.pack(fill=tk.X, pady=1)
            self._row_widgets.append(row)

        self._inner.update_idletasks()
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _render_row(self, idx, act):
        row = ttk.Frame(self._inner, style="Surface.TFrame")

        # ── Etiqueta del índice + tiempo acumulado ──
        cum_time = self._cumulative_time(idx)
        ttk.Label(row, text=f"#{idx+1}", style="Dim.TLabel", width=4).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(row, text=format_s(cum_time), style="Dim.TLabel", width=7, anchor="e").pack(side=tk.LEFT, padx=(0, 4))

        # ── Keycap visual ──
        if act["action"] == "press" and act.get("key") == "__wait__":
            # Espera
            wait = act.get("wait_before", 0.5)
            cap = tk.Canvas(row, width=50, height=36, bg=C["surface"], highlightthickness=0, bd=0)
            cap.create_rectangle(2, 2, 48, 34, fill="#3a3a2a", outline="#5a5a3a", width=1, tags="key")
            cap.create_text(25, 18, text="⏳", font=("Segoe UI", 13), fill="#c0c060", tags="key")
            cap.pack(side=tk.LEFT, padx=2)

            ttk.Label(row, text="Espera", style="Compact.TLabel", anchor="w", width=14).pack(side=tk.LEFT, padx=4)

            wait_var = tk.StringVar(value=str(wait))
            ttk.Label(row, text="s:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(8, 2))
            ttk.Entry(row, textvariable=wait_var, width=6).pack(side=tk.LEFT)
            ttk.Button(row, text="✓", width=2, command=lambda v=wait_var, i=idx: self._update_attr(i, "wait_before", float(v.get()))).pack(side=tk.LEFT, padx=1)

        elif act["action"] == "press":
            key = act.get("key", "?")
            bg, fg = key_color(key)
            disp = key_display(key)

            cap = tk.Canvas(row, width=48, height=36, bg=C["surface"], highlightthickness=0, bd=0)
            x1, y1, x2, y2 = 3, 2, 45, 34
            cap.create_rectangle(x1, y1, x2, y2, fill=bg, outline=fg, width=2, tags="key")
            cap.create_text(24, 18, text=disp, font=("Segoe UI", 11, "bold"), fill=fg, tags="key")
            cap.pack(side=tk.LEFT, padx=2)

            ttk.Label(row, text="Presionar", style="Compact.TLabel", width=14, anchor="w").pack(side=tk.LEFT, padx=4)

            # Duración de presión
            dur = act.get("press_duration", 0.15)
            dur_var = tk.StringVar(value=str(dur))
            ttk.Label(row, text="Presión:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(8, 2))
            ttk.Entry(row, textvariable=dur_var, width=6).pack(side=tk.LEFT)
            ttk.Button(row, text="✓", width=2, command=lambda v=dur_var, i=idx: self._update_attr(i, "press_duration", float(v.get()))).pack(side=tk.LEFT, padx=1)

            # Espera antes del siguiente
            wait = act.get("wait_before", 0.5)
            wait_var = tk.StringVar(value=str(wait))
            ttk.Label(row, text="Espera:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(8, 2))
            ttk.Entry(row, textvariable=wait_var, width=6).pack(side=tk.LEFT)
            ttk.Button(row, text="✓", width=2, command=lambda v=wait_var, i=idx: self._update_attr(i, "wait_before", float(v.get()))).pack(side=tk.LEFT, padx=1)

        elif act["action"] == "click":
            btn = act.get("button", "left")
            bg, fg = KEY_COLORS["mouse"]

            cap = tk.Canvas(row, width=48, height=36, bg=C["surface"], highlightthickness=0, bd=0)
            cap.create_oval(14, 8, 34, 28, fill=bg, outline=fg, width=2)
            if btn == "right":
                cap.create_text(24, 18, text="R", font=("Segoe UI", 9, "bold"), fill=fg)
            else:
                cap.create_text(24, 18, text="🖱", font=("Segoe UI", 12), fill=fg)
            cap.pack(side=tk.LEFT, padx=2)

            ttk.Label(row, text=f"Click {btn}", style="Compact.TLabel", width=14, anchor="w").pack(side=tk.LEFT, padx=4)
            ttk.Label(row, text=f"({act.get('x',0)},{act.get('y',0)})", style="Dim.TLabel", width=12).pack(side=tk.LEFT)

            # Duración de presión del click
            dur = act.get("press_duration", 0.05)
            dur_var = tk.StringVar(value=str(dur))
            ttk.Label(row, text="Presión:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(8, 2))
            ttk.Entry(row, textvariable=dur_var, width=6).pack(side=tk.LEFT)
            ttk.Button(row, text="✓", width=2, command=lambda v=dur_var, i=idx: self._update_attr(i, "press_duration", float(v.get()))).pack(side=tk.LEFT, padx=1)

            # Espera antes del siguiente
            wait = act.get("wait_before", 0.5)
            wait_var = tk.StringVar(value=str(wait))
            ttk.Label(row, text="Espera:", style="Dim.TLabel").pack(side=tk.LEFT, padx=(8, 2))
            ttk.Entry(row, textvariable=wait_var, width=6).pack(side=tk.LEFT)
            ttk.Button(row, text="✓", width=2, command=lambda v=wait_var, i=idx: self._update_attr(i, "wait_before", float(v.get()))).pack(side=tk.LEFT, padx=1)

        # Botones de reorden y acción
        ttk.Button(row, text="⬆", width=2, command=lambda i=idx: self._move_up(i)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row, text="⬇", width=2, command=lambda i=idx: self._move_down(i)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row, text="✎", width=2, command=lambda i=idx: self._edit_action(i)).pack(side=tk.RIGHT, padx=1)
        ttk.Button(row, text="🗑", width=2, command=lambda i=idx: self._remove_action(i)).pack(side=tk.RIGHT, padx=1)

        return row

    def _update_attr(self, idx, attr, value):
        """Actualiza un atributo de una acción y refresca la lista."""
        if 0 <= idx < len(self.actions):
            self.actions[idx][attr] = value
            self._refresh_list()

    def _cumulative_time(self, idx):
        """Tiempo acumulado desde el inicio hasta el inicio de la acción idx."""
        total = 0.0
        for i in range(idx):
            act = self.actions[i]
            total += act.get("wait_before", 0) + act.get("press_duration", 0)
        return total

    # ── Canvas helpers ────────────────────────────────────

    def _on_inner_configure(self, event):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self._canvas.itemconfig(self._win_id, width=event.width)

    def _on_mousewheel(self, event):
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Save ──────────────────────────────────────────────

    def _save_and_close(self):
        name = self.macro_name.get().strip()
        if not name:
            messagebox.showwarning("Falta nombre", "Asigna un nombre a la macro.", parent=self)
            return
        if not self.actions:
            messagebox.showwarning("Sin acciones", "La macro no tiene acciones.", parent=self)
            return

        macro_data = {
            "name": name,
            "actions": self.actions,
        }
        if self.on_save:
            self.on_save(macro_data)
        self.destroy()


# ── Diálogos auxiliares ──────────────────────────────────

class _InputDialog(ctk.CTkToplevel):
    """Diálogo para pedir varios valores de texto."""
    def __init__(self, parent, title, *fields):
        # fields es una secuencia de pares: label, default, label, default, ...
        super().__init__(parent, fg_color=C["bg"])
        self.title(title)
        self.result = None

        frm = ttk.Frame(self, padding=15)
        frm.pack(fill=tk.BOTH, expand=True)

        self._vars = []
        for i in range(0, len(fields), 2):
            lbl = fields[i]
            default = fields[i+1]
            ttk.Label(frm, text=lbl, style="Compact.TLabel").pack(anchor="w", pady=(6, 2))
            var = tk.StringVar(value=default)
            ttk.Entry(frm, textvariable=var, width=30).pack(fill=tk.X)
            self._vars.append(var)

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy, style="Compact.TButton").pack(side=tk.RIGHT, padx=3)
        ttk.Button(btn_frame, text="Aceptar", command=self._ok, style="Accent.TButton").pack(side=tk.RIGHT, padx=3)

        self.transient(parent)
        self.grab_set()
        self.geometry("380x250")
        self.resizable(False, False)
        self._center_on(parent)

        # Foco en el primer campo
        frm.after(100, lambda: frm.winfo_children()[1].focus_set())

    def _ok(self):
        self.result = tuple(v.get() for v in self._vars)
        self.destroy()

    def _center_on(self, parent):
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        dw, dh = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")


class _SimpleInput(ctk.CTkToplevel):
    """Diálogo para un solo valor."""
    def __init__(self, parent, title, label, default):
        super().__init__(parent, fg_color=C["bg"])
        self.title(title)
        self.result = None

        frm = ttk.Frame(self, padding=15)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=label, style="Compact.TLabel").pack(anchor="w", pady=(6, 4))
        self._var = tk.StringVar(value=default)
        ttk.Entry(frm, textvariable=self._var, width=25).pack(fill=tk.X)

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btn_frame, text="Cancelar", command=self.destroy, style="Compact.TButton").pack(side=tk.RIGHT, padx=3)
        ttk.Button(btn_frame, text="Aceptar", command=self._ok, style="Accent.TButton").pack(side=tk.RIGHT, padx=3)

        self.transient(parent)
        self.grab_set()
        self.geometry("300x130")
        self.resizable(False, False)
        self._center_on(parent)
        frm.after(100, lambda: frm.winfo_children()[1].focus_set())

    def _ok(self):
        self.result = self._var.get()
        self.destroy()

    def _center_on(self, parent):
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        dw, dh = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")
