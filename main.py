import ttkbootstrap as ttk
from gui import OrchestratorApp


def main():
    root = ttk.Window(themename="cyborg")
    OrchestratorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
