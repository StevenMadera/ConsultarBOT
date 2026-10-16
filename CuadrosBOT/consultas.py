"""Consulta de una persona en la libreta militar, el SIMIT y el RNMC; cada una devuelve los datos para la observación."""
import asyncio
import json
import re
import time

import pymupdf
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from comun import log
from observacion import NO_OBLIGADO, Anotacion, Libreta, Simit
from tabla import Fila, normalizar

URL_LIBRETA = "https://definicion.libretamilitar.mil.co/consulta-estado-situacion-militar"
URL_SIMIT = "https://www.fcm.org.co/simit/#/home-public"
URL_RNMC = "https://srvcnpc.policia.gov.co/PSC/frm_cnp_consulta.aspx"
TIPOS_LIBRETA = {"CC": "cc", "TI": "ti"}
TIPOS_RNMC = {"CC": "55", "CE": "57", "TI": "56", "NIT": "1"}

# null mientras el SIMIT valida o carga, o mientras sigue a la vista el resultado de la cédula anterior.
SIMIT_ESTADO_JS = r"""cedula => {
    const visible = sel => [...document.querySelectorAll(sel)].some(
        e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    );
    if (visible('#whcModal') || visible('#loader.active')) return null;
    if (visible('#modal-multiples-personas')) return 'varios';
    const texto = document.body.innerText;
    if (/Comparendos:\s*\d/.test(texto) && new RegExp('\\b' + cedula + '\\b').test(texto)) return 'resultado';
    if (visible('.modal.show, .swal2-popup, [role=alertdialog]')) return 'mensaje';
    return null;
}"""
RNMC_ESTADO_JS = r"""() => {
    const boton = document.querySelector('#ctl00_ContentPlaceHolder3_btn_salir_modal');
    if (boton && boton.offsetParent !== null) return 'error';
    if (document.querySelector('#gv_expedientes')) return 'anotaciones';
    if (/no tiene medidas correctivas/i.test(document.body.innerText)) return 'sin_anotaciones';
    return null;
}"""
RNMC_DETALLE_JS = r"""() => Object.fromEntries(
    ['txt_expediente', 'txt_art', 'txt_numeral', 'txt_relatoHechos', 'txt_descargos'].map(
        k => [k, (document.querySelector('#ctl00_ContentPlaceHolder3_' + k)?.innerText || '').trim()]
    )
)"""


class DatoErrado(Exception):
    """El sitio rechazó un dato de la fila (por ejemplo, la fecha de expedición)."""


async def esperar_estado(page, js: str, segundos: float, arg=None):
    """Lo que devuelva `js` en la página, en cuanto deje de ser null; None si pasan `segundos`.
    Sigue esperando aunque la página se recargue (envíos de formularios ASP.NET)."""
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        try:
            estado = await page.evaluate(js, arg)
            if estado:
                return estado
        except PlaywrightError:
            pass
        await asyncio.sleep(0.5)
    return None


async def minimizar(page):
    cdp = await page.context.new_cdp_session(page)
    try:
        ventana = (await cdp.send("Browser.getWindowForTarget"))["windowId"]
        await cdp.send("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": "minimized"}})
    finally:
        await cdp.detach()


def estado_del_certificado(pdf: bytes) -> str:
    """El valor de "Estado Tarjeta Militar:" del certificado, que va a la derecha en el mismo renglón."""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        lineas = [(n, linea["bbox"], "".join(s["text"] for s in linea["spans"]).strip())
                  for n, pagina in enumerate(doc) for bloque in pagina.get_text("dict")["blocks"]
                  for linea in bloque.get("lines", [])]
    # Mayores de 50 años: certificado sin estado, "NO se encuentra obligado a definir su situación militar".
    if re.search(r"no\s+se\s+encuentra\s+obligado\s+a\s+definir", " ".join(t for *_, t in lineas), re.IGNORECASE):
        return NO_OBLIGADO
    for n, (x0, y0, x1, y1), texto in lineas:
        if re.match(r"estado\s+tarjeta\s+militar", texto, re.IGNORECASE):
            if texto.split(":", 1)[1:] and texto.split(":", 1)[1].strip():
                return " ".join(texto.split(":", 1)[1].split())
            valor = [t for m, (a, b, _, _), t in lineas if m == n and abs(b - y0) < 3 and a >= x1 - 1 and t]
            if valor:
                return " ".join(" ".join(valor).split())
    raise ValueError("la libreta entregó un certificado de un tipo que no se reconoce; revísalo a mano")


