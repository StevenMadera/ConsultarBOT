"""Captura de una página en Google Chrome (Linux), sin automatizarlo.

FBI y Consilium bloquean los navegadores automatizados: se abren en un Chrome normal, con un perfil propio del bot, y
si hace falta la persona pasa ahí la verificación o acepta las cookies. La imagen se le pide a ese Chrome por su puerto
de depuración local, sin ejecutar nada en la página: no hace falta mover ventanas ni simular teclas, y la ventanita
del bot no sale en la captura.
"""
import base64
import io
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from comun import CAPTURAS, FueraDeServicio, log
from rendimiento import etapa

ESPERA_MINIMA = 2
ESPERA_ESTABLE_MAXIMA = 12
# La verificación automática de Consilium puede tardar más de 20 s antes de mostrar la página.
ESPERA_MAXIMA = 60
TITULOS_VERIFICACION = ("just a moment", "un momento", "verifying", "verificando")
# Página de Chrome maximizado en la pantalla de referencia (1366 x 768 al 100 %), en píxeles. Cada sitio indica el
# zoom con el que ahí se ven el logo, los datos y el resultado; la captura muestra esa misma área en cualquier pantalla.
PAGINA_REFERENCIA = (1366, 649)
# Pasos manuales ya hechos en esta sesión: Chrome recuerda, por ejemplo, el consentimiento de cookies.
_PASOS_HECHOS: set[str] = set()
# Una pregunta al usuario no coincide con una captura: quien tiene la pantalla y pregunta no vuelve a pedirla.
PANTALLA = threading.Lock()
# Chrome solo abre el puerto de depuración con un perfil distinto del habitual; así tampoco se toca el de la persona.
PERFIL = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "PaginasBOT" / "chrome"
NAVEGADORES = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
OPCIONES_CHROME = (
    "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
    # Con el puerto de depuración abierto, Chrome marca navigator.webdriver y Cloudflare (Consilium) no deja pasar.
    "--disable-blink-features=AutomationControlled",
    # Con inicio de sesión automático, Chrome pediría desbloquear el llavero de GNOME al abrir.
    "--password-store=basic",
    # Minimizada o tapada, la página sigue cargando al ritmo normal.
    "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
)
# Las direcciones locales no deben ir por el proxy del sistema.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class _Conexion:
    """WebSocket mínimo al puerto de depuración de Chrome: solo envía órdenes y lee sus respuestas."""

    def __init__(self, url: str, espera: float = 10):
        partes = urlsplit(url)
        self._socket = socket.create_connection((partes.hostname, partes.port), timeout=espera)
        self._resto = b""
        self._id = 0
        clave = base64.b64encode(os.urandom(16)).decode()
        self._socket.sendall(
            f"GET {partes.path} HTTP/1.1\r\nHost: {partes.hostname}:{partes.port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {clave}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        while b"\r\n\r\n" not in self._resto:
            parte = self._socket.recv(4096)
            if not parte:
                raise ConnectionError("Chrome cerró la conexión")
            self._resto += parte
        cabecera, _, self._resto = self._resto.partition(b"\r\n\r\n")
        estado = cabecera.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101" not in estado:
            raise ConnectionError(f"Chrome rechazó la conexión: {estado}")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()

    def _recibir(self, n: int) -> bytes:
        datos = bytearray(self._resto[:n])
        self._resto = self._resto[n:]
        while len(datos) < n:
            parte = self._socket.recv(min(n - len(datos), 1 << 20))
            if not parte:
                raise ConnectionError("Chrome cerró la conexión")
            datos += parte
        return bytes(datos)

    def _enviar(self, codigo: int, datos: bytes):
        mascara = os.urandom(4)
        n = len(datos)
        if n < 126:
            cabeza = bytes((0x80 | codigo, 0x80 | n))
        elif n < 1 << 16:
            cabeza = bytes((0x80 | codigo, 0x80 | 126)) + n.to_bytes(2, "big")
        else:
            cabeza = bytes((0x80 | codigo, 0x80 | 127)) + n.to_bytes(8, "big")
        self._socket.sendall(cabeza + mascara + bytes(b ^ mascara[i & 3] for i, b in enumerate(datos)))

    def _mensaje(self) -> bytes:
        partes = []
        while True:
            primero, segundo = self._recibir(2)
            n = segundo & 0x7F
            if n == 126:
                n = int.from_bytes(self._recibir(2), "big")
            elif n == 127:
                n = int.from_bytes(self._recibir(8), "big")
            mascara = self._recibir(4) if segundo & 0x80 else None
            datos = self._recibir(n)
            if mascara:
                datos = bytes(b ^ mascara[i & 3] for i, b in enumerate(datos))
            codigo = primero & 0x0F
            if codigo == 0x8:
                raise ConnectionError("Chrome cerró la conexión")
            if codigo == 0x9:
                self._enviar(0xA, datos)
                continue
            if codigo == 0xA:
                continue
            partes.append(datos)
            if primero & 0x80:
                return b"".join(partes)

    def enviar(self, metodo: str, parametros: dict | None = None, espera: float = 30) -> dict:
        self._id += 1
        self._enviar(0x1, json.dumps({"id": self._id, "method": metodo, "params": parametros or {}}).encode())
        fin = time.monotonic() + espera
        while True:
            restante = fin - time.monotonic()
            if restante <= 0:
                raise TimeoutError(f"Chrome no respondió a {metodo}")
            self._socket.settimeout(restante)
            respuesta = json.loads(self._mensaje())
            if respuesta.get("id") == self._id:
                if "error" in respuesta:
                    raise RuntimeError(f"{metodo}: {respuesta['error'].get('message')}")
                return respuesta.get("result", {})

    def cerrar(self):
        try:
            self._enviar(0x8, b"")
        except OSError:
            pass
        self._socket.close()


def _es_verificacion(titulo: str) -> bool:
    return any(t in titulo.lower() for t in TITULOS_VERIFICACION)


def _en_blanco(rgb: bytes, ancho: int) -> bool:
    # Debajo de la cabecera (el aviso del FBI y la de Consilium se dibujan antes que el resto) y sin la barra de
    # desplazamiento, todo blanco puro.
    fila = ancho * 3
    filas = len(rgb) // fila
    resto = b"".join(rgb[i * fila:(i + 1) * fila - 90] for i in range(filas // 4, filas))
    return resto.count(255) > len(resto) * 0.995


def _ejecutable() -> str | None:
    """Google Chrome; si no está, Chromium o el Chromium que bajó Playwright."""
    if propio := os.environ.get("PAGINASBOT_CHROME"):
        return propio
    for nombre in NAVEGADORES:
        if ruta := shutil.which(nombre):
            return ruta
    bajados = (Path.home() / ".cache" / "ms-playwright").glob("chromium-*/chrome-linux*/chrome")
    bajados = sorted(bajados, key=lambda p: int(re.sub(r"\D", "", p.parts[-3]) or 0))
    return str(bajados[-1]) if bajados else None


def _json(puerto: int, ruta: str):
    with _LOCAL.open(f"http://127.0.0.1:{puerto}{ruta}", timeout=5) as respuesta:
        return json.loads(respuesta.read())


def _puerto() -> int | None:
    """Puerto de depuración del Chrome del bot; None si no está abierto."""
    try:
        puerto = int((PERFIL / "DevToolsActivePort").read_text().split()[0])
        _json(puerto, "/json/version")
        return puerto
    except (OSError, ValueError, IndexError):
        return None


def _navegador(puerto: int) -> _Conexion:
    return _Conexion(_json(puerto, "/json/version")["webSocketDebuggerUrl"])


def _de_pagina(puerto: int, objetivo: str) -> _Conexion:
    return _Conexion(f"ws://127.0.0.1:{puerto}/devtools/page/{objetivo}")


def _paginas(puerto: int) -> list[dict]:
    return [t for t in _json(puerto, "/json/list") if t.get("type") == "page"]


def _pagina(puerto: int | None, objetivo: str) -> dict | None:
    """Título y dirección de la página `objetivo`; None si su ventana (o Chrome) se cerró."""
    if puerto is None:
        return None
    try:
        return next((t for t in _paginas(puerto) if t.get("id") == objetivo), None)
    except (OSError, ValueError):
        return None


def _iniciar(url: str) -> tuple[int, str]:
    """Abre el Chrome del bot mostrando `url`; devuelve su puerto y el id de esa página."""
    ejecutable = _ejecutable()
    if not ejecutable:
        raise RuntimeError("no se encontró Google Chrome; vuelve a ejecutar el instalador")
    PERFIL.mkdir(parents=True, exist_ok=True)
    (PERFIL / "DevToolsActivePort").unlink(missing_ok=True)
    subprocess.Popen([ejecutable, f"--user-data-dir={PERFIL}", *OPCIONES_CHROME, "--new-window", url],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    fin = time.monotonic() + 30
    while time.monotonic() < fin:
        if puerto := _puerto():
            try:
                if paginas := _paginas(puerto):
                    return puerto, paginas[0]["id"]
            except OSError:
                pass
        time.sleep(0.2)
    raise RuntimeError("Chrome no abrió en 30 s")


def _abrir(url: str, minimizada: bool) -> tuple[int, str]:
    """Abre `url` en una ventana nueva del Chrome del bot (lo abre si hace falta)."""
    puerto = _puerto()
    if puerto is None:
        puerto, objetivo = _iniciar(url)
    else:
        with _navegador(puerto) as chrome:
            objetivo = chrome.enviar("Target.createTarget", {"url": url, "newWindow": True})["targetId"]
    if minimizada:
        _ventana(puerto, objetivo, "minimized")
    return puerto, objetivo


def _ventana(puerto: int, objetivo: str, estado: str):
    """Minimiza la ventana de la página `objetivo`, o la maximiza y la pone al frente."""
    with _navegador(puerto) as chrome:
        ventana = chrome.enviar("Browser.getWindowForTarget", {"targetId": objetivo})["windowId"]
        actual = chrome.enviar("Browser.getWindowBounds", {"windowId": ventana})["bounds"]["windowState"]
        if actual == "minimized" and estado != "minimized":
            # Chrome no pasa de minimizada a maximizada sin restaurarla antes.
            chrome.enviar("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": "normal"}})
        if actual != estado:
            chrome.enviar("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": estado}})
        if estado != "minimized":
            chrome.enviar("Target.activateTarget", {"targetId": objetivo})


