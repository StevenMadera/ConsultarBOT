"""Captura de una página en el navegador por defecto, sin controlarlo.

Cloudflare bloquea los navegadores automatizados, así que la verificación la pasa una persona en su
navegador normal; este módulo solo abre la página y toma la captura de pantalla de su ventana.
"""
import ctypes
import re
import subprocess
import threading
import time
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import mss
import mss.tools
import pygetwindow as gw

from comun import CAPTURAS, FueraDeServicio, log
from rendimiento import etapa

ESPERA_MINIMA = 2
ESPERA_ESTABLE_MAXIMA = 12
# La verificación automática de Consilium puede tardar más de 20 s antes de mostrar la página.
ESPERA_MAXIMA = 60
TITULOS_VERIFICACION = ("just a moment", "un momento", "verifying", "verificando")
# Páginas del bot sin el título de su consulta: el FBI cuando responde «Page not found» y la libreta militar (que
# Chrome vuelve a abrir con la sesión anterior si quedó abierta).
OTROS_TITULOS_DEL_BOT = ("Federal Bureau of Investigation", "Reclutamiento | Ejercito Colombia")
# Ventana principal de Chrome y Edge, y de Firefox.
CLASES_NAVEGADOR = ("Chrome_WidgetWin_1", "MozillaWindowClass")
_ENUM_VENTANAS = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
# Página de Chrome maximizado en la pantalla de referencia (1366 x 768 al 100 %), en píxeles. Cada sitio indica el
# zoom con el que ahí se ven el logo, los datos y el resultado (el que se usaba a mano en Chrome); en otro equipo se
# buscan el zoom y el tamaño de ventana que muestran exactamente lo mismo. Solo cambia la nitidez de la captura.
PAGINA_REFERENCIA = (1366, 649)
# Niveles de zoom de Chrome y Edge: Ctrl + y Ctrl - pasan al siguiente.
NIVELES_ZOOM = (0.25, 1 / 3, 0.5, 2 / 3, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3)
# Más nitidez no hace falta en el PDF y la imagen pesaría mucho (pantallas 4K).
AMPLIACION_MAXIMA = 2
VK_CONTROL, VK_MENU, VK_0, VK_ADD, VK_SUBTRACT, KEYEVENTF_KEYUP = 0x11, 0x12, 0x30, 0x6B, 0x6D, 0x0002


class _Colocacion(ctypes.Structure):
    # WINDOWPLACEMENT: estado (maximizada o no) y tamaño normal de una ventana.
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]
# Pasos manuales ya hechos en esta sesión: Edge recuerda, por ejemplo, el consentimiento de cookies.
_PASOS_HECHOS: set[str] = set()
# Lo que el bot abrió en el navegador del usuario (con él cerrado, Chrome puede abrir además las ventanas de la sesión
# anterior): las ventanas, los títulos y sitios que las reconocen si abren tarde, y las ventanas que ya había antes.
# Al terminar cada consulta y al cerrar el bot no queda ninguna abierta.
_ABIERTAS: set[int] = set()
_MARCAS: set[str] = set()
_PREVIAS: set[int] | None = None
# Una captura necesita el foco y la pantalla: no puede coincidir con otra ni con una pregunta en la consola.
# Quien lo tiene y pregunta al usuario debe hacerlo sin volver a pedirlo.
PANTALLA = threading.Lock()


def _es_verificacion(titulo: str) -> bool:
    return any(t in titulo.lower() for t in TITULOS_VERIFICACION)


