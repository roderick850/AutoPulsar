import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import subprocess
import threading
import time
import ctypes

from config_manager import load_config, save_config, DEFAULT_SETTINGS
from executor import Executor
from hotkey import HotkeyListener
from mini_bar import MiniBar, format_time as mini_format_time


def format_time(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# ── Dark Theme Colors ──────────────────────────────────────────────
DARK_COLORS = {
    "bg":           "#1e1e2e",   # main background
    "surface":      "#282840",   # frames, cards
    "surface_alt":  "#313148",   # alternate surface (treeview rows)
    "border":       "#3b3b56",   # subtle borders
    "text":         "#cdd6f4",   # primary text
    "text_dim":     "#8b8da8",   # secondary text
    "accent":       "#7c7cf8",   # buttons, highlights
    "accent_hover": "#9696ff",   # hover state
    "green":        "#5cce8e",   # success / ready
    "green_dim":    "#3a8a5e",   # darker green
    "red":          "#e06070",   # stop / error
    "yellow":       "#e0b860",   # warning
    "blue":         "#6090e0",   # running
    "purple":       "#b090e0",   # waiting
    "menu_bg":      "#252538",   # menu bar background
    "menu_fg":      "#cdd6f4",   # menu bar text
    "menu_active":  "#3b3b56",   # menu hover
}


def _apply_dark_titlebar(toplevel, retries=5):
    """Dark title bar on Windows 10/11 with retry logic.
    Also forces the window to redraw so the dark mode takes effect."""
    if os.name != "nt":
        return
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    for attempt in range(retries):
        try:
            toplevel.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(toplevel.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int(1)),
            )
            # Force redraw
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                0x0002 | 0x0001
            )
            break
        except Exception:
            if attempt < retries - 1:
                import time
                time.sleep(0.1)
    try:
        toplevel.update_idletasks()
        hwnd2 = toplevel.winfo_id()
        for attr in (19, 20):
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd2, attr,
                    ctypes.byref(ctypes.c_int(1)),
                    ctypes.sizeof(ctypes.c_int(1)),
                )
            except Exception:
                pass
    except Exception:
        pass


class OrchestratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TinyTask Orchestrator")
        self.root.minsize(600, 380)
        self.root.configure(bg=DARK_COLORS["bg"])

        # Dark title bar on Windows (with retry logic)
        _apply_dark_titlebar(self.root)

        # Estado
        config = load_config()
        self.playlist = config["playlist"]
        self.settings = config["settings"]

        # Restore saved window geometry, or use default
        saved_geometry = self.settings.get("window_geometry", "")
        if saved_geometry:
            try:
                self.root.geometry(saved_geometry)
            except tk.TclError:
                self.root.geometry("750x500")
        else:
            self.root.geometry("750x500")
        self.executor_thread = None
        self.stop_event = threading.Event()
        self.launch_event = threading.Event()
        self.is_running = False

        # Hotkey global configurable (toggles: start all / stop)
        self.saved_hotkey = self.settings.get("hotkey", "f10")
        self.hotkey = HotkeyListener()
        self.hotkey.start(self.saved_hotkey, self._hotkey_toggle)
        self.hotkey_var_set_to = self.saved_hotkey.upper()

        # Setup dark theme before building UI
        self._setup_dark_theme()

        # ── Mini Bar state (must be before _build_menu) ──
        self.mini_bar = None
        self._mini_bar_enabled = self.settings.get("mini_bar_enabled", True)

        # ── Treeview item map (iid → type/index) ──
        self._item_map = {}

        # ── Menu Bar ──
        self._build_menu()

        # Construir UI
        self._build_ui()
        self._refresh_list()
        self._update_time_labels()

        # Guardar al cerrar
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ═══════════════════════════════════════════════════════════════
    # MENU BAR
    # ═══════════════════════════════════════════════════════════════

    def _build_menu(self):
        """Custom dark menu bar using Menubutton widgets.
        Native tk.Menu ignores bg on the horizontal bar in Windows -
        Menubutton gives full color control."""
        c = DARK_COLORS

        # Menu bar container frame
        self._menubar_frame = tk.Frame(
            self.root, bg=c["menu_bg"], height=28,
            highlightthickness=0, borderwidth=0
        )
        self._menubar_frame.pack(fill=tk.X, side=tk.TOP)
        self._menubar_frame.pack_propagate(False)

        # Archivo
        file_mb = tk.Menubutton(
            self._menubar_frame, text=" Archivo ",
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=0,
            padx=6, pady=3, cursor="hand2",
        )
        file_mb.pack(side=tk.LEFT)
        file_menu = tk.Menu(
            file_mb, tearoff=0,
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=1, relief="solid",
        )
        file_menu.add_command(label="💾 Guardar playlist", command=self._menu_save,
                              accelerator="Ctrl+S")
        file_menu.add_separator(background=c["border"])
        file_menu.add_command(label="🚪 Salir", command=self._on_close, accelerator="Alt+F4")
        file_mb.config(menu=file_menu)

        # Ver
        view_mb = tk.Menubutton(
            self._menubar_frame, text=" Ver ",
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=0,
            padx=6, pady=3, cursor="hand2",
        )
        view_mb.pack(side=tk.LEFT)
        view_menu = tk.Menu(
            view_mb, tearoff=0,
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=1, relief="solid",
        )
        self._mini_bar_var = tk.BooleanVar(value=self._mini_bar_enabled)
        view_menu.add_checkbutton(
            label="📊 Mini Bar siempre visible",
            variable=self._mini_bar_var,
            command=self._toggle_mini_bar,
            selectcolor=c["surface_alt"],
        )
        view_menu.add_separator(background=c["border"])
        view_menu.add_command(label="🗟️ Restaurar tamaño", command=self._menu_reset_size)
        view_mb.config(menu=view_menu)

        # Ayuda
        help_mb = tk.Menubutton(
            self._menubar_frame, text=" Ayuda ",
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=0,
            padx=6, pady=3, cursor="hand2",
        )
        help_mb.pack(side=tk.LEFT)
        help_menu = tk.Menu(
            help_mb, tearoff=0,
            bg=c["menu_bg"], fg=c["menu_fg"],
            activebackground=c["menu_active"], activeforeground="#ffffff",
            font=("Segoe UI", 9), borderwidth=1, relief="solid",
        )
        help_menu.add_command(label="ℹ️ Acerca de TinyTask Orchestrator",
                              command=self._menu_about)
        help_mb.config(menu=help_menu)

        # Ctrl+S shortcut
        self.root.bind_all("<Control-s>", lambda e: self._menu_save())
    def _menu_save(self):
        """Guardar playlist actual."""
        settings = self._gather_settings()
        save_config(self.playlist, settings)
        self._dark_dialog("Guardado", "Playlist y configuración guardadas.", "success")

    def _menu_reset_size(self):
        """Restaurar tamaño default."""
        self.root.geometry("750x500")
        self._dark_dialog("Tamaño", "Ventana restaurada a 750×500.", "info")

    def _menu_about(self):
        """Mostrar diálogo Acerca de."""
        msg = (
            "TinyTask Orchestrator v1.2.0\n\n"
            "Automatización de tareas con ejecución\n"
            "por tiempos fijos, loops y hotkeys globales.\n\n"
            "Agrupamiento de scripts — organizá tareas\n"
            "en grupos y movelos como bloques. 📁\n\n"
            "Modo Mini Bar para gaming en monitor único.\n\n"
            "Creado por Roderick + Hefesto 🛠️"
        )
        self._dark_dialog("Acerca de", msg, "info")

    def _toggle_mini_bar(self):
        """Activar/desactivar Mini Bar desde el menú."""
        enabled = self._mini_bar_var.get()
        self._mini_bar_enabled = enabled
        if enabled:
            if self.mini_bar is None:
                self._create_mini_bar()
            self.mini_bar.show()
        else:
            if self.mini_bar is not None:
                self.mini_bar.hide()

    def _hide_mini_bar(self):
        """Oculta la Mini Bar (llamado al finalizar ejecución)."""
        if self.mini_bar is not None and self.mini_bar.is_visible():
            self.mini_bar.hide()

    def _create_mini_bar(self):
        """Crear la Mini Bar si no existe."""
        if self.mini_bar is not None:
            return
        self.mini_bar = MiniBar(self, self.settings)
        self.mini_bar.root.lift()

    def _ensure_mini_bar(self):
        """Asegurar que la mini bar existe y está visible."""
        if self.mini_bar is None:
            self._create_mini_bar()
        if not self.mini_bar.is_visible():
            self.mini_bar.show()

    # ═══════════════════════════════════════════════════════════════
    # DARK THEME (sin cambios de lógica, solo colores)
    # ═══════════════════════════════════════════════════════════════

    def _setup_dark_theme(self):
        """Configure ttk styles for a compact dark theme (clam-based)."""
        style = ttk.Style()
        style.theme_use("clam")

        c = DARK_COLORS

        # ── Global defaults ──
        style.configure(".", background=c["bg"], foreground=c["text"],
                        font=("Segoe UI", 9), borderwidth=0)

        # ── Frame ──
        style.configure("TFrame", background=c["bg"])
        style.configure("Dark.TFrame", background=c["surface"])

        # ── LabelFrame ──
        style.configure("TLabelframe", background=c["bg"], foreground=c["text_dim"],
                        bordercolor=c["border"], borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["text_dim"],
                        font=("Segoe UI", 9))

        # ── Label ──
        style.configure("TLabel", background=c["bg"], foreground=c["text"],
                        font=("Segoe UI", 9))
        style.configure("Dark.TLabel", background=c["surface"], foreground=c["text"],
                        font=("Segoe UI", 9))
        style.configure("Dim.TLabel", foreground=c["text_dim"], font=("Segoe UI", 9))
        style.configure("Bold.TLabel", foreground=c["text"], font=("Segoe UI", 9, "bold"))

        # ── Button ──
        style.configure("TButton", background=c["accent"], foreground="#ffffff",
                        borderwidth=0, focusthickness=0, relief="flat",
                        padding=(8, 3), font=("Segoe UI", 9))
        style.map("TButton",
                  background=[("active", c["accent_hover"]),
                              ("disabled", c["surface_alt"])],
                  foreground=[("disabled", c["text_dim"])])

        # ── Entry ──
        style.configure("TEntry", fieldbackground=c["surface_alt"],
                        foreground=c["text"], borderwidth=1,
                        bordercolor=c["border"], relief="solid",
                        padding=(4, 2), insertcolor=c["text"])

        # ── Combobox ──
        style.configure("TCombobox", fieldbackground=c["surface_alt"],
                        background=c["surface_alt"], foreground=c["text"],
                        arrowcolor=c["text"], borderwidth=1,
                        bordercolor=c["border"], relief="solid",
                        padding=(4, 2))
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["surface_alt"]),
                                   ("disabled", c["surface"])],
                  background=[("readonly", c["surface_alt"])],
                  foreground=[("readonly", c["text"]),
                              ("disabled", c["text_dim"])])
        self.root.option_add("*TCombobox*Listbox.background", c["surface_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))

        # ── Treeview ──
        style.configure("Treeview", background=c["surface"],
                        foreground=c["text"], fieldbackground=c["surface"],
                        borderwidth=1, bordercolor=c["border"],
                        relief="solid", rowheight=22)
        style.configure("Treeview.Heading", background=c["surface_alt"],
                        foreground=c["text"], font=("Segoe UI", 8, "bold"),
                        borderwidth=0, relief="flat", padding=(4, 2))
        style.map("Treeview",
                  background=[("selected", c["accent"])],
                  foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading",
                  background=[("active", c["border"])])

        # ── Scrollbar ──
        style.configure("TScrollbar", background=c["bg"],
                        troughcolor=c["surface_alt"], borderwidth=0,
                        arrowsize=12, arrowcolor=c["text_dim"])
        style.map("TScrollbar",
                  background=[("active", c["border"])])

        # ── Progressbar ──
        style.configure("TProgressbar", background=c["green"],
                        troughcolor=c["surface_alt"], borderwidth=0,
                        thickness=8)

        # ── Spinbox ──
        style.configure("TSpinbox", fieldbackground=c["surface_alt"],
                        foreground=c["text"], borderwidth=1,
                        bordercolor=c["border"], relief="solid",
                        padding=(4, 2), arrowcolor=c["text"],
                        insertcolor=c["text"])

        # ── Compact variants ──
        style.configure("Compact.TButton", padding=(5, 1), font=("Segoe UI", 8))
        style.configure("Compact.TLabel", font=("Segoe UI", 8))
        style.configure("Compact.TEntry", padding=(2, 1), font=("Segoe UI", 8))

    def _build_ui(self):
        c = DARK_COLORS

        # ===== Frame Configuración del Loop (compacto) =====
        loop_frame = ttk.LabelFrame(self.root, text=" Loop ", padding=5)
        loop_frame.pack(fill=tk.X, padx=5, pady=(5, 3))

        ttk.Label(loop_frame, text="Modo:", style="Compact.TLabel").pack(side=tk.LEFT, padx=(0, 3))
        self.loop_mode_var = tk.StringVar(value=self.settings.get("loop_mode", "once"))
        mode_combo = ttk.Combobox(
            loop_frame,
            textvariable=self.loop_mode_var,
            values=["once", "fixed", "infinite"],
            width=10,
            state="readonly",
        )
        mode_combo.pack(side=tk.LEFT, padx=2)
        mode_combo.bind("<<ComboboxSelected>>", self._on_loop_mode_change)

        ttk.Label(loop_frame, text="×", style="Compact.TLabel").pack(side=tk.LEFT, padx=(8, 3))
        self.loop_count_var = tk.StringVar(value=str(self.settings.get("loop_count", 1)))
        self.loop_count_entry = ttk.Entry(loop_frame, textvariable=self.loop_count_var, width=6, validate="key")
        self.loop_count_entry.config(validatecommand=(self.root.register(self._validate_int_positive), "%P"))
        self.loop_count_entry.pack(side=tk.LEFT, padx=2)

        ttk.Label(loop_frame, text="Pausa:", style="Compact.TLabel").pack(side=tk.LEFT, padx=(10, 3))
        self.loop_delay_var = tk.StringVar(value=str(self.settings.get("loop_delay", 0)))
        self.loop_delay_entry = ttk.Entry(loop_frame, textvariable=self.loop_delay_var, width=5, validate="key")
        self.loop_delay_entry.config(validatecommand=(self.root.register(self._validate_int_non_negative), "%P"))
        self.loop_delay_entry.pack(side=tk.LEFT, padx=2)
        ttk.Label(loop_frame, text="s", style="Dim.TLabel").pack(side=tk.LEFT)

        # Tiempo estimado total
        self.total_time_label = ttk.Label(loop_frame, text="Total: 0s", style="Dim.TLabel")
        self.total_time_label.pack(side=tk.RIGHT, padx=5)

        self._on_loop_mode_change(None)

        # ===== Frame lista (principal, expande) =====
        list_frame = ttk.Frame(self.root)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=3)

        columns = ("orden", "hab", "primero", "nombre", "reps", "duracion", "pausa", "tiempo")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="tree headings", selectmode="extended",
        )
        # Column #0 = tree column (expander arrows for groups)
        self.tree.column("#0", width=30, minwidth=24, stretch=False, anchor="w")
        self.tree.heading("#0", text="")

        self.tree.heading("orden", text="#")
        self.tree.heading("hab", text="✓")
        self.tree.heading("primero", text="1°")
        self.tree.heading("nombre", text="Script")
        self.tree.heading("reps", text="Reps")
        self.tree.heading("duracion", text="Dur (s)")
        self.tree.heading("pausa", text="Pausa (s)")
        self.tree.heading("tiempo", text="Tiempo")

        self.tree.column("orden", width=28, anchor="center")
        self.tree.column("hab", width=26, anchor="center")
        self.tree.column("primero", width=28, anchor="center")
        self.tree.column("nombre", width=190, anchor="w")
        self.tree.column("reps", width=50, anchor="center")
        self.tree.column("duracion", width=55, anchor="center")
        self.tree.column("pausa", width=55, anchor="center")
        self.tree.column("tiempo", width=70, anchor="center")

        # Click on checkbox column toggles enabled/disabled
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        # Double-click on editable columns for inline editing
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        # Right-click context menu
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        # Track collapse/expand in real time so persisted state is always accurate
        self.tree.bind("<<TreeviewOpen>>", self._on_group_expand_collapse)
        self.tree.bind("<<TreeviewClose>>", self._on_group_expand_collapse)
        self._inline_entry = None

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        # Ensure treeview respects its container height (don't expand to show all rows)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # Mousewheel scrolling
        def _on_mousewheel(event):
            self.tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.tree.bind("<MouseWheel>", _on_mousewheel)

        # ===== Frame botones (compacto) =====
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=5, pady=(0, 3))

        ttk.Button(btn_frame, text="➕ Agregar", command=self._add_script, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="✏️ Editar", command=self._edit_script, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="📋 Clonar", command=self._clone_script, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="🗑️ Quitar", command=self._remove_script, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_frame, text="⬆", command=self._move_up, style="Compact.TButton", width=3).pack(
            side=tk.LEFT, padx=(8, 1)
        )
        ttk.Button(btn_frame, text="⬇", command=self._move_down, style="Compact.TButton", width=3).pack(
            side=tk.LEFT, padx=1
        )

        # ===== Frame botones de grupo =====
        group_btn_frame = ttk.Frame(self.root)
        group_btn_frame.pack(fill=tk.X, padx=5, pady=(0, 3))

        ttk.Button(group_btn_frame, text="📁 Agrupar", command=self._group_selected, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(group_btn_frame, text="✂️ Desagrupar", command=self._ungroup_selected, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(group_btn_frame, text="🏷️ Renombrar", command=self._rename_group, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )

        # ===== Frame ejecución (compacto) =====
        exec_frame = ttk.LabelFrame(self.root, text=" Ejecución ", padding=5)
        exec_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        # Status visual con colores sobre fondo oscuro
        self.status_label = tk.Label(
            exec_frame,
            text=" LISTO ",
            font=("Segoe UI", 9, "bold"),
            fg="#ffffff",
            bg=c["green"],
            padx=8,
            pady=2,
        )
        self.status_label.pack(anchor=tk.W, pady=(0, 3))

        # Progress bar + percentage label
        progress_frame = ttk.Frame(exec_frame)
        progress_frame.pack(fill=tk.X, pady=2)

        self.progress = ttk.Progressbar(
            progress_frame, orient=tk.HORIZONTAL, mode="determinate"
        )
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_pct_label = ttk.Label(progress_frame, text="0%", width=5, style="Compact.TLabel")
        self.progress_pct_label.pack(side=tk.LEFT, padx=(3, 0))

        # Botones de acción
        ttk.Button(exec_frame, text="▶ Iniciar", command=self._start, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(exec_frame, text="▶1 Seleccionado", command=self._run_selected, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(exec_frame, text="⏹ Detener", command=self._stop, style="Compact.TButton").pack(
            side=tk.LEFT, padx=2
        )

        # Hotkey configurable
        ttk.Label(exec_frame, text="Hotkey:", style="Compact.TLabel").pack(side=tk.LEFT, padx=(10, 3))
        self.hotkey_var = tk.StringVar(value=self.hotkey_var_set_to)
        hotkey_combo = ttk.Combobox(
            exec_frame,
            textvariable=self.hotkey_var,
            values=["F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
            width=4,
            state="readonly",
        )
        hotkey_combo.pack(side=tk.LEFT, padx=2)
        ttk.Label(exec_frame, text="(solo ▶ Iniciar todo / ⏹ Detener)", style="Dim.TLabel").pack(side=tk.LEFT, padx=(3, 0))
        hotkey_combo.bind("<<ComboboxSelected>>", self._on_hotkey_change)

        # Countdown timer
        self.countdown_label = ttk.Label(exec_frame, text="⏱ --:--", style="Bold.TLabel")
        self.countdown_label.pack(side=tk.RIGHT, padx=5)

    # ═══════════════════════════════════════════════════════════════
    # HOTKEY
    # ═══════════════════════════════════════════════════════════════

    def _on_hotkey_change(self, event):
        new_key = self.hotkey_var.get().lower()
        self.hotkey.stop()
        self.hotkey.start(new_key, self._hotkey_toggle)
        self.saved_hotkey = new_key
        self.settings["hotkey"] = new_key

    def _hotkey_toggle(self):
        """Called by the global hotkey.
        - If running: stops execution (works for any running mode).
        - If idle: starts the entire playlist."""
        # Debounce: ignore presses within 500ms to prevent accidental double-tap
        now = time.time()
        last = getattr(self, "_last_hotkey", 0)
        if now - last < 0.5:
            return
        self._last_hotkey = now
        def action():
            if self.is_running:
                self._stop()
            else:
                self._start()
        self.root.after(0, action)

    # ═══════════════════════════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════════════════════════

    def _validate_int_positive(self, value):
        if value == "":
            return True
        try:
            v = int(value)
            return v >= 1
        except ValueError:
            return False

    def _validate_int_non_negative(self, value):
        if value == "":
            return True
        try:
            v = int(value)
            return v >= 0
        except ValueError:
            return False

    # ═══════════════════════════════════════════════════════════════
    # TIME CALCULATIONS
    # ═══════════════════════════════════════════════════════════════

    # Overhead constants (must match executor.py)
    _LAUNCH_BUFFER = 2.0     # Post-launch buffer per execution
    _INITIAL_SLEEP = 1.0     # Initial sleep before first execution

    def _calc_item_time(self, item):
        reps = item["repetitions"]
        duration = item["duration"]
        pause = item["pause"]
        # Last repetition has no trailing pause
        task_time = (duration + pause) * reps - pause
        # Each execution has a launch buffer overhead
        overhead = self._LAUNCH_BUFFER * reps
        return max(task_time + overhead, 0)

    def _parse_int(self, var, default=0):
        try:
            return int(var.get())
        except (ValueError, TypeError):
            return default

    def _calc_total_time(self, playlist=None, settings=None):
        if playlist is None:
            # When showing the UI estimate, only count enabled items
            playlist = [item for item in self.playlist if item.get("enabled", True)]
        target = playlist
        # Sum item times (already includes per-launch buffer overhead)
        loop_time = sum(self._calc_item_time(item) for item in target)
        # Add initial sleep overhead (once per run)
        loop_time += self._INITIAL_SLEEP
        # Use settings if provided (from _execute), otherwise read from UI
        if settings:
            mode = settings.get("loop_mode", "once")
            count = settings.get("loop_count", 1) if mode == "fixed" else 1
            delay = settings.get("loop_delay", 0)
        else:
            mode = self.loop_mode_var.get()
            count = self._parse_int(self.loop_count_var, 1) if mode == "fixed" else 1
            delay = self._parse_int(self.loop_delay_var, 0)
        if mode == "infinite":
            return None  # Infinite
        # first_loop_only items count only once regardless of loop count
        once_time = sum(self._calc_item_time(item) for item in target
                        if item.get("first_loop_only", False))
        repeat_time = sum(self._calc_item_time(item) for item in target
                          if not item.get("first_loop_only", False))
        total = once_time + repeat_time * count + delay * max(count - 1, 0)
        return total

    def _update_time_labels(self):
        total = self._calc_total_time()
        if total is None:
            self.total_time_label.config(text="Total: ∞")
        else:
            self.total_time_label.config(text=f"Total: {format_time(total)}")

    def _on_loop_mode_change(self, event):
        mode = self.loop_mode_var.get()
        if mode == "infinite":
            self.loop_count_entry.config(state="disabled")
        else:
            self.loop_count_entry.config(state="normal")
        self._update_time_labels()

    # ═══════════════════════════════════════════════════════════════
    # PLAYLIST UI
    # ═══════════════════════════════════════════════════════════════

    def _refresh_list(self):
        """Rebuild treeview from flat playlist using group paths for hierarchy."""
        # ── Capture current open/close state of all groups ──
        old_open_state = {}
        for iid, (typ, data) in self._item_map.items():
            if typ == "group":
                old_open_state[data] = self.tree.item(iid, "open")

        # ── First load: restore from persisted settings ──
        if not old_open_state:
            persisted = self.settings.get("collapsed_groups", [])
            for path in persisted:
                old_open_state[path] = False

        for i in self.tree.get_children():
            self.tree.delete(i)
        self._item_map.clear()

        # Tag styles for visual differentiation
        self.tree.tag_configure("group_row",
            font=("Segoe UI", 9, "bold"),
            background="#1a3048",  # azul más intenso para headers de grupo
            foreground="#cdd6f4",
        )
        self.tree.tag_configure("script_grouped",
            font=("Segoe UI", 9),
            background="#1e2a3a",  # azul oscuro — contraste claro con surface
            foreground=DARK_COLORS["text"],
        )
        self.tree.tag_configure("script_ungrouped",
            font=("Segoe UI", 9),
            background=DARK_COLORS["surface"],
            foreground=DARK_COLORS["text"],
        )

        # group_nodes: {group_path: treeview_iid}
        group_nodes = {}

        for idx, item in enumerate(self.playlist):
            group = item.get("group", None)
            item_time = self._calc_item_time(item)
            enabled = item.get("enabled", True)
            check = "✅" if enabled else "❌"
            primero = "🔂" if item.get("first_loop_only", False) else ""

            if group:
                parts = group.split("/")
                parent_iid = ""
                current_path = ""

                for depth, part in enumerate(parts):
                    current_path = f"{current_path}/{part}" if current_path else part
                    if current_path not in group_nodes:
                        # Indent subgroup names same formula as scripts
                        group_indent = "  " + "    " * depth
                        group_iid = self.tree.insert(
                            parent_iid, tk.END,
                            text=" ",  # columna árbol necesita contenido para jerarquía
                            values=("", "", "", f"{group_indent}📁 {part}", "", "", "", ""),
                            tags=("group_row",),
                            open=old_open_state.get(current_path, True),
                        )
                        group_nodes[current_path] = group_iid
                        self._item_map[group_iid] = ("group", current_path)
                    parent_iid = group_nodes[current_path]

                # Script inside a group — tinted background
                # Indent script name: 2 spaces base + 4 per extra nesting depth
                indent = "  " + "    " * (len(parts) - 1)
                script_iid = self.tree.insert(
                    parent_iid, tk.END,
                    text=" ",
                    values=(idx + 1, check, primero, f"{indent}{os.path.basename(item['path'])}",
                            item["repetitions"], item["duration"],
                            item["pause"], format_time(item_time)),
                    tags=("script_grouped",),
                )
            else:
                # Ungrouped — default surface background
                script_iid = self.tree.insert(
                    "", tk.END,
                    text=" ",
                    values=(idx + 1, check, primero, os.path.basename(item["path"]),
                            item["repetitions"], item["duration"],
                            item["pause"], format_time(item_time)),
                    tags=("script_ungrouped",),
                )
            self._item_map[script_iid] = ("script", idx)

        # ── Update group rows with enabled/primero summary ──
        for path, iid in group_nodes.items():
            indices = [
                i for i, item in enumerate(self.playlist)
                if item.get("group") and
                (item["group"] == path or item["group"].startswith(path + "/"))
            ]
            if indices:
                enabled_count = sum(1 for i in indices if self.playlist[i].get("enabled", True))
                primero_count = sum(1 for i in indices if self.playlist[i].get("first_loop_only", False))
                total = len(indices)
                hab_text = "✅" if enabled_count == total else f"{enabled_count}/{total}"
                primero_text = "🔂" if primero_count == total and total > 0 else (str(primero_count) if primero_count else "")
                current = list(self.tree.item(iid, "values"))
                current[1] = hab_text   # col #2 (hab)
                current[2] = primero_text  # col #3 (primero)
                self.tree.item(iid, values=current)

        self._update_time_labels()

        # ── Persist collapsed group state ──
        collapsed = []
        for iid, (typ, data) in self._item_map.items():
            if typ == "group" and not self.tree.item(iid, "open"):
                collapsed.append(data)
        self.settings["collapsed_groups"] = collapsed

        # Force scrollbar to update (sometimes gets stale after full rebuild)
        self.tree.update_idletasks()

    def _on_tree_click(self, event):
        """Toggle enabled/disabled or first_loop_only when clicking their columns.
        On group rows, toggles all scripts inside the group."""
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)

        if region != "cell" or not item_id:
            return

        info = self._item_map.get(item_id)
        if info is None:
            return

        # ── Group toggle: apply to all scripts in the group ──
        if info[0] == "group":
            group_path = info[1]
            indices = self._get_group_indices(group_path)
            if not indices:
                return
            if column == "#2":  # hab
                any_enabled = any(self.playlist[i].get("enabled", True) for i in indices)
                new_state = not any_enabled
                for i in indices:
                    self.playlist[i]["enabled"] = new_state
                self._refresh_list()
            elif column == "#3":  # primero
                any_primero = any(self.playlist[i].get("first_loop_only", False) for i in indices)
                new_state = not any_primero
                for i in indices:
                    self.playlist[i]["first_loop_only"] = new_state
                self._refresh_list()
            return

        # ── Script toggle ──
        if info[0] != "script":
            return

        idx = info[1]

        if column == "#2":  # hab (enabled/disabled)
            current = self.playlist[idx].get("enabled", True)
            self.playlist[idx]["enabled"] = not current
            self._refresh_list()
        elif column == "#3":  # primero (first_loop_only)
            current = self.playlist[idx].get("first_loop_only", False)
            self.playlist[idx]["first_loop_only"] = not current
            self._refresh_list()

    def _on_group_expand_collapse(self, event):
        """Real-time sync of collapsed state when user clicks expand/collapse arrows."""
        # Update saved collapsed list immediately from current tree state
        collapsed = []
        for iid, (typ, data) in self._item_map.items():
            if typ == "group" and not self.tree.item(iid, "open"):
                collapsed.append(data)
        self.settings["collapsed_groups"] = collapsed

    def _on_tree_double_click(self, event):
        """Inline editing: double-click on reps/duration/pause cell to edit directly.
        Double-click on group header renames the group."""
        self._dismiss_inline_edit()

        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        item_id = self.tree.identify_row(event.y)

        info = self._item_map.get(item_id)
        if info is None:
            return

        # ── Double-click on group header → rename ──
        if info[0] == "group":
            self._rename_group_dialog(info[1])
            return

        # ── Script editing ──
        if region != "cell":
            return
        idx = info[1]

        editable_columns = {"#5": "repetitions", "#6": "duration", "#7": "pause"}
        if column not in editable_columns:
            return
        field = editable_columns[column]
        current_value = self.playlist[idx][field]

        # Get cell bounding box
        bbox = self.tree.bbox(item_id, column)
        if not bbox:
            return

        x, y, width, height = bbox

        # Create entry overlay on the cell
        entry = ttk.Entry(self.tree, justify="center")
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, str(current_value))
        entry.select_range(0, tk.END)
        entry.focus_set()
        self._inline_entry = entry

        # Validation function per field
        if field == "repetitions":
            validate_fn = self._validate_int_positive
        else:
            validate_fn = self._validate_int_non_negative

        def save_edit(*args):
            value = entry.get().strip()
            if value == "":
                # Empty — revert to original (don't save)
                self._dismiss_inline_edit()
                return
            if not validate_fn(value):
                # Invalid — revert
                self._dismiss_inline_edit()
                return
            try:
                new_val = int(value)
            except ValueError:
                self._dismiss_inline_edit()
                return

            self.playlist[idx][field] = new_val
            self._refresh_list()
            # Re-select the edited item
            children = self.tree.get_children()
            if idx < len(children):
                self.tree.selection_set(children[idx])
            self._dismiss_inline_edit()

        def cancel_edit(*args):
            self._dismiss_inline_edit()

        entry.bind("<Return>", save_edit)
        entry.bind("<Escape>", cancel_edit)
        entry.bind("<FocusOut>", save_edit)

    def _dismiss_inline_edit(self):
        """Destroy the inline editing entry if one exists."""
        if self._inline_entry is not None:
            try:
                self._inline_entry.destroy()
            except tk.TclError:
                pass
            self._inline_entry = None

    # ═══════════════════════════════════════════════════════════════
    # RIGHT-CLICK CONTEXT MENU
    # ═══════════════════════════════════════════════════════════════

    def _on_tree_right_click(self, event):
        """Show context menu on right-click. Does NOT modify selection."""
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return

        info = self._item_map.get(item_id)
        if info is None:
            return

        menu = tk.Menu(self.root, tearoff=0,
                       bg=DARK_COLORS["menu_bg"], fg=DARK_COLORS["menu_fg"],
                       activebackground=DARK_COLORS["menu_active"],
                       activeforeground="#ffffff",
                       font=("Segoe UI", 9))

        if info[0] == "group":
            group_path = info[1]
            menu.add_command(label="📁 Crear subgrupo aquí",
                           command=lambda: self._context_create_subgroup(group_path))
            menu.add_command(label="➕ Agregar scripts al grupo",
                           command=lambda: self._context_add_to_group(group_path))
            menu.add_command(label="🏷️ Renombrar",
                           command=lambda: self._rename_group_dialog(group_path))
            menu.add_separator()
            menu.add_command(label="✂️ Desagrupar todo",
                           command=lambda: self._context_ungroup(group_path))
            menu.add_command(label="🗑️ Eliminar grupo",
                           command=lambda: self._context_remove_group(group_path))

        elif info[0] == "script":
            menu.add_command(label="📁 Agrupar seleccionados",
                           command=self._group_selected)
            menu.add_command(label="✂️ Desagrupar",
                           command=self._ungroup_selected)

        menu.tk_popup(event.x_root, event.y_root)

    def _context_create_subgroup(self, parent_path):
        """Create a subgroup under parent_path using currently selected scripts."""
        sel = self.tree.selection()
        indices = []
        for iid in sel:
            info = self._item_map.get(iid)
            if info and info[0] == "script":
                indices.append(info[1])

        if not indices:
            self._dark_dialog("Subgrupo",
                "Seleccioná los scripts que querés mover al subgrupo,\n"
                "luego clic derecho en el grupo destino → Crear subgrupo.", "info")
            return

        label = f"Crear subgrupo dentro de «{parent_path}»\nNombre:"
        self._ask_group_name(lambda name: self._do_group(indices, name, parent_path),
                           label=label)

    def _context_add_to_group(self, group_path):
        """Add currently selected scripts to an existing group (right-click menu)."""
        sel = self.tree.selection()
        indices = []
        for iid in sel:
            info = self._item_map.get(iid)
            if info and info[0] == "script":
                indices.append(info[1])

        if not indices:
            self._dark_dialog("Agregar a grupo",
                "Seleccioná los scripts que querés agregar,\n"
                "luego clic derecho en el grupo destino → Agregar scripts.", "info")
            return

        # Filter: only add scripts not already in this group
        existing = set(self._get_group_indices(group_path))
        new_indices = [i for i in indices if i not in existing]
        if not new_indices:
            self._dark_dialog("Ya están",
                "Esos scripts ya pertenecen al grupo «{}».".format(group_path), "info")
            return

        # Assign group to the new scripts
        for idx in new_indices:
            self.playlist[idx]["group"] = group_path

        # Find where the group block currently sits
        all_group = self._get_group_indices(group_path)
        last_group_pos = max(all_group)

        # Extract the new items from their current positions (reverse order)
        new_items = []
        for idx in sorted(new_indices, reverse=True):
            new_items.insert(0, self.playlist.pop(idx))

        # Recalculate insertion point (may have shifted after removals)
        all_group = self._get_group_indices(group_path)
        insert_at = max(all_group) + 1 if all_group else last_group_pos

        # Insert new items after the last existing group item
        for item in new_items:
            self.playlist.insert(insert_at, item)
            insert_at += 1

        self._refresh_list()

    def _context_ungroup(self, group_path):
        """Ungroup all items in a group (via right-click menu)."""
        for idx in self._get_group_indices(group_path):
            self.playlist[idx]["group"] = None
        self._refresh_list()

    def _context_remove_group(self, group_path):
        """Remove all items in a group (via right-click menu)."""
        indices = self._get_group_indices(group_path)
        for i in sorted(indices, reverse=True):
            del self.playlist[i]
        self._refresh_list()

    # ═══════════════════════════════════════════════════════════════
    # DIALOGS
    # ═══════════════════════════════════════════════════════════════

    def _dark_dialog(self, title, message, kind="info"):
        """Custom dark-themed dialog to replace native messagebox."""
        colors = {"info": DARK_COLORS["blue"], "warning": DARK_COLORS["yellow"],
                  "error": DARK_COLORS["red"], "success": DARK_COLORS["green"]}
        accent = colors.get(kind, DARK_COLORS["blue"])

        dlg = tk.Toplevel(self.root, bg=DARK_COLORS["bg"])
        # Dark titlebar on dialog
        dlg.after(50, lambda: _apply_dark_titlebar(dlg, retries=3))
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.lift()

        frame = ttk.Frame(dlg, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=message, style="Bold.TLabel",
                  wraplength=350, justify=tk.CENTER).pack(pady=(5, 12))

        btn = tk.Button(frame, text="  Aceptar  ",
                        bg=accent, fg="#ffffff", font=("Segoe UI", 9, "bold"),
                        borderwidth=0, activebackground=DARK_COLORS["accent_hover"],
                        cursor="hand2", padx=20, pady=4,
                        command=dlg.destroy)
        btn.pack()

        # Center on parent
        dlg.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        dlg.wait_window()

    def _add_script(self):
        path = filedialog.askopenfilename(
            title="Seleccionar script TinyTask",
            filetypes=[("Ejecutables", "*.exe"), ("Todos", "*.*")],
        )
        if not path:
            return

        if not os.path.isfile(path):
            self._dark_dialog("Error", f"El archivo no existe:\n{path}", "error")
            return

        win = tk.Toplevel(self.root, bg=DARK_COLORS["bg"])
        win.title("Agregar script")
        win.geometry("300x260")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        # Dark titlebar
        win.after(50, lambda: _apply_dark_titlebar(win, retries=3))

        # Center on parent
        win.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = win.winfo_width(), win.winfo_height()
        win.geometry(f"300x260+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        ttk.Label(form, text=f"Script: {os.path.basename(path)}", style="Dim.TLabel").pack(pady=(0, 10))

        row1 = ttk.Frame(form)
        row1.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row1, text="Repeticiones:", style="Compact.TLabel").pack(side=tk.LEFT)
        reps_var = tk.IntVar(value=1)
        ttk.Spinbox(row1, from_=1, to=999, textvariable=reps_var, width=8).pack(side=tk.RIGHT)

        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row2, text="Duración (s):", style="Compact.TLabel").pack(side=tk.LEFT)
        dur_var = tk.IntVar(value=10)
        ttk.Spinbox(row2, from_=1, to=9999, textvariable=dur_var, width=8).pack(side=tk.RIGHT)

        row3 = ttk.Frame(form)
        row3.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row3, text="Pausa entre reps (s):", style="Compact.TLabel").pack(side=tk.LEFT)
        pause_var = tk.IntVar(value=0)
        ttk.Spinbox(row3, from_=0, to=9999, textvariable=pause_var, width=8).pack(side=tk.RIGHT)

        time_preview = ttk.Label(form, text="Tiempo: 10s", style="Dim.TLabel")
        time_preview.pack(pady=(0, 8))

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.config(text=f"Tiempo: {format_time(total)}")

        reps_var.trace_add("write", update_preview)
        dur_var.trace_add("write", update_preview)
        pause_var.trace_add("write", update_preview)

        def save():
            self.playlist.append(
                {
                    "path": path,
                    "repetitions": reps_var.get(),
                    "duration": dur_var.get(),
                    "pause": pause_var.get(),
                    "enabled": True,
                    "first_loop_only": False,
                    "group": None,
                }
            )
            self._refresh_list()
            win.destroy()

        ttk.Button(form, text="Guardar", command=save, style="Compact.TButton").pack()

    def _edit_script(self):
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná un script de la lista para editarlo.", "info")
            return
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "script":
            self._dark_dialog("Grupo", "Seleccioná un script, no un header de grupo.", "info")
            return
        idx = info[1]
        item = self.playlist[idx]

        win = tk.Toplevel(self.root, bg=DARK_COLORS["bg"])
        win.title("Editar script")
        win.geometry("300x280")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        # Dark titlebar
        win.after(50, lambda: _apply_dark_titlebar(win, retries=3))

        # Center on parent
        win.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = win.winfo_width(), win.winfo_height()
        win.geometry(f"300x280+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        ttk.Label(form, text=f"Script: {os.path.basename(item['path'])}", style="Dim.TLabel").pack(pady=(0, 10))

        row1 = ttk.Frame(form)
        row1.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row1, text="Repeticiones:", style="Compact.TLabel").pack(side=tk.LEFT)
        reps_var = tk.IntVar(value=item["repetitions"])
        ttk.Spinbox(row1, from_=1, to=999, textvariable=reps_var, width=8).pack(side=tk.RIGHT)

        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row2, text="Duración (s):", style="Compact.TLabel").pack(side=tk.LEFT)
        dur_var = tk.IntVar(value=item["duration"])
        ttk.Spinbox(row2, from_=1, to=9999, textvariable=dur_var, width=8).pack(side=tk.RIGHT)

        row3 = ttk.Frame(form)
        row3.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row3, text="Pausa entre reps (s):", style="Compact.TLabel").pack(side=tk.LEFT)
        pause_var = tk.IntVar(value=item["pause"])
        ttk.Spinbox(row3, from_=0, to=9999, textvariable=pause_var, width=8).pack(side=tk.RIGHT)

        time_preview = ttk.Label(form, text=f"Tiempo: {format_time(self._calc_item_time(item))}", style="Dim.TLabel")
        time_preview.pack(pady=(0, 8))

        def update_preview(*args):
            total = (dur_var.get() + pause_var.get()) * reps_var.get() - pause_var.get()
            total = max(total, 0)
            time_preview.config(text=f"Tiempo: {format_time(total)}")

        reps_var.trace_add("write", update_preview)
        dur_var.trace_add("write", update_preview)
        pause_var.trace_add("write", update_preview)

        def save():
            self.playlist[idx] = {
                "path": item["path"],
                "repetitions": reps_var.get(),
                "duration": dur_var.get(),
                "pause": pause_var.get(),
            }
            self._refresh_list()
            win.destroy()

        ttk.Button(form, text="Guardar cambios", command=save, style="Compact.TButton").pack()

    def _remove_script(self):
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None:
            return

        item_type, ref = info

        if item_type == "group":
            # Remove all items in the group
            indices = self._get_group_indices(ref)
            for i in sorted(indices, reverse=True):
                del self.playlist[i]
        elif item_type == "script":
            del self.playlist[ref]
        self._refresh_list()

    def _clone_script(self):
        """Duplicar el script seleccionado."""
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná un script de la lista para clonarlo.", "info")
            return
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "script":
            self._dark_dialog("Grupo", "Seleccioná un script, no un header de grupo.", "info")
            return
        idx = info[1]
        original = self.playlist[idx]
        clone = dict(original)
        self.playlist.insert(idx + 1, clone)
        self._refresh_list()

    def _move_up(self):
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None:
            return

        item_type, ref = info

        if item_type == "group":
            self._move_block_up(ref)
        elif item_type == "script":
            idx = ref
            if idx > 0:
                cur_group = self.playlist[idx].get("group", None)
                prev_group = self.playlist[idx - 1].get("group", None)
                if cur_group == prev_group:
                    # Same group — simple swap
                    self.playlist[idx], self.playlist[idx - 1] = (
                        self.playlist[idx - 1],
                        self.playlist[idx],
                    )
                    self._refresh_list()
                    self._reselect_script(idx - 1)
                else:
                    # Different group — try block-level swap
                    self._move_script_past_block(idx, direction="up")

    def _move_down(self):
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None:
            return

        item_type, ref = info

        if item_type == "group":
            self._move_block_down(ref)
        elif item_type == "script":
            idx = ref
            if idx < len(self.playlist) - 1:
                cur_group = self.playlist[idx].get("group", None)
                nxt_group = self.playlist[idx + 1].get("group", None)
                if cur_group == nxt_group:
                    # Same group — simple swap
                    self.playlist[idx], self.playlist[idx + 1] = (
                        self.playlist[idx + 1],
                        self.playlist[idx],
                    )
                    self._refresh_list()
                    self._reselect_script(idx + 1)
                else:
                    # Different group — try block-level swap
                    self._move_script_past_block(idx, direction="down")

    # ═══════════════════════════════════════════════════════════════
    # GROUP / BLOCK MOVEMENT
    # ═══════════════════════════════════════════════════════════════

    def _move_script_past_block(self, idx, direction):
        """Swap a script with the entire adjacent block of items that share
        the same group (different from the script's own group)."""
        if direction == "up":
            if idx <= 0:
                return
            # Find the block above: items sharing the same group as the item directly above
            adj_group = self.playlist[idx - 1].get("group")
            block_start = idx - 1
            while block_start > 0 and self.playlist[block_start - 1].get("group") == adj_group:
                block_start -= 1
            above_block = self.playlist[block_start:idx]
            script = self.playlist[idx:idx + 1]
            # Swap: script moves before the block
            self.playlist = (self.playlist[:block_start] + script + above_block +
                             self.playlist[idx + 1:])
            new_idx = block_start
        else:  # down
            if idx >= len(self.playlist) - 1:
                return
            # Find the block below: items sharing the same group as the item directly below
            adj_group = self.playlist[idx + 1].get("group")
            block_end = idx + 2
            while (block_end < len(self.playlist) and
                   self.playlist[block_end].get("group") == adj_group):
                block_end += 1
            below_block = self.playlist[idx + 1:block_end]
            script = self.playlist[idx:idx + 1]
            # Swap: script moves after the block
            self.playlist = (self.playlist[:idx] + below_block + script +
                             self.playlist[block_end:])
            new_idx = idx + len(below_block)

        self._refresh_list()
        self._reselect_script(new_idx)

    def _get_group_indices(self, group_name):
        """Return playlist indices for items in a group, including nested children."""
        return [i for i, item in enumerate(self.playlist)
                if item.get("group") and
                (item["group"] == group_name or item["group"].startswith(group_name + "/"))]

    def _to_blocks(self):
        """Convert playlist to top-level blocks (grouped by first segment of group path)."""
        blocks = []
        i = 0
        while i < len(self.playlist):
            item = self.playlist[i]
            group = item.get("group", None)
            if group:
                top_group = group.split("/")[0]
                block = []
                while i < len(self.playlist) and self.playlist[i].get("group", "").startswith(top_group):
                    block.append(self.playlist[i])
                    i += 1
                blocks.append(block)
            else:
                blocks.append([item])
                i += 1
        return blocks

    def _blocks_to_playlist(self, blocks):
        """Flatten blocks back to playlist."""
        result = []
        for block in blocks:
            result.extend(block)
        return result

    def _find_block_index(self, blocks, group_name):
        """Find the index of the top-level block for a group (first segment)."""
        top = group_name.split("/")[0]
        for bi, block in enumerate(blocks):
            first_group = block[0].get("group", "")
            if first_group and first_group.startswith(top):
                return bi
        return -1

    def _reselect_group(self, group_path):
        """After _refresh_list, find and re-select the group header."""
        def _scan(parent):
            for iid in self.tree.get_children(parent):
                info = self._item_map.get(iid)
                if info and info[0] == "group" and info[1] == group_path:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                    return True
                if _scan(iid):
                    return True
            return False
        _scan("")

    def _reselect_script(self, playlist_idx):
        """After _refresh_list, find and re-select a script by playlist index."""
        for iid, (typ, data) in self._item_map.items():
            if typ == "script" and data == playlist_idx:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                return

    def _get_subgroup_range(self, group_path):
        """Return (start, end+1) indices in playlist for items in this group/subgroup.
        Returns None if group not found or items are scattered."""
        indices = [i for i, item in enumerate(self.playlist)
                   if item.get("group") == group_path or
                   (item.get("group") and item["group"].startswith(group_path + "/"))]
        if not indices:
            return None
        # Verify contiguity
        if indices != list(range(indices[0], indices[-1] + 1)):
            return None
        return (indices[0], indices[-1] + 1)

    def _move_block_up(self, group_path):
        """Move a group block up. Handles both top-level groups and sub-groups."""
        # Try sub-group range first
        rng = self._get_subgroup_range(group_path)
        if rng and rng[0] > 0:
            start, end = rng
            # Find the full block above (could be multi-item if it's another sub-group)
            prev_item = self.playlist[start - 1]
            prev_group = prev_item.get("group")
            above_start = start - 1
            while above_start > 0 and self.playlist[above_start - 1].get("group") == prev_group:
                above_start -= 1
            above_block = self.playlist[above_start:start]
            block = self.playlist[start:end]
            self.playlist = (self.playlist[:above_start] + block + above_block +
                             self.playlist[end:])
            self._refresh_list()
            self._reselect_group(group_path)
            return

        # Fall back to top-level block movement
        blocks = self._to_blocks()
        bi = self._find_block_index(blocks, group_path)
        if bi <= 0:
            return
        blocks[bi], blocks[bi - 1] = blocks[bi - 1], blocks[bi]
        self.playlist = self._blocks_to_playlist(blocks)
        self._refresh_list()
        self._reselect_group(group_path)

    def _move_block_down(self, group_path):
        """Move a group block down. Handles both top-level groups and sub-groups."""
        # Try sub-group range first
        rng = self._get_subgroup_range(group_path)
        if rng and rng[1] < len(self.playlist):
            start, end = rng
            # Find the full block below (could be multi-item if it's another sub-group)
            nxt_item = self.playlist[end]
            nxt_group = nxt_item.get("group")
            below_end = end + 1
            while (below_end < len(self.playlist) and
                   self.playlist[below_end].get("group") == nxt_group):
                below_end += 1
            below_block = self.playlist[end:below_end]
            block = self.playlist[start:end]
            self.playlist = (self.playlist[:start] + below_block + block +
                             self.playlist[below_end:])
            self._refresh_list()
            self._reselect_group(group_path)
            return

        # Fall back to top-level block movement
        blocks = self._to_blocks()
        bi = self._find_block_index(blocks, group_path)
        if bi < 0 or bi >= len(blocks) - 1:
            return
        blocks[bi], blocks[bi + 1] = blocks[bi + 1], blocks[bi]
        self.playlist = self._blocks_to_playlist(blocks)
        self._refresh_list()
        self._reselect_group(group_path)

    # ═══════════════════════════════════════════════════════════════
    # GROUP OPERATIONS
    # ═══════════════════════════════════════════════════════════════

    def _group_selected(self):
        """Assign selected script(s) to a group. If a group header is also selected,
        the new group nests inside it. Supports multi-select for batch grouping."""
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná scripts para agrupar.\n"
                              "Ctrl+Click en un 📁 grupo + scripts para crear subgrupo.", "info")
            return

        # Separate group headers and scripts from selection
        parent_path = None
        indices = []
        for iid in sel:
            info = self._item_map.get(iid)
            if info is None:
                continue
            if info[0] == "group":
                parent_path = info[1]  # Use last group in selection as parent
            elif info[0] == "script":
                indices.append(info[1])

        if not indices:
            self._dark_dialog("Grupo",
                "Seleccioná al menos un script junto con el grupo padre.\n\n"
                "1. Ctrl+Click en el 📁 grupo destino\n"
                "2. Ctrl+Click en los scripts a agrupar\n"
                "3. Clic en 📁 Agrupar", "info")
            return

        # Build descriptive label for the dialog
        if parent_path:
            label = f"Crear subgrupo dentro de «{parent_path}»\nNombre del subgrupo:"
        else:
            label = "Nombre del grupo:"

        self._ask_group_name(lambda name: self._do_group(indices, name, parent_path),
                           label=label)

    def _do_group(self, indices, group_name, parent_path=None):
        """Assign the given indices to `group_name`, optionally nested under parent_path."""
        if not group_name.strip():
            return
        group_name = group_name.strip()

        # Build full group path
        full_path = f"{parent_path}/{group_name}" if parent_path else group_name

        for idx in indices:
            self.playlist[idx]["group"] = full_path

        # Get ALL items with this group (including existing ones)
        all_group = set(self._get_group_indices(full_path))
        first_selected = min(indices)

        # Rebuild playlist: group items contiguous at first selected position
        before = [item for i, item in enumerate(self.playlist)
                  if i not in all_group and i < first_selected]
        group_items = [self.playlist[i] for i in sorted(all_group)]
        after = [item for i, item in enumerate(self.playlist)
                 if i not in all_group and i >= first_selected]

        self.playlist = before + group_items + after
        self._refresh_list()

    def _ungroup_selected(self):
        """Remove group assignment from selected scripts or entire group."""
        sel = self.tree.selection()
        if not sel:
            return

        indices = set()
        for iid in sel:
            info = self._item_map.get(iid)
            if info is None:
                continue
            if info[0] == "group":
                for i in self._get_group_indices(info[1]):
                    indices.add(i)
            elif info[0] == "script":
                indices.add(info[1])

        for idx in indices:
            self.playlist[idx]["group"] = None

        self._refresh_list()

    def _rename_group(self):
        """Rename the selected group."""
        sel = self.tree.selection()
        if not sel:
            return
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "group":
            self._dark_dialog("Grupo", "Seleccioná un header de grupo para renombrar.", "info")
            return
        self._rename_group_dialog(info[1])

    def _rename_group_dialog(self, old_path):
        """Show dialog to rename a group."""
        # Pre-fill with last segment (the group's own name)
        last_part = old_path.rsplit("/", 1)[-1]
        self._ask_group_name(lambda new_name: self._do_rename_group(old_path, new_name))

    def _do_rename_group(self, old_path, new_name):
        """Rename a group: change the last segment of the path for all matching items."""
        if not new_name.strip():
            return
        new_name = new_name.strip()

        # Replace old_path with new path (keeping parent intact)
        parent = old_path.rsplit("/", 1)[0] if "/" in old_path else ""
        new_path = f"{parent}/{new_name}" if parent else new_name

        if new_path == old_path:
            return

        for idx in self._get_group_indices(old_path):
            old_group = self.playlist[idx]["group"]
            # Replace only the matching prefix
            if old_group == old_path:
                self.playlist[idx]["group"] = new_path
            elif old_group.startswith(old_path + "/"):
                self.playlist[idx]["group"] = new_path + old_group[len(old_path):]

        self._refresh_list()

    def _ask_group_name(self, callback, label="Nombre:"):
        """Show a small dialog to ask for a group name."""
        win = tk.Toplevel(self.root, bg=DARK_COLORS["bg"])
        win.title("Nombre del grupo")
        win.geometry("300x130")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        win.lift()

        win.after(50, lambda: _apply_dark_titlebar(win, retries=3))
        win.update_idletasks()
        pw, ph = self.root.winfo_width(), self.root.winfo_height()
        px, py = self.root.winfo_x(), self.root.winfo_y()
        dw, dh = win.winfo_width(), win.winfo_height()
        win.geometry(f"300x130+{px + (pw - dw)//2}+{py + (ph - dh)//2}")

        form = ttk.Frame(win, padding=10)
        form.pack(fill=tk.BOTH, expand=True)

        # Support multi-line labels
        for line in label.split("\n"):
            ttk.Label(form, text=line, style="Compact.TLabel").pack(anchor=tk.W)
        ttk.Label(form, text="", style="Compact.TLabel").pack()  # spacer
        name_var = tk.StringVar()
        entry = ttk.Entry(form, textvariable=name_var, width=30)
        entry.pack(fill=tk.X, pady=(0, 8))
        entry.focus_set()

        def save():
            callback(name_var.get())
            win.destroy()

        entry.bind("<Return>", lambda e: save())
        ttk.Button(form, text="Guardar", command=save, style="Compact.TButton").pack()

    # ═══════════════════════════════════════════════════════════════
    # EXECUTION
    # ═══════════════════════════════════════════════════════════════

    def _gather_settings(self):
        settings = {
            "loop_mode": self.loop_mode_var.get(),
            "loop_count": self._parse_int(self.loop_count_var, 1),
            "loop_delay": self._parse_int(self.loop_delay_var, 0),
            "hotkey": self.hotkey_var.get().lower(),
            "window_geometry": self.root.geometry(),
            "mini_bar_enabled": self._mini_bar_enabled,
            # ── Carry over persisted state ──
            "collapsed_groups": self.settings.get("collapsed_groups", []),
        }
        if self.mini_bar is not None:
            mb = self.mini_bar.get_settings()
            settings.update(mb)
        return settings

    def _start(self):
        if not self.playlist:
            self._dark_dialog("Vacío", "No hay scripts en la lista.", "warning")
            return
        # Only run enabled items
        active = [item for item in self.playlist if item.get("enabled", True)]
        if not active:
            self._dark_dialog("Sin habilitados", "No hay scripts habilitados. Activá alguno con el checkbox ✅.", "warning")
            return
        self._execute(active, self._gather_settings())

    def _run_selected(self):
        sel = self.tree.selection()
        if not sel:
            self._dark_dialog("Seleccionar", "Seleccioná un script de la lista para ejecutarlo solo.", "info")
            return
        info = self._item_map.get(sel[0])
        if info is None or info[0] != "script":
            self._dark_dialog("Grupo", "Seleccioná un script, no un header de grupo.", "info")
            return
        idx = info[1]
        item = self.playlist[idx]

        # Force single-run settings for the selected item only
        override_settings = {
            "loop_mode": "once",
            "loop_count": 1,
            "loop_delay": 0,
        }
        self._execute([item], override_settings)

    def _execute(self, playlist, settings):
        if self.is_running:
            return
        if not playlist:
            return

        # Save active playlist for duration lookups during execution
        self._exec_playlist = playlist

        # Ensure any previous thread has fully terminated
        if self.executor_thread is not None and self.executor_thread.is_alive():
            self.executor_thread.join(timeout=5)

        # Fresh state for every new run
        self.is_running = True
        self.stop_event = threading.Event()
        self.launch_event = threading.Event()

        # Compute real total time based on the actual playlist being run
        self._exec_total_time = self._calc_total_time(playlist, settings)

        # Per-item timing tracking (for infinite mode script countdown)
        self._cur_item_start_time = 0
        self._cur_item_total_time = 0

        # ── Show mini bar if enabled ──
        if self._mini_bar_enabled:
            self._ensure_mini_bar()

        callbacks = {
            "on_start_run": lambda total_global, total_per_loop, max_loops: self.root.after(
                0, lambda: self._cb_start_run(total_global, total_per_loop, max_loops)
            ),
            "on_start_loop": lambda current, max_loops, total_global: self.root.after(
                0, lambda: self._cb_start_loop(current, max_loops, total_global)
            ),
            "on_start_item": lambda idx, name, reps: self.root.after(
                0, lambda: self._cb_start_item(idx, name, reps)
            ),
            "on_repeat": lambda global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops: self.root.after(
                0,
                lambda: self._cb_repeat(
                    global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops
                ),
            ),
            "on_loop_delay": lambda current, delay, total_global: self.root.after(
                0, lambda: self._cb_loop_delay(current, delay, total_global)
            ),
            "on_finish": lambda msg, done, total_global, total_per_loop, loops, max_loops: self.root.after(
                0, lambda: self._cb_finish(msg, done, total_global, total_per_loop, loops, max_loops)
            ),
            "on_error": lambda msg: self.root.after(0, lambda: self._cb_error(msg)),
            "on_launch": lambda path: self.root.after(0, lambda: self._do_launch(path)),
        }

        self.executor_thread = Executor(
            playlist, settings, callbacks, self.stop_event, self.launch_event
        )
        self.executor_thread.start()

    def _stop(self):
        if not self.is_running:
            return
        self.stop_event.set()
        self._set_status("DETENIENDO...", DARK_COLORS["yellow"])
        # Update mini bar
        if self.mini_bar is not None:
            elapsed = time.time() - self._exec_start_time
            self.mini_bar.update("Deteniendo...", 0, 1, mini_format_time(int(elapsed)), True)

    def _do_launch(self, path):
        """Launch the .exe using os.startfile, the most native Windows way.
        This is exactly what happens when you double-click a file in Explorer.
        It runs completely detached from Python with zero inheritance issues.
        
        On failure, does NOT set launch_event — the executor will timeout
        and report the error properly instead of silently continuing."""
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen([path], shell=False)
        except Exception as e:
            self._dark_dialog(
                "Error al lanzar",
                f"No se pudo ejecutar:\n{path}\n\nError: {e}",
                "error"
            )
            # Do NOT set launch_event on error — executor timeout will catch it
            return
        self.launch_event.set()

    def _update_progress(self, value, maximum):
        """Update progress bar and percentage label."""
        self.progress["maximum"] = maximum
        self.progress["value"] = value
        pct = (value / maximum * 100) if maximum > 0 else 0
        self.progress_pct_label.config(text=f"{int(pct)}%")

    def _poll_timer(self):
        """Update progress bar and countdown based on real elapsed time."""
        if not self.is_running:
            return
        elapsed = time.time() - self._exec_start_time
        if self._exec_total_time is not None:
            remaining = max(self._exec_total_time - elapsed, 0)
            self.countdown_label.config(text=f"⏱️ {format_time(int(remaining))}")
            prog = min(int(elapsed), self._exec_total_time)
            self._update_progress(prog, self._exec_total_time)

            # ── Update mini bar ──
            if self.mini_bar is not None and self.mini_bar.is_visible():
                self.mini_bar.update(
                    self.status_label.cget("text").replace(" EJECUTANDO | ", ""),
                    prog,
                    self._exec_total_time,
                    f"-{mini_format_time(int(remaining))}",
                    True,
                )
        else:
            # Infinite mode: show script countdown + total session elapsed
            elapsed = time.time() - self._exec_start_time
            item_elapsed = time.time() - self._cur_item_start_time
            item_remaining = max(self._cur_item_total_time - item_elapsed, 0)
            self.countdown_label.config(
                text=f"⏱️ -{format_time(int(item_remaining))} │ {format_time(int(elapsed))}"
            )
            if self.mini_bar is not None and self.mini_bar.is_visible():
                prog_max = self._cur_item_total_time if self._cur_item_total_time > 0 else 1
                prog_val = int(item_elapsed) % max(prog_max, 1)
                time_text = f"-{mini_format_time(int(item_remaining))}│{mini_format_time(int(elapsed))}"
                self.mini_bar.update(
                    self.status_label.cget("text").replace(" EJECUTANDO | ", ""),
                    prog_val,
                    prog_max,
                    time_text,
                    True,
                )
        self.root.after(500, self._poll_timer)

    def _cb_start_run(self, total_global, total_per_loop, max_loops):
        self._exec_start_time = time.time()
        if max_loops is None:
            self._exec_total_time = None
            self._update_progress(0, total_per_loop)
            status_text = f"EJECUTANDO | Loop ∞ | Reps/loop: {total_per_loop}"
            self._set_status(status_text, DARK_COLORS["blue"])
        else:
            self._update_progress(0, self._exec_total_time or 1)
            status_text = f"EJECUTANDO | Loop 1/{max_loops} | Total reps: {total_global}"
            self._set_status(status_text, DARK_COLORS["blue"])
        self._poll_timer()

    def _cb_start_loop(self, current, max_loops, total_global):
        if max_loops is None:
            total_per_loop = self.progress["maximum"]
            self._update_progress(0, total_per_loop)
        if max_loops is None:
            status_text = f"EJECUTANDO | Loop {current} (∞)"
        else:
            status_text = f"EJECUTANDO | Loop {current}/{max_loops}"
        self._set_status(status_text, DARK_COLORS["blue"])

    def _cb_start_item(self, idx, name, reps):
        # Track per-script timing for infinite mode countdown
        if hasattr(self, '_exec_playlist') and idx < len(self._exec_playlist):
            item = self._exec_playlist[idx]
            self._cur_item_start_time = time.time()
            self._cur_item_total_time = self._calc_item_time(item)

    def _cb_repeat(self, global_rep, total_global, total_per_loop, name, current, total_item, loop, max_loops):
        # Infinite mode: track per-loop progress by rep count (bar is reset each loop)
        if max_loops is None:
            loop_progress = ((global_rep - 1) % total_per_loop) + 1
            self._update_progress(loop_progress, total_per_loop)

        loop_str = f"L{loop}" if max_loops is None else f"L{loop}/{max_loops}"
        if total_global is None:
            total_str = "∞"
        else:
            total_str = f"{global_rep}/{total_global}"
        status_text = (
            f"EJECUTANDO | {loop_str} | {name}: {current}/{total_item} | Total: {total_str}"
        )
        self._set_status(status_text, DARK_COLORS["blue"])

        # ── Update mini bar with more detail ──
        if self.mini_bar is not None and self.mini_bar.is_visible():
            elapsed = time.time() - self._exec_start_time
            short_status = f"{name}: {current}/{total_item} | {loop_str}"
            progress_max = total_global if total_global is not None else total_per_loop
            progress_val = global_rep
            if self._exec_total_time is not None:
                remaining = max(self._exec_total_time - elapsed, 0)
                time_text = f"-{mini_format_time(int(remaining))}"
            else:
                item_elapsed = time.time() - self._cur_item_start_time
                item_remaining = max(self._cur_item_total_time - item_elapsed, 0)
                time_text = f"-{mini_format_time(int(item_remaining))}│{mini_format_time(int(elapsed))}"
            self.mini_bar.update(short_status, progress_val, progress_max, time_text, True)

    def _cb_loop_delay(self, current, delay, total_global):
        self._set_status(f"ESPERANDO | Loop {current} → pausa {delay}s", DARK_COLORS["purple"])

    def _cb_finish(self, msg, done, total_global, total_per_loop, loops, max_loops):
        self.is_running = False
        loop_str = f"{loops} loops" if max_loops is None else f"{loops}/{max_loops} loops"
        total_str = f"{done}/{total_global}" if total_global is not None else f"{done} (∞)"
        if msg == "Detenido":
            self._set_status(f"DETENIDO | {loop_str} | {total_str} reps", DARK_COLORS["red"])
        elif msg == "Completado":
            self._set_status(f"COMPLETADO | {loop_str} | {total_str} reps", DARK_COLORS["green"])
            # Update mini bar + schedule auto-hide BEFORE the blocking dialog
            if self.mini_bar is not None:
                elapsed = time.time() - self._exec_start_time
                self.mini_bar.update(f"{msg}", 0, 1, mini_format_time(int(elapsed)), False)
                self.root.after(3000, self._hide_mini_bar)
            self._dark_dialog("Finalizado", f"Ejecución completada.\n{loop_str}\n{total_str} reps realizadas.", "success")
        else:
            self._set_status(f"{msg} | {loop_str} | {total_str} reps", "#7f8c8d")
        self._update_progress(self._exec_total_time or done, self._exec_total_time or total_per_loop or 1)

        # ── Mini Bar: mostrar estado final y ocultar ──
        # (Completado already handles its own auto-hide above)
        if self.mini_bar is not None and msg != "Completado":
            elapsed = time.time() - self._exec_start_time
            self.mini_bar.update(f"{msg}", 0, 1, mini_format_time(int(elapsed)), False)
            self.root.after(3000, self._hide_mini_bar)

    def _set_status(self, text, color):
        """Update the status label with text and background color."""
        self.status_label.config(text=f" {text} ", bg=color)

    def _cb_error(self, msg):
        self._dark_dialog("Error", msg, "error")
        self.is_running = False
        self._set_status(f"Error: {msg}", DARK_COLORS["red"])
        if self.mini_bar is not None:
            self.mini_bar.reset()

    def _on_close(self):
        settings = self._gather_settings()
        save_config(self.playlist, settings)
        self.hotkey.stop()
        if self.mini_bar is not None:
            self.mini_bar.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = OrchestratorApp(root)
    root.mainloop()