class PaginaLibreta:
    """Va en una ventana minimizada: al navegador sin ventana el cortafuegos del sitio le bloquea la página."""

    def __init__(self, page):
        self.page = page
        self.respuestas: asyncio.Queue = asyncio.Queue()

    async def preparar(self):
        await minimizar(self.page)
        # La respuesta de la consulta es el certificado en PDF o una página con el motivo del rechazo.
        await self.page.route("**/estado-situacion-militar?**", self._interceptar)

    async def _interceptar(self, route):
        try:
            respuesta = await route.fetch()
            cuerpo = await respuesta.body()
        except PlaywrightError as e:
            self.respuestas.put_nowait((None, str(e).encode()))
            await route.abort()
            return
        self.respuestas.put_nowait((respuesta.status, cuerpo))
        await route.fulfill(response=respuesta, body=cuerpo)

    async def _pedir(self, tipo: str, numero: str) -> tuple[int | None, bytes]:
        if self.page.is_closed():
            # Se cerró la ventana: se abre otra.
            self.page = await self.page.context.new_page()
            await self.preparar()
        while not self.respuestas.empty():
            self.respuestas.get_nowait()
        await self.page.goto(URL_LIBRETA, wait_until="domcontentloaded", timeout=60000)
        await self.page.locator("#tipoDocumento").select_option(TIPOS_LIBRETA[tipo])
        await self.page.locator("#numeroDocumento").fill(numero)
        await self.page.get_by_role("button", name="Consultar", exact=True).click()
        try:
            return await asyncio.wait_for(self.respuestas.get(), 60)
        except TimeoutError:
            raise ValueError("la libreta militar no respondió en 1 minuto") from None

    async def consultar(self, fila: Fila, intento: int = 0) -> Libreta:
        if fila.sexo == "M" or fila.tipo not in TIPOS_LIBRETA:
            return Libreta(None, no_aplica=True)
        motivo = ""
        # Quien se inscribió siendo menor puede figurar con tarjeta de identidad (mismo número).
        for tipo in [fila.tipo] + (["TI"] if fila.tipo == "CC" else []):
            estado, cuerpo = await self._pedir(tipo, fila.cedula)
            if estado == 200 and cuerpo[:4] == b"%PDF":
                return Libreta(estado_del_certificado(cuerpo))
            if estado != 400:
                raise ValueError(f"la libreta militar respondió {estado or cuerpo.decode(errors='replace')[:120]}")
            motivo = " ".join(re.sub(r"<[^>]+>", " ", cuerpo.decode("utf-8", "replace")).split())
        log(f"Libreta {fila.cedula}: sin certificado ({motivo}).")
        return Libreta(None)