def _en_blanco(rgb: bytes, ancho: int) -> bool:
    # Debajo de la cabecera (el aviso del FBI y la de Consilium se dibujan antes que el resto) y sin la barra de
    # desplazamiento, todo blanco puro.
    fila = ancho * 3
    filas = len(rgb) // fila
    resto = b"".join(rgb[i * fila:(i + 1) * fila - 90] for i in range(filas // 4, filas))
    return resto.count(255) > len(resto) * 0.995


def _ventanas_nuevas(antes: set[int]) -> list:
    """Ventanas del navegador que no estaban en `antes`: las que abrió el bot para esta consulta."""
    # El editor también es de clase Chrome y puede tener abierto un archivo con el mismo texto en el nombre.
    return [v for v in gw.getAllWindows() if v._hWnd not in antes and _clase(v._hWnd) in CLASES_NAVEGADOR
            and "Visual Studio Code" not in v.title]


def _esperar_pagina(titulo: str, nombre: str, preguntar, antes: set[int], url: str, precargada: bool,
                    espera: float = ESPERA_MAXIMA, reabrir: bool = True):
    """(ventana, motivo): la ventana nueva cuyo título contiene `titulo` (motivo None); si sale la verificación, pide
    pasarla.

    Se reconoce por el título y no por el orden en que aparece: con el navegador cerrado, las ventanas del FBI y de
    Consilium pueden abrir tarde y en cualquier orden. Si el sitio está caído devuelve la ventana que lo muestra y el
    motivo: la página de error del navegador lleva el nombre del sitio como título. Si la ventana abierta de antemano
    no muestra la página en 10 s (se cerró a mano, o el FBI respondió «Page not found»), la abre de nuevo.
    """
    pagina_de_error = re.compile(rf"{re.escape(urlsplit(url).hostname)}(\s|$)", re.I)
    inicio = time.time()
    fin = inicio + espera
    avisado, caida_desde = False, None
    # Las ventanas que había cuando se abrió la de este sitio: las nuevas desde entonces son solo suyas.
    solo_suyas = None if precargada else antes
    reabrir = precargada and reabrir
    while time.time() < fin:
        nuevas = _ventanas_nuevas(antes)
        encontrada = next((v for v in nuevas if titulo in v.title), None)
        if encontrada:
            _ABIERTAS.add(encontrada._hWnd)
            return encontrada, None
        verificando = next((v for v in nuevas if _es_verificacion(v.title)), None)
        if verificando and not avisado:
            avisado = True
            log(f"{nombre}: verificación de Cloudflare detectada")
            _encima(verificando)
            preguntar(f"{nombre}: pasa la verificación en el navegador y pulsa Enter... ")
            fin = time.time() + espera
        caida = next((v for v in nuevas if pagina_de_error.match(v.title)), None)
        if caida is None:
            caida_desde = None
        elif caida_desde is None:
            caida_desde = time.time()
        elif time.time() - caida_desde >= 5:
            # Ese título no es un instante de la carga: la página de error se queda.
            _ABIERTAS.add(caida._hWnd)
            return caida, "el navegador mostró que no se puede acceder al sitio"
        if reabrir and not avisado and time.time() - inicio > 10:
            reabrir = False
            log(f"{nombre}: su ventana no muestra la página; se abre de nuevo.")
            solo_suyas = abrir_en_ventanas([url])
        time.sleep(0.2)
    if solo_suyas:
        # Una ventana de este sitio que terminó de cargar otra página: es lo que el sitio respondió.
        otra = next((v for v in _ventanas_nuevas(solo_suyas) if v.title and not _es_verificacion(v.title)
                     and not re.match(r"(Sin título|Untitled)\b", v.title)), None)
        if otra:
            _ABIERTAS.add(otra._hWnd)
            return otra, "el sitio no mostró la página de la consulta"
    raise RuntimeError(f"No se encontró la ventana del navegador con la página de {nombre}")


def _navegador_por_defecto() -> str | None:
    """Ejecutable con el que Windows abre los enlaces https."""
    # Se pregunta a Windows: en Windows 11 la clave UserChoice del registro puede seguir diciendo Edge.
    ASSOCF_IS_PROTOCOL, ASSOCSTR_EXECUTABLE = 0x1000, 2
    tamano = wintypes.DWORD(260)
    ruta = ctypes.create_unicode_buffer(tamano.value)
    if ctypes.windll.shlwapi.AssocQueryStringW(ASSOCF_IS_PROTOCOL, ASSOCSTR_EXECUTABLE, "https", "open",
                                               ruta, ctypes.byref(tamano)):
        return None
    return ruta.value or None


def _clase(hwnd: int) -> str:
    clase = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, clase, 64)
    return clase.value


def _dar_foco(hwnd: int) -> bool:
    """Pone la ventana en primer plano: recibe las teclas y Chrome deja de darla por tapada (si no, no dibuja la
    página). True si lo logra."""
    user32 = ctypes.windll.user32
    if user32.GetForegroundWindow() != hwnd:
        user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        # Desde segundo plano Windows no deja dar el foco a otra ventana; con Alt pulsada sí.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    return user32.GetForegroundWindow() == hwnd


def _encima(ventana):
    """Maximiza la ventana y la pone encima de las demás, aunque Windows no deje darle el foco a otro programa."""
    SWP_NOSIZE_NOMOVE, SW_MAXIMIZE = 0x0003, 3
    user32 = ctypes.windll.user32
    hwnd = wintypes.HWND(ventana._hWnd)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    # Con un -1 simple (sin el tipo HWND) Windows ignora la orden.
    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, SWP_NOSIZE_NOMOVE)  # HWND_TOPMOST
    user32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, SWP_NOSIZE_NOMOVE)  # HWND_NOTOPMOST
    _dar_foco(ventana._hWnd)


