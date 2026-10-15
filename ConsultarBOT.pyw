"""Abre ConsultarBOT con doble clic, sin la ventana negra de la consola."""
import ctypes
import traceback

try:
    from interfaz import main

    main()
except Exception:
    ctypes.windll.user32.MessageBoxW(0, traceback.format_exc(), "ConsultarBOT no pudo abrir", 0x10)
