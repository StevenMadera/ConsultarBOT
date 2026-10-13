import functools
import queue
import re
import sys
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

import pymupdf
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from comun import CAPTURAS, log
from consulta_consilium import consultar_consilium
from consulta_fbi import consultar_fbi

URL = "https://www.procuraduria.gov.co/Pages/Consulta-de-Antecedentes.aspx"
URL_RUNT = "https://portalpublico.runt.gov.co/#/consulta-ciudadano-documento/consulta/consulta-ciudadano-documento"
URL_SIMIT = "https://www.fcm.org.co/simit/#/home-public"
URL_LIBRETA = "https://definicion.libretamilitar.mil.co/consulta-estado-situacion-militar"
URL_OFAC = "https://sanctionssearch.ofac.treas.gov/"
URL_GOVUK = "https://www.gov.uk/"
URL_JUSTICIA = "https://www.justice.gov/"
URL_EUMOSTWANTED = "https://eumostwanted.eu/"
ARCHIVO_CONSOLIDADO = Path(__file__).parent / "Plantilla" / "consolidatedLegacyByNAME.html"
ANCHO_MAXIMO = 1600
SALIDAS = Path(__file__).parent / "salidas"
PLANTILLA = Path(__file__).parent / "Plantilla" / "Hombre.pdf"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

TIPOS = {
    "CC": "Cédula de ciudadanía",
    "PEP": "PEP",
    "NIT": "Nit",
    "CE": "Cédula extranjeria",
    "PPT": "PPT",
}

TIPOS_LIBRETA = {
    "CC": "Cédula de ciudadanía (CC)",
    "TI": "Tarjeta de identidad (TI)",
}

TIPOS_RUNT = {
    "CC": "Cédula Ciudadanía",
    "CE": "Cédula de Extranjería",
    "PPT": "Permiso por Protección Temporal",
}

CAPITALES = {
    "amazonas": "leticia", "antioquia": "medellin", "arauca": "arauca",
    "atlantico": "barranquilla", "bolivar": "cartagena", "boyaca": "tunja",
    "caldas": "manizales", "caqueta": "florencia", "casanare": "yopal",
    "cauca": "popayan", "cesar": "valledupar", "choco": "quibdo",
    "cordoba": "monteria", "cundinamarca": "bogota", "guainia": "inirida",
    "guaviare": "san jose del guaviare", "huila": "neiva", "la guajira": "riohacha",
    "guajira": "riohacha", "magdalena": "santa marta", "meta": "villavicencio",
    "narino": "pasto", "norte de santander": "cucuta", "putumayo": "mocoa",
    "quindio": "armenia", "risaralda": "pereira",
    "san andres": "san andres", "santander": "bucaramanga", "sucre": "sincelejo",
    "tolima": "ibague", "valle del cauca": "cali", "valle": "cali",
    "vaupes": "mitu", "vichada": "puerto carreno", "colombia": "bogota",
}


def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def parsear(linea: str) -> dict:
    m = re.match(r"^\s*(CC|CE|PEP|PPT|NIT|TI)\s+([\d.,\s]+?)\s+([^\d].*?)\s*$", linea, re.I)
    if not m:
        raise ValueError("Formato esperado: TIPO NUMERO NOMBRE (ej. CC 1000000000 Juan Perez Gomez)")
    return {"tipo": m[1].upper(), "numero": re.sub(r"\D", "", m[2]), "nombre": m[3]}


