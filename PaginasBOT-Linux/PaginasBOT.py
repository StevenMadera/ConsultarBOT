"""Abre PaginasBOT en Linux. Si algo falla al abrir, lo muestra en una ventana y lo deja en log.txt."""
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

try:
    from interfaz import main

    main()
except Exception:
    detalle = traceback.format_exc()
    with open(Path(__file__).with_name("log.txt"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} ERROR al abrir: {detalle}\n")
    try:
        import tkinter as tk
        from tkinter import messagebox

        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showerror("PaginasBOT no pudo abrir", detalle)
    except Exception:
        # Sin Tkinter (falta python3-tk): el aviso del escritorio.
        subprocess.run(["zenity", "--error", "--title=PaginasBOT no pudo abrir", f"--text={detalle[-1500:]}"])
