import asyncio
import functools
import os
import re
import io
import pytesseract
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import quote_plus

from PIL import Image, ImageOps, ImageFilter

import pymupdf
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from captura_navegador import PAGINA_REFERENCIA, PANTALLA, abrir_en_ventanas
from comun import CAPTURAS, FueraDeServicio, avisar, leer_respuesta, log
import documentos
from documentos import estado_ventana
from consulta_consilium import consultar_consilium, url_consilium
from consulta_fbi import consultar_fbi, url_fbi
from consulta_justicia import TITULO_JUSTICIA, consultar_justicia, url_justicia
from consulta_terroristas import TITULO_TERRORISTAS, consultar_terroristas, url_terroristas



URL = "https://www.procuraduria.gov.co/Pages/Consulta-de-Antecedentes.aspx"
# Tras enviar la consulta el portal puede tardar minutos; si se recarga, la consulta empieza de cero.
ESPERA_PROCURADURIA = 240
# Sin respuesta en este tiempo, se lanza una segunda consulta en paralelo sin recargar la primera; cuando el portal
# funciona responde en menos de 30 s.
SEGUNDA_PROCURADURIA = 50
URL_RUNT = "https://portalpublico.runt.gov.co/#/consulta-ciudadano-documento/consulta/consulta-ciudadano-documento"
URL_SIMIT = "https://www.fcm.org.co/simit/#/home-public"
URL_LIBRETA = "https://definicion.libretamilitar.mil.co/consulta-estado-situacion-militar"
URL_OFAC = "https://sanctionssearch.ofac.treas.gov/"
URL_GOVUK = "https://www.gov.uk/"
URL_EUMOSTWANTED = "https://eumostwanted.eu/search/node?keys={nombre}"
URL_EURLEX = "https://eur-lex.europa.eu/legal-content/ES/TXT/HTML/?uri=OJ:L:2022:025:FULL&from=ES"
# GALLAGHER
URL_INSIGHT = "https://insightcrime.org/es/?s={nombre}"
URL_CONTADORES = "https://sgr.jcc.gov.co:8181/apex/f?p=119:1:0:::::"
URL_DIAN = "https://www.dian.gov.co/Proveedores_Ficticios/Proveedores-Ficticios-{fecha:%d%m%Y}.pdf"
FECHA_DIAN = date(2026, 10, 15)
URL_SAMM = "https://samm.dsca.mil/search/site_search"
URL_BID = "https://www.iadb.org/es/search"
URL_CANADA = "https://international.canada.ca/en/global-affairs"
URL_TESORO = "https://ofac.treasury.gov/"
ARCHIVO_CONSOLIDADO = Path(__file__).parent / "Plantilla" / "consolidatedLegacyByNAME.html"
ANCHO_MAXIMO = 1600
# Pantalla virtual de las páginas automáticas: más pequeña, menos trabajo de dibujo; el recuadro del PDF no necesita más.
VIEWPORT = {"width": 1366, "height": 768}
# La Procuraduría muestra el resultado dentro de un marco (iframe): más estrecha, el texto ocupa más alto
# y podría quedar cortado dentro del marco. Se mantiene el ancho con el que se verificó.
VIEWPORT_PROCURADURIA = {"width": 1920, "height": 1080}
SALIDAS = Path(__file__).parent / "salidas"
PLANTILLAS = {
    "Hombre": Path(__file__).parent / "Plantilla" / "Hombre.pdf",
    # Igual a la de hombre, sin la página de la libreta militar.
    "Mujer": Path(__file__).parent / "Plantilla" / "Mujer.pdf",
    # GALLAGHER: la misma para hombre y mujer; a la mujer la libreta militar se le marca «No Aplica».
    "Gallagher": Path(__file__).parent / "Plantilla" / "FORMATO PÁGINAS GALLAGHER.pdf",
}
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

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")
# Cada palabra de la frase es un grupo, así se puede cambiar una por una aunque la frase quede partida en dos renglones.
# Contraloría: "hoy miércoles 14 de octubre de 2026" (el año es opcional).
HOY_RE = re.compile(r"\b(hoy)\s+(\S+)\s+(\d{1,2})\s+(de)\s+([^\W\d_]+)(?:\s+(de)\s+(\d{4}))?", re.IGNORECASE)
# Registraduría: "Expedida el 15 de octubre de 2026" y "hasta el 14 de Noviembre de 2026".
EXPEDIDA_RE = re.compile(r"\b(expedida)\s+(el)\s+(\d{1,2})\s+(de)\s+([^\W\d_]+)\s+(de)\s+(\d{4})", re.IGNORECASE)
VALIDEZ_RE = re.compile(r"\b(hasta)\s+(el)\s+(\d{1,2})\s+(de)\s+([^\W\d_]+)\s+(de)\s+(\d{4})", re.IGNORECASE)


pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
print("Ruta OCR:", pytesseract.pytesseract.tesseract_cmd)
print("Versión OCR:", pytesseract.get_tesseract_version())


def leer_captcha(imagen):
    if not imagen:
        print("[CAPTCHA] No se recibió ninguna imagen.")
        return None

    try:
        # La captura de Playwright llega como bytes
        img = Image.open(io.BytesIO(imagen)).convert("L")

        # Mejorar el contraste y ampliar el texto
        img = ImageOps.autocontrast(img)
        img = img.resize(
            (img.width * 3, img.height * 3)
        )
        img = img.filter(ImageFilter.SHARPEN)

        # Reconocer texto, priorizando letras y números
        texto = pytesseract.image_to_string(
            img,
            config="--oem 3 --psm 6"
        )

        print(f"\nTexto detectado en CAPTCHA:\n{texto}")

        # Ajusta este patrón al formato real del ID
        coincidencia = re.search(
            r"\b(?=\d*[a-z])(?=[a-z]*\d)[a-z\d]+\b",
            texto,
            re.IGNORECASE
        )

        if coincidencia:
            captcha = coincidencia.group(0)
            print(f"[CAPTCHA] Captcha detectado: {captcha}")
            return captcha

        print("[CAPTCHA] No se detectó ningún captcha.")
        return None


    except Exception as e:
        print(f"Error leyendo CAPTCHA: {e}")
        return None



def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


CAMPOS = ("nombre", "documento", "empresa", "fecha consulta", "quien consulta", "fecha expedicion", "sexo")
FORMATO = (
    "Copia la fila de Excel con: nombre, documento, empresa, fecha consulta, quien consulta, fecha expedicion, sexo "
    "(ej. Santiago Flores Alonso | 1000858557 | FALABELLA CEDI | 16/09/2026 | STEVEN | 25/06/2020 | H). "
    "Sexo: H (hombre) o M (mujer). A mano, separa los datos con ;"
)
SEXOS = {"H": "Hombre", "HOMBRE": "Hombre", "M": "Mujer", "MUJER": "Mujer"}


def leer_fecha(texto: str, campo: str) -> date:
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    raise ValueError(f"{campo.capitalize()} no válida: {texto!r}. Usa dd/mm/aaaa.")


def partir_fila(linea: str) -> list[str]:
    """Fila copiada de Excel (separada por tabulaciones) o escrita a mano con ; o , entre los datos."""
    separador = "\t" if "\t" in linea else ";" if ";" in linea else ","
    campos = [" ".join(campo.split()) for campo in linea.split(separador)]
    # Celdas vacías a los lados si se copió más de la fila.
    while campos and not campos[0]:
        campos.pop(0)
    while campos and not campos[-1]:
        campos.pop()
    return campos


def parsear(linea: str) -> dict:
    return validar(partir_fila(linea))


