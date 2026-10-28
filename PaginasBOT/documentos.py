"""Páginas 14 a 19 del informe: cada una entrega un PDF que se añade al final, en el orden de la portada."""
import asyncio
import base64
import contextlib
import itertools
import random
import re
import time
import weakref
from datetime import date, datetime
from pathlib import Path

import pymupdf
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from comun import CAPTURAS, FueraDeServicio, avisar, log
from captcha_registraduria import JS_IMAGEN_CAPTCHA, imagen_desde_data_url, lecturas, leer_captcha
from carga_paginas import (BLOQUEO_JS, BLOQUEO_RE, MOTIVO_BLOQUEO, VERIFICACION_JS, VIGENCIA_PRECARGA, SitioBloqueado,
                           bloqueo_en_pagina, cargar_pagina, esperar_estable, instante_carga, marcar_precarga,
                           pasar_verificacion, revisar_verificacion, verificacion_en_pagina)
from rendimiento import etapa

ORDEN = ("bdme", "contraloria", "antecedentes", "registraduria", "rnmc", "inhabilidades")
ESPERA_RESULTADO_INHABILIDADES = 30000

URL_BDME = "https://cobrocoactivonew.ramajudicial.gov.co/BDME_Publica2_list.php"
CAMPO_BDME = "#BDME_Consulta_Doc_SancionadoId"
# Formulario que la página de la Contraloría muestra dentro de un marco.
URL_CONTRALORIA = "https://cfiscal.contraloria.gov.co/Certificados/CertificadoPersonaNatural.aspx"
URL_ANTECEDENTES = "https://antecedentes.policia.gov.co:7005/WebJudicial/antecedentes.xhtml"
URL_REGISTRADURIA = "https://certvigenciacedula.registraduria.gov.co/Datos.aspx?AspxAutoDetectCookieSupport=1"
# Tras el «Acceso bloqueado» de su Cloudflare, cada visita alarga el bloqueo de la IP (que comparten los equipos de la
# oficina): durante este tiempo, si su ventana sigue mostrando el aviso, se da por bloqueada sin volver a cargarla.
PAUSA_BLOQUEO = 15 * 60
_bloqueada_hasta = 0.0
URL_RNMC = "https://srvcnpc.policia.gov.co/PSC/frm_cnp_consulta.aspx"
URL_INHABILIDADES = "https://inhabilidades.policia.gov.co:8080/consulta"
EMPRESA_INHABILIDADES = "Secap"
NIT_INHABILIDADES = "8301340838"

TIPOS_CONTRALORIA = {"CC": "CC", "CE": "CE", "TI": "TI", "PEP": "PEP", "PPT": "PPT"}
TIPOS_ANTECEDENTES = {"CC": "cc", "CE": "cx"}
TIPOS_RNMC = {"CC": "55", "CE": "57", "TI": "56", "NIT": "1"}
TIPOS_INHABILIDADES = {"CC": "CC", "CE": "CX"}

RECAPTCHA_RESUELTO_JS = """() => [...document.querySelectorAll('textarea[name=g-recaptcha-response]')]
    .some(t => t.value.length > 0)"""
ESPERA_CAPTCHA = 180000
# Segundos que la casilla de Google puede seguir sin dejarse marcar (sin terminar de cargar, o girando tras pulsarla
# sin abrir las imágenes) antes de dar el sitio por fuera de servicio. Funcionando, responde en 1-3 s.
ESPERA_CASILLA = 30
# Con el aviso de Google de que el sitio superó su cuota gratuita de reCAPTCHA, la casilla que no avanza en estos
# segundos ya no va a avanzar: se recarga de una vez (antes se perdían 50-65 s esperando).
ESPERA_CON_CUOTA = 6
CUOTA_RE = re.compile(r"cuota|quota", re.I)
MOTIVO_CUOTA = " (Google avisa que el sitio superó su cuota gratuita de reCAPTCHA)"
# Si la casilla tardó más que esto en marcarse, o no se marcó, su contexto queda en casillas_lentas y main cambia
# esa ventana por una limpia: Google la endurece cada vez más en la misma ventana.
CASILLA_LENTA = 60
casillas_lentas = weakref.WeakSet()
ESTADO_CASILLA_JS = """e => {
    const r = e.getBoundingClientRect(), giro = e.querySelector('.recaptcha-checkbox-spinner');
    return {lista: r.width > 0 && r.height > 0, girando: !!giro && parseFloat(getComputedStyle(giro).opacity) > 0};
}"""
# El recuadro de las imágenes de Google: va en la página, oculto (visibility hidden) mientras no se usa.
DESAFIO_VISIBLE_JS = """() => [...document.querySelectorAll('iframe[src*="/bframe"]')].some(f => {
    const r = f.getBoundingClientRect();
    return getComputedStyle(f).visibility === 'visible' && r.height > 100 && r.bottom > 0;
})"""
# Ese recuadro se crea 1-3 s después de que se ve la casilla (con el equipo ocupado, mucho después) y, hasta que carga,
# la casilla pulsada se queda girando. Con su código (#recaptcha-token) cargado, responde al pulsarla.
DESAFIO = 'iframe[src*="/bframe"]'
ESPERA_CAPTCHA_LISTO = 45
# Buster (extensión que resuelve el reto de audio de Google), para Antecedentes e Inhabilidades.
# Contextos con la extensión cargada: los marca main al abrirlos.
contextos_buster = weakref.WeakSet()
# Si Google bloquea el audio para esta red, no se vuelve a intentar con Buster hasta reiniciar el bot.
audio_bloqueado = False
BOTON_BUSTER = ".help-button-holder"
BLOQUEO_AUDIO = ".rc-doscaptcha-body"
AUDIO_JS = "html => html.querySelector('audio#audio-source')?.src ?? ''"
# Segundos entre pulsaciones de Buster (mientras trabaja ignora las pulsaciones, así que pulsar seguido no lo
# interrumpe: apenas falla un intento empieza el siguiente), rondas de audio como máximo, tiempo total, y tras cuánto
# se vuelve a pulsar la casilla si el reto se cerró sin marcarla.
REINTENTO_BUSTER = 2
RONDAS_BUSTER = 5
ESPERA_BUSTER = 90
REINTENTO_CASILLA_BUSTER = 15
# Lo que la casilla envía a Google al pulsarla y al resolver las imágenes.
ENVIO_CASILLA_RE = re.compile(r"/recaptcha/(?:api2|enterprise)/(?:reload|userverify)")
# Segundos que un captcha aguanta cargado: después el código cambia o el sitio pierde la sesión. Medido: la sesión de
# Antecedentes seguía vigente a los 111 s y había vencido a los 122 s; Inhabilidades seguía sirviendo a los 4 minutos.
VIGENCIA_CAPTCHA = 60
VIGENCIAS = {"antecedentes": 100, "inhabilidades": 180}
# Registraduría: envíos con el código leído por el bot antes de pedirlo en la consola, e imágenes que se piden
# (sin enviar nada) cuando una lectura no es confiable.
INTENTOS_AUTO_REGISTRADURIA = 3
INTENTOS_LECTURA_REGISTRADURIA = 3
# RUNT y Registraduría se llenan a ritmo de persona para no parecer automatizados: segundos entre pasos y entre
# teclas.
PAUSA_HUMANA = (0.2, 0.5)
TECLA_HUMANA = (0.03, 0.08)
# Mientras espera su turno, la página se recarga cuando le quedan estos segundos de vigencia.
MARGEN_RECARGA = 15
# Cuando hay varios captchas listos: los dos de texto seguidos en la ventanita y el RUNT primero, porque su
# resultado es el que más tarda en procesarse; después las casillas de la Policía.
ORDEN_TURNOS = ("runt", "registraduria", "antecedentes", "inhabilidades")


def vigencia(clave: str) -> float:
    """Segundos que aguanta cargado el formulario con captcha del sitio."""
    return VIGENCIAS.get(clave, VIGENCIA_CAPTCHA)