def _fijar_encima(hwnd: int, fija: bool):
    """Deja la ventana por encima de todas, también de las que se activen después (o la vuelve normal): tapada,
    Chrome deja de dibujar la página y la captura saldría con lo que haya encima."""
    insertar = wintypes.HWND(-1 if fija else -2)  # HWND_TOPMOST / HWND_NOTOPMOST
    ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), insertar, 0, 0, 0, 0, 0x0013)  # sin mover ni activar


def abrir_en_ventanas(urls: list[str]) -> set[int] | None:
    """Abre cada dirección en una ventana nueva del navegador por defecto y las minimiza: cargan mientras se hace lo
    demás. Devuelve las ventanas que ya había (las demás son del bot); None si no se reconoce el navegador."""
    ejecutable = _navegador_por_defecto()
    if not ejecutable:
        return None
    global _PREVIAS
    antes = {v._hWnd for v in gw.getAllWindows()}
    if _PREVIAS is None:
        _PREVIAS = antes
    _MARCAS.update(urlsplit(url).hostname for url in urls)
    for url in urls:
        subprocess.Popen([ejecutable, "--new-window", url])
    # Con el navegador cerrado puede tardar en abrirlas.
    minimizadas = set()
    fin = time.time() + 15
    while len(minimizadas) < len(urls) and time.time() < fin:
        for ventana in _ventanas_nuevas(antes):
            if ventana._hWnd not in minimizadas and ventana.title:
                ventana.minimize()
                minimizadas.add(ventana._hWnd)
        time.sleep(0.2)
    _ABIERTAS.update(minimizadas)
    return antes


def pagina_cargada(url: str, titulo: str, antes: set[int]) -> bool:
    """La ventana abierta de antemano ya muestra la página o la de error del navegador; no toma la pantalla."""
    error = re.compile(rf"{re.escape(urlsplit(url).hostname)}(\s|$)", re.I)
    return any(titulo in v.title or error.match(v.title) for v in _ventanas_nuevas(antes))


def _es_chromium(hwnd: int) -> bool:
    return _clase(hwnd) == "Chrome_WidgetWin_1"


def _area_chrome(hwnd: int) -> tuple[int, int, int, int] | None:
    """Rectángulo (izq, arr, der, abajo) de la página en Chrome y Edge, que la exponen como subventana; sin
    pestañas ni barra de direcciones. None mientras no se muestre."""
    areas = []

    def revisar(hijo, _):
        if _clase(hijo) == "Chrome_RenderWidgetHostHWND" and ctypes.windll.user32.IsWindowVisible(hijo):
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hijo, ctypes.byref(r))
            areas.append((r.left, r.top, r.right, r.bottom))
        return True

    ctypes.windll.user32.EnumChildWindows(hwnd, _ENUM_VENTANAS(revisar), 0)
    return max(areas, key=lambda a: (a[2] - a[0]) * (a[3] - a[1])) if areas else None


def _area_pagina(ventana) -> tuple[int, int, int, int] | None:
    """Rectángulo de la página; en Firefox, toda la ventana."""
    if _es_chromium(ventana._hWnd):
        # Sin la subventana, la página aún no se dibuja tras salir de minimizada.
        return _area_chrome(ventana._hWnd)
    return ventana.left, ventana.top, ventana.right, ventana.bottom


def _area_estable(hwnd: int) -> tuple[int, int, int, int] | None:
    """El área de la página cuando deja de cambiar (el navegador la ajusta después de cambiar la ventana)."""
    lecturas = [_area_chrome(hwnd)]
    fin = time.time() + 5
    while time.time() < fin and not (lecturas[-1] and lecturas[-3:] == [lecturas[-1]] * 3):
        time.sleep(0.1)
        lecturas.append(_area_chrome(hwnd))
    return lecturas[-1]


