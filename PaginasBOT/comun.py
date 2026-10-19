import threading
from datetime import datetime
from pathlib import Path

# Igual al nombre del .pyw: el instalador da al acceso directo el id f"Soporte.{NOMBRE}".
NOMBRE = "PaginasBOT"
RAIZ = Path(__file__).parent
CAPTURAS = RAIZ / "capturas"
_consola = threading.Lock()
# Mensajes retenidos mientras se espera una respuesta en la consola, para que no tapen la pregunta.
_retenidos: list[str] | None = None
# La interfaz recibe aquí los mensajes y el estado de cada sitio; en la consola nadie escucha.
_oyente = None


class FueraDeServicio(Exception):
    """El sitio no respondió o mostró un error propio: en el informe va `captura`, la página caída (PNG o PDF).
    Sin `captura`, quien tiene la página la guarda tal como quedó; con `direccion`, antes deja en ella el aviso de
    Chrome de que esa dirección no respondió."""

    def __init__(self, motivo: str, captura: Path | None = None, direccion: str | None = None):
        super().__init__(motivo)
        self.captura = captura
        self.direccion = direccion


def escuchar(funcion):
    global _oyente
    _oyente = funcion


def avisar(tipo: str, **datos):
    if _oyente:
        _oyente(tipo, datos)


def log(msg: str):
    with open(RAIZ / "log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    avisar("log", texto=msg)
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