def _cerrar(puerto: int, objetivo: str):
    """Cierra la ventana de la consulta. La última queda en blanco y minimizada: si Chrome se cerrara, la siguiente
    captura tendría que esperar a que termine de cerrarse y vuelva a abrir."""
    try:
        paginas = _paginas(puerto)
        if any(t["id"] != objetivo for t in paginas):
            with _navegador(puerto) as chrome:
                chrome.enviar("Target.closeTarget", {"targetId": objetivo})
        elif paginas:
            with _de_pagina(puerto, objetivo) as pagina:
                pagina.enviar("Page.navigate", {"url": "about:blank"})
            _ventana(puerto, objetivo, "minimized")
    except (OSError, RuntimeError) as e:
        log(f"Chrome: no se cerró la ventana de la consulta ({e}).")


def cerrar_chrome():
    """Cierra el Chrome del bot, al cerrar PaginasBOT."""
    if puerto := _puerto():
        try:
            with _navegador(puerto) as chrome:
                chrome.enviar("Browser.close", espera=5)
        except (OSError, RuntimeError):
            pass


def abrir_en_ventanas(urls: list[str]) -> dict[str, str] | None:
    """Abre cada dirección en una ventana nueva del Chrome del bot y la minimiza: cargan mientras se hace lo demás.
    Devuelve {dirección: id de su página}; None si no hay Chrome instalado."""
    if not _ejecutable():
        return None
    abiertas = {url: _abrir(url, minimizada=True) for url in urls}
    # Una ventana recién creada puede ignorar la orden de minimizarse si aún no se mostraba: se repite.
    time.sleep(1)
    for puerto, objetivo in abiertas.values():
        try:
            _ventana(puerto, objetivo, "minimized")
        except (OSError, RuntimeError):
            pass
    return {url: objetivo for url, (_, objetivo) in abiertas.items()}


