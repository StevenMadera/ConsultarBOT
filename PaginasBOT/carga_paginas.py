"""Carga de las páginas de consulta: una sola pestaña por sitio, con una espera larga y, si no abre, una recarga."""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary, WeakSet

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from comun import RAIZ, FueraDeServicio, avisar, log
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
# Aviso con que el sitio (su Cloudflare) rechaza por un tiempo todo lo que llega desde esta dirección IP. Recargar
# solo alarga el bloqueo: no se repite la carga.
BLOQUEO_RE = r"Acceso bloqueado|tr[aá]fico sospechoso|you have been blocked"
BLOQUEO_JS = "(patron) => new RegExp(patron, 'i').test(document.body?.innerText ?? '')"
MOTIVO_BLOQUEO = ("el sitio bloqueó por un tiempo las consultas desde esta conexión (Cloudflare: «tráfico sospechoso "
                  "desde tu dirección IP»). Espera unos minutos sin consultarlo; si sigue, apaga la VPN o usa otra red")


# Verificación «Verifiquemos tu conexión» de Cloudflare (la Registraduría la muestra a veces). Llega con HTTP 403:
# recargar la repite y la hace más estricta, así que se espera a que pase sola o la pase el usuario.
VERIFICACION_JS = """() => {
    const texto = document.body?.innerText ?? '';
    return /Verifiquemos tu conexi[oó]n|comprobando que esta solicitud no provenga de un robot|Verify you are human|Checking (if the site connection is secure|your browser)|Performing security verification/i.test(texto)
        || /^(Un momento|Just a moment)/i.test(document.title)
        || !!document.querySelector('#challenge-form, #challenge-stage, iframe[src*="challenges.cloudflare.com"]');
}"""
# Sin la ventana a la vista Cloudflare suele no completar la verificación: se espera poco antes de mostrarla.
ESPERA_VERIFICACION_SOLA = 6
ESPERA_VERIFICACION = 300
# La cookie con que Cloudflare recuerda la verificación pasada (cf_clearance, ligada a este navegador y esta IP): se
# guarda para las ventanas nuevas y las siguientes consultas, y así no la vuelve a pedir mientras siga vigente.
COOKIES_CLOUDFLARE = RAIZ / "cookies_cloudflare.json"
# Navegadores y contextos con ventana: solo en ellos el usuario puede pasar la verificación.
VISIBLES: WeakSet = WeakSet()
NOMBRES_SITIOS = {"certvigenciacedula.registraduria.gov.co": "Registraduría"}
_bloqueos_verificacion: WeakKeyDictionary = WeakKeyDictionary()


class VerificacionPendiente(Exception):
    """La página muestra la verificación de Cloudflare: se resuelve fuera del tiempo de carga, sin recargar."""


def _una_verificacion() -> asyncio.Lock:
    """Una verificación a la vez en pantalla (un candado por bucle de eventos)."""
    bucle = asyncio.get_running_loop()
    if bucle not in _bloqueos_verificacion:
        _bloqueos_verificacion[bucle] = asyncio.Lock()
    return _bloqueos_verificacion[bucle]


class SitioBloqueado(FueraDeServicio):
    """El sitio rechaza por un tiempo esta conexión: no se vuelve a cargar en esta consulta."""

    def __init__(self, captura=None):
        super().__init__(MOTIVO_BLOQUEO, captura)


async def bloqueo_en_pagina(page: Page) -> bool:
    try:
        return await page.evaluate(BLOQUEO_JS, BLOQUEO_RE)
    except PlaywrightError:
        return False


def es_visible(page: Page) -> bool:
    try:
        return page.context in VISIBLES or page.context.browser in VISIBLES
    except TypeError:
        return False


async def verificacion_en_pagina(page: Page) -> bool:
    try:
        return bool(await page.evaluate(VERIFICACION_JS))
    except PlaywrightError:
        return False


async def revisar_verificacion(page: Page):
    """Lanza VerificacionPendiente si la página, en una ventana visible, muestra la verificación de Cloudflare."""
    if es_visible(page) and await verificacion_en_pagina(page):
        raise VerificacionPendiente


def _cookies_guardadas() -> list[dict]:
    try:
        cookies = json.loads(COOKIES_CLOUDFLARE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ahora = time.time()
    return [c for c in cookies if isinstance(c, dict) and (c.get("expires", -1) <= 0 or c["expires"] > ahora + 60)]


async def cargar_cookies_cloudflare(contexto):
    """Pone en un contexto nuevo las verificaciones de Cloudflare ya pasadas que sigan vigentes."""
    cookies = _cookies_guardadas()
    if cookies:
        try:
            await contexto.add_cookies(cookies)
        except PlaywrightError as error:
            log(f"Cloudflare: no se pudieron reutilizar las verificaciones anteriores ({_detalle(error)}).")


async def guardar_cookies_cloudflare(contexto):
    try:
        nuevas = [c for c in await contexto.cookies() if c["name"].startswith(("cf_", "__cf"))]
    except PlaywrightError:
        return
    if not nuevas:
        return
    claves = {(c["name"], c["domain"], c["path"]) for c in nuevas}
    cookies = [c for c in _cookies_guardadas() if (c["name"], c["domain"], c["path"]) not in claves] + nuevas
    try:
        COOKIES_CLOUDFLARE.write_text(json.dumps(cookies), encoding="utf-8")
    except OSError as error:
        log(f"Cloudflare: no se pudo guardar la verificación ({error}).")


async def _ventana(page: Page, estado: str):
    cdp = await page.context.new_cdp_session(page)
    try:
        ventana = (await cdp.send("Browser.getWindowForTarget"))["windowId"]
        if estado != "minimized":
            # Chrome no pasa de minimizada a maximizada sin restaurarla antes.
            await cdp.send("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": "normal"}})
        await cdp.send("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": estado}})
    finally:
        await cdp.detach()


