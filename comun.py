import threading
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).parent
CAPTURAS = RAIZ / "capturas"
_consola = threading.Lock()
# Mensajes retenidos mientras se espera una respuesta en la consola, para que no tapen la pregunta.
_retenidos: list[str] | None = None


def log(msg: str):
    with open(RAIZ / "log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    with _consola:
        if _retenidos is not None:
            _retenidos.append(msg)
            return
    print(msg)


def leer_respuesta(mensaje: str) -> str:
    """Como input(), pero los mensajes de las demás consultas esperan a que se responda."""
    global _retenidos
    with _consola:
        _retenidos = []
    try:
        return input(mensaje)
    finally:
        with _consola:
            pendientes, _retenidos = _retenidos, None
        for msg in pendientes:
            print(msg)