def vigencia_precarga(clave: str) -> float:
    """Edad máxima de una precarga para usarla al empezar la consulta: con el mismo margen de los demás sitios (llenar
    el formulario y esperar el turno), su captcha no vence antes de resolverlo."""
    return vigencia(clave) - (VIGENCIA_CAPTCHA - VIGENCIA_PRECARGA)


async def pausa_humana(minimo: float = PAUSA_HUMANA[0], maximo: float = PAUSA_HUMANA[1]):
    await asyncio.sleep(random.uniform(minimo, maximo))


async def escribir_despacio(campo, texto: str):
    """Escribe tecla por tecla, con un ritmo irregular, en vez de pegar el texto de golpe."""
    await campo.fill("")
    for letra in texto:
        await campo.press_sequentially(letra)
        await asyncio.sleep(random.uniform(*TECLA_HUMANA))


async def estado_ventana(page, estado: str):
    """Minimiza o maximiza la ventana de una página del navegador visible."""
    cdp = await page.context.new_cdp_session(page)
    try:
        ventana = (await cdp.send("Browser.getWindowForTarget"))["windowId"]
        actual = (await cdp.send("Browser.getWindowBounds", {"windowId": ventana}))["bounds"]["windowState"]
        if actual == "minimized" and estado != "minimized":
            # Chrome no pasa de minimizada a maximizada sin restaurarla antes.
            await cdp.send("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": "normal"}})
        await cdp.send("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": estado}})
    finally:
        await cdp.detach()


async def al_frente(page):
    await estado_ventana(page, "maximized")
    await page.bring_to_front()


async def precargar(page, url: str, lista=None):
    """Deja el formulario listo para reutilizarlo una vez, mientras su captcha siga vigente."""
    await estado_ventana(page, "minimized")
    marcar_precarga(await cargar_pagina(page, url, lista), url)


class Turno:
    def __init__(self, turnos: "Turnos"):
        self._turnos, self._activo = turnos, True

    def soltar(self):
        """Deja pasar al siguiente; se puede llamar antes de salir del bloque, apenas se envía el captcha."""
        if self._activo:
            self._activo = False
            self._turnos._soltar()


class Turnos:
    """Un captcha a la vez en pantalla. Entre las páginas listas pasa primero la que va antes en ORDEN_TURNOS; la
    que debe recargarse sale de la fila mientras recarga, así no hace esperar a las demás."""

    def __init__(self):
        self._ocupado = False
        self._fila: list[tuple] = []
        self._orden = itertools.count()
        # Formularios con captcha ya llenos (o que no llegarán a estarlo): el escalonado de la consulta los espera.
        self._preparados: dict[str, asyncio.Event] = {}

    def _preparado(self, clave: str) -> asyncio.Event:
        return self._preparados.setdefault(clave, asyncio.Event())

    def preparado(self, clave: str):
        """El formulario del sitio ya está lleno (o falló): lo que esperaba a los formularios puede empezar."""
        self._preparado(clave).set()

    async def esperar_preparados(self, claves, segundos: float) -> bool:
        """True si los formularios de `claves` quedan llenos antes de `segundos`."""
        esperas = [asyncio.ensure_future(self._preparado(clave).wait()) for clave in claves]
        if not esperas:
            return True
        try:
            _, pendientes = await asyncio.wait(esperas, timeout=segundos)
            return not pendientes
        finally:
            for espera in esperas:
                espera.cancel()

    def _repartir(self):
        if not self._ocupado and self._fila:
            entrada = min(self._fila)
            self._fila.remove(entrada)
            self._ocupado = True
            entrada[2].set_result(None)

    def _soltar(self):
        self._ocupado = False
        self._repartir()

    async def _esperar(self, prioridad: int, segundos: float | None) -> bool:
        with etapa("cola"):
            return await self._esperar_turno(prioridad, segundos)

    async def _esperar_turno(self, prioridad: int, segundos: float | None) -> bool:
        """True cuando le toca el turno; False si antes pasan `segundos`."""
        futuro = asyncio.get_running_loop().create_future()
        entrada = (prioridad, next(self._orden), futuro)
        self._fila.append(entrada)
        self._repartir()
        try:
            await asyncio.wait({futuro}, timeout=segundos)
        except asyncio.CancelledError:
            # Si el turno llegó justo al cancelar, pasa al siguiente.
            if futuro.done():
                self._soltar()
            else:
                self._fila.remove(entrada)
            raise
        if futuro.done():
            return True
        self._fila.remove(entrada)
        return False

    @contextlib.asynccontextmanager
    async def turno(self, clave: str, lleno_en, llenar):
        """El turno de la página `clave`: si va a caducar mientras espera, se recarga fuera de la fila."""
        # Quien pide turno ya tiene su formulario lleno.
        self.preparado(clave)
        prioridad = ORDEN_TURNOS.index(clave)
        while True:
            restante = vigencia(clave) - MARGEN_RECARGA - (time.monotonic() - lleno_en())
            if restante > 0 and await self._esperar(prioridad, restante):
                break
            try:
                await llenar()
            except (PlaywrightError, ValueError):
                # Sin página nueva se espera igual; al llegar el turno, la consulta la vuelve a cargar.
                if await self._esperar(prioridad, None):
                    break
        turno = Turno(self)
        try:
            yield turno
        finally:
            turno.soltar()

    @contextlib.asynccontextmanager
    async def pantalla(self):
        """La pantalla para las capturas de Chrome, después de los captchas que ya estén en la fila."""
        await self._esperar(len(ORDEN_TURNOS), None)
        turno = Turno(self)
        try:
            yield turno
        finally:
            turno.soltar()


def tipo_para(tabla: dict, persona: dict, sitio: str) -> str:
    if persona["tipo"] not in tabla:
        raise ValueError(f"{sitio} no consulta el tipo de documento {persona['tipo']}; no aplica.")
    return tabla[persona["tipo"]]


def fecha_informe(persona: dict) -> str:
    """La fecha de consulta (dd-mm-aaaa) que lleva el informe: `persona["fecha_consulta"]` si existe, ya sea una
    fecha o un texto; si no, la de hoy."""
    valor = persona.get("fecha_consulta")
    if not valor:
        return f"{datetime.now():%d/%m/%Y}"
    if isinstance(valor, (date, datetime)):
        return f"{valor:%d/%m/%Y}"
    return str(valor).replace("-", "/")


def ruta_documento(clave: str, persona: dict) -> Path:
    CAPTURAS.mkdir(exist_ok=True)
    return CAPTURAS / f"{clave}_{persona['numero']}_{datetime.now():%Y%m%d_%H%M%S}.pdf"


def validar_pdf(ruta: Path):
    try:
        with pymupdf.open(ruta) as doc:
            if doc.page_count:
                return
    except Exception:
        pass
    raise ValueError(f"El archivo descargado no es un PDF válido: {ruta.name}")


def imagen_a_pdf(png: bytes, ruta: Path) -> Path:
    """Hoja A4 con la captura arriba, al ancho de la hoja (como la última página de la plantilla)."""
    margen = 28
    doc = pymupdf.open()
    pagina = doc.new_page(width=595, height=842)
    zona = pymupdf.Rect(margen, margen, 595 - margen, 842 - margen)
    with pymupdf.open(stream=png, filetype="png") as imagen:
        alto = zona.width * imagen[0].rect.height / imagen[0].rect.width
    zona.y1 = min(zona.y1, zona.y0 + alto)
    pagina.insert_image(zona, stream=png, keep_proportion=True)
    doc.save(ruta, garbage=3, deflate=True)
    doc.close()
    return ruta


# Borde inferior del contenido visible: debajo solo queda fondo vacío.
ALTO_CONTENIDO_JS = """() => {
    let abajo = 0;
    for (const e of document.body.querySelectorAll('*')) {
        const r = e.getBoundingClientRect();
        const conTexto = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
        if (r.width && r.height && (conTexto || ['IMG', 'INPUT', 'SELECT', 'BUTTON', 'IFRAME', 'CANVAS'].includes(e.tagName)))
            abajo = Math.max(abajo, r.bottom + window.scrollY);
    }
    return Math.ceil(abajo);
}"""