class PaginaSimit:
    def __init__(self, page):
        self.page = page
        self.carga: asyncio.Task | None = None
        self.recargar = False

    async def abrir(self):
        """Carga el SIMIT y espera su validación de seguridad (unos 20 s)."""
        page = self.page
        log("SIMIT: cargando el sitio y su validación de seguridad (unos 20 s)…")
        # Ya en el SIMIT, goto solo cambia el hash de la página y no recarga.
        ya_abierto = page.url.startswith(URL_SIMIT.split("#")[0])
        await page.goto(URL_SIMIT, wait_until="domcontentloaded", timeout=60000)
        if ya_abierto:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
        validacion = page.locator("#whcModal")
        try:
            await validacion.wait_for(state="visible", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        try:
            await validacion.wait_for(state="hidden", timeout=120000)
        except PlaywrightTimeoutError:
            raise ValueError("el SIMIT no terminó su validación de seguridad en 2 minutos") from None
        try:
            await page.locator("button.close:visible").first.click(timeout=5000)
        except PlaywrightTimeoutError:
            pass
        self.recargar = False

    async def consultar(self, fila: Fila, elegir, intento: int = 0) -> Simit:
        if self.carga:
            error = (await asyncio.gather(self.carga, return_exceptions=True))[0]
            if isinstance(error, Exception):
                log(f"SIMIT: la primera carga falló ({str(error).splitlines()[0] if str(error) else error!r}); "
                    "se carga de nuevo.")
            self.carga = None
        try:
            return await self._consultar(fila, elegir)
        except Exception:
            self.recargar = True
            raise

    async def _consultar(self, fila: Fila, elegir) -> Simit:
        page = self.page
        caja = page.locator("#txtBusqueda")
        if self.recargar or not await caja.is_visible():
            await self.abrir()
        await caja.fill(fila.cedula)
        # Desde el estado de cuenta de la cédula anterior se busca la siguiente sin recargar.
        if "estado-cuenta" in page.url:
            await caja.press("Enter")
        else:
            await page.locator("#consultar").click(timeout=120000)
        estado = await esperar_estado(page, SIMIT_ESTADO_JS, 120, fila.cedula)
        if estado == "varios":
            await self._elegir_persona(fila, elegir)
            estado = await esperar_estado(page, SIMIT_ESTADO_JS, 120, fila.cedula)
        if estado is None:
            raise ValueError("el SIMIT no mostró el resultado en 2 minutos")
        if estado == "mensaje":
            aviso = await page.locator(".modal.show, .swal2-popup, [role=alertdialog]").first.inner_text()
            raise ValueError(f"el SIMIT mostró un aviso: {' '.join(aviso.split())[:150]}")
        texto = await page.evaluate("document.body.innerText")

        def cantidad(etiqueta: str) -> int:
            hallado = re.search(etiqueta + r":\s*(\d+)", texto)
            if not hallado:
                raise ValueError(f"el resumen del SIMIT no trae «{etiqueta}»")
            return int(hallado[1])

        total = re.search(r"Total:\s*\$\s*([\d.,]+)", texto)
        return Simit(cantidad("Comparendos"), cantidad("Multas"), cantidad("Acuerdos de pago"),
                     f"$ {total[1]}" if total else "$ 0")

    async def _elegir_persona(self, fila: Fila, elegir):
        """Varias personas con el mismo documento: la del nombre; si no se distingue, la elige el usuario."""
        dialogo = self.page.locator("#modal-multiples-personas")
        radios = dialogo.get_by_role("radio")
        etiquetas, puntos = [], []
        palabras = normalizar(fila.nombre).split()
        for i in range(await radios.count()):
            etiqueta = (await radios.nth(i).evaluate(
                "e => e.labels?.[0]?.innerText || e.getAttribute('aria-label') || e.parentElement?.innerText || ''"
            )).strip()
            etiquetas.append(" ".join(etiqueta.split()))
            # El SIMIT muestra el nombre tapado: "JH** FR*** CONT*****".
            prefijos = [normalizar(p) for p in re.findall(r"([A-ZÁÉÍÓÚÜÑ]{2,})\*+", etiqueta.upper())]
            iguales = sum(any(p.startswith(prefijo) for p in palabras) for prefijo in prefijos)
            puntos.append(iguales / len(prefijos) if prefijos else 0)
        mejor = max(puntos, default=0)
        candidatos = [i for i, p in enumerate(puntos) if p == mejor and p >= 0.75]
        if len(candidatos) == 1:
            eleccion = candidatos[0]
        else:
            eleccion = await elegir(f"El SIMIT encontró varias personas con la cédula {fila.cedula}. "
                                    f"Elige la que corresponde a {fila.nombre or 'la persona de la fila'}:", etiquetas)
            if eleccion is None:
                raise ValueError("no se eligió la persona entre las que mostró el SIMIT")
        radio = radios.nth(eleccion)
        radio_id = await radio.get_attribute("id")
        if radio_id:
            await dialogo.locator(f"label[for='{radio_id}']").click()
        else:
            await radio.click(force=True)
        await dialogo.get_by_role("button", name=re.compile(r"continuar", re.IGNORECASE)).click()
        await dialogo.wait_for(state="hidden", timeout=120000)


class PaginaRnmc:
    def __init__(self, page):
        self.page = page

    async def consultar(self, fila: Fila, intento: int = 0) -> tuple[list[Anotacion], str]:
        """Las anotaciones (con el detalle de cada expediente) y el nombre que figura en el RNMC."""
        tipo = TIPOS_RNMC.get(fila.tipo)
        if tipo is None:
            log(f"RNMC {fila.cedula}: no consulta el tipo de documento {fila.tipo}.")
            return [], ""
        page = self.page
        await page.goto(URL_RNMC, wait_until="domcontentloaded", timeout=60000)
        await page.locator("#ctl00_ContentPlaceHolder3_ddlTipoDoc").select_option(tipo)
        fecha = page.locator("#txtFechaexp")
        await fecha.wait_for(state="visible", timeout=15000)
        await fecha.fill(f"{fila.expedicion:%d/%m/%Y}")
        await page.locator("#ctl00_ContentPlaceHolder3_txtExpediente").fill(fila.cedula)
        await page.locator("#ctl00_ContentPlaceHolder3_btnConsultar2").click()
        estado = await esperar_estado(page, RNMC_ESTADO_JS, 60)
        if estado is None:
            raise ValueError("el RNMC no mostró el resultado en 1 minuto")
        if estado == "error":
            mensaje = " ".join((await page.locator(
                "#modalNotificaciones .modal-body, .modal.in .modal-body, .modal.show .modal-body"
            ).first.inner_text()).split())
            # El RNMC valida la cédula y su fecha de expedición contra la Registraduría.
            if re.search(r"fecha de expedici", mensaje, re.IGNORECASE):
                raise DatoErrado("el RNMC (Policía) indica que la fecha de expedición no corresponde a la cédula: "
                                 "revisa la fecha y el número de cédula")
            raise ValueError(f"el RNMC respondió: {mensaje}")
        texto = await page.evaluate("document.body.innerText")
        hallado = re.search(r"Nombre:\s*([^\n]+)", texto)
        nombre = hallado[1].strip().rstrip(".").strip() if hallado else ""
        if estado == "sin_anotaciones":
            return [], nombre

        tabla = await page.locator("#gv_expedientes").evaluate(
            "t => [...t.rows].map(r => [...r.cells].map(c => c.innerText.trim()))")
        titulos = [normalizar(t) for t in tabla[0]]
        columna_fecha = titulos.index("FECHA") if "FECHA" in titulos else None
        filas = [f for f in tabla[1:] if f and re.fullmatch(r"[\w-]*\d[\w-]*", f[0])]
        if await page.locator("#gv_expedientes a[href*='Page$']").count():
            raise ValueError("el RNMC reparte las anotaciones en varias páginas; consúltalo a mano")
        anotaciones = []
        for i, celdas in enumerate(filas):
            expediente = celdas[0]
            # Cada expediente abre su detalle (artículo, numeral, relato, descargos) debajo de la tabla.
            await page.locator(f"#gv_expedientes a[href*=\"ver${i}'\"]").click()
            arg = json.dumps(expediente)
            listo = await esperar_estado(
                page, f"() => document.querySelector('#ctl00_ContentPlaceHolder3_txt_expediente')?.innerText.trim() === {arg}",
                60)
            if not listo:
                raise ValueError(f"el RNMC no mostró el detalle del expediente {expediente}")
            detalle = await page.evaluate(RNMC_DETALLE_JS)
            anotaciones.append(Anotacion(
                expediente=detalle["txt_expediente"] or expediente,
                articulo=detalle["txt_art"],
                numeral=detalle["txt_numeral"],
                relato=detalle["txt_relatoHechos"],
                descargos=detalle["txt_descargos"],
                fecha=celdas[columna_fecha] if columna_fecha is not None and columna_fecha < len(celdas) else "",
            ))
        return anotaciones, nombre
