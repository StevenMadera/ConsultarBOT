"""Consulta un lote de filas en la libreta militar, el SIMIT y el RNMC (los tres a la vez) y arma el Excel."""
import asyncio
import re
import time
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import consultas
from comun import avisar, log
from observacion import SIN_NOVEDAD, observacion
from tabla import SITIOS, Tabla, guardar_excel, mismo_nombre

# Analítica, publicidad y chats de terceros: no aportan a la consulta y gastan red y CPU.
HOSTS_BLOQUEADOS = (
    "google-analytics.com", "analytics.google.com", "googletagmanager.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "connect.facebook.net", "hotjar.com", "hotjar.io",
    "clarity.ms", "tawk.to", "zdassets.com", "livechatinc.com", "intercom.io", "intercomcdn.com",
    "crisp.chat", "jivosite.com", "hs-scripts.com", "botmaker.com", "cliengo.com", "freshchat.com",
)
REGLAS_BLOQUEO = ", ".join(f"MAP {host} ~NOTFOUND, MAP *.{host} ~NOTFOUND" for host in HOSTS_BLOQUEADOS)
VIEWPORT = {"width": 1366, "height": 768}
INTENTOS = 2


def minutos(segundos: float) -> str:
    segundos = round(segundos)
    return f"{segundos // 60}:{segundos % 60:02d}"


def explicar(error: Exception) -> str:
    texto = str(error).strip()
    if isinstance(error, PlaywrightTimeoutError) or re.search(r"Timeout \d+ms exceeded", texto):
        return "el sitio no respondió a tiempo"
    if re.search(r"ERR_CONNECTION_(CLOSED|RESET)", texto):
        return "el sitio cortó la conexión; si estás con VPN, apágala"
    if "net::ERR_" in texto:
        return "no se pudo conectar con el sitio"
    if "has been closed" in texto:
        return "se cerró el navegador"
    return (texto.splitlines()[0] if texto else type(error).__name__)[:160]


async def abrir_navegadores(p) -> dict:
    """Se abren al iniciar, mientras se pegan las filas; el SIMIT hace de una vez su validación de seguridad."""
    automatico, visible = await asyncio.gather(
        p.chromium.launch(headless=True, args=[f"--host-resolver-rules={REGLAS_BLOQUEO}"]),
        p.chromium.launch(headless=False, args=[f"--host-resolver-rules={REGLAS_BLOQUEO}"]),
    )
    contexto_simit, contexto_rnmc, contexto_libreta = await asyncio.gather(
        automatico.new_context(viewport=VIEWPORT),
        automatico.new_context(viewport=VIEWPORT, ignore_https_errors=True),
        visible.new_context(no_viewport=True),
    )
    simit = consultas.PaginaSimit(await contexto_simit.new_page())
    rnmc = consultas.PaginaRnmc(await contexto_rnmc.new_page())
    libreta = consultas.PaginaLibreta(await contexto_libreta.new_page())
    await libreta.preparar()
    simit.carga = asyncio.create_task(simit.abrir())
    return {"navegadores": (automatico, visible), "libreta": libreta, "simit": simit, "rnmc": rnmc}


async def cerrar_navegadores(apertura: asyncio.Task):
    apertura.cancel()
    recursos = (await asyncio.gather(apertura, return_exceptions=True))[0]
    if isinstance(recursos, dict):
        if recursos["simit"].carga:
            recursos["simit"].carga.cancel()
            await asyncio.gather(recursos["simit"].carga, return_exceptions=True)
        for navegador in recursos["navegadores"]:
            try:
                await navegador.close()
            except PlaywrightError:
                pass


def resumen(tabla: Tabla) -> dict:
    filas = [f for f in tabla.filas if not f.vacia]
    rojas = [f for f in filas if f.errores or f.avisos]
    amarillas = [f for f in filas if f.fallas and f not in rojas]
    listas = [f for f in filas if f not in rojas and f not in amarillas]
    return {"total": len(filas), "rojas": len(rojas), "amarillas": len(amarillas), "listas": len(listas),
            "sin_novedad": sum(f.observacion == SIN_NOVEDAD for f in listas)}