def medidas_ventana(zoom: float, escala: float, disponible: tuple[int, int]) -> tuple[float, int, int]:
    """Zoom del navegador y tamaño en píxeles del área de la página para mostrar lo mismo que en la pantalla de
    referencia con `zoom`, con la escala de Windows `escala` y sin pasar del espacio `disponible`. De los zooms que
    caben, el mayor: la captura más nítida."""
    cabe = min(disponible[0] / PAGINA_REFERENCIA[0], disponible[1] / PAGINA_REFERENCIA[1], AMPLIACION_MAXIMA)
    # El margen cubre el redondeo: en la pantalla de referencia el zoom del sitio cabe justo.
    nivel = max((z for z in NIVELES_ZOOM if escala * z / zoom <= cabe + 0.001), default=NIVELES_ZOOM[0])
    factor = escala * nivel / zoom
    return (nivel, min(disponible[0], round(PAGINA_REFERENCIA[0] * factor)),
            min(disponible[1], round(PAGINA_REFERENCIA[1] * factor)))


def _zoom(hwnd: int, pasos: int) -> bool:
    """Zoom de la pestaña: 100 % (Ctrl 0) y `pasos` niveles más (Ctrl +) o menos (Ctrl -). False si el navegador
    no toma el foco."""
    user32 = ctypes.windll.user32
    if not _dar_foco(hwnd):
        return False
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    for tecla in (VK_0, *[VK_ADD if pasos > 0 else VK_SUBTRACT] * abs(pasos)):
        user32.keybd_event(tecla, 0, 0, 0)
        user32.keybd_event(tecla, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    return True


def _ajustar_ventana(ventana, zoom: float, nombre: str):
    """Deja la página como se ve en la pantalla de referencia con `zoom`: elige el zoom del navegador para esta
    pantalla y achica la ventana si hace falta. El navegador recuerda ese zoom para el sitio."""
    user32 = ctypes.windll.user32
    hwnd = ventana._hWnd
    if not _es_chromium(hwnd):
        return  # Firefox: se captura maximizada, con su zoom.
    # Maximizada, la página ocupa todo el espacio que tiene en esa pantalla.
    user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
    maxima = _area_estable(hwnd)
    if maxima is None:
        return
    disponible = (maxima[2] - maxima[0], maxima[3] - maxima[1])
    escala = user32.GetDpiForWindow(hwnd) / 96
    nivel, ancho, alto = medidas_ventana(zoom, escala, disponible)
    if not _zoom(hwnd, NIVELES_ZOOM.index(nivel) - NIVELES_ZOOM.index(1)):
        log(f"{nombre}: el navegador no tomó el foco para ajustar el zoom; se captura maximizada con su zoom.")
        return
    if (ancho, alto) != disponible:
        user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
        # La página queda donde estaba maximizada (a la vista y sin la barra de tareas encima), con el tamaño pedido.
        objetivo = (maxima[0], maxima[1], maxima[0] + ancho, maxima[1] + alto)
        for _ in range(5):
            area = _area_estable(hwnd)
            if area is None or area == objetivo:
                break
            izq, arr, der, aba = area
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            user32.SetWindowPos(hwnd, None, r.left + maxima[0] - izq, r.top + maxima[1] - arr,
                                r.right - r.left + ancho - (der - izq), r.bottom - r.top + alto - (aba - arr),
                                0x0014)  # SWP_NOZORDER | SWP_NOACTIVATE
        else:
            area = _area_estable(hwnd)
        if area != objetivo:
            log(f"{nombre}: no se pudo ajustar el tamaño de la ventana ({area}); se captura maximizada.")
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            _area_estable(hwnd)
            return
    log(f"{nombre}: zoom {nivel:.0%}, página de {ancho}x{alto} px (escala de Windows {escala:.0%}).")


def _colocacion(hwnd: int) -> _Colocacion:
    colocacion = _Colocacion(length=ctypes.sizeof(_Colocacion))
    ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(colocacion))
    return colocacion


def _region_en_pantalla(ventana, pantalla: dict) -> dict | None:
    """Área de la página recortada a la pantalla; None mientras la ventana siga minimizada (fuera de la pantalla)."""
    area = _area_pagina(ventana)
    if area is None:
        return None
    izq, arr, der, aba = area
    region = {"left": max(izq, pantalla["left"]), "top": max(arr, pantalla["top"])}
    region["width"] = min(der, pantalla["left"] + pantalla["width"]) - region["left"]
    region["height"] = min(aba, pantalla["top"] + pantalla["height"]) - region["top"]
    return region if region["width"] > 0 and region["height"] > 0 else None