def validar(campos: list[str]) -> dict:
    """Los datos de la persona a partir de los campos, en el orden de CAMPOS."""
    if len(campos) != len(CAMPOS):
        raise ValueError(f"Llegaron {len(campos)} datos y se esperan {len(CAMPOS)}. {FORMATO}")
    for campo, valor in zip(CAMPOS, campos):
        if not valor:
            raise ValueError(f"Falta el dato '{campo}'. {FORMATO}")
    nombre, documento, empresa, fecha_consulta, consultor, fecha_expedicion, sexo = campos
    if re.search(r"\d", nombre):
        raise ValueError(f"El nombre no puede tener números: {nombre!r}. {FORMATO}")
    if sexo.upper() not in SEXOS:
        raise ValueError(f"Sexo no válido: {sexo!r}; usa H (hombre) o M (mujer). {FORMATO}")
    # Si no se indica el tipo de documento, es cédula de ciudadanía.
    m = re.fullmatch(r"(?:([A-Za-z.]+)\s*)?([\d.,\s]+)", documento)
    tipo = (m[1] or "CC").replace(".", "").upper() if m else ""
    numero = re.sub(r"\D", "", m[2]) if m else ""
    if tipo not in ("CC", "CE", "PEP", "PPT", "NIT", "TI") or not numero:
        raise ValueError(f"Documento no válido: {documento!r} (ej. 1000858557 o CE 123456; tipos: CC, CE, PEP, PPT, NIT, TI).")
    return {
        "nombre": nombre,
        "tipo": tipo,
        "numero": numero,
        "empresa": empresa,
        "fecha_consulta": leer_fecha(fecha_consulta, "fecha de consulta"),
        "consultor": consultor,
        "fecha_expedicion": leer_fecha(fecha_expedicion, "fecha de expedición"),
        "sexo": SEXOS[sexo.upper()],
        # La plantilla del informe: GALLAGHER tiene la suya; las demás empresas, la de hombre o la de mujer.
        "formato": "Gallagher" if "GALLAGHER" in empresa.upper() else SEXOS[sexo.upper()],
    }


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
    "Hombre": {
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
    },
    # Sin la libreta militar, desde OFAC cada sitio sube medio espacio y la Lista UE comparte la última página.
    "Mujer": {
        "procuraduria": (1, "arriba"),
        "runt": (1, "abajo"),
        "simit": (2, "arriba"),
        "ofac": (2, "abajo"),
        "consolidado": (3, "arriba"),
        "govuk": (3, "abajo"),
        "fbi": (4, "arriba"),
        "justicia": (4, "abajo"),
        "terroristas": (5, "arriba"),
        "consolium": (5, "abajo"),
        "eumostwanted": (6, "arriba"),
        "consilium": (6, "abajo"),
    },
    "Gallagher": {
        "procuraduria": (1, "arriba"),
        "eumostwanted": (1, "abajo"),
        "ofac": (2, "arriba"),
        "consolidado": (2, "abajo"),
        "simit": (3, "arriba"),
        "govuk": (3, "abajo"),
        "libreta": (4, "arriba"),
        "fbi": (4, "abajo"),
        "justicia": (5, "arriba"),
        "terroristas": (5, "abajo"),
        "consilium": (6, "arriba"),
        "runt": (6, "abajo"),
        "consolium": (7, "arriba"),
        "insight": (7, "abajo"),
        "contadores": (8, "arriba"),
        "dian": (8, "abajo"),
        "samm": (9, "arriba"),
        "bid": (9, "abajo"),
        "canada": (10, "arriba"),
        "tesoro": (10, "abajo"),
    },
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
    # Las últimas hojas de GALLAGHER no tienen pie.
    pie = min((b[1] for b in pagina.get_text("blocks") if b[1] > c2.y1), default=pagina.rect.height - 30)
    return pymupdf.Rect(c2.x0, c2.y1 + 2, c2.x1, pie - 4)


@functools.cache
def proporcion_zona(clave: str, formato: str) -> float:
    """Alto/ancho del recuadro de la plantilla donde va la captura."""
    indice, hueco = UBICACION_PDF[formato][clave]
    with pymupdf.open(PLANTILLAS[formato]) as doc:
        zona = zona_pdf(doc[indice], hueco)
    return zona.height / zona.width


def filas_portada(formato: str) -> list[str]:
    """Los sitios en el orden de la tabla de la portada: primero las capturas, por página y hueco; luego los PDF."""
    ubicaciones = UBICACION_PDF[formato]
    huecos = ("arriba", "abajo", "unico")
    orden = sorted(ubicaciones, key=lambda clave: (ubicaciones[clave][0], huecos.index(ubicaciones[clave][1])))
    return orden + list(documentos.ORDEN)


def sitios_de(persona: dict) -> list[str]:
    """Los sitios que se le consultan a la persona, en el orden del informe."""
    return [clave for clave in filas_portada(persona["formato"]) if clave != "libreta" or persona["sexo"] == "Hombre"]


def centro_columna(palabras, titulo: str) -> float:
    """Centro del título de una columna de la portada, con las palabras del renglón en que termina."""
    partes = titulo.split()
    for i, palabra in enumerate(palabras):
        if palabra[4] != partes[-1]:
            continue
        rect = pymupdf.Rect(palabra[:4])
        for parte, anterior in zip(reversed(partes[:-1]), reversed(palabras[:i])):
            if anterior[4] != parte or abs(anterior[1] - palabra[1]) > 2:
                break
            rect |= anterior[:4]
        return (rect.x0 + rect.x1) / 2
    raise ValueError(f"La portada no tiene la columna «{titulo}».")


def mover_chulos(pagina, formato: str, claves: set[str], columna: str):
    """En la tabla de la portada pasa el chulo de esos sitios de «Verificada» a la columna `columna`."""
    if not claves:
        return
    def centro(rect):
        return (rect[0] + rect[2]) / 2

    palabras = pagina.get_text("words")
    verificada = next(p for p in palabras if p[4] == "Verificada")
    desplazamiento = centro_columna(palabras, columna) - centro(verificada)
    chulos = [(pymupdf.Rect(i["bbox"]), i["xref"]) for i in pagina.get_image_info(xrefs=True)
              if verificada[0] <= centro(i["bbox"]) <= verificada[2]]
    mover = []
    for n, clave in enumerate(filas_portada(formato), start=1):
        if clave not in claves:
            continue
        fila = pagina.search_for(f"Página {n} -")
        chulo = next(((r, x) for r, x in chulos if fila and fila[0].y0 - 2 < (r.y0 + r.y1) / 2 < fila[0].y1 + 2), None)
        if chulo is None:
            log(f"PDF: no se encontró el chulo de la fila {n} ({clave}) en la portada; no se marcó «{columna}».")
            continue
        mover.append(chulo)
        pagina.add_redact_annot(chulo[0], fill=False)
    # Solo se quita la imagen del chulo: la tabla y el texto se quedan.
    pagina.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                            text=pymupdf.PDF_REDACT_TEXT_NONE)
    for rect, xref in mover:
        pagina.insert_image(rect + (desplazamiento, 0, desplazamiento, 0), xref=xref)


def pegar_captura(pagina, captura: Path, hueco: str):
    zona = zona_pdf(pagina, hueco)
    if hueco == "unico":
        # La zona llega hasta el pie: la imagen va pegada al cuadro de título, no centrada en el vacío.
        pix = pymupdf.Pixmap(str(captura))
        zona.y1 = min(zona.y1, zona.y0 + zona.width * pix.height / pix.width)
    pagina.insert_image(zona, filename=str(captura), keep_proportion=True)


def rellenar_portada(pagina, persona: dict):
    """Cambia las "xxxx" de Nombre, Identificación y Fecha Consulta por los datos, con la letra de la plantilla."""
    valores = (persona["nombre"], persona["numero"], f"{persona['fecha_consulta']:%d/%m/%Y}")
    campos = []
    for bloque in pagina.get_text("rawdict")["blocks"]:
        for linea in bloque.get("lines", []):
            for span in linea["spans"]:
                letras = [c for c in span["chars"] if c["c"].strip()]
                if letras and all(c["c"] == "x" for c in letras):
                    campos.append((pymupdf.Rect(letras[0]["bbox"]) | letras[-1]["bbox"], letras[0]["origin"], span))
    campos.sort(key=lambda campo: campo[0].y0)
    for rect, _, _ in campos:
        pagina.add_redact_annot(rect, fill=False)
    # Solo se borra el texto: los recuadros y las imágenes de la portada se quedan.
    pagina.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for (_, origen, span), valor in zip(campos, valores):
        pagina.insert_text(origen, valor, fontname="helv", fontsize=span["size"], color=pymupdf.sRGB_to_pdf(span["color"]))


def fuente_de(span) -> str:
    """Fuente base de PyMuPDF más parecida a la del documento (Times o Helvetica, normal o negrita)."""
    nombre = span["font"].lower()
    negrita = bool(span["flags"] & 16) or "bold" in nombre
    if "times" in nombre:
        return "tibo" if negrita else "tiro"
    return "hebo" if negrita else "helv"


def igualar_mayusculas(original: str, nuevo: str) -> str:
    """El texto nuevo conserva la forma del original: MAYÚSCULAS, Capitalizado o minúsculas."""
    if len(original) > 1 and original.isupper():
        return nuevo.upper()
    if original[:1].isupper():
        return nuevo[:1].upper() + nuevo[1:]
    return nuevo