async def procesar(tabla: Tabla, apertura: asyncio.Task, elegir) -> Path:
    """Consulta las filas válidas en los tres sitios, escribe las observaciones y guarda el Excel."""
    inicio = time.monotonic()
    r = await apertura
    validas = [f for f in tabla.filas if f.consultable]
    log(f"Lote: {len(tabla.filas)} filas, {len(validas)} para consultar.")

    async def libreta(fila, intento):
        fila.libreta = await r["libreta"].consultar(fila, intento)
        return "no_aplica" if fila.libreta.no_aplica else None

    async def simit(fila, intento):
        fila.simit = await r["simit"].consultar(fila, elegir, intento)

    async def rnmc(fila, intento):
        fila.anotaciones, nombre = await r["rnmc"].consultar(fila, intento)
        # El RNMC ya validó cédula y fecha de expedición: si el nombre no se parece, el que está mal es el nombre.
        if nombre and fila.nombre and not mismo_nombre(nombre, fila.nombre):
            log(f"Fila {fila.numero} ({fila.cedula}): el nombre no coincide con el del RNMC ({nombre}).")
            fila.avisos.append(f"el nombre pegado ({fila.nombre}) no coincide con el registrado en el RNMC: {nombre}")

    async def intentar(sitio: str, fila, consulta, intentos: int) -> bool:
        avisar("fila", n=fila.numero, sitio=sitio, estado="en_curso")
        for intento in range(intentos):
            try:
                estado = await consulta(fila, intento) or "listo"
            except consultas.DatoErrado as e:
                fila.errores.append(str(e))
                log(f"Fila {fila.numero} ({fila.cedula}), {SITIOS[sitio]}: {e}")
                avisar("fila", n=fila.numero, sitio=sitio, estado="dato", detalle=str(e))
                return True
            except Exception as e:
                fila.fallas[sitio] = explicar(e)
                log(f"Fila {fila.numero} ({fila.cedula}), {SITIOS[sitio]}, intento {intento + 1}: {e}")
            else:
                fila.fallas.pop(sitio, None)
                avisar("fila", n=fila.numero, sitio=sitio, estado=estado)
                return True
        avisar("fila", n=fila.numero, sitio=sitio, estado="error", detalle=fila.fallas[sitio])
        return False

    async def recorrer(sitio: str, consulta):
        fallidas = []
        for fila in validas:
            # Otro sitio pudo encontrar un dato errado en esta fila: ya no se consulta.
            if not fila.consultable:
                avisar("fila", n=fila.numero, sitio=sitio, estado="omitida")
                continue
            if not await intentar(sitio, fila, consulta, INTENTOS):
                fallidas.append(fila)
        # Una vez más las que fallaron, al final: el sitio pudo recuperarse.
        for fila in fallidas:
            if fila.consultable:
                await intentar(sitio, fila, consulta, 1)
        log(f"Tiempo: {SITIOS[sitio]} terminó a los {minutos(time.monotonic() - inicio)}")

    for resultado in await asyncio.gather(recorrer("libreta", libreta), recorrer("simit", simit),
                                          recorrer("rnmc", rnmc), return_exceptions=True):
        if isinstance(resultado, Exception):
            log(f"ERROR inesperado: {resultado!r}")
    for fila in validas:
        if fila.consultable and not fila.fallas and None not in (fila.libreta, fila.simit, fila.anotaciones):
            fila.observacion = observacion(fila.libreta, fila.simit, fila.anotaciones)
        elif fila.consultable and not fila.fallas:
            fila.fallas["lote"] = "la consulta se interrumpió"
    ruta = guardar_excel(tabla)
    log(f"Excel: {ruta}")
    log(f"Tiempo total: {minutos(time.monotonic() - inicio)}")
    return ruta