def _esperar_pagina(puerto: int | None, objetivo: str, titulo: str, nombre: str, preguntar, url: str,
                    precargada: bool) -> tuple[int, str, str | None]:
    """(puerto, página, motivo): la página cuando su título contiene `titulo` (motivo None); si sale la verificación,
    pide pasarla.

    Si el sitio está caído devuelve la página de error del navegador (su título es el nombre del sitio) y el motivo.
    Si la ventana se cerró, la abre de nuevo; si la abierta de antemano no muestra la consulta en 10 s (el FBI a
    veces responde «Page not found»), la carga de nuevo.
    """
    pagina_de_error = re.compile(rf"{re.escape(urlsplit(url).hostname)}(\s|$)", re.I)
    inicio = time.monotonic()
    fin = inicio + ESPERA_MAXIMA
    avisado, caida_desde, recargar, reabierta = False, None, precargada, False
    pagina = None
    while time.monotonic() < fin:
        pagina = _pagina(puerto, objetivo)
        if pagina is None:
            if reabierta:
                raise RuntimeError(f"se cerró la ventana de {nombre}")
            log(f"{nombre}: su ventana se cerró; se abre de nuevo.")
            puerto, objetivo = _abrir(url, minimizada=False)
            reabierta = True
            continue
        titulo_actual = pagina.get("title", "")
        if titulo in titulo_actual:
            return puerto, objetivo, None
        if _es_verificacion(titulo_actual) and not avisado:
            avisado = True
            log(f"{nombre}: verificación de Cloudflare detectada")
            _ventana(puerto, objetivo, "maximized")
            preguntar(f"{nombre}: pasa la verificación en el navegador y pulsa Enter... ")
            fin = time.monotonic() + ESPERA_MAXIMA
        if pagina_de_error.match(titulo_actual) or pagina.get("url", "").startswith("chrome-error:"):
            caida_desde = caida_desde or time.monotonic()
            if time.monotonic() - caida_desde >= 5:
                # Ese título no es un instante de la carga: la página de error se queda.
                return puerto, objetivo, "el navegador mostró que no se puede acceder al sitio"
        else:
            caida_desde = None
        if recargar and not avisado and time.monotonic() - inicio > 10:
            recargar = False
            log(f"{nombre}: su ventana no muestra la página; se carga de nuevo.")
            with _de_pagina(puerto, objetivo) as conexion:
                conexion.enviar("Page.navigate", {"url": url})
        time.sleep(0.2)
    if pagina and pagina.get("title") and not _es_verificacion(pagina["title"]) and pagina.get("url", "").startswith("http"):
        # Terminó de cargar otra página: es lo que el sitio respondió.
        return puerto, objetivo, "el sitio no mostró la página de la consulta"
    raise RuntimeError(f"Chrome no mostró la página de {nombre}")