def reescribir_frases(pagina, patron, cambiar) -> int:
    """Busca `patron` en la página y cambia las palabras que indique `cambiar(hallado)` ({grupo: texto nuevo}).

    En cada renglón se reescribe desde la primera palabra cambiada hasta el final del renglón, con la letra original
    y terminando donde terminaba. Lo anterior a esa palabra no se toca. Devuelve cuántas frases cambió."""
    cambios, frases = [], 0
    for bloque in pagina.get_text("rawdict")["blocks"]:
        # Todas las letras del párrafo seguidas; entre renglones se intercala un espacio.
        letras = []
        for n, linea in enumerate(bloque.get("lines", [])):
            if letras:
                letras.append((None, n, None))
            letras += [(c, n, span) for span in linea["spans"] for c in span["chars"]]
        texto = "".join(c["c"] if c else " " for c, _, _ in letras)
        for hallado in patron.finditer(texto):
            nuevos = cambiar(hallado)
            frases += 1
            # Cada palabra de la frase, en el renglón donde está.
            por_renglon = {}
            for i in range(1, patron.groups + 1):
                if hallado.group(i) is not None:
                    por_renglon.setdefault(letras[hallado.start(i)][1], []).append(i)
            for n, grupos in por_renglon.items():
                cambiados = [i for i in grupos if i in nuevos]
                if not cambiados:
                    continue
                grupos = grupos[grupos.index(cambiados[0]):]
                inicio, fin = hallado.start(grupos[0]), hallado.end(grupos[-1])
                palabras = [(igualar_mayusculas(hallado.group(i), nuevos[i]) if i in nuevos else hallado.group(i),
                             letras[hallado.start(i)][2]) for i in grupos]
                # Las palabras que siguen a la frase en el mismo renglón.
                resto, actual = [], None
                for c, ln, span in letras[fin:]:
                    if ln != n:
                        break
                    if c["c"].isspace():
                        actual = None
                    elif actual is None:
                        actual = [c["c"], span]
                        resto.append(actual)
                    else:
                        actual[0] += c["c"]
                # Una coma o punto pegado a la fecha ("2026,") sigue pegado a la palabra nueva.
                if resto and fin < len(letras) and letras[fin][1] == n and not letras[fin][0]["c"].isspace():
                    pegado, span_pegado = palabras.pop()
                    palabras.append((pegado + resto.pop(0)[0], span_pegado))
                palabras += [(t, s) for t, s in resto]
                segmento = [c for c, ln, _ in letras[inicio:] if c and ln == n]
                rect = pymupdf.Rect(segmento[0]["bbox"])
                for c in segmento:
                    rect |= pymupdf.Rect(c["bbox"])
                cambios.append((rect, segmento[0]["origin"], palabras))
    if not cambios:
        return 0
    for rect, _, _ in cambios:
        pagina.add_redact_annot(rect, fill=False)
    # Solo se borra el texto: los recuadros y las imágenes se quedan.
    pagina.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    for rect, origen, palabras in cambios:
        medidas = []
        for texto, span in palabras:
            fuente = fuente_de(span)
            medidas.append((texto, span, fuente, pymupdf.Font(fuente).text_length(texto, fontsize=span["size"])))
        espacio = pymupdf.Font(medidas[0][2]).text_length(" ", fontsize=medidas[0][1]["size"])
        # El espacio sobrante (o faltante) se reparte entre los huecos, para que el renglón termine donde terminaba.
        sobrante = rect.x1 - origen[0] - sum(m[3] for m in medidas)
        hueco = max(espacio * 0.5, sobrante / (len(medidas) - 1)) if len(medidas) > 1 else 0
        x = origen[0]
        for texto, span, fuente, ancho in medidas:
            pagina.insert_text((x, origen[1]), texto, fontname=fuente, fontsize=span["size"],
                               color=pymupdf.sRGB_to_pdf(span["color"]))
            x += ancho + hueco
    return frases


def fecha_en_frase(fecha):
    """Cambia día, mes y año de una frase "el 15 de octubre de 2026" (grupos 3, 5 y 7)."""
    return lambda hallado: {3: str(fecha.day), 5: MESES[fecha.month - 1], 7: str(fecha.year)}


def rellenar_hoy(pagina, persona: dict) -> int:
    """Contraloría: "hoy miércoles 14 de octubre de 2026" -> fecha de consulta. Devuelve cuántas frases cambió."""
    fecha = persona["fecha_consulta"]

    def cambiar(hallado):
        nuevos = {2: DIAS[fecha.weekday()], 3: str(fecha.day), 5: MESES[fecha.month - 1]}
        if hallado.group(7):
            nuevos[7] = str(fecha.year)
        return nuevos

    return reescribir_frases(pagina, HOY_RE, cambiar)


def rellenar_registraduria(pagina, persona: dict) -> int:
    """Registraduría: "Expedida el ..." -> fecha de consulta; "hasta el ..." -> fecha de consulta + 30 días.
    Devuelve cuántas frases cambió (2 si encontró las dos)."""
    fecha = persona["fecha_consulta"]
    return (reescribir_frases(pagina, EXPEDIDA_RE, fecha_en_frase(fecha))
            + reescribir_frases(pagina, VALIDEZ_RE, fecha_en_frase(fecha + timedelta(days=30))))


def insertar_en_pdf(capturas: dict[str, Path], persona: dict, caidos: set[str] = frozenset()) -> Path:
    """`caidos`: sitios fuera de servicio; su captura es la página caída."""
    formato = persona["formato"]
    # Mujer.pdf trae un objeto interno mal ubicado: si la reparación automática llega al guardar, descarta los cambios.
    with pymupdf.open(PLANTILLAS[formato]) as plantilla:
        doc = pymupdf.open("pdf", plantilla.tobytes(garbage=3))
    rellenar_portada(doc[0], persona)
    mover_chulos(doc[0], formato, caidos, "Fuera de Servicio")
    mover_chulos(doc[0], formato, set(UBICACION_PDF[formato]) - {*sitios_de(persona)}, "No Aplica")
    ubicaciones = UBICACION_PDF[formato]
    for clave, captura in capturas.items():
        if clave in ubicaciones:
            indice, hueco = ubicaciones[clave]
            pegar_captura(doc[indice], captura, hueco)
    # Los PDF de cada sitio van al final, en el orden de la portada (páginas 14 a 19; 13 a 18 en la de mujer).
    for clave in documentos.ORDEN:
        if clave in capturas:
            with pymupdf.open(capturas[clave]) as anexo:
                # La página caída no tiene esas fechas.
                if clave == "contraloria" and clave not in caidos:
                    if not sum(rellenar_hoy(pagina, persona) for pagina in anexo):
                        log("PDF: no se encontró 'hoy <día> <n> de <mes>' en el documento de la Contraloría; esa fecha no se cambió.")
                elif clave == "registraduria" and clave not in caidos:
                    if sum(rellenar_registraduria(pagina, persona) for pagina in anexo) < 2:
                        log("PDF: no se encontraron las dos fechas ('Expedida el…' y 'hasta el…') en el documento de la Registraduría.")
                doc.insert_pdf(anexo)
        else:
            log(f"PDF: falta el documento de {clave}; se omite.")
    SALIDAS.mkdir(exist_ok=True)
    base = re.sub(r'[<>:"/\\|?*]', "", f"{persona['numero']} - {persona['nombre']}").strip()
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


async def precargar_runt(page):
    """El RUNT descarga ~12 MB al abrir: se carga minimizado mientras se escriben los datos y luego sale de la caché."""
    await documentos.precargar(page, URL_RUNT)
    await page.locator("img.img-fluid").first.wait_for(state="visible", timeout=90000)


# Recuadro del aviso: el antecesor más grande del texto que no ocupa todo el ancho (el fondo gris sí lo ocupa).
CAJA_AVISO_JS = """e => {
    let caja = e;
    while (caja.parentElement && caja.parentElement.getBoundingClientRect().width < innerWidth * 0.9) caja = caja.parentElement;
    const r = caja.getBoundingClientRect();
    return {alto: r.height, abajo: r.bottom};
}"""


async def pantalla_referencia(cdp, alto: int = PAGINA_REFERENCIA[1]):
    """La página de la ventana visible se dibuja con el ancho de la pantalla de referencia, no con el de este equipo."""
    await cdp.send("Emulation.setDeviceMetricsOverride",
                   {"width": PAGINA_REFERENCIA[0], "height": alto, "deviceScaleFactor": 0, "mobile": False})


async def captura_aviso_sobre_datos(page, ruta: Path, aviso):
    """El aviso se centra en la pantalla y tapa los datos consultados: se captura en una pantalla virtual más alta
    para que quede debajo de ellos."""
    ancho, alto = PAGINA_REFERENCIA
    cdp = await page.context.new_cdp_session(page)
    try:
        await pantalla_referencia(cdp)
        await page.wait_for_timeout(500)
        datos_abajo = await page.locator("input[formcontrolname=primerApellido]").evaluate("e => e.getBoundingClientRect().bottom")
        caja = await aviso.evaluate(CAJA_AVISO_JS)
        alto_necesario = max(alto, round(2 * (datos_abajo + 30) + caja["alto"]))
        await pantalla_referencia(cdp, alto_necesario)
        await page.wait_for_timeout(500)
        caja = await aviso.evaluate(CAJA_AVISO_JS)
        recorte = {"x": 0, "y": 0, "width": ancho, "height": min(alto_necesario, round(caja["abajo"]) + 30)}
        await page.screenshot(path=str(ruta), clip=recorte)
    finally:
        await cdp.send("Emulation.clearDeviceMetricsOverride")
        await cdp.detach()