# Ancho que necesita el contenido, incluidas las tablas que se salen de su recuadro.
ANCHO_CONTENIDO_JS = """() => {
    let ancho = document.documentElement.scrollWidth;
    for (const e of document.body.querySelectorAll('*')) {
        if (e.scrollWidth > e.clientWidth + 1 && e.clientWidth)
            ancho = Math.max(ancho, e.getBoundingClientRect().left + e.scrollWidth);
    }
    return Math.ceil(ancho);
}"""

# Ancho útil de una hoja A4 con los márgenes de Chrome al imprimir (1 cm por lado), en píxeles CSS.
ANCHO_IMPRIMIBLE = round((8.27 - 2 / 2.54) * 96)

# Chrome ya reduce solo una página más ancha que la hoja, pero un recuadro con barra de desplazamiento corta lo que
# no le cabe (la tabla de 15 columnas del RNMC): solo ese recuadro se reduce hasta que su contenido quepa.
REDUCIR_RECORTADOS_JS = """() => {
    for (const e of document.body.querySelectorAll('*')) {
        if (e.clientWidth && e.scrollWidth > e.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(e).overflowX))
            e.style.zoom = Math.floor(e.clientWidth / e.scrollWidth * 100) / 100;
    }
}"""
# Antecedentes, RNMC e Inhabilidades escriben las fechas como dd/mm/aaaa: cada una se sustituye por la fecha de
# consulta del informe (dd-mm-aaaa), que llega como argumento.
FECHA_CONSULTA_JS = r"""(fechaConsulta) => {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) {
        const nuevo = n.nodeValue.replace(/\b\d{2}\/\d{2}\/\d{4}\b/g, fechaConsulta);
        if (nuevo !== n.nodeValue) n.nodeValue = nuevo;
    }
}"""

FECHA_PROCURADURIA_JS = r"""(fechaConsulta) => {
    const [dia, mes, anio] = fechaConsulta.split('/').map(Number);
    const fecha = new Date(Date.UTC(anio, mes - 1, dia));
    const diaSemana = new Intl.DateTimeFormat('es-CO', {weekday: 'long', timeZone: 'UTC'}).format(fecha);
    const nombreMes = new Intl.DateTimeFormat('es-CO', {month: 'long', timeZone: 'UTC'}).format(fecha);
    const fechaLarga = `${diaSemana}, ${nombreMes} ${dia}, ${anio}`;
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: n => n.parentElement.closest('script, style, noscript')
            ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    const nodos = [];
    let texto = '';
    for (let n; (n = w.nextNode());) {
        nodos.push({n, inicio: texto.length, fin: texto.length + n.nodeValue.length});
        texto += n.nodeValue;
    }
    const patron = /fecha\s*(?:de\s*)?consulta\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4}|[a-záéíóúñ]+\s*,\s*[a-záéíóúñ]+\s+\d{1,2}\s*,\s*\d{4})\b/gi;
    const fechas = [...texto.matchAll(patron)];
    for (const hallado of fechas.reverse()) {
        const fin = hallado.index + hallado[0].length;
        const inicio = fin - hallado[1].length;
        const nueva = /^\d/.test(hallado[1]) ? fechaConsulta : fechaLarga;
        let escrito = false;
        for (const {n, inicio: desde, fin: hasta} of nodos) {
            if (hasta <= inicio || desde >= fin) continue;
            n.nodeValue = n.nodeValue.slice(0, Math.max(0, inicio - desde))
                + (escrito ? '' : nueva)
                + n.nodeValue.slice(Math.min(n.nodeValue.length, fin - desde));
            escrito = true;
        }
    }
    return fechas.length;
}"""

async def pagina_a_pdf(page, ruta: Path) -> Path:
    with etapa("captura"):
        return await _pagina_a_pdf(page, ruta)


async def _pagina_a_pdf(page, ruta: Path) -> Path:
    if page.viewport_size:
        ancho = await page.evaluate(ANCHO_CONTENIDO_JS)
        if ancho > page.viewport_size["width"]:
            # Si no, lo que se sale por la derecha (columnas de tablas, logos) queda cortado.
            await page.set_viewport_size({"width": min(ancho + 20, 1600), "height": page.viewport_size["height"]})
            await page.wait_for_timeout(500)
    alto_total = await page.evaluate("document.documentElement.scrollHeight")
    alto = min(alto_total, await page.evaluate(ALTO_CONTENIDO_JS) + 30)
    ancho = await page.evaluate("document.documentElement.clientWidth")
    recorte = {"x": 0, "y": 0, "width": ancho, "height": alto} if alto > 200 else None
    return imagen_a_pdf(await page.screenshot(full_page=True, clip=recorte), ruta)