def _imagen_estable(puerto: int, objetivo: str, nombre: str, zoom: float, espera_minima: float) -> bytes:
    """PNG de la página como se ve en la pantalla de referencia con `zoom`, cuando deja de cambiar."""
    ancho, alto = PAGINA_REFERENCIA
    with _de_pagina(puerto, objetivo) as pagina:
        pagina.enviar("Emulation.setDeviceMetricsOverride", {
            "width": round(ancho / zoom), "height": round(alto / zoom), "deviceScaleFactor": zoom, "mobile": False})
        log(f"{nombre}: zoom {zoom:.0%}, página de {ancho}x{alto} px.")
        try:
            # Los resultados se aplican después de cargar: se captura cuando la página deja de cambiar.
            inicio, previo, iguales = time.monotonic(), None, 0
            while True:
                transcurrido = time.monotonic() - inicio
                png = base64.b64decode(pagina.enviar("Page.captureScreenshot", {"format": "png"})["data"])
                imagen = Image.open(io.BytesIO(png)).convert("RGB")
                actual = imagen.tobytes()
                iguales = iguales + 1 if actual == previo else 0
                previo = actual
                if _en_blanco(actual, imagen.width):
                    # A veces la página tarda en dibujar el contenido (el FBI): en blanco no se captura.
                    if transcurrido >= ESPERA_MAXIMA:
                        log(f"{nombre}: la página siguió en blanco {ESPERA_MAXIMA} s; se captura así.")
                        return png
                elif transcurrido >= ESPERA_ESTABLE_MAXIMA or (transcurrido >= espera_minima and iguales >= 2):
                    return png
                time.sleep(0.4)
        finally:
            try:
                pagina.enviar("Emulation.clearDeviceMetricsOverride", espera=5)
            except (OSError, RuntimeError):
                pass


