"""Carga de las páginas de consulta: una sola pestaña por sitio, con una espera larga y, si no abre, una recarga."""
import asyncio
import time
from collections.abc import Awaitable, Callable
from weakref import WeakKeyDictionary

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from comun import FueraDeServicio, log
from rendimiento import etapa

# Segundos por intento: con todas las páginas cargando a la vez, el RUNT (unos 12 MB) puede tardar más de un minuto.
ESPERA_CARGA = 120
INTENTOS = 2
# Una precarga más vieja no sirve: tras llenar el formulario llegaría a su turno pasados los 45 s en que el captcha
# se recarga (caduca a los 60 s), y esa recarga ya no tendría la prioridad del inicio.
VIGENCIA_PRECARGA = 30
# Con el equipo ocupado, comprobar el formulario precargado puede tardar; recargarlo tarda mucho más (RUNT: 60-140 s).
ESPERA_REUTILIZAR = 20
_precargadas: WeakKeyDictionary = WeakKeyDictionary()
_cargadas_en: WeakKeyDictionary = WeakKeyDictionary()


def marcar_precarga(page: Page, url: str):
    _precargadas[page] = (url, instante_carga(page))


def instante_carga(page: Page) -> float:
    return _cargadas_en.get(page, time.monotonic())


async def esperar_estable(page: Page, timeout: int = 10000):
    """Espera a que la página deje de cambiar. Si sigue cambiando pasado `timeout` (un carrusel, un reloj), se sigue
    igual: la captura de la página como está es mejor que ninguna."""
    try:
        await page.evaluate("() => { delete window.__estableBot; }")
        await page.wait_for_function(
            """() => {
                if (!document.body || document.fonts.status !== 'loaded') return false;
                const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
                if ([...document.querySelectorAll(
                    'mat-spinner, mat-progress-spinner, mat-progress-bar, ngx-spinner, .spinner, .loading, .loader'
                )].some(visible)) return false;
                if ([...document.images].some(e => visible(e) && !e.complete)) return false;
                if (document.getAnimations().some(a => a.playState === 'running'
                    && Number.isFinite(a.effect.getTiming().iterations))) return false;
                const firma = document.body.innerText + '|' + document.body.scrollWidth + '|' + document.body.scrollHeight;
                const ahora = performance.now();
                if (!window.__estableBot || window.__estableBot.firma !== firma) {
                    window.__estableBot = {firma, desde: ahora};
                    return false;
                }
                return ahora - window.__estableBot.desde >= 400;
            }""",
            timeout=timeout, polling=100,
        )
    except PlaywrightError as error:
        log(f"La página siguió cambiando {timeout // 1000} s; se captura como está ({_detalle(error)}).")


def _detalle(error: BaseException) -> str:
    lineas = str(error).splitlines()
    detalle = lineas[0] if lineas and lineas[0] else "tiempo agotado"
    # Qué parte del formulario no apareció (el sitio o, por ejemplo, la casilla de Google).
    esperando = next((linea.strip(" -") for linea in lineas if "waiting for locator(" in linea), None)
    return f"{detalle} {esperando}" if esperando else detalle


async def _detener(page: Page):
    """Corta la carga que quedó pendiente: si no, la misma dirección espera unos 20 s antes de volver a pedirse."""
    if page.is_closed():
        return
    try:
        async with asyncio.timeout(10):
            cdp = await page.context.new_cdp_session(page)
            try:
                await cdp.send("Page.stopLoading")
            finally:
                await cdp.detach()
    except (PlaywrightError, TimeoutError) as error:
        log(f"Carga: no se pudo detener la carga pendiente ({_detalle(error)}).")


async def _abrir(page: Page, url: str, lista, recargar: bool, espera: float):
    milisegundos = espera * 1000
    if recargar and "#" in url and page.url.split("#")[0] == url.split("#")[0]:
        # En una página de una sola aplicación (RUNT, SIMIT) ir a la misma dirección solo cambia el #: se vuelve al
        # formulario y se recarga, o quedarían el resultado y el captcha anteriores.
        await page.goto(url, wait_until="commit", timeout=milisegundos)
        respuesta = await page.reload(wait_until="domcontentloaded", timeout=milisegundos)
    else:
        respuesta = await page.goto(url, wait_until="domcontentloaded", timeout=milisegundos)
    if respuesta and respuesta.status >= 400:
        raise PlaywrightError(f"el sitio respondió HTTP {respuesta.status}")
    if lista:
        await lista(page)
    else:
        await page.wait_for_load_state("load", timeout=milisegundos)


async def cargar_pagina(
    page: Page,
    url: str,
    lista: Callable[[Page], Awaitable[object]] | None = None,
    *,
    recargar: bool = False,
    configurar: Callable[[Page], Awaitable[object]] | None = None,
    reutilizar: bool = False,
    vigencia: float = VIGENCIA_PRECARGA,
    espera: float | None = None,
    intentos: int | None = None,
) -> Page:
    with etapa("carga"):
        return await _cargar_pagina(page, url, lista, recargar=recargar, configurar=configurar,
                                   reutilizar=reutilizar, vigencia=vigencia, espera=espera or ESPERA_CARGA,
                                   intentos=intentos or INTENTOS)


async def _cargar_pagina(page, url, lista, *, recargar, configurar, reutilizar, vigencia, espera, intentos):
    """Solo navega y verifica el formulario (`lista`). Nunca rellena ni envía una consulta o un captcha.

    Cada intento espera hasta `espera` segundos; si la página no abre, se detiene su carga y se recarga. Sin
    respuesta tras `intentos`, el sitio está fuera de servicio."""
    precarga = _precargadas.pop(page, None)
    if reutilizar and precarga and precarga[0] == url and time.monotonic() - precarga[1] < vigencia \
            and not page.is_closed():
        try:
            await asyncio.wait_for(lista(page) if lista else page.wait_for_load_state("load"), ESPERA_REUTILIZAR)
            log("Carga inicial: se reutiliza el formulario precargado vigente.")
            return page
        except (PlaywrightError, TimeoutError):
            log("Carga inicial: la precarga ya no está lista; se vuelve a cargar.")
    if configurar:
        await configurar(page)
    errores = []
    for intento in range(intentos):
        if intento:
            log(f"{url}: no abrió ({errores[-1]}); se recarga.")
            await _detener(page)
        try:
            await asyncio.wait_for(_abrir(page, url, lista, recargar or intento > 0, espera), espera)
        except (PlaywrightError, TimeoutError) as error:
            errores.append(_detalle(error) if str(error) else f"no abrió en {espera:.0f} s")
            continue
        _cargadas_en[page] = time.monotonic()
        return page
    detalle = f" ({errores[-1]})" if errores else ""
    raise FueraDeServicio(f"la página no abrió ni recargándola{detalle}", direccion=url)