def resolver_pregunta(texto: str, numero: str, nombre: str) -> str:
    t = sin_tildes(texto.lower())
    operacion = re.search(r"(\d+)\s*([+\-*/x])\s*(\d+)", t)
    if operacion:
        a, op, b = int(operacion[1]), operacion[2], int(operacion[3])
        return str({"+": a + b, "-": a - b, "*": a * b, "x": a * b, "/": a // b if b else 0}[op])
    cantidad = re.search(r"(\d+|un|uno|una|dos|tres|cuatro|cinco|seis)\s+(?:primer|ultim)[oa]s\s+(digitos|letras)", t)
    if cantidad is None:
        cantidad = re.search(r"(?:primer|ultim)[oa]s\s+(?:(\d+|un|uno|una|dos|tres|cuatro|cinco|seis)\s+)?(digitos|letras)", t)
    if cantidad:
        palabras = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}
        n = int(cantidad[1]) if cantidad[1] and cantidad[1].isdigit() else palabras.get(cantidad[1], 2)
        ultimos = re.search(r"ultim[oa]s", t) is not None
        base = numero if cantidad[2] == "digitos" else sin_tildes(nombre.split()[0].lower())
        return base[-n:] if ultimos else base[:n]
    capital = re.search(r"capital\s+d(?:e|el|e la)\s+(.+?)\s*(?:\(|\?|$)", t)
    if capital:
        depto = capital[1].strip()
        if depto in CAPITALES:
            return CAPITALES[depto]
        for nombre_depto, cap in CAPITALES.items():
            if nombre_depto in depto:
                return cap
    raise ValueError(f"Pregunta desconocida: {texto!r}")


# Página de la plantilla (0-based) y hueco: "arriba" = entre los dos cuadros de título, "abajo" = desde el segundo hasta el pie,
# "unico" = desde el único cuadro de la página hasta el pie.
UBICACION_PDF = {
    "procuraduria": (1, "arriba"),
    "runt": (1, "abajo"),
    "simit": (2, "arriba"),
    "libreta": (2, "abajo"),
    "ofac": (3, "arriba"),
    "govuk": (4, "arriba"),
    "fbi": (4, "abajo"),
    "justicia": (5, "arriba"),
    "eumostwanted": (6, "abajo"),
    "consilium": (7, "unico"),
    "consolidado": (3, "abajo"),
}


def zona_pdf(pagina, hueco: str) -> pymupdf.Rect:
    cuadros = sorted(
        (d["rect"] for d in pagina.get_drawings() if d["rect"].width > 300 and d["rect"].height < 40),
        key=lambda r: r.y0,
    )
    if hueco == "unico":
        c = cuadros[0]
        pie = min((b[1] for b in pagina.get_text("blocks") if b[1] > c.y1), default=pagina.rect.height - 30)
        return pymupdf.Rect(c.x0, c.y1 + 2, c.x1, pie - 4)
    c1, c2 = cuadros[:2]
    if hueco == "arriba":
        return pymupdf.Rect(c1.x0, c1.y1 + 2, c1.x1, c2.y0 - 2)
    pie = min(b[1] for b in pagina.get_text("blocks") if b[1] > c2.y1)
    return pymupdf.Rect(c2.x0, c2.y1 + 2, c2.x1, pie - 4)


@functools.cache
def proporcion_zona(clave: str) -> float:
    """Alto/ancho del recuadro de la plantilla donde va la captura."""
    indice, hueco = UBICACION_PDF[clave]
    with pymupdf.open(PLANTILLA) as doc:
        zona = zona_pdf(doc[indice], hueco)
    return zona.height / zona.width


def pegar_captura(pagina, captura: Path, hueco: str):
    zona = zona_pdf(pagina, hueco)
    if hueco == "unico":
        # La zona llega hasta el pie: la imagen va pegada al cuadro de título, no centrada en el vacío.
        pix = pymupdf.Pixmap(str(captura))
        zona.y1 = min(zona.y1, zona.y0 + zona.width * pix.height / pix.width)
    pagina.insert_image(zona, filename=str(captura), keep_proportion=True)


def insertar_en_pdf(capturas: dict[str, Path], numero: str, nombre: str) -> Path:
    doc = pymupdf.open(PLANTILLA)
    for clave, captura in capturas.items():
        indice, hueco = UBICACION_PDF[clave]
        pegar_captura(doc[indice], captura, hueco)
    SALIDAS.mkdir(exist_ok=True)
    base = re.sub(r'[<>:"/\\|?*]', "", f"{numero} - {nombre}").strip()
    ruta = SALIDAS / f"{base}.pdf"
    contador = 2
    while ruta.exists():
        ruta = SALIDAS / f"{base} ({contador}).pdf"
        contador += 1
    doc.save(ruta)
    doc.close()
    return ruta


def primer_apellido(nombre: str) -> str:
    partes = nombre.split()
    return partes[2] if len(partes) >= 4 else partes[1] if len(partes) >= 2 else partes[0]


def esperar_sin_carga(page):
    """Espera a que no haya spinners ni texto 'Cargando' y a que el DOM deje de cambiar."""
    try:
        page.wait_for_function(
            """() => {
                const visible = e => e.offsetParent !== null || getComputedStyle(e).position === 'fixed';
                const spinners = [...document.querySelectorAll(
                    'mat-spinner, mat-progress-spinner, mat-progress-bar, ngx-spinner, .spinner, .loading, .loader, .cdk-overlay-backdrop-showing'
                )].filter(visible);
                return spinners.length === 0 && !/cargando/i.test(document.body.innerText);
            }""",
            timeout=15000,
            polling=300,
        )
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass
    # Deja terminar la animación del panel desplegable.
    page.wait_for_timeout(700)


def consultar_runt(page, persona: dict, pedir_captcha) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    apellido = re.sub(r"[^\w\sñÑáéíóúÁÉÍÓÚüÜ]", "", primer_apellido(persona["nombre"]))
    campo_captcha = page.locator("input[formcontrolname=captcha]")
    for intento in range(3):
        page.goto(URL_RUNT, wait_until="domcontentloaded")
        page.locator("mat-select[formcontrolname=tipoDocumento]").click()
        page.get_by_role("option", name=TIPOS_RUNT[persona["tipo"].upper()], exact=True).click()
        page.locator("input[formcontrolname=documento]").fill(numero)
        page.locator("input[formcontrolname=primerApellido]").fill(apellido)
        page.locator("img.img-fluid").first.scroll_into_view_if_needed()
        page.bring_to_front()

        campo_captcha.fill(pedir_captcha())
        page.get_by_role("button", name="Consultar Información").click()
        try:
            page.wait_for_function(
                """() => {
                    const c = document.querySelector('input[formcontrolname=captcha]');
                    return !c || c.offsetParent === null || document.body.innerText.includes('captcha no es valido');
                }""",
                timeout=20000,
            )
        except PlaywrightTimeoutError:
            pass
        if not campo_captcha.is_visible():
            break
        log(f"RUNT: captcha incorrecto o sin resultado, intento {intento + 1}/3")
    else:
        raise ValueError("RUNT: no se obtuvo resultado tras 3 intentos")
    page.wait_for_load_state("networkidle")
    esperar_sin_carga(page)

    # Desplegar "Licencia(s) de conducción" si está colapsada.
    licencias = page.locator("mat-expansion-panel-header", has_text=re.compile(r"licencia.*conducci", re.I)).first
    try:
        if licencias.get_attribute("aria-expanded", timeout=8000) != "true":
            licencias.click()
            page.wait_for_load_state("networkidle")
    except PlaywrightTimeoutError:
        log("RUNT: no se encontró el apartado de licencias de conducción")

    esperar_sin_carga(page)
    page.evaluate("document.querySelectorAll('*').forEach(e => { if (e.scrollTop) e.scrollTop = 0; }); window.scrollTo(0, 0)")
    page.wait_for_timeout(300)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"runt_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    page.screenshot(path=str(ruta), full_page=True)
    return ruta


def consultar_simit(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    page.goto(URL_SIMIT, wait_until="domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    try:
        page.locator("button.close:visible").first.click(timeout=15000)
    except PlaywrightTimeoutError:
        pass
    page.locator("#txtBusqueda").fill(numero)
    page.locator("#consultar").click()
    page.wait_for_url("**/estado-cuenta", timeout=20000)
    page.get_by_text("Comparendos:").first.wait_for(timeout=20000)
    page.wait_for_timeout(500)

    # Contrae la pestaña "Herramientas de accesibilidad" (el sitio recuerda su estado entre cargas).
    try:
        page.locator("#x-insuit-tab-title").wait_for(state="attached", timeout=15000)
        # El widget vive en un shadow DOM: solo el locator lo alcanza.
        desplegada = page.locator("#x-insuit-tab-title").evaluate("e => e.getBoundingClientRect().right > 40")
    except PlaywrightTimeoutError:
        desplegada = False
        log("SIMIT: no apareció la pestaña de accesibilidad")
    if desplegada:
        page.locator("#x-insuit-tab-arrow").click()
        page.wait_for_timeout(1200)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"simit_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    page.screenshot(path=str(ruta), full_page=True)
    return ruta


def ajustar_ventana_a_recuadro(page, clave: str) -> bool:
    """Ensancha la ventana hasta que la página completa tenga la proporción del recuadro del PDF.

    Devuelve False si la página es demasiado larga: entonces la ventana queda con esa proporción y solo se ve el inicio.
    """
    prop = proporcion_zona(clave)
    page.set_viewport_size({"width": page.viewport_size["width"], "height": 600})
    for _ in range(3):
        alto = page.evaluate("document.documentElement.scrollHeight")
        ancho = max(900, round(alto / prop))
        if ancho > ANCHO_MAXIMO:
            page.set_viewport_size({"width": ANCHO_MAXIMO, "height": round(ANCHO_MAXIMO * prop)})
            page.wait_for_timeout(400)
            return False
        page.set_viewport_size({"width": ancho, "height": 600})
        page.wait_for_timeout(400)
    # Un visor de PDF solo pinta lo visible: la ventana debe abarcar toda la página.
    page.set_viewport_size({"width": ancho, "height": alto})
    return True


def captura_ajustada(page, ruta: Path, clave: str, espera: int = 1000):
    completa = ajustar_ventana_a_recuadro(page, clave)
    page.wait_for_timeout(espera)
    page.screenshot(path=str(ruta), full_page=completa)


def consultar_govuk(page, persona: dict) -> Path:
    page.goto(URL_GOVUK, wait_until="domcontentloaded")
    try:
        page.get_by_role("button", name="Reject additional cookies").click(timeout=5000)
        page.get_by_role("button", name="Hide cookie message").click(timeout=3000)
    except PlaywrightTimeoutError:
        pass
    campo = page.locator("input[name=keywords]:visible").first
    campo.fill(persona["nombre"])
    campo.press("Enter")
    page.wait_for_url("**/search/all**", timeout=20000)
    page.wait_for_load_state("load")

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"govuk_{re.sub(r'\D', '', persona['numero'])}_{datetime.now():%Y%m%d_%H%M%S}.png"
    captura_ajustada(page, ruta, "govuk")
    return ruta


def consultar_justicia(page, persona: dict) -> Path:
    page.goto(URL_JUSTICIA, wait_until="domcontentloaded")
    campo = page.locator("#search-field-en-small-desktop")
    campo.fill(persona["nombre"])
    campo.press("Enter")
    page.wait_for_url("**/search**", timeout=20000)
    page.get_by_text(re.compile(r"\d+ results?|no results", re.I)).first.wait_for(timeout=20000)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"justicia_{re.sub(r'\D', '', persona['numero'])}_{datetime.now():%Y%m%d_%H%M%S}.png"
    captura_ajustada(page, ruta, "justicia")
    return ruta


def consultar_eumostwanted(page, persona: dict) -> Path:
    page.goto(URL_EUMOSTWANTED, wait_until="domcontentloaded")
    try:
        page.get_by_text("I don't agree", exact=True).first.click(timeout=5000)
    except PlaywrightTimeoutError:
        pass
    campo = page.locator("#edit-keys")
    campo.fill(persona["nombre"])
    campo.press("Enter")
    page.wait_for_url("**/search**", timeout=20000)
    page.wait_for_load_state("load")

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"eumostwanted_{re.sub(r'\D', '', persona['numero'])}_{datetime.now():%Y%m%d_%H%M%S}.png"
    captura_ajustada(page, ruta, "eumostwanted")
    return ruta


BUSCAR_EN_DOCUMENTO_JS = """q => {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const rangos = []; let n;
    while ((n = w.nextNode())) {
        let i = n.nodeValue.indexOf(q);
        while (i !== -1) {
            const r = new Range(); r.setStart(n, i); r.setEnd(n, i + q.length); rangos.push(r);
            i = n.nodeValue.indexOf(q, i + q.length);
        }
    }
    const estilo = document.createElement('style');
    estilo.textContent = '::highlight(buscar){background:#ffcf00;color:#000}::highlight(actual){background:#ff9632;color:#000}';
    document.head.append(estilo);
    if (rangos.length) {
        CSS.highlights.set('buscar', new Highlight(...rangos));
        CSS.highlights.set('actual', new Highlight(rangos[0]));
        rangos[0].startContainer.parentElement.scrollIntoView({block: 'center'});
    }
    const barra = document.createElement('div');
    barra.style.cssText = 'position:fixed;top:8px;right:24px;z-index:99999;display:flex;align-items:center;gap:10px;' +
        'padding:8px 14px;background:#fff;border:1px solid #bbb;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.35);' +
        'font:15px Segoe UI,Arial,sans-serif;color:#222';
    const caja = document.createElement('span');
    caja.style.cssText = 'border:1px solid #888;border-radius:4px;padding:3px 8px;min-width:140px';
    caja.textContent = q;
    const cuenta = document.createElement('span');
    cuenta.style.color = rangos.length ? '#222' : '#c00';
    cuenta.textContent = rangos.length ? '1/' + rangos.length : '0/0';
    barra.append('Buscar:', caja, cuenta);
    document.body.append(barra);
    return rangos.length;
}"""


def consultar_documento(page, persona: dict) -> Path:
    """Equivale a abrir el documento y buscar el número con Ctrl+F: resalta las coincidencias y muestra la barra de búsqueda."""
    numero = re.sub(r"\D", "", persona["numero"])
    page.goto(ARCHIVO_CONSOLIDADO.as_uri(), wait_until="load")
    page.set_viewport_size({"width": 1600, "height": round(1600 * proporcion_zona("consolidado"))})
    coincidencias = page.evaluate(BUSCAR_EN_DOCUMENTO_JS, numero)
    log(f"Documento consolidado: {coincidencias} coincidencias para {numero}")
    page.wait_for_timeout(500)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"consolidado_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    page.screenshot(path=str(ruta))
    return ruta


def consultar_ofac(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    page.goto(URL_OFAC, wait_until="domcontentloaded")
    page.locator("#ctl00_MainContent_ddlType").select_option(label="Individual")
    page.locator("#ctl00_MainContent_ddlCountry").select_option(label="Colombia")
    page.locator("#ctl00_MainContent_txtID").fill(numero)
    with page.expect_navigation(wait_until="load", timeout=30000):
        page.locator("#ctl00_MainContent_btnSearch").click()
    page.wait_for_timeout(500)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"ofac_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    captura_ajustada(page, ruta, "ofac")
    return ruta


def consultar_libreta(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    page.goto(URL_LIBRETA, wait_until="domcontentloaded")
    page.locator("#tipoDocumento").select_option(label=TIPOS_LIBRETA[persona["tipo"].upper()])
    page.locator("#numeroDocumento").fill(numero)
    page.get_by_role("button", name="Consultar", exact=True).click()
    page.get_by_text("Aqui vera el estado consultado").wait_for(state="hidden", timeout=30000)
    # El certificado es un PDF embebido; necesita unos segundos para renderizar.
    page.locator("embed").wait_for(state="attached", timeout=15000)
    page.wait_for_timeout(2500)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"libreta_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    captura_ajustada(page, ruta, "libreta", espera=3000)
    return ruta


def consultar(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    page.goto(URL, wait_until="domcontentloaded")
    form = page.frame_locator("iframe[src*='webcert']")
    form.locator("#ddlTipoID").select_option(label=TIPOS[persona["tipo"].upper()])
    form.locator("#txtNumID").fill(numero)

    # La pregunta puede cambiar tras el postback del tipo de ID.
    page.wait_for_load_state("networkidle")
    pregunta = form.locator("#lblPregunta").inner_text().strip()
    respuesta = resolver_pregunta(pregunta, numero, persona["nombre"])
    log(f"Pregunta: {pregunta!r} -> {respuesta!r}")
    form.locator("#txtRespuestaPregunta").fill(respuesta)

    form.locator("#btnConsultar").click()
    try:
        form.get_by_text("Datos del ciudadano").wait_for(timeout=15000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_load_state("networkidle")

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    page.screenshot(path=str(ruta), full_page=True)
    return ruta


def consola_siempre_visible() -> int | None:
    """Fija la consola encima de todo, en una franja inferior de la pantalla; devuelve su handle."""
    if sys.platform != "win32":
        return None
    import ctypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    # Windows Terminal y la terminal de VS Code no exponen una ventana propia.
    if not hwnd or not ctypes.windll.user32.IsWindowVisible(hwnd):
        return None
    ancho = ctypes.windll.user32.GetSystemMetrics(0)
    alto = ctypes.windll.user32.GetSystemMetrics(1)
    alto_ventana = 240
    HWND_TOPMOST, SWP_NOACTIVATE = -1, 0x0010
    ctypes.windll.user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, alto - alto_ventana - 48, min(ancho, 900), alto_ventana, SWP_NOACTIVATE
    )
    return hwnd


def enfocar_consola(hwnd: int | None):
    if hwnd:
        import ctypes

        ctypes.windll.user32.SetForegroundWindow(hwnd)


class Trabajador(threading.Thread):
    """Navegador propio en un hilo; se abre al recibir la primera tarea."""

    def __init__(self, funcion, headless: bool, canal: str | None = None):
        super().__init__(daemon=True)
        self.funcion = funcion
        self.headless = headless
        self.canal = canal
        self.entrada = queue.Queue()
        self.salida = queue.Queue()

    def run(self):
        with sync_playwright() as p:
            browser = page = None
            while (tarea := self.entrada.get()) is not None:
                try:
                    if page is None:
                        browser = p.chromium.launch(headless=self.headless, channel=self.canal, args=["--start-maximized"])
                        # La Procuraduría bloquea el user agent "HeadlessChrome".
                        # Con ventana visible se usa el tamaño maximizado; en headless, Full HD.
                        contexto = {"viewport": {"width": 1920, "height": 1080}} if self.headless else {"no_viewport": True}
                        page = browser.new_page(ignore_https_errors=True, user_agent=USER_AGENT, **contexto)
                    self.salida.put((True, self.funcion(page, *tarea)))
                except Exception as e:
                    self.salida.put((False, e))
            if browser is not None:
                browser.close()


def main():
    consola = consola_siempre_visible()
    solicitudes_captcha = queue.Queue()

    def pedir_captcha() -> str:
        respuesta = queue.Queue()
        solicitudes_captcha.put(respuesta)
        return respuesta.get()

    trabajadores = {
        "procuraduria": Trabajador(consultar, headless=True),
        "runt": Trabajador(consultar_runt, headless=False),
        "simit": Trabajador(consultar_simit, headless=True),
        "ofac": Trabajador(consultar_ofac, headless=True),
        "govuk": Trabajador(consultar_govuk, headless=True),
        "justicia": Trabajador(consultar_justicia, headless=True),
        "eumostwanted": Trabajador(consultar_eumostwanted, headless=True),
        "consolidado": Trabajador(consultar_documento, headless=True),
        # El headless nuevo (canal "chromium") incluye el visor de PDF que usa la libreta.
        "libreta": Trabajador(consultar_libreta, headless=True, canal="chromium"),
    }
    for t in trabajadores.values():
        t.start()

    try:
        while True:
            linea = input("\nPega los datos (TIPO NUMERO NOMBRE) o Enter para salir: ")
            if not linea.strip():
                break
            try:
                persona = parsear(linea)
            except ValueError as e:
                log(f"ERROR: {e}")
                continue
            log(f"Datos: {persona} | apellido RUNT: {primer_apellido(persona['nombre'])}")
            trabajadores["procuraduria"].entrada.put((persona,))
            trabajadores["simit"].entrada.put((persona,))
            trabajadores["libreta"].entrada.put((persona,))
            trabajadores["ofac"].entrada.put((persona,))
            trabajadores["govuk"].entrada.put((persona,))
            trabajadores["justicia"].entrada.put((persona,))
            trabajadores["eumostwanted"].entrada.put((persona,))
            trabajadores["consolidado"].entrada.put((persona,))
            trabajadores["runt"].entrada.put((persona, pedir_captcha))

            resultados = {}
            while len(resultados) < len(trabajadores):
                try:
                    respuesta = solicitudes_captcha.get(timeout=0.2)
                    enfocar_consola(consola)
                    respuesta.put(input("Captcha del RUNT (5 caracteres, mira la ventana del RUNT): ").strip())
                except queue.Empty:
                    pass
                for nombre, t in trabajadores.items():
                    if nombre not in resultados:
                        try:
                            resultados[nombre] = t.salida.get_nowait()
                        except queue.Empty:
                            pass

            capturas = {}
            for nombre, (ok, valor) in resultados.items():
                if ok:
                    capturas[nombre] = valor
                    log(f"Captura {nombre}: {valor}")
                else:
                    log(f"ERROR {nombre}: {valor}")
            # Van aparte: la verificación de Cloudflare la pasa una persona en su navegador.
            for clave, consulta_manual in ("fbi", consultar_fbi), ("consilium", consultar_consilium):
                try:
                    capturas[clave] = consulta_manual(persona)
                    log(f"Captura {clave}: {capturas[clave]}")
                except Exception as e:
                    log(f"ERROR {clave}: {e}")
            if capturas:
                log(f"PDF: {insertar_en_pdf(capturas, persona['numero'], persona['nombre'])}")
    finally:
        for t in trabajadores.values():
            t.entrada.put(None)
        for t in trabajadores.values():
            t.join(timeout=10)


if __name__ == "__main__":
    main()
