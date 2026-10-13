import asyncio
import functools
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import quote_plus

import pymupdf
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from captura_navegador import PANTALLA
from comun import CAPTURAS, log
from consulta_consilium import consultar_consilium
from consulta_fbi import consultar_fbi
from consulta_justicia import consultar_justicia
from consulta_terroristas import consultar_terroristas

URL = "https://www.procuraduria.gov.co/Pages/Consulta-de-Antecedentes.aspx"
URL_RUNT = "https://portalpublico.runt.gov.co/#/consulta-ciudadano-documento/consulta/consulta-ciudadano-documento"
URL_SIMIT = "https://www.fcm.org.co/simit/#/home-public"
URL_LIBRETA = "https://definicion.libretamilitar.mil.co/consulta-estado-situacion-militar"
URL_OFAC = "https://sanctionssearch.ofac.treas.gov/"
URL_GOVUK = "https://www.gov.uk/"
URL_EUMOSTWANTED = "https://eumostwanted.eu/search/node?keys={nombre}"
URL_EURLEX = "https://eur-lex.europa.eu/legal-content/ES/TXT/HTML/?uri=OJ:L:2022:025:FULL&from=ES"
ARCHIVO_CONSOLIDADO = Path(__file__).parent / "Plantilla" / "consolidatedLegacyByNAME.html"
ANCHO_MAXIMO = 1600
# Pantalla virtual de las páginas automáticas: más pequeña, menos trabajo de dibujo; el recuadro del PDF no necesita más.
VIEWPORT = {"width": 1366, "height": 768}
# La Procuraduría muestra el resultado dentro de un marco (iframe): más estrecha, el texto ocupa más alto
# y podría quedar cortado dentro del marco. Se mantiene el ancho con el que se verificó.
VIEWPORT_PROCURADURIA = {"width": 1920, "height": 1080}
SALIDAS = Path(__file__).parent / "salidas"
PLANTILLA = Path(__file__).parent / "Plantilla" / "Hombre.pdf"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
# Analítica, publicidad y chats de terceros: no aportan a las capturas y gastan red y CPU.
# No incluye google.com ni gstatic.com (captchas) ni awswaf.com (verificación de EUR-Lex).
HOSTS_BLOQUEADOS = (
    "google-analytics.com", "analytics.google.com", "googletagmanager.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "connect.facebook.net", "hotjar.com", "hotjar.io",
    "clarity.ms", "tawk.to", "zdassets.com", "livechatinc.com", "intercom.io", "intercomcdn.com",
    "crisp.chat", "jivosite.com", "hs-scripts.com", "botmaker.com", "cliengo.com", "freshchat.com",
)
REGLAS_BLOQUEO = ", ".join(f"MAP {host} ~NOTFOUND, MAP *.{host} ~NOTFOUND" for host in HOSTS_BLOQUEADOS)

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
    if re.search(r"\bprimer nombre\b", t):
        primer_nombre = sin_tildes(nombre.split()[0].lower())
        if "letras" in t and re.search(r"\b(?:cantidad|numero|cuantas?)\b", t):
            return str(len(primer_nombre))
        return primer_nombre
    capital = re.search(r"capital\s+d(?:e|el|e la)\s+(.+?)\s*(?:\(|\?|$)", t)
    if capital:
        depto = re.sub(r"[^a-z\s]", " ", capital[1]).strip()
        depto = re.sub(r"\s+", " ", depto)
        if depto in CAPITALES:
            return CAPITALES[depto]
        coincidencia = get_close_matches(depto, CAPITALES, n=1, cutoff=0.78)
        if coincidencia:
            return CAPITALES[coincidencia[0]]
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
    "terroristas": (5, "abajo"),
    "consolium": (6, "arriba"),
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
    # Sin compresión, las capturas PNG dejan el PDF en ~50 MB; deflate es sin pérdida.
    doc.save(ruta, garbage=3, deflate=True)
    doc.close()
    return ruta