async def consultar_runt(page, persona: dict, preguntar, turnos: documentos.Turnos, listo: asyncio.Event) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    apellido = re.sub(r"[^\w\sñÑáéíóúÁÉÍÓÚüÜ]", "", primer_apellido(persona["nombre"]))
    campo_captcha = page.locator("input[formcontrolname=captcha]")
    imagen_captcha = page.locator("img.img-fluid").first
    # Respuesta válida para quien no tiene registro en el RUNT, no un captcha incorrecto.
    sin_registro = page.get_by_text(re.compile(r"no se ha encontrado la persona", re.I)).first
    lleno_en = 0.0

    async def llenar():
        nonlocal lleno_en
        for carga in range(2):
            # Ya en el RUNT, goto solo cambia el hash y no recarga: quedarían el captcha viejo y el aviso anterior encima.
            ya_abierto = page.url.startswith(URL_RUNT.split("#")[0])
            await page.goto(URL_RUNT, wait_until="domcontentloaded")
            if ya_abierto:
                await page.reload(wait_until="domcontentloaded")
            try:
                # Sin caché tarda de 15 s a más de 1 min (unos 25 módulos) y a veces se queda en blanco.
                await imagen_captcha.wait_for(state="visible", timeout=60000)
                break
            except PlaywrightTimeoutError:
                if carga:
                    raise FueraDeServicio("la página no cargó tras recargarla; el sitio está lento o caído") from None
                log("RUNT: la página no cargó en 60 s; se recarga.")
        await page.locator("mat-select[formcontrolname=tipoDocumento]").click()
        await page.get_by_role("option", name=TIPOS_RUNT[persona["tipo"].upper()], exact=True).click()
        await page.locator("input[formcontrolname=documento]").fill(numero)
        await page.locator("input[formcontrolname=primerApellido]").fill(apellido)
        lleno_en = time.monotonic()

    # Se llena minimizado; la ventana sale al frente cuando le toca el turno de captcha.
    await llenar()
    for intento in range(3):
        if intento:
            await llenar()
        async with turnos.turno("runt", lambda: lleno_en, llenar):
            # La página pudo cambiar mientras esperaba su turno (o no se pudo recargar a tiempo).
            if time.monotonic() - lleno_en > documentos.VIGENCIA_CAPTCHA or not await imagen_captcha.count():
                await llenar()

            await estado_ventana(page, "maximized")
            await imagen_captcha.scroll_into_view_if_needed()
            await page.bring_to_front()

            print("[CAPID] Intentando tomar captura...")

            try:
                imagen = await imagen_captcha.screenshot(timeout=5000)
                print(f"[CAPID] Captura obtenida: {len(imagen)} bytes")
            except PlaywrightError as e:
                imagen = None
                print(f"[CAPID] Error en captura: {e}")

            if imagen:
                print("[CAPID] Ejecutando OCR...")
                captcha = leer_captcha(imagen)
                print(f"[CAPID] Resultado OCR: {captcha!r}")
            else:
                print("[CAPID] No hay imagen para procesar.")


            pedido = time.monotonic()

            # Leer automáticamente el ID de la captura
            captcha = leer_captcha(imagen)

            if captcha:
                print(f"\nCAPTCHA detectado automáticamente: {captcha}")
                respuesta = captcha
            else:
                print("\nNo se pudo reconocer el CAPTCHA automáticamente.")

    # Mantener la entrada manual como respaldo
            captcha = leer_captcha(imagen)

            if captcha:
                respuesta = captcha
                log(f"BASER: CAPID detectado automáticamente: {respuesta}")
            else:
                respuesta = await preguntar(
                    "CAPTCHA del RUNT: escríbelo manualmente: ",
                    titulo="captcha del RUNT",
                    imagen=imagen,
                    intento=intento + 1
                )

            log(f"RUNT: código escrito en {time.monotonic() - pedido:.0f} s.")
            if not await campo_captcha.count():
                log("RUNT: la página cambió mientras se escribía el captcha; se carga de nuevo.")
                continue
            await campo_captcha.fill(respuesta)
            log(f"RUNT: se escribió el CAPTCHA: {respuesta}")
            await page.get_by_role("button", name="Consultar Información").click()
        # Mientras el sitio responde ya se puede resolver el captcha siguiente.
        listo.set()
        try:
            # Cada medio segundo: con otra ventana encima, la página no depende de dibujar para comprobarlo.
            await page.wait_for_function(
                """() => {
                    const c = document.querySelector('input[formcontrolname=captcha]');
                    return !c || c.offsetParent === null
                        || /captcha no es v[aá]lido|no se ha encontrado la persona/i.test(document.body.innerText);
                }""",
                timeout=20000,
                polling=500,
            )
        except PlaywrightTimeoutError:
            pass
        if await sin_registro.is_visible() or not await campo_captcha.is_visible():
            break
        log(f"RUNT: captcha incorrecto o sin resultado, intento {intento + 1}/3")
    else:
        raise ValueError("RUNT: no se obtuvo resultado tras 3 intentos")
    ir_arriba = "document.querySelectorAll('*').forEach(e => { if (e.scrollTop) e.scrollTop = 0; }); window.scrollTo(0, 0)"
    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"runt_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    if await sin_registro.is_visible():
        log("RUNT: la persona no está en estado ACTIVA o SIN REGISTRO; se captura el aviso como resultado.")
        await page.evaluate(ir_arriba)
        # Deja terminar la animación del aviso.
        await page.wait_for_timeout(700)
        await captura_aviso_sobre_datos(page, ruta, sin_registro)
        return ruta
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
    cdp = await page.context.new_cdp_session(page)
    try:
        await pantalla_referencia(cdp)
        await page.wait_for_timeout(500)
        await page.evaluate(ir_arriba)
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(ruta), full_page=True)
    finally:
        await cdp.send("Emulation.clearDeviceMetricsOverride")
        await cdp.detach()
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
            f"{opciones}\nNúmero de opción: ",
            titulo="SIMIT encontró varias personas", opciones=etiquetas,
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


async def ajustar_ventana_a_recuadro(page, clave: str, formato: str) -> bool:
    """Ensancha la ventana hasta que la página completa tenga la proporción del recuadro del PDF.

    Devuelve False si la página es demasiado larga: entonces la ventana queda con esa proporción y solo se ve el inicio.
    """
    prop = proporcion_zona(clave, formato)
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


async def captura_ajustada(page, ruta: Path, clave: str, formato: str, espera: int = 1000):
    completa = await ajustar_ventana_a_recuadro(page, clave, formato)
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
    await captura_ajustada(page, ruta, "govuk", persona["formato"])
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
    await captura_ajustada(page, ruta, "eumostwanted", persona["formato"])
    return ruta


async def capturar_sin_ventana(page, persona: dict, clave: str, url: str, titulo: str) -> Path:
    """State.gov y Justice: antes solo cargaban en Chrome; hoy cargan aquí y van en paralelo con los captchas."""
    respuesta = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if respuesta and respuesta.status >= 400:
        raise ValueError(f"el sitio respondió {respuesta.status}")
    # State.gov responde primero con una verificación de AWS (HTTP 202) que recarga sola la página.
    limite = time.monotonic() + 60
    while True:
        try:
            if titulo in await page.title():
                break
        except PlaywrightError:
            pass
        if time.monotonic() > limite:
            raise ValueError("la página no cargó")
        await page.wait_for_timeout(500)
    await page.wait_for_load_state("load", timeout=60000)

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"{clave}_{re.sub(r'\D', '', persona['numero'])}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await captura_ajustada(page, ruta, clave, persona["formato"])
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
    await page.set_viewport_size({"width": 1600, "height": round(1600 * proporcion_zona("consolidado", "Hombre"))})
    await page.goto(ARCHIVO_CONSOLIDADO.as_uri(), wait_until="load")


async def cargar_eurlex(page):
    """Diario Oficial L 25/2022, donde se busca el número como con Ctrl+F."""
    await page.set_viewport_size({"width": 1600, "height": round(1600 * proporcion_zona("consolium", "Hombre"))})
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
        # El recuadro cambia de alto según la plantilla; el ancho se mantiene para no reacomodar el documento.
        alto = round(1600 * proporcion_zona(self.clave, persona["formato"]))
        await self.page.set_viewport_size({"width": 1600, "height": alto})
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
    await captura_ajustada(page, ruta, "ofac", persona["formato"])
    return ruta


async def consultar_libreta(page, persona: dict) -> Path:
    numero = re.sub(r"\D", "", persona["numero"])
    await page.goto(URL_LIBRETA, wait_until="domcontentloaded")
    await page.locator("#tipoDocumento").select_option(label=TIPOS_LIBRETA[persona["tipo"].upper()])
    await page.locator("#numeroDocumento").fill(numero)
    await page.get_by_role("button", name="Consultar", exact=True).click()
    await page.get_by_text("Aqui vera el estado consultado").wait_for(state="hidden", timeout=30000)
    certificado = page.locator("embed")
    # Sin certificado el sitio explica el motivo en un recuadro ("El tipoDocumento no coincide..."): también es el resultado.
    mensaje = page.locator("iframe[srcdoc]")
    await certificado.or_(mensaje).first.wait_for(state="attached", timeout=15000)
    if await certificado.count():
        # El certificado es un PDF embebido; necesita unos segundos para renderizar.
        await page.wait_for_timeout(2500)
    else:
        texto = await page.frame_locator("iframe[srcdoc]").locator("body").inner_text()
        log(f"Libreta militar: sin certificado; el sitio respondió: {' '.join(texto.split())}")

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"libreta_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await captura_ajustada(page, ruta, "libreta", persona["formato"], espera=3000)
    return ruta


def ruta_captura(clave: str, persona: dict) -> Path:
    CAPTURAS.mkdir(exist_ok=True)
    return CAPTURAS / f"{clave}_{persona['numero']}_{datetime.now():%Y%m%d_%H%M%S}.png"


async def buscar_en_sitio(page, campo, persona: dict):
    """Escribe el nombre completo en el buscador del sitio y espera la página de resultados."""
    await campo.fill(persona["nombre"])
    async with page.expect_navigation(wait_until="load", timeout=60000):
        await campo.press("Enter")