def capturar_navegador(
    url: str,
    titulo: str,
    nombre: str,
    prefijo: str,
    numero: str,
    paso_manual: str | None = None,
    espera_minima: float = ESPERA_MINIMA,
    preguntar=input,
    antes: dict[str, str] | None = None,
    zoom: float = 1,
) -> Path:
    """Abre `url` en una ventana nueva del Chrome del bot y la captura cuando su título contiene `titulo`.

    Con `antes` (de `abrir_en_ventanas`) la página ya está abierta y cargada: solo se muestra y se captura.
    La página se ve como con `zoom` en la pantalla de referencia, sin importar la pantalla del equipo.
    Si el sitio está caído, captura la página de error del navegador y lanza FueraDeServicio con ella.
    """
    with PANTALLA:
        with etapa("captura"):
            try:
                return _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, antes,
                                 zoom)
            except OSError as e:
                raise RuntimeError(f"Chrome dejó de responder ({e})") from e


def _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, antes, zoom) -> Path:
    numero = re.sub(r"\D", "", numero)
    objetivo = (antes or {}).get(url)
    precargada = objetivo is not None
    if precargada:
        # Cargó mientras se resolvían los captchas: el margen para el JavaScript de la página ya pasó.
        espera_minima = min(espera_minima, ESPERA_MINIMA)
        puerto = _puerto()
    else:
        puerto, objetivo = _abrir(url, minimizada=False)
    puerto, objetivo, caida = _esperar_pagina(puerto, objetivo, titulo, nombre, preguntar, url, precargada)
    try:
        if caida:
            log(f"{nombre}: {caida}; se captura la página caída.")
        elif paso_manual and nombre not in _PASOS_HECHOS:
            _ventana(puerto, objetivo, "maximized")
            preguntar(f"{nombre}: {paso_manual} Pulsa Enter cuando la lista esté visible... ")
            _PASOS_HECHOS.add(nombre)
        # Minimizada, Chrome no dibuja la página: se muestra como en las demás capturas.
        _ventana(puerto, objetivo, "maximized")
        png = _imagen_estable(puerto, objetivo, nombre, zoom, espera_minima)
        CAPTURAS.mkdir(exist_ok=True)
        ruta = CAPTURAS / f"{prefijo}_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
        ruta.write_bytes(png)
        if caida:
            raise FueraDeServicio(caida, ruta)
        return ruta
    finally:
        _cerrar(puerto, objetivo)