def primer_apellido(nombre: str) -> str:
    partes = nombre.split()
    return partes[2] if len(partes) >= 4 else partes[1] if len(partes) >= 2 else partes[0]


async def esperar_sin_carga(page):
    """Espera a que no haya spinners ni texto 'Cargando' y a que el DOM deje de cambiar."""
    try:
        await page.wait_for_function(
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
        await page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass
    # Deja terminar la animación del panel desplegable.
    await page.wait_for_timeout(700)


async def consultar_runt(page, persona: dict, preguntar, captcha_listo: asyncio.Event) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    apellido = re.sub(r"[^\w\sñÑáéíóúÁÉÍÓÚüÜ]", "", primer_apellido(persona["nombre"]))
    campo_captcha = page.locator("input[formcontrolname=captcha]")
    for intento in range(3):
        await page.goto(URL_RUNT, wait_until="domcontentloaded")
        await page.locator("mat-select[formcontrolname=tipoDocumento]").click()
        await page.get_by_role("option", name=TIPOS_RUNT[persona["tipo"].upper()], exact=True).click()
        await page.locator("input[formcontrolname=documento]").fill(numero)
        await page.locator("input[formcontrolname=primerApellido]").fill(apellido)
        await page.locator("img.img-fluid").first.scroll_into_view_if_needed()
        await page.bring_to_front()

        await campo_captcha.fill(await preguntar("Captcha del RUNT (5 caracteres, mira la ventana del RUNT): "))
        await page.get_by_role("button", name="Consultar Información").click()
        try:
            await page.wait_for_function(
                """() => {
                    const c = document.querySelector('input[formcontrolname=captcha]');
                    return !c || c.offsetParent === null || document.body.innerText.includes('captcha no es valido');
                }""",
                timeout=20000,
            )
        except PlaywrightTimeoutError:
            pass
        if not await campo_captcha.is_visible():
            break
        log(f"RUNT: captcha incorrecto o sin resultado, intento {intento + 1}/3")
    else:
        raise ValueError("RUNT: no se obtuvo resultado tras 3 intentos")
    # Ya no hace falta mirar la ventana del RUNT: pueden empezar las capturas de Edge.
    captcha_listo.set()
    await page.wait_for_load_state("networkidle")
    await esperar_sin_carga(page)

    # Desplegar "Licencia(s) de conducción" si está colapsada.
    licencias = page.locator("mat-expansion-panel-header", has_text=re.compile(r"licencia.*conducci", re.I)).first
    try:
        if await licencias.get_attribute("aria-expanded", timeout=8000) != "true":
            await licencias.click()
            await page.wait_for_load_state("networkidle")
    except PlaywrightTimeoutError:
        log("RUNT: no se encontró el apartado de licencias de conducción")

    await esperar_sin_carga(page)
    await page.evaluate("document.querySelectorAll('*').forEach(e => { if (e.scrollTop) e.scrollTop = 0; }); window.scrollTo(0, 0)")
    await page.wait_for_timeout(300)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"runt_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await page.screenshot(path=str(ruta), full_page=True)
    return ruta


# null mientras SIMIT valida o carga; su validación de seguridad puede tardar más de 30 s.
SIMIT_ESTADO_JS = """() => {
    const visible = sel => [...document.querySelectorAll(sel)].some(
        e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    );
    if (visible('#whcModal') || visible('#loader.active')) return null;
    if (visible('#modal-multiples-personas')) return 'varios';
    if (/Comparendos:/.test(document.body.innerText)) return 'resultado';
    if (visible('.modal.show, .swal2-popup, [role=alertdialog]')) return 'mensaje';
    return null;
}"""


async def esperar_simit(page) -> str:
    try:
        estado = await page.wait_for_function(SIMIT_ESTADO_JS, timeout=120000, polling=500)
    except PlaywrightTimeoutError:
        raise ValueError("SIMIT no mostró el resultado en 2 minutos; no se generó captura.") from None
    return await estado.json_value()


async def seleccionar_persona_simit(page, persona: dict, pedir_seleccion):
    dialogo = page.locator("#modal-multiples-personas")
    radios = dialogo.get_by_role("radio")
    etiquetas = []
    puntuaciones = []
    tokens_nombre = [sin_tildes(token).upper() for token in persona["nombre"].split()]
    for indice in range(await radios.count()):
        radio = radios.nth(indice)
        etiqueta = (await radio.evaluate(
            "e => e.labels?.[0]?.innerText || e.getAttribute('aria-label') || e.parentElement?.innerText || ''"
        )).strip()
        etiquetas.append(etiqueta)
        prefijos = re.findall(r"([A-ZÁÉÍÓÚÜÑ]{2,})\*+", etiqueta.upper())
        coincidencias = sum(any(token.startswith(prefijo) for token in tokens_nombre) for prefijo in prefijos)
        puntuaciones.append(coincidencias / len(prefijos) if prefijos else 0)

    mejor = max(puntuaciones, default=0)
    candidatos = [i for i, puntuacion in enumerate(puntuaciones) if puntuacion == mejor and puntuacion >= 0.75]
    if len(candidatos) == 1:
        seleccion = candidatos[0]
    else:
        opciones = "\n".join(f"{i + 1}. {etiqueta}" for i, etiqueta in enumerate(etiquetas))
        if pedir_seleccion is None:
            raise ValueError(f"SIMIT requiere elegir una coincidencia:\n{opciones}")
        valor = await pedir_seleccion(
            "SIMIT encontró varias personas. Elige la que corresponde al nombre consultado:\n"
            f"{opciones}\nNúmero de opción: "
        )
        try:
            seleccion = int(valor) - 1
        except ValueError as error:
            raise ValueError("La opción de persona de SIMIT debe ser un número.") from error
        if not 0 <= seleccion < len(etiquetas):
            raise ValueError("La opción de persona de SIMIT está fuera del rango mostrado.")

    radio = radios.nth(seleccion)
    radio_id = await radio.get_attribute("id")
    if radio_id:
        await dialogo.locator(f"label[for='{radio_id}']").click()
    else:
        await radio.click(force=True)
    await dialogo.get_by_role("button", name=re.compile(r"continuar", re.I)).click()
    await dialogo.wait_for(state="hidden", timeout=120000)


async def consultar_simit(page, persona: dict, pedir_seleccion=None) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    # Una sola carga completa: si la página ya está en SIMIT, goto solo cambia el hash de la SPA y hay que recargar.
    ya_abierto = page.url.startswith(URL_SIMIT.split("#")[0])
    await page.goto(URL_SIMIT, wait_until="domcontentloaded")
    if ya_abierto:
        await page.reload(wait_until="domcontentloaded")
    validacion = page.locator("#whcModal")
    try:
        await validacion.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError:
        pass
    try:
        await validacion.wait_for(state="hidden", timeout=120000)
    except PlaywrightTimeoutError:
        raise ValueError("SIMIT no terminó su validación de seguridad en 2 minutos; no se generó captura.") from None
    try:
        await page.locator("button.close:visible").first.click(timeout=5000)
    except PlaywrightTimeoutError:
        pass
    await page.locator("#txtBusqueda").fill(numero)
    await page.locator("#consultar").click(timeout=120000)
    await page.wait_for_url("**/estado-cuenta", timeout=30000)
    estado = await esperar_simit(page)
    if estado == "varios":
        await seleccionar_persona_simit(page, persona, pedir_seleccion)
        estado = await esperar_simit(page)
    if estado != "resultado":
        log("SIMIT: no apareció el resumen de comparendos; se captura el mensaje mostrado por el sitio.")
    await page.wait_for_timeout(500)

    # Contrae la pestaña "Herramientas de accesibilidad" (el sitio recuerda su estado entre cargas).
    try:
        await page.locator("#x-insuit-tab-title").wait_for(state="attached", timeout=15000)
        # El widget vive en un shadow DOM: solo el locator lo alcanza.
        desplegada = await page.locator("#x-insuit-tab-title").evaluate("e => e.getBoundingClientRect().right > 40")
    except PlaywrightTimeoutError:
        desplegada = False
        log("SIMIT: no apareció la pestaña de accesibilidad")
    if desplegada:
        await page.locator("#x-insuit-tab-arrow").click()
        await page.wait_for_timeout(1200)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"simit_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await captura_sin_pie(page, ruta)
    return ruta


# Borde superior del pie del sitio (el último, si no está fijo en pantalla y empieza en la mitad inferior).
ALTO_HASTA_PIE_JS = """() => {
    const pies = [...document.querySelectorAll('footer, [role=contentinfo]')].filter(e =>
        !['fixed', 'sticky'].includes(getComputedStyle(e).position) && e.getBoundingClientRect().height > 0
    );
    if (!pies.length) return null;
    const arriba = Math.round(pies.at(-1).getBoundingClientRect().top + window.scrollY);
    return arriba > document.documentElement.scrollHeight / 2 ? arriba : null;
}"""


async def captura_sin_pie(page, ruta: Path):
    """Página completa sin el pie del sitio, que no aporta al PDF; si no se reconoce el pie, la página completa."""
    alto = await page.evaluate(ALTO_HASTA_PIE_JS)
    recorte = {"x": 0, "y": 0, "width": page.viewport_size["width"], "height": alto} if alto else None
    await page.screenshot(path=str(ruta), full_page=True, clip=recorte)


async def ajustar_ventana_a_recuadro(page, clave: str) -> bool:
    """Ensancha la ventana hasta que la página completa tenga la proporción del recuadro del PDF.

    Devuelve False si la página es demasiado larga: entonces la ventana queda con esa proporción y solo se ve el inicio.
    """
    prop = proporcion_zona(clave)
    await page.set_viewport_size({"width": page.viewport_size["width"], "height": 600})
    for _ in range(3):
        alto = await page.evaluate("document.documentElement.scrollHeight")
        ancho = max(900, round(alto / prop))
        if ancho > ANCHO_MAXIMO:
            await page.set_viewport_size({"width": ANCHO_MAXIMO, "height": round(ANCHO_MAXIMO * prop)})
            await page.wait_for_timeout(400)
            return False
        await page.set_viewport_size({"width": ancho, "height": 600})
        await page.wait_for_timeout(400)
    # Un visor de PDF solo pinta lo visible: la ventana debe abarcar toda la página.
    await page.set_viewport_size({"width": ancho, "height": alto})
    return True


async def captura_ajustada(page, ruta: Path, clave: str, espera: int = 1000):
    completa = await ajustar_ventana_a_recuadro(page, clave)
    await page.wait_for_timeout(espera)
    await page.screenshot(path=str(ruta), full_page=completa)


async def consultar_govuk(page, persona: dict) -> Path:
    await page.goto(URL_GOVUK, wait_until="domcontentloaded")
    try:
        await page.get_by_role("button", name="Reject additional cookies").click(timeout=5000)
        await page.get_by_role("button", name="Hide cookie message").click(timeout=3000)
    except PlaywrightTimeoutError:
        pass
    campo = page.locator("input[name=keywords]:visible").first
    await campo.fill(persona["nombre"])
    await campo.press("Enter")
    await page.wait_for_url("**/search/all**", timeout=20000)
    await page.wait_for_load_state("load")

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"govuk_{re.sub(r'\D', '', persona['numero'])}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await captura_ajustada(page, ruta, "govuk")
    return ruta


async def consultar_eumostwanted(page, persona: dict) -> Path:
    nombre = " ".join(persona["nombre"].split())
    # El sitio a veces tarda más de 30 s en entregar el HTML completo.
    await page.goto(URL_EUMOSTWANTED.format(nombre=quote_plus(nombre)), wait_until="commit", timeout=60000)
    await page.get_by_role(
        "heading", name=re.compile(r"search results|your search yielded no results", re.I)
    ).first.wait_for(timeout=60000)
    try:
        await page.get_by_text("I don't agree", exact=True).first.click(timeout=5000)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("load", timeout=30000)
    except PlaywrightTimeoutError:
        pass

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"eumostwanted_{re.sub(r'\D', '', persona['numero'])}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await captura_ajustada(page, ruta, "eumostwanted")
    return ruta


# El documento queda cargado entre consultas: primero se quita la búsqueda anterior.
BUSCAR_EN_DOCUMENTO_JS = """q => {
    document.getElementById('barra-buscar-bot')?.remove();
    document.getElementById('estilo-buscar-bot')?.remove();
    CSS.highlights.clear();
    window.scrollTo(0, 0);
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
    estilo.id = 'estilo-buscar-bot';
    estilo.textContent = '::highlight(buscar){background:#ffcf00;color:#000}::highlight(actual){background:#ff9632;color:#000}';
    document.head.append(estilo);
    if (rangos.length) {
        CSS.highlights.set('buscar', new Highlight(...rangos));
        CSS.highlights.set('actual', new Highlight(rangos[0]));
        rangos[0].startContainer.parentElement.scrollIntoView({block: 'center'});
    }
    const barra = document.createElement('div');
    barra.id = 'barra-buscar-bot';
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


async def cargar_consolidado(page):
    await page.set_viewport_size({"width": 1600, "height": round(1600 * proporcion_zona("consolidado"))})
    await page.goto(ARCHIVO_CONSOLIDADO.as_uri(), wait_until="load")


async def cargar_eurlex(page):
    """Diario Oficial L 25/2022, donde se busca el número como con Ctrl+F."""
    await page.set_viewport_size({"width": 1600, "height": round(1600 * proporcion_zona("consolium"))})
    await page.goto(URL_EURLEX, wait_until="domcontentloaded", timeout=60000)
    # La primera respuesta (HTTP 202) es una página intermedia que recarga sola el documento.
    await page.get_by_text("Diario Oficial").first.wait_for(timeout=60000)
    await page.wait_for_load_state("load", timeout=60000)


class Documento:
    """Documento fijo que se carga al iniciar y queda abierto; en cada consulta solo se busca el número."""

    def __init__(self, page, cargar, clave: str, nombre: str):
        self.page, self.cargar, self.clave, self.nombre = page, cargar, clave, nombre
        self.carga = asyncio.create_task(cargar(page))

    async def buscar(self, persona: dict) -> Path:
        """Equivale a buscar el número con Ctrl+F: resalta las coincidencias y muestra la barra de búsqueda."""
        try:
            await self.carga
        except Exception as e:
            log(f"{self.nombre}: falló la carga ({e}); se carga de nuevo.")
            self.carga = asyncio.create_task(self.cargar(self.page))
            await self.carga
        numero = re.sub(r"\D", "", persona["numero"])
        coincidencias = await self.page.evaluate(BUSCAR_EN_DOCUMENTO_JS, numero)
        log(f"{self.nombre}: {coincidencias} coincidencias para {numero}")
        await self.page.wait_for_timeout(500)

        CAPTURAS.mkdir(exist_ok=True)
        ruta = CAPTURAS / f"{self.clave}_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
        await self.page.screenshot(path=str(ruta))
        return ruta


async def consultar_ofac(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    await page.goto(URL_OFAC, wait_until="domcontentloaded")
    await page.locator("#ctl00_MainContent_ddlType").select_option(label="Individual")
    await page.locator("#ctl00_MainContent_ddlCountry").select_option(label="Colombia")
    await page.locator("#ctl00_MainContent_txtID").fill(numero)
    async with page.expect_navigation(wait_until="load", timeout=30000):
        await page.locator("#ctl00_MainContent_btnSearch").click()
    await page.wait_for_timeout(500)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"ofac_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await captura_ajustada(page, ruta, "ofac")
    return ruta


async def consultar_libreta(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    await page.goto(URL_LIBRETA, wait_until="domcontentloaded")
    await page.locator("#tipoDocumento").select_option(label=TIPOS_LIBRETA[persona["tipo"].upper()])
    await page.locator("#numeroDocumento").fill(numero)
    await page.get_by_role("button", name="Consultar", exact=True).click()
    await page.get_by_text("Aqui vera el estado consultado").wait_for(state="hidden", timeout=30000)
    # El certificado es un PDF embebido; necesita unos segundos para renderizar.
    await page.locator("embed").wait_for(state="attached", timeout=15000)
    await page.wait_for_timeout(2500)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"libreta_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await captura_ajustada(page, ruta, "libreta", espera=3000)
    return ruta


async def consultar(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    form = page.frame_locator("iframe[src*='webcert']")
    pregunta_loc = form.locator("#lblPregunta")
    respuesta_loc = form.locator("#txtRespuestaPregunta")
    cuerpo = form.locator("body")
    datos_ciudadano = form.get_by_text("Datos del ciudadano").first
    error_respuesta = form.get_by_text(
        re.compile(r"el valor ingresado para la respuesta no responde a la pregunta", re.I)
    ).first
    no_registrado = form.get_by_text(
        re.compile(r"el n[uú]mero de identificaci[oó]n ingresado no se encuentra registrado", re.I)
    ).first

    async def cargar_formulario():
        # El portal a veces tarda más de 30 s en cargar el formulario.
        await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        await form.locator("#ddlTipoID").select_option(label=TIPOS[persona["tipo"].upper()], timeout=90000)
        await form.locator("#txtNumID").fill(numero)
        # La pregunta puede cambiar tras el postback del tipo de ID.
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass

    await cargar_formulario()
    for intento in range(3):
        await pregunta_loc.wait_for(state="visible", timeout=30000)
        pregunta = (await pregunta_loc.inner_text()).strip()
        respuesta = resolver_pregunta(pregunta, numero, persona["nombre"])
        log(f"Pregunta: {pregunta!r} -> {respuesta!r}")
        await respuesta_loc.fill(respuesta)
        texto_inicial = await cuerpo.inner_text(timeout=10000)
        await form.locator("#btnConsultar").click()
        limite = time.monotonic() + 60
        estado = None
        consultando = False
        texto_previo = texto_inicial
        texto_estable_desde = None
        while time.monotonic() < limite:
            if await datos_ciudadano.is_visible():
                estado = "resultado"
                break
            if await no_registrado.is_visible():
                estado = "sin_registro"
                break
            if await error_respuesta.is_visible():
                estado = "respuesta_incorrecta"
                break
            texto_actual = await cuerpo.inner_text(timeout=2000)
            consultando = bool(re.search(r"consultando\s*,?\s*por favor espere|cargando", texto_actual, re.I))
            if consultando:
                texto_previo = texto_actual
                texto_estable_desde = None
                await page.wait_for_timeout(300)
                continue
            if texto_actual != texto_inicial and texto_actual == texto_previo:
                if texto_estable_desde is None:
                    texto_estable_desde = time.monotonic()
                elif time.monotonic() - texto_estable_desde >= 2:
                    estado = "resultado_estable"
                    break
            else:
                texto_previo = texto_actual
                texto_estable_desde = None
            await page.wait_for_timeout(250)
        if estado == "respuesta_incorrecta":
            if intento == 2:
                raise ValueError("Procuraduría rechazó tres respuestas de verificación.")
            log("Procuraduría rechazó la respuesta; se resolverá el nuevo reto.")
            continue
        if estado is None:
            if intento == 2:
                raise ValueError("Procuraduría no mostró un resultado en 3 intentos; no se generó captura.")
            motivo = "seguía consultando" if consultando else "la página no cambió"
            log(f"Procuraduría: sin resultado en 60 s ({motivo}); se repite la consulta ({intento + 2}/3).")
            await cargar_formulario()
            continue
        if estado == "resultado_estable":
            log("Procuraduría: contenido de resultado estable aunque cambió el rótulo del portal.")
        break
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await page.screenshot(path=str(ruta), full_page=True)
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


def preguntar_en_consola(mensaje: str, consola: int | None, pantalla_tomada: bool = False) -> str:
    """Pide un dato en la consola, sin coincidir con una captura de Edge (ambas necesitan el foco)."""
    if not pantalla_tomada:
        with PANTALLA:
            return preguntar_en_consola(mensaje, consola, pantalla_tomada=True)
    enfocar_consola(consola)
    return input(mensaje).strip()


def memoria_libre_mb() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
            (campo, ctypes.c_ulonglong)
            for campo in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                          "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")
        ]

    estado = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
        return None
    return estado.ullAvailPhys // 2**20


def limite_concurrencia() -> int:
    """Páginas automáticas a la vez: hasta una por núcleo lógico y por cada ~300 MB de RAM libre (mínimo 2)."""
    nucleos = os.cpu_count() or 2
    libre = memoria_libre_mb()
    return max(2, min(nucleos, libre // 300 if libre is not None else nucleos))


async def abrir_navegadores(p) -> dict:
    """Abre los navegadores y los documentos fijos mientras se escriben los datos.

    Un solo navegador sin ventana con un contexto aislado por consulta; el RUNT va aparte con ventana para el captcha.
    """
    # El headless nuevo (canal "chromium") incluye el visor de PDF que usa la libreta.
    automatico, visible = await asyncio.gather(
        p.chromium.launch(headless=True, channel="chromium", args=[f"--host-resolver-rules={REGLAS_BLOQUEO}"]),
        p.chromium.launch(headless=False, args=["--start-maximized"]),
    )
    # La Procuraduría bloquea el user agent "HeadlessChrome".
    opciones = {"ignore_https_errors": True, "user_agent": USER_AGENT}
    # Una pestaña por contexto: Chrome frena las pestañas en segundo plano de una misma ventana.
    procuraduria, simit, libreta, grupo, onu, eurlex = await asyncio.gather(
        automatico.new_context(viewport=VIEWPORT_PROCURADURIA, **opciones),
        *(automatico.new_context(viewport=VIEWPORT, **opciones) for _ in range(5)),
    )
    r = {
        "navegadores": (automatico, visible),
        "procuraduria": await procuraduria.new_page(),
        "simit": await simit.new_page(),
        "libreta": await libreta.new_page(),
        "grupo": grupo,
        # La ventana del RUNT se abre al consultar, para no tapar la pantalla mientras se escriben los datos.
        "contexto_runt": await visible.new_context(no_viewport=True, **opciones),
        "runt": None,
    }
    r["consolidado"] = Documento(await onu.new_page(), cargar_consolidado, "consolidado", "Documento consolidado")
    r["consolium"] = Documento(await eurlex.new_page(), cargar_eurlex, "consolium", "EUR-Lex")
    return r


async def en_pestana_nueva(contexto, consulta, persona: dict) -> Path:
    page = await contexto.new_page()
    try:
        return await consulta(page, persona)
    finally:
        await page.close()


def consultas_edge(persona: dict, preguntar) -> dict[str, Path]:
    """Páginas con verificación de Cloudflare: la pasa una persona en su navegador; se capturan una tras otra."""
    capturas = {}
    for clave, consulta in (
        ("terroristas", consultar_terroristas),
        ("justicia", consultar_justicia),
        ("fbi", consultar_fbi),
        ("consilium", consultar_consilium),
    ):
        try:
            capturas[clave] = consulta(persona, preguntar)
            log(f"Captura {clave}: {capturas[clave]}")
        except Exception as e:
            log(f"ERROR {clave}: {e}")
    return capturas


async def consultar_persona(persona: dict, r: dict, preguntar, preguntar_edge) -> dict[str, Path]:
    capturas: dict[str, Path] = {}
    limite = asyncio.Semaphore(limite_concurrencia())
    captcha_listo = asyncio.Event()

    async def registrar(clave: str, consulta):
        try:
            capturas[clave] = await consulta
            log(f"Captura {clave}: {capturas[clave]}")
        except Exception as e:
            # SIMIT, Procuraduría y RUNT cortan las conexiones que llegan por VPN.
            if re.search(r"ERR_CONNECTION_(CLOSED|RESET)", str(e)):
                e = "el sitio cerró la conexión. Si estás con VPN, apágala: este sitio rechaza las conexiones por VPN."
            log(f"ERROR {clave}: {e}")

    async def automatica(clave: str, funcion, *args):
        async with limite:
            await registrar(clave, funcion(*args))

    async def grupo_rapido():
        # Cada una tarda pocos segundos: en serie terminan antes que SIMIT, Procuraduría y RUNT.
        async with limite:
            await registrar("consolidado", r["consolidado"].buscar(persona))
            await registrar("consolium", r["consolium"].buscar(persona))
            for clave, consulta in (("ofac", consultar_ofac), ("govuk", consultar_govuk), ("eumostwanted", consultar_eumostwanted)):
                await registrar(clave, en_pestana_nueva(r["grupo"], consulta, persona))

    async def runt():
        async def consulta():
            # Se vuelve a abrir si se cerró la ventana.
            if r["runt"] is None or r["runt"].is_closed():
                r["runt"] = await r["contexto_runt"].new_page()
            return await consultar_runt(r["runt"], persona, preguntar, captcha_listo)

        try:
            await registrar("runt", consulta())
        finally:
            captcha_listo.set()

    async def edge():
        # Empieza tras el captcha del RUNT para no tapar su ventana; trabaja a la vez que las páginas automáticas.
        await captcha_listo.wait()
        capturas.update(await asyncio.to_thread(consultas_edge, persona, preguntar_edge))

    # Las más largas primero: el límite deja esperando a las últimas en un equipo débil.
    await asyncio.gather(
        automatica("procuraduria", consultar, r["procuraduria"], persona),
        automatica("simit", consultar_simit, r["simit"], persona, preguntar),
        grupo_rapido(),
        automatica("libreta", consultar_libreta, r["libreta"], persona),
        runt(),
        edge(),
    )
    return capturas


async def principal():
    consola = consola_siempre_visible()

    async def preguntar(mensaje: str) -> str:
        return await asyncio.to_thread(preguntar_en_consola, mensaje, consola)

    # Las capturas de Edge ya tienen la pantalla cuando preguntan.
    preguntar_edge = functools.partial(preguntar_en_consola, consola=consola, pantalla_tomada=True)

    async with async_playwright() as p:
        apertura = asyncio.create_task(abrir_navegadores(p))
        try:
            while True:
                linea = await asyncio.to_thread(input, "\nPega los datos (TIPO NUMERO NOMBRE) o Enter para salir: ")
                if not linea.strip():
                    break
                try:
                    persona = parsear(linea)
                except ValueError as e:
                    log(f"ERROR: {e}")
                    continue
                log(f"Datos: {persona} | apellido RUNT: {primer_apellido(persona['nombre'])}")
                capturas = await consultar_persona(persona, await apertura, preguntar, preguntar_edge)
                if capturas:
                    log(f"PDF: {insertar_en_pdf(capturas, persona['numero'], persona['nombre'])}")
        finally:
            apertura.cancel()
            recursos = (await asyncio.gather(apertura, return_exceptions=True))[0]
            if isinstance(recursos, dict):
                for documento in (recursos["consolidado"], recursos["consolium"]):
                    documento.carga.cancel()
                    await asyncio.gather(documento.carga, return_exceptions=True)
                for navegador in recursos["navegadores"]:
                    await navegador.close()


def main():
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
