import os
import shutil
from pathlib import Path


def buscar_tesseract() -> Path | None:
    ejecutable = shutil.which("tesseract")
    if ejecutable:
        return Path(ejecutable)

    carpetas = [
        Path(valor)
        for nombre in ("LOCALAPPDATA", "ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")
        if (valor := os.environ.get(nombre))
    ]
    for carpeta in carpetas:
        for ruta in (carpeta / "Programs" / "Tesseract-OCR" / "tesseract.exe",
                     carpeta / "Tesseract-OCR" / "tesseract.exe"):
            if ruta.is_file():
                return ruta
    return None
