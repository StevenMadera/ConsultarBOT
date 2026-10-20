import asyncio
import time
from collections.abc import Awaitable, Callable
from weakref import WeakKeyDictionary

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from comun import FueraDeServicio, log
from rendimiento import etapa

SEGUNDA_PESTANA = 15
RECARGA = 30
LIMITE_CARGA = 60
_sustitutas: dict[Page, Page] = {}
_precargadas: dict[Page, tuple[str, float]] = {}
_cargadas_en: WeakKeyDictionary = WeakKeyDictionary()
_limites: WeakKeyDictionary = WeakKeyDictionary()
MAX_SECUNDARIAS = 2


def limite_secundarias() -> asyncio.Semaphore:
    bucle = asyncio.get_running_loop()
    if bucle not in _limites:
        _limites[bucle] = asyncio.Semaphore(MAX_SECUNDARIAS)
    return _limites[bucle]


def marcar_precarga(page: Page, url: str):
    actual = pagina_actual(page)
    _precargadas[actual] = (url, instante_carga(actual))


def instante_carga(page: Page) -> float:
    return _cargadas_en.get(page, time.monotonic())


async def esperar_estable(page: Page, timeout: int = 10000):
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


def pagina_actual(page: Page) -> Page:
    while page in _sustitutas:
        page = _sustitutas[page]
    return page


async def cerrar_secundarias(page: Page):
    _precargadas.pop(page, None)
    while page in _sustitutas:
        siguiente = _sustitutas.pop(page)
        _precargadas.pop(siguiente, None)
        if not siguiente.is_closed():
            await siguiente.close()
        page = siguiente


async def conservar_pagina(page: Page) -> Page:
    actual = pagina_actual(page)
    while page in _sustitutas:
        siguiente = _sustitutas.pop(page)
        if not page.is_closed():
            await page.close()
        page = siguiente
    return actual


async def _detener(page: Page):
    if page.is_closed():
        return
    try:
        async with asyncio.timeout(3):
            cdp = await page.context.new_cdp_session(page)
            try:
                await cdp.send("Page.stopLoading")
            finally:
                await cdp.detach()
    except (PlaywrightError, TimeoutError) as error:
        detalle = str(error).splitlines()
        log(f"Carga inicial: no se pudo detener la pestaña pendiente ({detalle[0] if detalle else 'tiempo agotado'}).")


async def abrir_pestana_en_fondo(original: Page) -> Page:
    # context.new_page() activa la ventana y puede tapar el captcha que tiene el turno.
    contexto = original.context
    cdp = await contexto.new_cdp_session(original)
    try:
        info = await cdp.send("Target.getTargetInfo")
        async with contexto.expect_page() as pendiente:
            await cdp.send("Target.createTarget", {
                "url": "about:blank",
                "browserContextId": info["targetInfo"]["browserContextId"],
                "background": True,
            })
        return await pendiente.value
    finally:
        await cdp.detach()


async def cargar_pagina(
    page: Page,
    url: str,
    lista: Callable[[Page], Awaitable[object]] | None = None,
    *,
    recargar: bool = False,
    configurar: Callable[[Page], Awaitable[object]] | None = None,
    reutilizar: bool = False,
) -> Page:
    with etapa("carga"):
        return await _cargar_pagina(page, url, lista, recargar=recargar, configurar=configurar,
                                   reutilizar=reutilizar)