async def consultar_contadores(page, persona: dict) -> Path:
    """Junta Central de Contadores: el número buscado como con Ctrl+F en el listado de sancionados."""
    await page.goto(URL_CONTADORES, wait_until="domcontentloaded", timeout=90000)
    await page.get_by_text("CC/CE", exact=True).first.wait_for(timeout=60000)
    # Ctrl+F solo ve las filas que muestra la página: se muestran todas (el listado tiene unas cien).
    siguiente = page.locator("a[href*='apex.widget.report.paginate']", has_text="Siguiente")
    if await siguiente.count():
        region = re.search(r"paginate\('(\d+)'", await siguiente.first.get_attribute("href"))[1]
        await page.evaluate("r => apex.widget.report.paginate(r, {min: 1, max: 100000, fetched: 15})", region)
        await siguiente.first.wait_for(state="detached", timeout=60000)
    await page.set_viewport_size({"width": 1366, "height": round(1366 * proporcion_zona("contadores", persona["formato"]))})
    coincidencias = await page.evaluate(BUSCAR_EN_DOCUMENTO_JS, persona["numero"])
    log(f"Junta Central de Contadores: {coincidencias} coincidencias para {persona['numero']}")
    await page.wait_for_timeout(500)
    ruta = ruta_captura("contadores", persona)
    await page.screenshot(path=str(ruta))
    return ruta


_url_dian: tuple[date, str] | None = None


async def url_dian(request) -> str:
    """La lista vigente de proveedores ficticios: la DIAN publica cada una con su fecha en el nombre y deja las
    anteriores, así que se busca la más reciente desde FECHA_DIAN (una vez al día: el bot puede quedar abierto)."""
    global _url_dian
    hoy = date.today()
    if _url_dian and _url_dian[0] == hoy:
        return _url_dian[1]

    async def publicada(fecha: date) -> bool:
        try:
            respuesta = await request.head(URL_DIAN.format(fecha=fecha), timeout=20000)
        except PlaywrightError:
            return False
        return respuesta.ok and "pdf" in respuesta.headers.get("content-type", "")

    fechas = [hoy - timedelta(days=n) for n in range((hoy - FECHA_DIAN).days)]
    url = URL_DIAN.format(fecha=FECHA_DIAN)
    for inicio in range(0, len(fechas), 15):
        lote = fechas[inicio:inicio + 15]
        publicadas = [fecha for fecha, existe in zip(lote, await asyncio.gather(*(publicada(f) for f in lote))) if existe]
        if publicadas:
            url = URL_DIAN.format(fecha=publicadas[0])
            break
    _url_dian = (hoy, url)
    return url


def buscar_en_pdf(datos: bytes, texto: str, proporcion: float, ruta: Path) -> int:
    """Como Ctrl+F en el visor de PDF: resalta `texto`, muestra la primera coincidencia (o el inicio del documento) con
    la proporción del recuadro y la barra de búsqueda arriba a la derecha. Devuelve cuántas coincidencias hay."""
    with pymupdf.open(stream=datos, filetype="pdf") as doc:
        encontradas = [(pagina, rect) for pagina in doc for rect in pagina.search_for(texto)]
        for n, (pagina, rect) in enumerate(encontradas):
            resaltado = pagina.add_highlight_annot(rect)
            resaltado.set_colors(stroke=(1, 0.59, 0.2) if n == 0 else (1, 0.81, 0))
            resaltado.update()
        pagina = encontradas[0][0] if encontradas else doc[0]
        # Sin los márgenes en blanco de la hoja: la tabla ocupa solo una parte del ancho. La etiqueta del pie
        # («Información Pública», en la esquina de abajo) no cuenta.
        contenido = pymupdf.EMPTY_RECT()
        for bloque in pagina.get_text("blocks"):
            if bloque[1] < pagina.rect.height * 0.95:
                contenido |= bloque[:4]
        for imagen in pagina.get_image_info():
            contenido |= imagen["bbox"]
        contenido = (contenido + (-12, -12, 12, 12)) & pagina.rect if not contenido.is_empty else pagina.rect
        ancho = contenido.width
        alto = min(ancho * proporcion, pagina.rect.height)
        arriba = contenido.y0
        if encontradas:
            arriba = min(max(contenido.y0, encontradas[0][1].y0 - alto / 2), pagina.rect.height - alto)
        recorte = pymupdf.Rect(contenido.x0, arriba, contenido.x1, arriba + alto)
        # La barra, con la letra que tiene en las páginas web (15 px en 1600 de ancho).
        letra = ancho * 15 / 1600
        cuenta = f"1/{len(encontradas)}" if encontradas else "0/0"
        fuente = pymupdf.Font("helv")
        etiqueta, ancho_texto, ancho_cuenta = "Buscar:", fuente.text_length(texto, letra), fuente.text_length(cuenta, letra)
        margen = letra * 0.8
        caja = pymupdf.Rect(0, 0, ancho_texto + 2 * margen, letra * 1.7)
        barra_ancho = margen + fuente.text_length(etiqueta, letra) + margen + caja.width + margen + ancho_cuenta + margen
        barra = pymupdf.Rect(recorte.x1 - barra_ancho - letra * 2, arriba + letra * 0.6, recorte.x1 - letra * 2,
                             arriba + letra * 0.6 + letra * 2.6)
        pagina.draw_rect(barra, color=(0.73, 0.73, 0.73), fill=(1, 1, 1), width=0.8, radius=0.15)
        base = barra.y0 + barra.height / 2 + letra * 0.35
        x = barra.x0 + margen
        pagina.insert_text((x, base), etiqueta, fontsize=letra, fontname="helv", color=(0.13, 0.13, 0.13))
        x += fuente.text_length(etiqueta, letra) + margen
        caja = caja + (x, barra.y0 + (barra.height - caja.height) / 2, x, barra.y0 + (barra.height - caja.height) / 2)
        pagina.draw_rect(caja, color=(0.53, 0.53, 0.53), width=0.6, radius=0.2)
        pagina.insert_text((caja.x0 + margen, base), texto, fontsize=letra, fontname="helv", color=(0.13, 0.13, 0.13))
        pagina.insert_text((caja.x1 + margen, base), cuenta, fontsize=letra, fontname="helv",
                           color=(0.13, 0.13, 0.13) if encontradas else (0.8, 0, 0))
        pagina.get_pixmap(clip=recorte, matrix=pymupdf.Matrix(1600 / ancho, 1600 / ancho)).save(ruta)
    return len(encontradas)


async def consultar_dian(page, persona: dict) -> Path:
    """Proveedores ficticios de la DIAN (PDF): el número buscado como con Ctrl+F."""
    url = await url_dian(page.context.request)
    # Si el sitio está caído, la página de error queda en la pestaña.
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    respuesta = await page.context.request.get(url, timeout=90000)
    if not respuesta.ok:
        raise ValueError(f"La DIAN respondió {respuesta.status} al descargar la lista de proveedores ficticios.")
    ruta = ruta_captura("dian", persona)
    coincidencias = await asyncio.to_thread(buscar_en_pdf, await respuesta.body(), persona["numero"],
                                            proporcion_zona("dian", persona["formato"]), ruta)
    log(f"DIAN proveedores ficticios ({url.rsplit('/', 1)[-1]}): {coincidencias} coincidencias para {persona['numero']}")
    return ruta


async def consultar_samm(page, persona: dict) -> Path:
    await page.goto(URL_SAMM, wait_until="domcontentloaded", timeout=60000)
    await buscar_en_sitio(page, page.locator("#edit-search-api-fulltext"), persona)
    # El recuadro de resultados aparece con una animación.
    await page.wait_for_timeout(1500)
    ruta = ruta_captura("samm", persona)
    await captura_ajustada(page, ruta, "samm", persona["formato"])
    return ruta