def _minimizar_consola() -> int | None:
    # La consola puede quedar encima del navegador y saldría en la captura.
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
        ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return hwnd
    return None


def _cerrar_pestana(ventana, titulo: str):
    """Cierra con Ctrl+W la pestaña capturada, solo si el navegador sigue en primer plano mostrándola."""
    user32 = ctypes.windll.user32
    if user32.GetForegroundWindow() != ventana._hWnd or titulo not in ventana.title:
        return
    VK_W = 0x57
    for tecla, evento in ((VK_CONTROL, 0), (VK_W, 0), (VK_W, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP)):
        user32.keybd_event(tecla, 0, evento, 0)


def _cerrar_ventanas(ventanas: set[int], titulo: str):
    user32 = ctypes.windll.user32
    # Solo ventanas del navegador que siguen abiertas: el número de una ventana cerrada puede pasar a otra.
    ventanas = {hwnd for hwnd in ventanas if user32.IsWindow(hwnd) and _clase(hwnd) in CLASES_NAVEGADOR}
    for hwnd in ventanas:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    fin = time.monotonic() + 3
    while ventanas and time.monotonic() < fin:
        ventanas = {hwnd for hwnd in ventanas if user32.IsWindow(hwnd)}
        if ventanas:
            time.sleep(0.1)
    if ventanas:
        log(f"{titulo}: el navegador no cerró las ventanas de la consulta {sorted(ventanas)}.")


def _cerrar_ventanas_consultadas(ventana, titulo: str, antes: set[int]):
    ventanas = {v._hWnd for v in _ventanas_nuevas(antes)
                if v._hWnd == ventana._hWnd or titulo in v.title}
    if ventana._hWnd not in antes:
        ventanas.add(ventana._hWnd)
    _cerrar_ventanas(ventanas, titulo)
    _ABIERTAS.difference_update(ventanas)


def cerrar_ventanas_abiertas():
    """Cierra todo lo que el bot abrió en el navegador del usuario y siga abierto (una consulta cancelada, el FBI
    abierto dos veces, una ventana de la sesión anterior de Chrome): al terminar cada consulta y al cerrar el bot."""
    global _PREVIAS
    ventanas = set(_ABIERTAS)
    if _PREVIAS is not None:
        marcas = [marca.lower() for marca in (*_MARCAS, *TITULOS_VERIFICACION, *OTROS_TITULOS_DEL_BOT)]
        ventanas |= {v._hWnd for v in _ventanas_nuevas(_PREVIAS) if any(m in v.title.lower() for m in marcas)}
    if ventanas:
        _cerrar_ventanas(ventanas, "Chrome")
    _ABIERTAS.clear()
    _PREVIAS = None


def capturar_navegador(
    url: str,
    titulo: str,
    nombre: str,
    prefijo: str,
    numero: str,
    paso_manual: str | None = None,
    espera_minima: float = ESPERA_MINIMA,
    preguntar=input,
    antes: set[int] | None = None,
    zoom: float = 1,
    espera: float = ESPERA_MAXIMA,
    reabrir: bool = True,
) -> Path:
    """Abre `url` en una ventana nueva del navegador por defecto y la captura cuando su título contiene `titulo`.

    Con `antes` (de `abrir_en_ventanas`) la página ya está abierta y cargada: solo se muestra y se captura.
    La página se ve como con `zoom` en la pantalla de referencia, sin importar la pantalla del equipo.
    Si el sitio está caído, captura la página de error del navegador y lanza FueraDeServicio con ella.
    """
    with PANTALLA:
        with etapa("captura"):
            _MARCAS.add(titulo)
            return _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, antes, zoom,
                             espera, reabrir)