async def reducir_recortados(cdp, page):
    # Se mide como queda en la hoja: con el estilo de impresión y el ancho útil de la hoja.
    await cdp.send("Emulation.setEmulatedMedia", {"media": "print"})
    await cdp.send("Emulation.setDeviceMetricsOverride",
                   {"width": ANCHO_IMPRIMIBLE, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    try:
        await page.evaluate(REDUCIR_RECORTADOS_JS)
    finally:
        await cdp.send("Emulation.clearDeviceMetricsOverride")
        await cdp.send("Emulation.setEmulatedMedia", {"media": ""})


async def imprimir_pdf(page, ruta: Path, fecha_consulta: str, fondos: bool = False) -> Path:
    with etapa("captura"):
        return await _imprimir_pdf(page, ruta, fecha_consulta, fondos)


async def _imprimir_pdf(page, ruta: Path, fecha_consulta: str, fondos: bool = False) -> Path:
    """Lo mismo que Imprimir > Guardar como PDF en Chrome: hoja A4 como la plantilla, con las hojas que necesite.
    Toda fecha dd/mm/aaaa de la página queda como `fecha_consulta`."""
    await page.evaluate(FECHA_CONSULTA_JS, fecha_consulta)
    cdp = await page.context.new_cdp_session(page)
    try:
        await reducir_recortados(cdp, page)
        opciones = {"paperWidth": 8.27, "paperHeight": 11.69, "printBackground": fondos}
        try:
            datos = await cdp.send("Page.printToPDF", opciones)
        except PlaywrightError:
            # Chrome a veces falla la impresión mientras otra ventana tiene el foco; un reintento basta.
            await page.wait_for_timeout(1500)
            try:
                datos = await cdp.send("Page.printToPDF", opciones)
            except PlaywrightError:
                datos = None
    finally:
        with contextlib.suppress(PlaywrightError):
            await cdp.detach()
    if datos is None:
        log(f"{ruta.stem}: la impresión de la página falló; se guarda su captura en PDF.")
        return imagen_a_pdf(await page.screenshot(full_page=True, timeout=15000), ruta)
    ruta.write_bytes(base64.b64decode(datos["data"]))
    return ruta


async def pdf_de_pagina(page, ruta: Path) -> Path:
    """La página tal como está, en A4: lo mismo que Imprimir > Guardar como PDF. Si la impresión falla o no responde
    (pasó con la página de error del navegador), la captura de la página en una hoja A4."""
    cdp = await page.context.new_cdp_session(page)
    try:
        datos = await asyncio.wait_for(
            cdp.send("Page.printToPDF", {"paperWidth": 8.27, "paperHeight": 11.69, "printBackground": True}), 20)
    except (TimeoutError, PlaywrightError):
        datos = None
    finally:
        with contextlib.suppress(PlaywrightError, TimeoutError):
            await asyncio.wait_for(cdp.detach(), 5)
    if datos is None:
        log(f"{ruta.stem}: la impresión de la página falló; se guarda su captura en PDF.")
        return imagen_a_pdf(await page.screenshot(timeout=15000), ruta)
    ruta.write_bytes(base64.b64decode(datos["data"]))
    return ruta


# No dicen nada del sitio: la navegación se cortó por otra o este equipo se quedó sin internet.
ERRORES_LOCALES = ("ERR_ABORTED", "ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_NETWORK_IO_SUSPENDED")
# La página no contesta (ni siquiera para revisarla): con el equipo saturado, a veces es la ventana y no el sitio.
SIN_RESPUESTA = "el sitio no respondió"
ESTADO_HTTP_JS = "() => performance.getEntriesByType('navigation')[0]?.responseStatus ?? 0"


async def caida_en_pagina(page) -> str | None:
    """Motivo si la página es la de error del navegador, un error del servidor (5xx) o el aviso de bloqueo; None si
    no."""
    if page.url.startswith("chrome-error://"):
        return "el navegador no pudo abrir el sitio"
    try:
        estado = await page.evaluate(ESTADO_HTTP_JS)
    except PlaywrightError:
        return None
    if await bloqueo_en_pagina(page):
        return MOTIVO_BLOQUEO
    return f"el sitio respondió con el error {estado}" if estado >= 500 else None


async def motivo_caida(page, error: Exception) -> tuple[str | None, str | None]:
    """(motivo, dirección) si `error` se debe a que el sitio está caído; la dirección solo si no respondió a tiempo.
    (None, None) si falló por otra causa."""
    texto = str(error)
    red = re.search(r"net::(ERR_\w+)", texto)
    if red and red[1] not in ERRORES_LOCALES:
        if red[1] in ("ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET"):
            # SIMIT, Procuraduría y RUNT cortan así las conexiones que llegan por VPN.
            return f"el sitio cerró la conexión ({red[1]}). Si estás con VPN, apágala y repite la consulta", None
        return f"no se pudo conectar con el sitio ({red[1]})", None
    agotado = re.match(r"Page\.(?:goto|reload): Timeout (\d+)ms", texto)
    if agotado:
        direccion = re.search(r'navigating to "([^"]+)"', texto)
        return f"el sitio no respondió en {int(agotado[1]) // 1000} s", direccion[1] if direccion else page.url
    try:
        return await asyncio.wait_for(caida_en_pagina(page), 10), None
    except TimeoutError:
        # Un envío de formulario sigue sin respuesta: hasta cortarlo, la página no contesta.
        return SIN_RESPUESTA, page.url


async def detener_carga(page):
    """Corta la carga pendiente: mientras un envío espera respuesta, evaluate y los localizadores no contestan."""
    cdp = await page.context.new_cdp_session(page)
    try:
        await cdp.send("Page.stopLoading")
    finally:
        await cdp.detach()


async def aviso_tiempo_agotado(page, url: str):
    """Deja en la pestaña el aviso de Chrome «ha tardado demasiado tiempo en responder» para `url`, sin volver a
    esperar. Tras agotarse la espera la pestaña sigue mostrando la página anterior, que puede ser de otra consulta."""
    async def agotar(route):
        if route.request.is_navigation_request():
            await route.abort("timedout")
        else:
            await route.continue_()

    # La carga que sigue esperando retiene la dirección unos 20 s: se detiene antes de pedirla otra vez.
    await detener_carga(page)
    await page.route("**/*", agotar)
    try:
        await page.goto(url, wait_until="commit", timeout=15000)
    except PlaywrightError:
        pass  # Es el error esperado: la pestaña queda con el aviso.
    finally:
        await page.unroute("**/*", agotar)


async def descargar(page, accion, ruta: Path, espera: int = 60000) -> Path:
    async with page.expect_download(timeout=espera) as info:
        await accion()
    await (await info.value).save_as(ruta)
    validar_pdf(ruta)
    return ruta


async def descargar_pdf_resultado(page, boton, ruta: Path, espera: int = 30000) -> Path:
    resultado = asyncio.get_running_loop().create_future()

    async def recibir(route):
        if route.request.resource_type not in ("document", "xhr", "fetch", "other"):
            await route.continue_()
            return
        try:
            respuesta = await route.fetch(timeout=espera)
            tipo = respuesta.headers.get("content-type", "").lower()
            datos = await respuesta.body() if "pdf" in tipo or "octet-stream" in tipo else b""
            await route.fulfill(response=respuesta)
            if b"%PDF-" in datos[:1024] and not resultado.done():
                resultado.set_result(datos)
        except PlaywrightError as error:
            if not resultado.done():
                resultado.set_exception(error)
            with contextlib.suppress(PlaywrightError):
                await route.abort()

    await page.context.route("**/*", recibir)
    try:
        await boton.click(no_wait_after=True, timeout=espera)
        datos = await asyncio.wait_for(resultado, espera / 1000)
        ruta.write_bytes(datos)
        validar_pdf(ruta)
        return ruta
    finally:
        await page.context.unroute("**/*", recibir)
        if not resultado.done():
            resultado.cancel()


async def pdf_del_sitio_o_captura(page, boton, ruta: Path, exigir_pdf: bool = False) -> Path:
    """El PDF que entrega el sitio al pulsar `boton`; si no entrega ninguno, la página capturada como PDF."""
    boton = boton.filter(visible=True)
    if await boton.count():
        try:
            if exigir_pdf:
                return await descargar_pdf_resultado(page, boton.first, ruta)
            return await descargar(page, boton.first.click, ruta, espera=30000)
        except (PlaywrightTimeoutError, TimeoutError, ValueError) as e:
            if exigir_pdf:
                raise ValueError("Registraduría: no se pudo descargar el PDF del certificado; no se guardará "
                                 "la página como resultado.") from e
            log(f"{ruta.stem}: el sitio no entregó un PDF ({str(e).splitlines()[0]}); se guarda la página.")
    if exigir_pdf:
        raise ValueError("Registraduría: no apareció el enlace de descarga del certificado.")
    return await pagina_a_pdf(page, ruta)


async def esperar_captcha_listo(page, sitio: str):
    """Espera a que el captcha de Google termine de prepararse: su casilla se ve antes y, pulsada antes de tiempo, se
    queda girando. Si no termina en ESPERA_CAPTCHA_LISTO s se muestra igual (si la casilla se atasca, se recarga)."""
    try:
        await page.locator(DESAFIO).first.content_frame.locator("#recaptcha-token").wait_for(
            state="attached", timeout=ESPERA_CAPTCHA_LISTO * 1000)
    except PlaywrightTimeoutError:
        log(f"{sitio}: el captcha de Google no terminó de prepararse en {ESPERA_CAPTCHA_LISTO} s; se muestra igual.")


async def casilla_atascada(page, recuadro) -> bool:
    """True si la casilla de Google no se puede marcar: no terminó de cargar, o gira sin abrir las imágenes."""
    casilla = recuadro.content_frame.locator("#recaptcha-anchor")
    try:
        estado = await casilla.evaluate(ESTADO_CASILLA_JS, timeout=1000) if await casilla.count() else None
        if estado and estado["lista"] and not estado["girando"]:
            return False
        return not await page.evaluate(DESAFIO_VISIBLE_JS)
    except PlaywrightError:
        return True


async def aviso_de_cuota(recuadro) -> bool:
    """True si la casilla de Google muestra el aviso de que el sitio superó su cuota gratuita de reCAPTCHA."""
    try:
        return bool(CUOTA_RE.search(await recuadro.content_frame.locator("body").inner_text(timeout=1500)))
    except PlaywrightError:
        return False


async def resolver_con_buster(page, sitio: str, recuadro) -> bool:
    """Marca la casilla y pulsa Buster en el reto de audio. True si quedó resuelta; False para seguir a mano (sin
    Buster en esta ventana, Google bloqueó el audio, RONDAS_BUSTER rondas sin éxito, sin botón o tiempo agotado).
    FueraDeServicio si la casilla no avanza y Google avisa que el sitio superó su cuota: se recarga de una vez."""
    global audio_bloqueado
    if audio_bloqueado or page.context not in contextos_buster:
        return False
    casilla = recuadro.content_frame.locator("#recaptcha-anchor")
    marco = page.locator(DESAFIO).first
    try:
        await esperar_captcha_listo(page, sitio)
        log(f"{sitio}: Buster marca 'No soy un robot'.")
        await casilla.click(timeout=10000)
        inicio = ultimo_clic_casilla = time.monotonic()
        ultimo_clic_buster = revisada_cuota = 0.0
        audio_visto = None
        avisado = False
        rondas = 0
        while (ahora := time.monotonic()) < inicio + ESPERA_BUSTER:
            if await page.evaluate(RECAPTCHA_RESUELTO_JS):
                log(f"{sitio}: captcha resuelto con Buster en {ahora - inicio:.0f} s.")
                return True
            if await page.evaluate(DESAFIO_VISIBLE_JS):
                desafio = marco.content_frame
                if await desafio.locator(BLOQUEO_AUDIO).is_visible():
                    audio_bloqueado = True
                    log(f"{sitio}: Google bloqueó el audio para esta red; hasta reiniciar el bot, la casilla se "
                        "marca a mano.")
                    return False
                audio = await desafio.locator("html").evaluate(AUDIO_JS)
                if audio != audio_visto:
                    audio_visto = audio
                    ultimo_clic_buster = 0.0
                    rondas += bool(audio)
                    avisado = False
                if rondas > RONDAS_BUSTER:
                    log(f"{sitio}: Buster no lo resolvió en {RONDAS_BUSTER} rondas de audio; se marca a mano.")
                    return False
                if ahora - ultimo_clic_buster > REINTENTO_BUSTER:
                    boton = desafio.locator(BOTON_BUSTER).first
                    if not await boton.is_visible():
                        log(f"{sitio}: no aparece el botón de Buster en el reto; se marca a mano.")
                        return False
                    if not avisado:
                        log(f"{sitio}: pulsando Buster (ronda {rondas})." if audio else f"{sitio}: pulsando Buster.")
                        avisado = True
                    with contextlib.suppress(PlaywrightTimeoutError):
                        await boton.click(timeout=5000)
                    ultimo_clic_buster = ahora
            elif await casilla.get_attribute("aria-checked") == "false":
                # Pulsada, sin marcarse ni abrir el reto: con el aviso de cuota de Google no va a avanzar.
                if ahora - ultimo_clic_casilla > ESPERA_CON_CUOTA and ahora - revisada_cuota > 3:
                    revisada_cuota = ahora
                    if await aviso_de_cuota(recuadro):
                        raise FueraDeServicio(f"la casilla «No soy un robot» no avanzó en "
                                              f"{ahora - ultimo_clic_casilla:.0f} s{MOTIVO_CUOTA}")
                if ahora - ultimo_clic_casilla > REINTENTO_CASILLA_BUSTER:
                    log(f"{sitio}: Buster marca de nuevo 'No soy un robot'.")
                    await casilla.click(timeout=10000)
                    ultimo_clic_casilla = ahora
                    audio_visto = None
            await asyncio.sleep(0.5)
        log(f"{sitio}: Buster no lo resolvió en {ESPERA_BUSTER} s; se marca a mano.")
    except PlaywrightError as e:
        log(f"{sitio}: Buster no pudo seguir ({(str(e).splitlines() or [''])[0]}); se marca a mano.")
    return False


async def esperar_recaptcha(page, sitio: str, recuadro) -> bool:
    with etapa("usuario"):
        return await _esperar_recaptcha(page, sitio, recuadro)


async def _esperar_recaptcha(page, sitio: str, recuadro) -> bool:
    """True cuando la casilla queda marcada; False si Google la da por caducada. Si la casilla no se deja marcar
    durante ESPERA_CASILLA segundos, el sitio está fuera de servicio."""
    caducada = recuadro.content_frame.locator(".rc-anchor-error-msg-container")
    inicio = time.monotonic()
    limite = inicio + ESPERA_CAPTCHA / 1000
    atascada_desde = None
    # Para saber, si la casilla se atasca, si llegó a consultar a Google y si Google respondió.
    envios = {"enviados": 0, "respondidos": 0}

    def enviado(peticion):
        if ENVIO_CASILLA_RE.search(peticion.url):
            envios["enviados"] += 1

    def respondido(respuesta):
        if ENVIO_CASILLA_RE.search(respuesta.url):
            envios["respondidos"] += 1

    page.on("request", enviado)
    page.on("response", respondido)
    avisar("accion", titulo=sitio, texto="Marca «No soy un robot» en la ventana del navegador. Sigue solo al marcarla.")
    try:
        while time.monotonic() < limite:
            if await page.evaluate(RECAPTCHA_RESUELTO_JS):
                tardo = time.monotonic() - inicio
                log(f"{sitio}: casilla marcada en {tardo:.0f} s.")
                if tardo > CASILLA_LENTA:
                    casillas_lentas.add(page.context)
                return True
            if await caducada.is_visible():
                return False
            if not await casilla_atascada(page, recuadro):
                atascada_desde = None
            elif atascada_desde is None:
                atascada_desde = time.monotonic()
            elif (atascada := time.monotonic() - atascada_desde) >= ESPERA_CASILLA or (
                    atascada >= ESPERA_CON_CUOTA and await aviso_de_cuota(recuadro)):
                motivo = f"la casilla «No soy un robot» no terminó de cargar en {atascada:.0f} s"
                with contextlib.suppress(PlaywrightError):
                    if not await page.locator(DESAFIO).count():
                        motivo += " (el captcha de Google no terminó de prepararse)"
                    elif envios["enviados"] > envios["respondidos"]:
                        motivo += " (Google no respondió al pulsarla)"
                    elif not envios["enviados"]:
                        motivo += " (la casilla no llegó a consultar a Google)"
                if await aviso_de_cuota(recuadro):
                    motivo += MOTIVO_CUOTA
                raise FueraDeServicio(motivo)
            await asyncio.sleep(0.5)
        casillas_lentas.add(page.context)
        raise ValueError(f"{sitio}: no se resolvió el captcha en {ESPERA_CAPTCHA // 60000} minutos.")
    finally:
        page.remove_listener("request", enviado)
        page.remove_listener("response", respondido)
        avisar("accion_fin")


async def esperar_cambio(page, texto_antes: str, espera: int = 60000):
    """Espera a que el contenido cambie tras enviar el formulario y se quede quieto 2 s."""
    limite = time.monotonic() + espera / 1000
    while time.monotonic() < limite:
        try:
            await page.wait_for_function(
                """antes => {
                    const ahora = document.body.innerText;
                    if (ahora === antes) return false;
                    if (window.__previoBot !== ahora) { window.__previoBot = ahora; window.__desdeBot = Date.now(); return false; }
                    return Date.now() - window.__desdeBot > 2000;
                }""",
                arg=texto_antes,
                timeout=max(1, (limite - time.monotonic()) * 1000),
                polling=500,
            )
            return
        except PlaywrightTimeoutError:
            return
        except PlaywrightError:
            # El envío recargó la página entera: se sigue esperando en la nueva.
            try:
                await page.wait_for_load_state("load", timeout=max(1, (limite - time.monotonic()) * 1000))
            except PlaywrightError:
                return


async def esperar_resultado_inhabilidades(page, resultado):
    try:
        await page.wait_for_load_state("load", timeout=ESPERA_RESULTADO_INHABILIDADES)
    except PlaywrightTimeoutError:
        log("Inhabilidades (Policía): la navegación sigue cargando; se espera a que desaparezca la barra.")
    try:
        await resultado.wait_for(state="visible", timeout=ESPERA_RESULTADO_INHABILIDADES)
        await page.locator("#pb_loader").wait_for(state="hidden", timeout=ESPERA_RESULTADO_INHABILIDADES)
    except PlaywrightTimeoutError as error:
        raise FueraDeServicio(
            "Inhabilidades (Policía): el resultado no terminó de cargar o sigue mostrando la barra de carga.",
            direccion=URL_INHABILIDADES,
        ) from error
    if not await esperar_estable(page, timeout=ESPERA_RESULTADO_INHABILIDADES):
        raise FueraDeServicio(
            "Inhabilidades (Policía): el resultado siguió cambiando y no se pudo confirmar que terminó de cargar.",
            direccion=URL_INHABILIDADES,
        )


async def consultar_bdme(page, persona: dict) -> Path:
    page = await cargar_pagina(page, URL_BDME, lambda p: p.locator(CAMPO_BDME).wait_for(timeout=60000))
    await page.locator(CAMPO_BDME).fill(persona["numero"])
    antes = await page.evaluate("document.body.innerText")
    await page.locator("#Consulta_BDME_6").click()
    await esperar_cambio(page, antes)
    # Tras consultar, la página vacía el campo: se vuelve a escribir el número para que se vea en el PDF.
    await page.locator(CAMPO_BDME).fill(persona["numero"])
    return await pagina_a_pdf(page, ruta_documento("bdme", persona))


async def consultar_contraloria(page, persona: dict) -> Path:
    tipo = tipo_para(TIPOS_CONTRALORIA, persona, "La Contraloría")
    page = await cargar_pagina(page, URL_CONTRALORIA, lambda p: p.locator("#ddlTipoDocumento").wait_for(timeout=60000))
    await page.locator("#ddlTipoDocumento").select_option(tipo)
    await page.locator("#txtNumeroDocumento").fill(persona["numero"])
    # El captcha del sitio no se toca: el certificado se descarga sin él.
    return await descargar(page, page.locator("#btnBuscar").click, ruta_documento("contraloria", persona))


async def consultar_rnmc(page, persona: dict) -> Path:
    tipo = tipo_para(TIPOS_RNMC, persona, "El RNMC")
    if persona["fecha_expedicion"] is None and persona["tipo"] != "NIT":
        raise ValueError("RNMC: sin fecha de expedición; se omite.")
    for intento in range(2):
        page = await cargar_pagina(page, URL_RNMC, lambda p: p.locator("#ctl00_ContentPlaceHolder3_ddlTipoDoc").wait_for(timeout=60000))
        await page.locator("#ctl00_ContentPlaceHolder3_ddlTipoDoc").select_option(tipo)
        fecha = page.locator("#txtFechaexp")
        expediente = page.locator("#ctl00_ContentPlaceHolder3_txtExpediente")
        texto_fecha = None
        try:
            await fecha.wait_for(state="visible", timeout=15000)
            texto_fecha = f"{persona['fecha_expedicion']:%d/%m/%Y}"
            await fecha.fill(texto_fecha)
            buscar = page.locator("#ctl00_ContentPlaceHolder3_btnConsultar2")
        except PlaywrightTimeoutError:
            # El NIT no pide fecha de expedición.
            buscar = page.locator("#ctl00_ContentPlaceHolder3_btnConsultar")
        await expediente.fill(persona["numero"])
        # El selector de fecha del sitio puede cambiar la casilla después de llenarla: se revisa antes de enviar.
        for _ in range(3):
            if texto_fecha is None or await fecha.input_value() == texto_fecha:
                break
            log(f"RNMC: la casilla de la fecha quedó en {await fecha.input_value()!r}; se escribe otra vez.")
            await fecha.fill(texto_fecha)
            await expediente.focus()
        antes = await page.evaluate("document.body.innerText")
        await buscar.click()
        await esperar_cambio(page, antes)
        error = page.locator("#ctl00_ContentPlaceHolder3_btn_salir_modal")
        if not await error.is_visible():
            break
        mensaje = " ".join((await page.locator(".modal.in .modal-body, .modal.show .modal-body").first.inner_text()).split())
        # A veces el sitio rechaza una fecha que sí es la correcta y a la siguiente consulta la acepta.
        if intento or not re.search(r"fecha.*no es correcta", mensaje, re.I):
            raise ValueError(f"RNMC: {mensaje}")
        log(f"RNMC: el sitio respondió «{mensaje}» con la fecha {texto_fecha}; se consulta otra vez.")
    # El botón Imprimir del sitio solo abre la impresión del navegador: se imprime la página igual, con sus franjas azules.
    return await imprimir_pdf(page, ruta_documento("rnmc", persona), fecha_informe(persona), fondos=True)


async def consultar_antecedentes(page, persona: dict, turnos: Turnos, listo) -> Path:
    tipo = tipo_para(TIPOS_ANTECEDENTES, persona, "Antecedentes (Policía)")
    acepto = page.locator("[id='aceptaOption:0']")
    cedula = page.locator("#cedulaInput")
    recuadro = page.locator("#captchaAntecedentes iframe").first
    lleno_en = 0.0
    primera_carga = True

    async def llenar():
        nonlocal lleno_en, page, acepto, cedula, recuadro, primera_carga
        reutilizar, primera_carga = primera_carga, False
        page = await cargar_pagina(page, URL_ANTECEDENTES, formulario_antecedentes_listo, reutilizar=reutilizar,
                                   vigencia=vigencia_precarga("antecedentes"))
        acepto = page.locator("[id='aceptaOption:0']")
        cedula = page.locator("#cedulaInput")
        recuadro = page.locator("#captchaAntecedentes iframe").first
        await page.locator("#cedulaTipo").select_option(tipo)
        await pausa_humana()
        await escribir_despacio(cedula, persona["numero"])
        lleno_en = instante_carga(page)

    await llenar()
    # Motivo si el último intento falló por el sitio: no respondió o perdió la consulta.
    caida = None
    for intento in range(3):
        if intento:
            await llenar()
        async with turnos.turno("antecedentes", lambda: lleno_en, llenar) as turno:
            # La página pudo cambiar mientras esperaba su turno (o no se pudo recargar a tiempo).
            if not await recuadro.count() or not await cedula.count() \
                    or time.monotonic() - lleno_en > vigencia("antecedentes"):
                await llenar()
            await al_frente(page)
            # Al centro de la ventana, por encima de la consola, que ocupa la parte baja de la pantalla.
            await page.locator("#captchaAntecedentes").evaluate("e => e.scrollIntoView({block: 'center'})")
            log("Antecedentes (Policía): marca 'No soy un robot' en la ventana que quedó al frente.")
            try:
                resuelto = await resolver_con_buster(page, "Antecedentes (Policía)", recuadro) \
                    or await esperar_recaptcha(page, "Antecedentes (Policía)", recuadro)
            except FueraDeServicio as error:
                # Como en Inhabilidades: la casilla atascada suele funcionar al recargar.
                if intento == 2:
                    raise
                log(f"Antecedentes (Policía): {error}; se recarga y se llenan los datos.")
                caida = None
                continue
            if not resuelto:
                log("Antecedentes (Policía): Google dio la casilla por caducada; se carga de nuevo la página.")
                caida = None
                continue
            try:
                # El resultado llega en una página nueva; si el sitio rechaza la casilla, vuelve el formulario con un aviso.
                # Responde en 2-9 s: sin respuesta en 30 s, la consulta se repite.
                await pausa_humana()
                async with page.expect_navigation(timeout=30000):
                    await page.locator("#j_idt17").click()
                    # Mientras el sitio responde ya se puede resolver el captcha siguiente.
                    turno.soltar()
                    listo.set()
            except PlaywrightTimeoutError:
                log(f"Antecedentes (Policía): el sitio no respondió a la consulta, intento {intento + 1}/3.")
                caida = "el sitio no respondió a la consulta"
                await detener_carga(page)
                continue
        if await acepto.count():
            # Si el servidor perdió la sesión, en vez del resultado vuelven los términos de uso con «No acepto» marcado.
            log(f"Antecedentes (Policía): el sitio volvió a los términos de uso sin el resultado, intento {intento + 1}/3.")
            caida = "el sitio volvió a los términos de uso sin dar el resultado"
            continue
        if not await cedula.count():
            break
        caida = None
        aviso = " ".join(" ".join(await page.locator(".ui-messages").all_inner_texts()).split())
        log(f"Antecedentes (Policía): el sitio no mostró el resultado ({aviso or 'volvió el formulario'}), "
            f"intento {intento + 1}/3.")
    else:
        if caida:
            raise FueraDeServicio(f"{caida} (3 intentos)", direccion=URL_ANTECEDENTES)
        raise ValueError("Antecedentes (Policía): no se obtuvo el resultado tras 3 intentos.")
    return await imprimir_pdf(page, ruta_documento("antecedentes", persona), fecha_informe(persona))


async def consultar_registraduria(page, persona: dict, preguntar, turnos: Turnos, listo) -> Path:
    global _bloqueada_hasta
    if persona["tipo"] != "CC":
        raise ValueError("La Registraduría solo certifica cédulas de ciudadanía; no aplica.")
    if persona["fecha_expedicion"] is None:
        raise ValueError("Registraduría: sin fecha de expedición; se omite.")
    if time.monotonic() < _bloqueada_hasta and await bloqueo_en_pagina(page):
        log("Registraduría: su ventana sigue mostrando el bloqueo de hace un rato; no se carga de nuevo para no "
            "alargarlo.")
        raise SitioBloqueado
    try:
        return await _consultar_registraduria(page, persona, preguntar, turnos, listo)
    except SitioBloqueado:
        _bloqueada_hasta = time.monotonic() + PAUSA_BLOQUEO
        raise


async def _consultar_registraduria(page, persona: dict, preguntar, turnos: Turnos, listo) -> Path:
    fecha = persona["fecha_expedicion"]
    cedula = page.locator("#ContentPlaceHolder1_TextBox1")
    codigo = page.locator("#ContentPlaceHolder1_TextBox2")
    imagen = page.locator("#datos_contentplaceholder1_captcha1_CaptchaImage")
    lleno_en = 0.0
    primera_carga = True

    async def llenar():
        nonlocal lleno_en, page, cedula, codigo, imagen, primera_carga
        reutilizar, primera_carga = primera_carga, False
        for vuelta in range(2):
            page = await cargar_pagina(page, URL_REGISTRADURIA, formulario_registraduria_listo,
                                       reutilizar=reutilizar and not vuelta)
            cedula = page.locator("#ContentPlaceHolder1_TextBox1")
            codigo = page.locator("#ContentPlaceHolder1_TextBox2")
            imagen = page.locator("#datos_contentplaceholder1_captcha1_CaptchaImage")
            try:
                await escribir_despacio(cedula, persona["numero"])
                await pausa_humana()
                await page.locator("#ContentPlaceHolder1_DropDownList1").select_option(f"{fecha.day:02d}", timeout=10000)
                await pausa_humana()
                await page.locator("#ContentPlaceHolder1_DropDownList2").select_option(f"{fecha.month:02d}", timeout=10000)
                await pausa_humana()
                await page.locator("#ContentPlaceHolder1_DropDownList3").select_option(str(fecha.year), timeout=10000)
                break
            except PlaywrightError as e:
                # La página cambió a medio llenar (recarga del sitio o pestaña reutilizada): se carga de nuevo.
                if vuelta or await bloqueo_en_pagina(page):
                    raise
                log(f"Registraduría: el formulario cambió mientras se llenaba ({e.message.splitlines()[0]}); "
                    "se carga de nuevo.")
        lleno_en = instante_carga(page)

    async def leer_codigo() -> str:
        """Lee el código sin enviar nada; si no hay lectura confiable, pide otra imagen (hasta 3)."""
        for vuelta in range(INTENTOS_LECTURA_REGISTRADURIA):
            try:
                if vuelta:
                    await page.locator('a[title="Change the code"]').click(timeout=5000)
                    await page.wait_for_timeout(1200)
                await page.wait_for_function("e => e && e.complete && e.naturalWidth > 0",
                                             arg=await imagen.element_handle(timeout=10000), timeout=10000)
                foto = imagen_desde_data_url(await imagen.evaluate(JS_IMAGEN_CAPTCHA))
                (modelo, ocr), texto = await asyncio.to_thread(lambda: (lecturas(foto), leer_captcha(foto)))
            except Exception as e:
                log(f"Registraduría: no se pudo leer el código automáticamente ({e}).")
                return ""
            log(f"Registraduría: lecturas del código: modelo={modelo} ocr={ocr} -> {texto or '(sin lectura confiable)'}")
            if texto:
                return texto
        return ""

    async def automatico() -> bool:
        """Hasta 3 envíos con el código leído por el bot, sin la consola. True si ya hay resultado."""
        nonlocal enviado
        for intento in range(INTENTOS_AUTO_REGISTRADURIA):
            if intento or not await codigo.count() or time.monotonic() - lleno_en > VIGENCIA_CAPTCHA:
                await llenar()
            texto = await leer_codigo()
            if not texto:
                return False
            await escribir_despacio(codigo, texto)
            antes = await page.evaluate("document.body.innerText")
            await pausa_humana()
            # Si el código no es válido, el sitio muestra una alerta de JS: Playwright la cierra sola.
            await page.locator("#ContentPlaceHolder1_Button1").click()
            enviado = True
            listo.set()
            await esperar_cambio(page, antes, espera=30000)
            if await bloqueo_en_pagina(page):
                raise SitioBloqueado
            if await verificacion_en_pagina(page):
                await pasar_verificacion(page, "Registraduría")
                if await codigo.count():
                    continue
            if not await codigo.is_visible():
                log(f"Registraduría: código resuelto automáticamente ({texto}).")
                return True
            log(f"Registraduría: el código leído ({texto}) no fue aceptado, intento automático "
                f"{intento + 1}/{INTENTOS_AUTO_REGISTRADURIA}")
        return False

    await llenar()
    # Lee y envía el código sin turno: con el formulario lleno, lo que esperaba a los captchas ya puede empezar.
    turnos.preparado("registraduria")
    enviado = False
    resuelto = await automatico()
    if not resuelto:
        log("Registraduría: no se resolvió el código automáticamente; se pide escribirlo.")
        # Tras un envío fallido el formulario se carga de nuevo, como entre intentos manuales.
        if enviado:
            await llenar()
    for intento in range(0 if resuelto else 3):
        if intento:
            await llenar()
        async with turnos.turno("registraduria", lambda: lleno_en, llenar):
            # La página pudo cambiar mientras esperaba su turno (o no se pudo recargar a tiempo).
            if not await codigo.count() or time.monotonic() - lleno_en > VIGENCIA_CAPTCHA:
                await llenar()
            await al_frente(page)
            # La imagen del código queda al centro, por encima de la consola.
            await imagen.evaluate("e => e.scrollIntoView({block: 'center'})")
            try:
                png = await imagen.screenshot(timeout=5000)
            except PlaywrightError:
                png = None
            pedido = time.monotonic()
            respuesta = await preguntar("Código de la Registraduría: míralo en su ventana y escríbelo AQUÍ en la consola: ",
                                        titulo="Código de la Registraduría", imagen=png, intento=intento + 1)
            log(f"Registraduría: código escrito en {time.monotonic() - pedido:.0f} s.")
            if not await codigo.count():
                log("Registraduría: la página cambió mientras se escribía el código; se carga de nuevo.")
                continue
            await escribir_despacio(codigo, respuesta)
            antes = await page.evaluate("document.body.innerText")
            await pausa_humana()
            await page.locator("#ContentPlaceHolder1_Button1").click()
        # Mientras el sitio responde ya se puede resolver el captcha siguiente.
        listo.set()
        await esperar_cambio(page, antes, espera=30000)
        if await bloqueo_en_pagina(page):
            raise SitioBloqueado
        if await verificacion_en_pagina(page):
            # Cloudflare también puede pedirla al enviar: pasada, se repite la consulta con un código nuevo.
            await pasar_verificacion(page, "Registraduría")
            if await codigo.count():
                continue
        if not await codigo.is_visible():
            break
        log(f"Registraduría: código incorrecto o sin resultado, intento {intento + 1}/3")
    else:
        if not resuelto:
            raise ValueError("Registraduría: no se obtuvo resultado tras 3 intentos.")
    boton = page.get_by_role("button", name=re.compile(r"descargar|imprimir|generar|certificado", re.I)).or_(
        page.locator("input[type=submit][value*='escargar' i], input[type=submit][value*='mprimir' i]")
    ).or_(
        page.get_by_role("link", name=re.compile(r"descargar|generar|certificado", re.I)).filter(
            has_not_text=re.compile(r"manual|instructivo|ayuda", re.I))
    ).or_(
        page.locator("a[download], a[href*='.pdf' i]").filter(
            has_not_text=re.compile(r"manual|instructivo|ayuda", re.I))
    )
    return await pdf_del_sitio_o_captura(page, boton, ruta_documento("registraduria", persona), exigir_pdf=True)


async def consultar_inhabilidades(page, persona: dict, turnos: Turnos, listo) -> Path:
    tipo = tipo_para(TIPOS_INHABILIDADES, persona, "Inhabilidades (Policía)")
    if persona["fecha_expedicion"] is None:
        raise ValueError("Inhabilidades (Policía): sin fecha de expedición; se omite.")
    terminos = page.locator("#cbCondiciones")
    recaptcha = page.locator("iframe[title='reCAPTCHA']").first
    lleno_en = 0.0
    primera_carga = True

    async def llenar():
        nonlocal lleno_en, page, terminos, recaptcha, resultado, primera_carga
        reutilizar, primera_carga = primera_carga, False
        page = await cargar_pagina(page, URL_INHABILIDADES, formulario_inhabilidades_listo, reutilizar=reutilizar,
                                   vigencia=vigencia_precarga("inhabilidades"))
        terminos = page.locator("#cbCondiciones")
        recaptcha = page.locator("iframe[title='reCAPTCHA']").first
        resultado = page.locator("#res")
        await llenar_inhabilidades(page, persona, tipo)
        lleno_en = instante_carga(page)

    resultado = page.locator("#res")
    avisos = []

    async def aviso(dialogo):
        # Sin esto Playwright cierra solo los mensajes emergentes del sitio y no se sabe qué dijeron.
        avisos.append(dialogo.message)
        await dialogo.dismiss()

    await llenar()
    # Motivo si el último intento falló porque el sitio no respondió a la consulta.
    caida = None
    escuchando = None
    try:
        for intento in range(3):
            if intento:
                await llenar()
            async with turnos.turno("inhabilidades", lambda: lleno_en, llenar) as turno:
                # La página pudo cambiar mientras esperaba su turno (o no se pudo recargar a tiempo).
                if not await recaptcha.count() or not await terminos.count() \
                        or time.monotonic() - lleno_en > vigencia("inhabilidades"):
                    await llenar()
                if escuchando is not page:
                    if escuchando:
                        escuchando.remove_listener("dialog", aviso)
                    page.on("dialog", aviso)
                    escuchando = page
                avisos.clear()
                caida = None
                await al_frente(page)
                await recaptcha.evaluate("e => e.scrollIntoView({block: 'center'})")
                log("Inhabilidades (Policía): marca 'No soy un robot' en la ventana que quedó al frente.")
                try:
                    resuelto = await resolver_con_buster(page, "Inhabilidades (Policía)", recaptcha) \
                        or await esperar_recaptcha(page, "Inhabilidades (Policía)", recaptcha)
                except FueraDeServicio as error:
                    if intento == 2:
                        raise
                    log(f"Inhabilidades (Policía): {error}; se recarga y se rellenan los datos.")
                    continue
                if not resuelto:
                    log("Inhabilidades (Policía): Google dio la casilla por caducada; se carga de nuevo la página.")
                    continue
                try:
                    # El sitio responde con una página nueva que trae el resultado en la sección #res.
                    await pausa_humana()
                    async with page.expect_navigation(timeout=30000):
                        await page.locator("#btnConsultar").click()
                        # Mientras el sitio responde ya se puede resolver el captcha siguiente.
                        turno.soltar()
                        listo.set()
                except PlaywrightTimeoutError as e:
                    # Agotado el clic, el envío sigue sin respuesta; sin cortarlo, la página no contesta.
                    if str(e).startswith("Locator.click"):
                        caida = "el sitio no respondió a la consulta"
                        await detener_carga(page)
            if await resultado.count():
                break
            if caida:
                log(f"Inhabilidades (Policía): el sitio no respondió a la consulta en 30 s, intento {intento + 1}/3.")
                continue
            motivo = avisos + await page.locator(".text-validation").all_inner_texts()
            log(f"Inhabilidades (Policía): el sitio no mostró el resultado "
                f"({'; '.join(motivo) or 'devolvió el formulario vacío'}), intento {intento + 1}/3.")
        else:
            if caida:
                raise FueraDeServicio(f"{caida} (3 intentos)", direccion=URL_INHABILIDADES)
            raise ValueError("Inhabilidades (Policía): el sitio no mostró el resultado tras 3 intentos.")
    finally:
        if escuchando:
            escuchando.remove_listener("dialog", aviso)
    await esperar_resultado_inhabilidades(page, resultado)
    # La página entera con gráficos de fondo, como al guardarla desde Chrome: el pie lleva su línea azul y los logos.
    return await imprimir_pdf(page, ruta_documento("inhabilidades", persona), fecha_informe(persona), fondos=True)


async def formulario_inhabilidades_listo(page):
    await page.locator("#tipo").wait_for(timeout=60000)
    recaptcha = page.locator("iframe[title='reCAPTCHA']").first
    await recaptcha.wait_for(state="visible", timeout=60000)
    await recaptcha.content_frame.locator("#recaptcha-anchor").wait_for(state="visible", timeout=60000)
    await esperar_captcha_listo(page, "Inhabilidades (Policía)")


async def formulario_antecedentes_listo(page):
    acepto = page.locator("[id='aceptaOption:0']")
    cedula = page.locator("#cedulaInput")
    await acepto.or_(cedula).first.wait_for(timeout=60000)
    if await acepto.is_visible():
        await acepto.check()
        await page.locator("#continuarBtn").click(timeout=60000)
    await cedula.wait_for(state="visible", timeout=60000)
    await page.locator("#captchaAntecedentes iframe").first.wait_for(state="visible", timeout=60000)
    await esperar_captcha_listo(page, "Antecedentes (Policía)")


async def formulario_registraduria_listo(page):
    # Con el aviso de bloqueo o la verificación de Cloudflare el formulario no llegará: se reconocen de una vez.
    await page.wait_for_function(
        f"(patron) => document.querySelector('#ContentPlaceHolder1_TextBox1') || ({BLOQUEO_JS})(patron)"
        f" || ({VERIFICACION_JS})()",
        arg=BLOQUEO_RE, timeout=60000, polling=500)
    if await bloqueo_en_pagina(page):
        raise SitioBloqueado
    await revisar_verificacion(page)
    await page.locator("#ContentPlaceHolder1_TextBox1").wait_for(timeout=60000)
    await page.locator("#datos_contentplaceholder1_captcha1_CaptchaImage").wait_for(timeout=60000)


async def llenar_inhabilidades(page, persona: dict, tipo: str):
    terminos = page.locator("#cbCondiciones")
    await page.locator("#tipo").select_option(tipo)
    await pausa_humana()
    await escribir_despacio(page.locator("#nuip"), persona["numero"])
    await pausa_humana()
    await escribir_despacio(page.locator("#fechaExpNuip"), f"{persona['fecha_expedicion']:%d/%m/%Y}")
    # Cierra el calendario que se abre al escribir la fecha.
    await page.keyboard.press("Escape")
    await pausa_humana()
    await escribir_despacio(page.locator("#nombreEmpresa"), EMPRESA_INHABILIDADES)
    await pausa_humana()
    await escribir_despacio(page.locator("#nitEmpresa"), NIT_INHABILIDADES)
    await pausa_humana()
    # La casilla real está oculta: se pulsa el cuadro dibujado, lejos del enlace a los términos.
    await page.locator("label[for=cbCondiciones]").click(position={"x": 4, "y": 8})
    if not await terminos.is_checked():
        await terminos.evaluate("e => e.click()")
    if not await terminos.is_checked():
        raise ValueError("Inhabilidades (Policía): no se pudo marcar la casilla de términos de uso.")