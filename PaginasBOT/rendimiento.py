import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

from comun import log

_medicion: ContextVar[dict[str, float] | None] = ContextVar("medicion", default=None)
_etapa: ContextVar[str | None] = ContextVar("etapa", default=None)


@contextmanager
def etapa(nombre: str):
    tiempos = _medicion.get()
    anterior = _etapa.get()
    token = _etapa.set(nombre)
    inicio = time.monotonic()
    try:
        yield
    finally:
        if tiempos is not None:
            transcurrido = time.monotonic() - inicio
            tiempos[nombre] += transcurrido
            if anterior:
                tiempos[anterior] -= transcurrido
        _etapa.reset(token)


@contextmanager
def medir_sitio(clave: str):
    tiempos: dict[str, float] = defaultdict(float)
    token = _medicion.set(tiempos)
    etapa_token = _etapa.set(None)
    inicio = time.monotonic()
    try:
        yield
    finally:
        total = time.monotonic() - inicio
        restante = max(0, total - sum(tiempos.values()))
        detalle = " ".join(f"{nombre}={tiempos[nombre]:.2f}s"
                           for nombre in ("cola", "carga", "usuario", "captura"))
        _medicion.reset(token)
        _etapa.reset(etapa_token)
        log(f"Rendimiento {clave}: {detalle} respuesta_procesamiento={restante:.2f}s total={total:.2f}s")


async def capturar_pantalla(page, **opciones):
    with etapa("captura"):
        return await page.screenshot(**opciones)