def _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, antes, zoom,
              espera=ESPERA_MAXIMA, reabrir=True) -> Path:
    numero = re.sub(r"\D", "", numero)
    precargada = antes is not None
    if precargada:
        # Cargó mientras se resolvían los captchas: el margen para el JavaScript de la página ya pasó.
        espera_minima = min(espera_minima, ESPERA_MINIMA)
    else:
        antes = abrir_en_ventanas([url])
    propia = antes is not None
    if not propia:
        # Navegador no reconocido: pestaña nueva, buscada entre todas sus ventanas.
        webbrowser.open(url, new=2)
        antes = set()
        time.sleep(1)  # La pestaña nueva tarda un instante en tomar el foco.
    ventana, caida = _esperar_pagina(titulo, nombre, preguntar, antes, url, precargada, espera, reabrir)

    def mostrar():
        if propia:
            # Minimizada no se dibuja: se muestra como las demás capturas, maximizada.
            _encima(ventana)
            return
        if ventana.isMinimized:
            ventana.restore()
        # Windows puede negar el foco; la ventana se captura igual si está visible.
        _dar_foco(ventana._hWnd)

    consola = None
    hwnd = ventana._hWnd
    try:
        if caida:
            log(f"{nombre}: {caida}; se captura la página caída.")
        elif paso_manual and nombre not in _PASOS_HECHOS:
            mostrar()
            preguntar(f"{nombre}: {paso_manual} Pulsa Enter cuando la lista esté visible... ")
            _PASOS_HECHOS.add(nombre)

        consola = _minimizar_consola()
        mostrar()
        # Encima de todo desde ya: si otra ventana la tapa, Chrome no dibuja la página que estuvo minimizada.
        _fijar_encima(hwnd, True)
        time.sleep(0.3)

        with mss.mss() as sct:
            pantalla = sct.monitors[0]
            # Recién restaurada, Windows puede tardar en sacarla de minimizada (pasó con el FBI en un equipo cargado) y
            # Chrome en volver a dibujar una página que estuvo minimizada un rato (pasó con Consilium).
            fin = time.time() + 30
            empujon = time.time() + 5
            while _region_en_pantalla(ventana, pantalla) is None:
                if time.time() > fin:
                    raise RuntimeError(f"la ventana de {nombre} no mostró la página; no se pudo capturar")
                if time.time() > empujon:
                    # A veces Chrome la sigue dando por oculta aunque esté al frente: minimizarla y volver a mostrarla
                    # la hace dibujar (pasó con Consilium).
                    ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
                    time.sleep(0.5)
                    empujon = time.time() + 5
                mostrar()
                _fijar_encima(hwnd, True)
                time.sleep(1)

            # Para devolverla como estaba: el navegador usa el tamaño de la última ventana para las nuevas.
            colocacion = _colocacion(hwnd)
            _ajustar_ventana(ventana, zoom, nombre)
            # El aviso del zoom tapa una esquina de la página durante 1,5 s.
            espera_minima = max(espera_minima, 3)

            # Los resultados se aplican después de cargar: se captura cuando la página deja de cambiar.
            inicio, previo, iguales = time.time(), None, 0
            while True:
                transcurrido = time.time() - inicio
                if (region := _region_en_pantalla(ventana, pantalla)) is None:
                    # Chrome no está dibujando la página: se espera a que vuelva a mostrarla.
                    if transcurrido >= ESPERA_MAXIMA:
                        raise RuntimeError(f"la ventana de {nombre} dejó de mostrar la página; no se pudo capturar")
                    previo, iguales = None, 0
                    time.sleep(0.4)
                    continue
                actual = sct.grab(region).rgb
                iguales = iguales + 1 if actual == previo else 0
                previo = actual
                if _en_blanco(actual, region["width"]):
                    # A veces la página tarda en dibujar el contenido (el FBI): en blanco no se captura.
                    if transcurrido >= ESPERA_MAXIMA:
                        log(f"{nombre}: la página siguió en blanco {ESPERA_MAXIMA} s; se captura así.")
                        break
                elif transcurrido >= ESPERA_ESTABLE_MAXIMA or (transcurrido >= espera_minima and iguales >= 2):
                    break
                time.sleep(0.4)
            imagen = sct.grab(region)

        ctypes.windll.user32.SetWindowPlacement(hwnd, ctypes.byref(colocacion))
        CAPTURAS.mkdir(exist_ok=True)
        ruta = CAPTURAS / f"{prefijo}_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
        mss.tools.to_png(imagen.rgb, imagen.size, output=str(ruta))
        if caida:
            raise FueraDeServicio(caida, ruta)
        return ruta
    finally:
        _fijar_encima(hwnd, False)
        try:
            if propia:
                _cerrar_ventanas_consultadas(ventana, titulo, antes)
            else:
                _cerrar_pestana(ventana, urlsplit(url).hostname if caida else titulo)
        finally:
            if consola:
                ctypes.windll.user32.ShowWindow(consola, 9)  # SW_RESTORE
