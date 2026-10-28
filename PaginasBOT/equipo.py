"""Ocupación del procesador de todo el equipo: este bot y lo demás que esté abierto.

Con el procesador saturado, empezar más trabajo no adelanta nada: hace más lento lo que ya estaba en marcha (en un
equipo de 2 núcleos, un formulario que solo tarda 5-15 s llegó a tardar 75-130 s con todo a la vez). Lo que puede
esperar (cargas de respaldo, precargas, modelos) espera a que haya respiro; en un equipo con capacidad de sobra no
espera nada.
"""
import asyncio
import ctypes
import sys
import time
from ctypes import wintypes

# Fracción del procesador ocupada desde la que el equipo se da por saturado.
SATURADO = 0.85
# Segundos entre mediciones: más seguido, la medición salta con cada ráfaga corta.
MUESTRA = 1.0

_anterior: tuple[int, int] | None = None
_medido_en = 0.0
_ocupacion = 0.0


def _tiempos() -> tuple[int, int] | None:
    """(tiempo ocioso, tiempo total) acumulados de todos los núcleos; None si no se puede medir."""
    if sys.platform != "win32":
        return None
    ocioso, nucleo, usuario = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(ocioso), ctypes.byref(nucleo), ctypes.byref(usuario)):
        return None

    def valor(f):
        return (f.dwHighDateTime << 32) | f.dwLowDateTime

    # El tiempo del núcleo del sistema ya incluye el ocioso.
    return valor(ocioso), valor(nucleo) + valor(usuario)


def ocupacion() -> float:
    """Fracción del procesador ocupada desde la medición anterior (al menos MUESTRA s), de 0 a 1."""
    global _anterior, _medido_en, _ocupacion
    ahora = time.monotonic()
    if _anterior is not None and ahora - _medido_en < MUESTRA:
        return _ocupacion
    actual = _tiempos()
    if actual is None:
        return 0.0
    if _anterior is not None and actual[1] > _anterior[1]:
        _ocupacion = 1 - (actual[0] - _anterior[0]) / (actual[1] - _anterior[1])
    _anterior, _medido_en = actual, ahora
    return _ocupacion


def saturado() -> bool:
    return ocupacion() >= SATURADO


async def esperar_respiro(tope: float, seguir=None) -> float:
    """Espera, hasta `tope` segundos, a que el procesador no esté saturado; deja de esperar si `seguir()` da False
    (por ejemplo, empezó una consulta que necesita lo que esperaba). Devuelve los segundos que esperó."""
    inicio = time.monotonic()
    if _tiempos() is None:
        return 0.0

    def esperando():
        return time.monotonic() - inicio < tope and (seguir is None or seguir())

    # Una medición reciente: la anterior puede ser de hace mucho.
    if time.monotonic() - _medido_en > 3 * MUESTRA:
        ocupacion()
        await asyncio.sleep(MUESTRA)
    while esperando() and saturado():
        await asyncio.sleep(MUESTRA)
    return time.monotonic() - inicio
