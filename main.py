import customtkinter as ctk
from gui import OrchestratorApp

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

root = ctk.CTk()
app = OrchestratorApp(root)
root.mainloop()