async def _esperar_sin_verificacion(page: Page, segundos: float) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if page.is_closed():
            return False
        if not await verificacion_en_pagina(page):
            # Tras pasarla, Cloudflare recarga el sitio: se espera a que la página real termine de abrir.
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PlaywrightError:
                pass
            if not await verificacion_en_pagina(page):
                return True
        await asyncio.sleep(0.5)
    return False


async def pasar_verificacion(page: Page, sitio: str | None = None):
    """Si la página muestra la verificación de Cloudflare, espera a que pase: primero sola y, si no, con la ventana al
    frente para que el usuario marque la casilla. Al pasar guarda la cookie para las siguientes cargas."""
    sitio = sitio or NOMBRES_SITIOS.get(urlsplit(page.url).hostname or "", urlsplit(page.url).hostname or "el sitio")
    if not await _esperar_sin_verificacion(page, ESPERA_VERIFICACION_SOLA):
        async with _una_verificacion():
            log(f"{sitio}: Cloudflare pide verificar la conexión; marca la casilla en la ventana que quedó al frente.")
            try:
                await _ventana(page, "maximized")
                await page.bring_to_front()
            except PlaywrightError:
                pass
            avisar("accion", titulo=f"{sitio}: verificación de Cloudflare",
                   texto="Si aparece la casilla «Verifica que eres un ser humano», márcala en la ventana del "
                         "navegador. Sigue solo al pasar.")
            try:
                with etapa("usuario"):
                    pasada = await _esperar_sin_verificacion(page, ESPERA_VERIFICACION)
            finally:
                avisar("accion_fin")
            if not pasada:
                raise FueraDeServicio(f"no se pasó la verificación de Cloudflare en {ESPERA_VERIFICACION // 60} "
                                      "minutos")
            try:
                await _ventana(page, "minimized")
            except PlaywrightError:
                pass
    log(f"{sitio}: verificación de Cloudflare superada.")
    await guardar_cookies_cloudflare(page.context)


def marcar_precarga(page: Page, url: str):
    _precargadas[page] = (url, instante_carga(page))


def instante_carga(page: Page) -> float:
    return _cargadas_en.get(page, time.monotonic())


async def esperar_estable(page: Page, timeout: int = 10000):
    """Espera a que la página deje de cambiar. Si sigue cambiando pasado `timeout` (un carrusel, un reloj), se sigue
    igual: la captura de la página como está es mejor que ninguna. Devuelve si alcanzó estabilidad."""
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
        return True
    except PlaywrightError as error:
        log(f"La página siguió cambiando {timeout // 1000} s; se captura como está ({_detalle(error)}).")
        return False


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
    if await bloqueo_en_pagina(page):
        raise SitioBloqueado
    # Antes del estado HTTP: la verificación de Cloudflare llega con 403.
    await revisar_verificacion(page)
    if respuesta and respuesta.status >= 400:
        raise PlaywrightError(f"el sitio respondió HTTP {respuesta.status}")
    if lista:
        await lista(page)
    else:
        await page.wait_for_load_state("load", timeout=milisegundos)


async def _tras_verificacion(page: Page, lista, espera: float):
    """Espera a que se pase la verificación de Cloudflare (sin tope de carga: la resuelve el usuario) y luego el
    formulario."""
    for _ in range(2):
        await pasar_verificacion(page)
        try:
            await asyncio.wait_for(lista(page) if lista else page.wait_for_load_state("load"), espera)
            return
        except VerificacionPendiente:
            continue
    raise FueraDeServicio("Cloudflare siguió pidiendo verificar la conexión")


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
        except VerificacionPendiente:
            log("Carga inicial: la precarga quedó en la verificación de Cloudflare.")
            try:
                await _tras_verificacion(page, lista, ESPERA_REUTILIZAR)
                _cargadas_en[page] = time.monotonic()
                return page
            except (PlaywrightError, TimeoutError):
                log("Carga inicial: tras la verificación el formulario no está listo; se vuelve a cargar.")
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
        except VerificacionPendiente:
            try:
                await _tras_verificacion(page, lista, espera)
            except (PlaywrightError, TimeoutError) as error:
                errores.append(_detalle(error) if str(error) else f"no abrió en {espera:.0f} s")
                continue
        except (PlaywrightError, TimeoutError) as error:
            errores.append(_detalle(error) if str(error) else f"no abrió en {espera:.0f} s")
            continue
        _cargadas_en[page] = time.monotonic()
        return page
    detalle = f" ({errores[-1]})" if errores else ""
    raise FueraDeServicio(f"la página no abrió ni recargándola{detalle}", direccion=url)