async def _cargar_pagina(page, url, lista, *, recargar, configurar, reutilizar):
    """Solo navega y verifica el formulario. Nunca rellena ni envía una consulta o un captcha."""
    original = pagina_actual(page)
    precarga = _precargadas.pop(original, None)
    if reutilizar and precarga and precarga[0] == url and time.monotonic() - precarga[1] < 45 \
            and not original.is_closed():
        try:
            async with asyncio.timeout(1):
                if lista:
                    await lista(original)
                else:
                    await original.wait_for_load_state("load", timeout=1000)
            log("Carga inicial: se reutiliza el formulario precargado vigente.")
            return original
        except (PlaywrightError, TimeoutError):
            log("Carga inicial: la precarga ya no está lista; se vuelve a cargar.")
    paginas = [original]
    tareas: dict[asyncio.Task, Page | None] = {}
    errores = []
    ganadora = None
    inicio = asyncio.get_running_loop().time()
    permiso = False
    tarea_apertura = None

    async def intentar(pagina, recarga=False):
        if configurar:
            await configurar(pagina)
        if recarga:
            respuesta = await pagina.reload(wait_until="domcontentloaded", timeout=60000)
        else:
            respuesta = await pagina.goto(url, wait_until="domcontentloaded", timeout=60000)
        if respuesta and respuesta.status >= 400:
            raise PlaywrightError(f"el sitio respondió HTTP {respuesta.status}")
        if lista:
            await lista(pagina)
        else:
            await pagina.wait_for_load_state("load", timeout=60000)
        return pagina

    def lanzar(pagina, recarga=False):
        tarea = asyncio.create_task(intentar(pagina, recarga))
        tareas[tarea] = pagina

    async def recoger(hasta):
        while asyncio.get_running_loop().time() < inicio + hasta:
            if not tareas:
                await asyncio.sleep(max(0, inicio + hasta - asyncio.get_running_loop().time()))
                return None
            hechas, _ = await asyncio.wait(
                tareas, timeout=max(0, inicio + hasta - asyncio.get_running_loop().time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for tarea in hechas:
                tareas.pop(tarea)
                try:
                    return tarea.result()
                except PlaywrightError as error:
                    errores.append(str(error).splitlines()[0])
        return None

    async def cancelar():
        pendientes = list(tareas)
        for tarea in pendientes:
            tarea.cancel()
        await asyncio.gather(*pendientes, return_exceptions=True)
        tareas.clear()

    async def abrir_segunda():
        nonlocal permiso
        await limite_secundarias().acquire()
        permiso = True
        segunda = await abrir_pestana_en_fondo(original)
        paginas.append(segunda)
        if original.viewport_size:
            await segunda.set_viewport_size(original.viewport_size)
        return await intentar(segunda)

    try:
        lanzar(original, recargar)
        ganadora = await recoger(SEGUNDA_PESTANA)
        if ganadora is None:
            log(f"{url}: no cargó en {SEGUNDA_PESTANA} s; se abre una segunda pestaña.")
            tarea_apertura = asyncio.create_task(abrir_segunda())
            tareas[tarea_apertura] = None
            ganadora = await recoger(RECARGA)
        if ganadora is None:
            log(f"{url}: no cargó en {RECARGA} s; se conserva la carga original y se reintenta la secundaria.")
            if len(paginas) > 1:
                if tarea_apertura in tareas:
                    tareas.pop(tarea_apertura)
                    tarea_apertura.cancel()
                    await asyncio.gather(tarea_apertura, return_exceptions=True)
                secundaria = paginas[1]
                lanzar(secundaria, secundaria.url not in ("", "about:blank", "chrome-error://chromewebdata/"))
            elif not tareas:
                lanzar(original)
            ganadora = await recoger(LIMITE_CARGA)
        if ganadora is None:
            detalle = f" ({errores[-1]})" if errores else ""
            raise FueraDeServicio(
                f"la página no respondió en {LIMITE_CARGA} s tras reintentar la carga{detalle}",
                direccion=url,
            )
        if ganadora is not original:
            _sustitutas[original] = ganadora
        _cargadas_en[ganadora] = time.monotonic()
        return ganadora
    except TimeoutError as error:
        raise FueraDeServicio(f"la página no respondió en {LIMITE_CARGA} s", direccion=url) from error
    finally:
        await cancelar()
        try:
            for pagina in paginas:
                if pagina is original:
                    if ganadora is not original:
                        await _detener(original)
                elif pagina is not ganadora and not pagina.is_closed():
                    await pagina.close()
        finally:
            if permiso:
                limite_secundarias().release()