async def consultar_bid(page, persona: dict) -> Path:
    await page.goto(URL_BID, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.get_by_role("button", name="Rechazarlas todas").click(timeout=8000)
    except PlaywrightTimeoutError:
        pass
    # Solo el nombre: los demás filtros quedan como están.
    await buscar_en_sitio(page, page.locator("input[name=query]:visible").first, persona)
    # El menú tiene otro «Resultados» oculto: sin el filtro, se esperaría a que ese se viera.
    await page.get_by_text(re.compile(r"^\s*resultados\s*$", re.I)).filter(visible=True).first.wait_for(timeout=30000)
    ruta = ruta_captura("bid", persona)
    await captura_ajustada(page, ruta, "bid", persona["formato"])
    return ruta


async def consultar_canada(page, persona: dict) -> Path:
    # Canada.ca invita al azar a una encuesta («Government of Canada», Yes / No) con una ventanita encima de los
    # resultados, a veces segundos después de cargar: sin su gestor de invitaciones no sale.
    await page.route(re.compile(r"/invitation-manager/|/im\.xml"), lambda route: route.abort())
    await page.goto(URL_CANADA, wait_until="domcontentloaded", timeout=60000)
    await buscar_en_sitio(page, page.locator("#wb-srch-q"), persona)
    await page.get_by_text(re.compile(r"results? for|no (search )?results", re.I)).first.wait_for(timeout=30000)
    ruta = ruta_captura("canada", persona)
    await captura_ajustada(page, ruta, "canada", persona["formato"])
    return ruta


async def consultar_tesoro(page, persona: dict) -> Path:
    """Buscador del sitio de OFAC (Tesoro de EE. UU.): los resultados salen en search.usa.gov."""
    await page.goto(URL_TESORO, wait_until="domcontentloaded", timeout=60000)
    await page.get_by_role("button", name="SEARCH").first.click()
    await buscar_en_sitio(page, page.locator("form[action*='search.usa.gov'] input[name=query]"), persona)
    # Los resultados se dibujan un par de segundos después de cargar.
    await page.get_by_text(re.compile(r"\b\d+ results?\b|no results found", re.I)).first.wait_for(timeout=30000)
    ruta = ruta_captura("tesoro", persona)
    await captura_ajustada(page, ruta, "tesoro", persona["formato"])
    return ruta


async def consultar_insight(page, persona: dict, preguntar_edge) -> Path:
    """InSight Crime. Su verificación («Checking your browser») solo pasa con la ventana a la vista; si pide confirmar
    que es una persona, lo hace el usuario."""
    await documentos.al_frente(page)
    await page.goto(URL_INSIGHT.format(nombre=quote_plus(persona["nombre"])), wait_until="domcontentloaded", timeout=60000)
    cargada = "() => document.title.includes('InSight Crime')"
    try:
        await page.wait_for_function(cargada, timeout=30000, polling=500)
    except PlaywrightTimeoutError:
        verificacion = page.get_by_text(re.compile(r"checking your browser|confirm you are human|i am human", re.I))
        if not await verificacion.filter(visible=True).count():
            raise
        await asyncio.to_thread(preguntar_edge, "InSight Crime: confirma en su ventana que eres una persona y pulsa «Listo».")
        await page.wait_for_function(cargada, timeout=60000, polling=500)
    try:
        await page.wait_for_load_state("load", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    # Su aviso de cookies no se va al cerrarlo (al sitio le falta ese estilo) y tocar la página abre encima la búsqueda
    # de Jetpack: no se toca nada y el aviso (con su pestaña «Manage consent») se oculta para la captura.
    await page.add_style_tag(content=".cmplz-cookiebanner, .cmplz-manage-consent { display: none !important; }")
    ruta = ruta_captura("insight", persona)
    await captura_ajustada(page, ruta, "insight", persona["formato"])
    return ruta


async def consultar(page, persona: dict, nombre: str = "Procuraduría") -> Path:
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
    # Página de "problema técnico" (formulario de incidentes): también es un resultado válido para la captura.
    incidente = form.get_by_text(re.compile(r"se ha presentado un problema t[eé]cnico", re.I)).first
    incidente_pagina = page.get_by_text(re.compile(r"se ha presentado un problema t[eé]cnico", re.I)).first

    async def cargar_formulario() -> bool:
        """Carga el formulario; False si el portal muestra de entrada la página de problema técnico."""
        # El portal a veces tarda más de 30 s en cargar el formulario.
        await page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        tipo_id = form.locator("#ddlTipoID")
        await tipo_id.or_(incidente).first.wait_for(timeout=90000)
        if await incidente.is_visible():
            return False
        await tipo_id.select_option(label=TIPOS[persona["tipo"].upper()], timeout=90000)
        await form.locator("#txtNumID").fill(numero)
        # La pregunta puede cambiar tras el postback del tipo de ID.
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        return True

    estado = None if await cargar_formulario() else "incidente"
    pregunta = None
    for intento in range(3):
        if estado == "incidente":
            break
        await pregunta_loc.wait_for(state="visible", timeout=30000)
        pregunta = (await pregunta_loc.inner_text()).strip()
        respuesta = resolver_pregunta(pregunta, numero, persona["nombre"])
        log(f"{nombre}, pregunta: {pregunta!r} -> {respuesta!r}")
        await respuesta_loc.fill(respuesta)
        texto_inicial = await cuerpo.inner_text(timeout=10000)
        await form.locator("#btnConsultar").click()
        inicio = time.monotonic()
        siguiente_aviso = inicio + 60
        estado = None
        texto_previo = texto_inicial
        texto_estable_desde = None
        while time.monotonic() - inicio < ESPERA_PROCURADURIA:
            if time.monotonic() >= siguiente_aviso:
                log(f"{nombre}: sigue consultando ({round((time.monotonic() - inicio) / 60)} min); se espera sin recargar.")
                siguiente_aviso += 60
            try:
                if await datos_ciudadano.is_visible():
                    estado = "resultado"
                    break
                if await no_registrado.is_visible():
                    estado = "sin_registro"
                    break
                if await incidente.is_visible() or await incidente_pagina.is_visible():
                    estado = "incidente"
                    break
                if await error_respuesta.is_visible():
                    estado = "respuesta_incorrecta"
                    break
                texto_actual = await cuerpo.inner_text(timeout=2000)
            except PlaywrightError:
                # El marco se está recargando con la respuesta.
                await page.wait_for_timeout(500)
                continue
            # En blanco mientras llega la respuesta: con el portal lento duró más de 15 s y se capturó así.
            if not texto_actual.strip() or re.search(r"consultando\s*,?\s*por favor espere|cargando", texto_actual, re.I):
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
                raise ValueError(f"{nombre} rechazó tres respuestas de verificación.")
            log(f"{nombre} rechazó la respuesta; se resolverá el nuevo reto.")
            continue
        if estado is None:
            raise FueraDeServicio(f"el portal no mostró el resultado en {ESPERA_PROCURADURIA // 60} minutos")
        if estado == "resultado_estable":
            log(f"{nombre}: contenido de resultado estable aunque cambió el rótulo del portal.")
        break
    if estado == "incidente":
        log(f"{nombre}: el portal mostró 'problema técnico' (formulario de incidentes); se captura como fuera de servicio.")
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    # Al dar el resultado, el portal cambia la pregunta y deja la respuesta anterior en la casilla:
    # se escribe (sin enviar) la respuesta de la pregunta nueva para que la captura no las muestre cruzadas.
    try:
        nueva = pregunta if estado == "incidente" else (await pregunta_loc.inner_text(timeout=5000)).strip()
    except PlaywrightTimeoutError:
        nueva = pregunta
    if nueva != pregunta:
        try:
            respuesta = resolver_pregunta(nueva, numero, persona["nombre"])
        except ValueError:
            respuesta = ""
        log(f"{nombre}: el portal cambió la pregunta a {nueva!r}; se escribe {respuesta!r} para la captura.")
        await respuesta_loc.fill(respuesta)
        await respuesta_loc.evaluate("e => e.blur()")

    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
    await page.screenshot(path=str(ruta), full_page=True)
    if estado == "incidente":
        raise FueraDeServicio("el portal mostró el formulario de atención de incidentes (problema técnico)", ruta)
    return ruta


async def consultar_procuraduria(page, persona: dict) -> Path:
    """Si el portal se queda consultando, una segunda consulta en otra pestaña; vale la primera que responda."""
    inicio = time.monotonic()
    primera = asyncio.create_task(consultar(page, persona))
    await asyncio.wait({primera}, timeout=SEGUNDA_PROCURADURIA)
    if primera.done():
        return primera.result()
    log(f"Procuraduría: no respondió en {SEGUNDA_PROCURADURIA} s; se lanza una segunda consulta sin recargar la primera.")
    contexto = await page.context.browser.new_context(viewport=VIEWPORT_PROCURADURIA, ignore_https_errors=True,
                                                      user_agent=USER_AGENT)
    segunda = asyncio.create_task(consultar(await contexto.new_page(), persona, "Procuraduría (2.ª consulta)"))
    pendientes, error = {primera, segunda}, None
    try:
        while pendientes:
            restante = ESPERA_PROCURADURIA - (time.monotonic() - inicio)
            hechas, pendientes = await asyncio.wait(pendientes, timeout=max(0, restante),
                                                    return_when=asyncio.FIRST_COMPLETED)
            if not hechas:
                break
            for tarea in hechas:
                if tarea.exception() is None:
                    return tarea.result()
                error = error or tarea.exception()
        # El formulario de incidentes ya es el resultado (fuera de servicio) aunque la otra consulta siga esperando.
        if error and (not pendientes or isinstance(error, FueraDeServicio)):
            raise error
        raise FueraDeServicio(f"el portal no mostró el resultado en {ESPERA_PROCURADURIA // 60} minutos")
    finally:
        for tarea in (primera, segunda):
            tarea.cancel()
        await asyncio.gather(primera, segunda, return_exceptions=True)
        await contexto.close()


def ventana_consola() -> int | None:
    """Ventana de la consola, para darle el foco al preguntar; None si la terminal no tiene una propia."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    # Windows Terminal y la terminal de VS Code no exponen una ventana propia.
    if not hwnd or not ctypes.windll.user32.IsWindowVisible(hwnd):
        return None
    # Si quedó fija encima de todo por la versión anterior (bloqueaba la parte baja de la pantalla), se suelta.
    HWND_NOTOPMOST, SWP_NOSIZE_NOMOVE_NOACTIVATE = wintypes.HWND(-2), 0x0013
    ctypes.windll.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOSIZE_NOMOVE_NOACTIVATE)
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
    return leer_respuesta(mensaje).strip()


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
    procuraduria, simit, libreta, grupo, eeuu, onu, eurlex, gallagher, pdfs = await asyncio.gather(
        automatico.new_context(viewport=VIEWPORT_PROCURADURIA, **opciones),
        *(automatico.new_context(viewport=VIEWPORT, **opciones) for _ in range(7)),
        # Más estrecha: estas páginas van a una hoja propia y así el texto queda más grande.
        automatico.new_context(viewport={"width": 1024, "height": 768}, **opciones),
    )
    r = {
        "navegadores": (automatico, visible),
        "procuraduria": await procuraduria.new_page(),
        "simit": await simit.new_page(),
        "libreta": await libreta.new_page(),
        "grupo": grupo,
        "eeuu": eeuu,
        "gallagher": gallagher,
        "documentos": pdfs,
        "ventanas": {},
        "precargas": {},
    }
    # Sitios con captcha: cada uno en su ventana, minimizada mientras se llena; sale al frente en su turno.
    async def ventana_minimizada():
        page = await (await visible.new_context(no_viewport=True, **opciones)).new_page()
        await estado_ventana(page, "minimized")
        return page

    claves = ("runt", "antecedentes", "registraduria", "inhabilidades")
    r["ventanas"] = dict(zip(claves, await asyncio.gather(*(ventana_minimizada() for _ in claves))))

    async def precargar_despues_del_runt(page, url):
        # El RUNT es el primer captcha y su página es la más pesada: las demás cargan cuando termina.
        await asyncio.gather(r["precargas"]["runt"], return_exceptions=True)
        await documentos.precargar(page, url)

    r["precargas"]["runt"] = asyncio.create_task(precargar_runt(r["ventanas"]["runt"]))
    for clave, url in (("antecedentes", documentos.URL_ANTECEDENTES), ("registraduria", documentos.URL_REGISTRADURIA),
                       ("inhabilidades", documentos.URL_INHABILIDADES)):
        r["precargas"][clave] = asyncio.create_task(precargar_despues_del_runt(r["ventanas"][clave], url))
    r["consolidado"] = Documento(await onu.new_page(), cargar_consolidado, "consolidado", "Documento consolidado")
    r["consolium"] = Documento(await eurlex.new_page(), cargar_eurlex, "consolium", "EUR-Lex")
    return r


async def capturar_caida(page, clave: str, persona: dict) -> Path:
    """La página caída para el informe: en PDF si el sitio entrega un documento; si no, una captura con la proporción
    de su recuadro y un ancho en el que el aviso de error se lee bien."""
    try:
        # La página de error del navegador escribe su texto un instante después de aparecer; minimizada no hay
        # cuadros de animación, por eso se revisa cada 200 ms.
        await page.wait_for_function("() => document.body?.innerText.trim()", timeout=5000, polling=200)
    except PlaywrightError:
        pass
    if clave in documentos.ORDEN:
        return await documentos.pdf_de_pagina(page, documentos.ruta_documento(clave, persona))
    CAPTURAS.mkdir(exist_ok=True)
    ruta = CAPTURAS / f"{clave}_{re.sub(r'\D', '', persona['numero'])}_{datetime.now():%Y%m%d_%H%M%S}.png"
    tamano = {"width": 900, "height": round(900 * proporcion_zona(clave, persona["formato"]))}
    original = page.viewport_size
    if original:
        await page.set_viewport_size(tamano)
        try:
            await page.screenshot(path=str(ruta))
        finally:
            await page.set_viewport_size(original)
        return ruta
    # Ventana visible (sin pantalla virtual de Playwright).
    cdp = await page.context.new_cdp_session(page)
    try:
        await cdp.send("Emulation.setDeviceMetricsOverride", {**tamano, "deviceScaleFactor": 0, "mobile": False})
        await page.screenshot(path=str(ruta))
    finally:
        await cdp.send("Emulation.clearDeviceMetricsOverride")
        await cdp.detach()
    return ruta


async def con_caida(page, clave: str, persona: dict, consulta) -> Path:
    """Espera la consulta; si el sitio está caído, lanza FueraDeServicio con la página caída guardada."""
    try:
        ruta = await consulta
    except FueraDeServicio as e:
        if e.captura is None:
            try:
                if e.direccion:
                    await documentos.aviso_tiempo_agotado(page, e.direccion)
                e.captura = await capturar_caida(page, clave, persona)
            except PlaywrightError as falla:
                log(f"{clave}: no se pudo guardar la página caída ({str(falla).splitlines()[0]}).")
        raise
    except Exception as e:
        try:
            motivo, direccion = await documentos.motivo_caida(page, e)
        except PlaywrightError:
            motivo = None
        if not motivo:
            raise
        captura = None
        try:
            if direccion:
                await documentos.aviso_tiempo_agotado(page, direccion)
            captura = await capturar_caida(page, clave, persona)
        except PlaywrightError as falla:
            log(f"{clave}: no se pudo guardar la página caída ({str(falla).splitlines()[0]}).")
        raise FueraDeServicio(motivo, captura) from e
    # Tras enviar el formulario, la página de error del navegador o del servidor se imprimiría como el documento.
    if clave in documentos.ORDEN and (motivo := await documentos.caida_en_pagina(page)):
        raise FueraDeServicio(motivo, ruta)
    return ruta


async def en_pestana_nueva(contexto, consulta, persona: dict, clave: str) -> Path:
    page = await contexto.new_page()
    try:
        return await con_caida(page, clave, persona, consulta(page, persona))
    finally:
        await page.close()


CONSULTAS_CHROME = {
    "fbi": consultar_fbi,
    "consilium": consultar_consilium,
    "terroristas": consultar_terroristas,
    "justicia": consultar_justicia,
}


def abrir_paginas_chrome(persona: dict) -> set[int] | None:
    """FBI y Consilium solo cargan en Chrome: se abren al empezar, cada una en su ventana minimizada, y cargan
    mientras se resuelven los captchas; al final solo queda capturarlas. Devuelve las ventanas que ya había."""
    with PANTALLA:
        antes = abrir_en_ventanas([url_fbi(persona), url_consilium(persona)])
    if antes is None:
        log("Chrome: no se reconoce el navegador por defecto; FBI y Consilium se abrirán al capturarlas.")
    return antes


def consultas_edge(persona: dict, preguntar, claves: list[str], antes: set[int] | None,
                   caidos: set[str]) -> dict[str, Path]:
    """Páginas que bloquean el navegador automático: se capturan en el navegador del usuario, una tras otra."""
    capturas = {}
    for clave in claves:
        avisar("sitio", clave=clave, estado="en_curso")
        try:
            # State.gov y Justice llegan aquí solo si fallaron sin ventana: se abren ahora.
            capturas[clave] = CONSULTAS_CHROME[clave](persona, preguntar, antes if clave in ("fbi", "consilium") else None)
            log(f"Captura {clave}: {capturas[clave]}")
            avisar("sitio", clave=clave, estado="listo")
        except FueraDeServicio as e:
            capturas[clave] = e.captura
            caidos.add(clave)
            log(f"FUERA DE SERVICIO {clave}: {e}; en el informe va la página caída: {e.captura}")
            avisar("sitio", clave=clave, estado="fuera_servicio", detalle=str(e))
        except Exception as e:
            log(f"ERROR {clave}: {e}")
            avisar("sitio", clave=clave, estado="error", detalle=str(e))
    return capturas


def minutos(segundos: float) -> str:
    segundos = round(segundos)
    return f"{segundos // 60}:{segundos % 60:02d}"


async def consultar_persona(persona: dict, r: dict, preguntar, preguntar_edge) -> tuple[dict[str, Path], set[str]]:
    """Las capturas de cada sitio y los sitios fuera de servicio (su captura es la página caída)."""
    inicio = time.monotonic()
    capturas: dict[str, Path] = {}
    caidos: set[str] = set()
    limite = asyncio.Semaphore(limite_concurrencia())
    # Un captcha a la vez: mientras se resuelve uno, los demás sitios ya se están llenando o procesando el suyo.
    turnos = documentos.Turnos()
    listos = {clave: asyncio.Event() for clave in r["ventanas"]}
    antes_chrome = None
    # Si el navegador automático no puede con State.gov o Justice, se capturan en Chrome como antes.
    a_chrome = []

    async def abrir_en_chrome():
        nonlocal antes_chrome
        try:
            antes_chrome = await asyncio.to_thread(abrir_paginas_chrome, persona)
        except Exception as e:
            log(f"Chrome: no se pudieron abrir de antemano FBI y Consilium ({e}); se abrirán al capturarlas.")

    apertura_chrome = asyncio.create_task(abrir_en_chrome())

    async def registrar(clave: str, consulta):
        avisar("sitio", clave=clave, estado="en_curso")
        try:
            capturas[clave] = await consulta
            log(f"Captura {clave}: {capturas[clave]}")
            avisar("sitio", clave=clave, estado="listo")
        except FueraDeServicio as e:
            caidos.add(clave)
            if e.captura:
                capturas[clave] = e.captura
            log(f"FUERA DE SERVICIO {clave}: {e}; en el informe va la página caída: {e.captura or 'no se pudo guardar'}")
            avisar("sitio", clave=clave, estado="fuera_servicio", detalle=str(e))
        except Exception as e:
            # SIMIT, Procuraduría y RUNT cortan las conexiones que llegan por VPN.
            if re.search(r"ERR_CONNECTION_(CLOSED|RESET)", str(e)):
                e = "el sitio cerró la conexión. Si estás con VPN, apágala: este sitio rechaza las conexiones por VPN."
            log(f"ERROR {clave}: {e}")
            avisar("sitio", clave=clave, estado="no_aplica" if "no aplica" in str(e) else "error", detalle=str(e))

    async def automatica(clave: str, funcion, page, *args):
        async with limite:
            await registrar(clave, con_caida(page, clave, persona, funcion(page, *args)))

    async def grupo_rapido():
        # Cada una tarda pocos segundos: en serie terminan antes que SIMIT, Procuraduría y RUNT.
        async with limite:
            for documento in (r["consolidado"], r["consolium"]):
                await registrar(documento.clave,
                                con_caida(documento.page, documento.clave, persona, documento.buscar(persona)))
            for clave, consulta in (("ofac", consultar_ofac), ("govuk", consultar_govuk), ("eumostwanted", consultar_eumostwanted)):
                await registrar(clave, en_pestana_nueva(r["grupo"], consulta, persona, clave))

    async def grupo_documentos():
        # Sitios sin captcha que entregan un PDF (páginas 14, 15 y 18).
        async with limite:
            for clave, consulta in (("bdme", documentos.consultar_bdme), ("contraloria", documentos.consultar_contraloria),
                                    ("rnmc", documentos.consultar_rnmc)):
                await registrar(clave, en_pestana_nueva(r["documentos"], consulta, persona, clave))

    async def grupo_gallagher(consultas):
        # Los sitios que solo están en el formato de GALLAGHER.
        async with limite:
            for clave, consulta in consultas:
                await registrar(clave, en_pestana_nueva(r["gallagher"], consulta, persona, clave))

    async def grupo_eeuu():
        async with limite:
            for clave, url, titulo in (("terroristas", url_terroristas(persona), TITULO_TERRORISTAS),
                                       ("justicia", url_justicia(persona), TITULO_JUSTICIA)):
                avisar("sitio", clave=clave, estado="en_curso")
                page = await r["eeuu"].new_page()
                try:
                    capturas[clave] = await capturar_sin_ventana(page, persona, clave, url, titulo)
                    log(f"Captura {clave}: {capturas[clave]}")
                    avisar("sitio", clave=clave, estado="listo")
                except Exception as e:
                    log(f"{clave}: no cargó en el navegador automático ({str(e).splitlines()[0]}); se capturará en Chrome.")
                    a_chrome.append(clave)
                finally:
                    await page.close()

    eeuu = asyncio.create_task(grupo_eeuu())

    async def con_captcha(clave: str, consulta, *args):
        async def ejecutar():
            # Si la precarga sigue en curso se espera; si falló, la consulta vuelve a cargar la página.
            precarga = r["precargas"][clave]
            esperada = not precarga.done()
            await asyncio.gather(precarga, return_exceptions=True)
            # Si el sitio no respondió a la precarga mientras se la esperaba, ya está caído: no se espera otra carga.
            error = None if precarga.cancelled() else precarga.exception()
            caido = esperada and isinstance(error, PlaywrightTimeoutError) and str(error).startswith("Page.goto:")
            for intento in range(2):
                page = r["ventanas"][clave]
                if page.is_closed():
                    # Se cerró la ventana: se abre otra en el mismo contexto.
                    page = r["ventanas"][clave] = await page.context.new_page()
                    await estado_ventana(page, "minimized")
                try:
                    return await con_caida(page, clave, persona, precarga if caido else
                                           consulta(page, persona, *args, turnos, listos[clave]))
                except FueraDeServicio:
                    raise
                except Exception:
                    if page.is_closed() and not intento:
                        log(f"{clave}: se cerró su ventana durante la consulta; se abre otra y se repite.")
                        continue
                    # Lo que mostraba la ventana al fallar, para poder ver la causa.
                    ruta = CAPTURAS / f"error_{clave}_{datetime.now():%Y%m%d_%H%M%S}.png"
                    try:
                        await page.screenshot(path=str(ruta), timeout=10000)
                        log(f"{clave}: pantalla del error guardada en {ruta.name}")
                    except PlaywrightError:
                        pass
                    raise

        try:
            await registrar(clave, ejecutar())
        finally:
            listos[clave].set()
            # Se minimiza al terminar para que las ventanas no se acumulen en pantalla.
            if not r["ventanas"][clave].is_closed():
                try:
                    await estado_ventana(r["ventanas"][clave], "minimized")
                except PlaywrightError:
                    pass

    async def insight():
        # Su verificación solo pasa con la ventana a la vista: va en el navegador visible, cuando el bot tiene la pantalla.
        contexto = await r["navegadores"][1].new_context(viewport=VIEWPORT)
        page = await contexto.new_page()
        try:
            return await con_caida(page, "insight", persona, consultar_insight(page, persona, preguntar_edge))
        finally:
            await contexto.close()

    async def edge():
        # Empieza cuando ya se enviaron todos los captchas; uno que haya que repetir y ya esté en la fila pasa antes.
        await asyncio.gather(*(evento.wait() for evento in listos.values()))
        log(f"Tiempo: captchas enviados a los {minutos(time.monotonic() - inicio)}")
        await asyncio.gather(apertura_chrome, eeuu, return_exceptions=True)
        async with turnos.pantalla():
            desde = time.monotonic()
            claves = ["fbi", "consilium", *a_chrome]
            avisar("capturando", claves=claves)
            try:
                # Las que no se pudieron abrir de antemano se abren al capturarlas, como antes.
                capturas.update(await asyncio.to_thread(consultas_edge, persona, preguntar_edge, claves, antes_chrome,
                                                        caidos))
            finally:
                avisar("capturando", claves=[])
            log(f"Tiempo: capturas en Chrome {minutos(time.monotonic() - desde)}")
            if persona["formato"] == "Gallagher":
                await registrar("insight", insight())

    async def fase(nombre: str, *tareas):
        await asyncio.gather(*tareas)
        log(f"Tiempo: {nombre} a los {minutos(time.monotonic() - inicio)}")

    # Las más largas primero: el límite deja esperando a las últimas en un equipo débil.
    await asyncio.gather(
        fase(
            "consultas automáticas listas",
            automatica("procuraduria", consultar_procuraduria, r["procuraduria"], persona),
            automatica("simit", consultar_simit, r["simit"], persona, preguntar),
            grupo_rapido(),
            # A las mujeres no se les consulta la libreta militar.
            *([automatica("libreta", consultar_libreta, r["libreta"], persona)] if persona["sexo"] == "Hombre" else []),
            eeuu,
            grupo_documentos(),
            # La Junta de Contadores es la más lenta: va con las que tardan poco.
            *([grupo_gallagher((("contadores", consultar_contadores), ("dian", consultar_dian))),
               grupo_gallagher((("samm", consultar_samm), ("bid", consultar_bid), ("canada", consultar_canada),
                                ("tesoro", consultar_tesoro)))]
              if persona["formato"] == "Gallagher" else []),
        ),
        con_captcha("runt", consultar_runt, preguntar),
        con_captcha("antecedentes", documentos.consultar_antecedentes),
        con_captcha("registraduria", documentos.consultar_registraduria, preguntar),
        con_captcha("inhabilidades", documentos.consultar_inhabilidades),
        edge(),
    )
    return capturas, caidos


async def procesar(persona: dict, apertura: asyncio.Task, preguntar, preguntar_edge) -> Path | None:
    """Consulta a la persona en todos los sitios y arma su informe; devuelve el PDF (None si no hubo capturas)."""
    inicio = time.monotonic()
    log(
        f"Datos: {persona['nombre']} | {persona['tipo']} {persona['numero']} | {persona['empresa']} | "
        f"consulta {persona['fecha_consulta']:%d/%m/%Y} por {persona['consultor']} | "
        f"expedición {persona['fecha_expedicion']:%d/%m/%Y} | {persona['sexo']} | "
        f"apellido RUNT: {primer_apellido(persona['nombre'])}"
    )
    capturas, caidos = await consultar_persona(persona, await apertura, preguntar, preguntar_edge)
    ruta = insertar_en_pdf(capturas, persona, caidos) if capturas else None
    if ruta:
        log(f"PDF: {ruta}")
    log(f"Tiempo total: {minutos(time.monotonic() - inicio)}")
    return ruta


async def cerrar_navegadores(apertura: asyncio.Task):
    apertura.cancel()
    recursos = (await asyncio.gather(apertura, return_exceptions=True))[0]
    if isinstance(recursos, dict):
        for tarea in (recursos["consolidado"].carga, recursos["consolium"].carga, *recursos["precargas"].values()):
            tarea.cancel()
            await asyncio.gather(tarea, return_exceptions=True)
        for navegador in recursos["navegadores"]:
            await navegador.close()


async def principal():
    consola = ventana_consola()

    # La imagen del captcha y las opciones son para la ventana; la consola tiene el texto.
    async def preguntar(mensaje: str, **_) -> str:
        return await asyncio.to_thread(preguntar_en_consola, mensaje, consola)

    # Las capturas de Edge ya tienen la pantalla cuando preguntan.
    preguntar_edge = functools.partial(preguntar_en_consola, consola=consola, pantalla_tomada=True)

    async with async_playwright() as p:
        apertura = asyncio.create_task(abrir_navegadores(p))
        try:
            while True:
                linea = await asyncio.to_thread(
                    leer_respuesta,
                    "\nPega la fila de Excel (nombre, documento, empresa, fecha consulta, quien consulta, "
                    "fecha expedicion, sexo H/M) o Enter para salir: ",
                )
                if not linea.strip():
                    break
                try:
                    persona = parsear(linea)
                except ValueError as e:
                    log(f"ERROR: {e}")
                    continue
                await procesar(persona, apertura, preguntar, preguntar_edge)
        finally:
            await cerrar_navegadores(apertura)


def main():
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()