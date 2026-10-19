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

import mss
import mss.tools
import pygetwindow as gw

from comun import CAPTURAS, log

ESPERA_MINIMA = 2
ESPERA_ESTABLE_MAXIMA = 12
# La verificación automática de Consilium puede tardar más de 20 s antes de mostrar la página.
ESPERA_MAXIMA = 60
TITULOS_VERIFICACION = ("just a moment", "un momento", "verifying", "verificando")
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
# Una captura necesita el foco y la pantalla: no puede coincidir con otra ni con una pregunta en la consola.
# Quien lo tiene y pregunta al usuario debe hacerlo sin volver a pedirlo.
PANTALLA = threading.Lock()


def _es_verificacion(titulo: str) -> bool:
    return any(t in titulo.lower() for t in TITULOS_VERIFICACION)


def _hay_verificacion() -> bool:
    return any(_es_verificacion(titulo) for titulo in gw.getAllTitles())


def _en_blanco(rgb: bytes, ancho: int) -> bool:
    # Debajo de la cabecera (el aviso del FBI y la de Consilium se dibujan antes que el resto) y sin la barra de
    # desplazamiento, todo blanco puro.
    fila = ancho * 3
    filas = len(rgb) // fila
    resto = b"".join(rgb[i * fila:(i + 1) * fila - 90] for i in range(filas // 4, filas))
    return resto.count(255) > len(resto) * 0.995


def _buscar_ventana(titulo: str):
    # El editor puede tener abierto un archivo cuyo nombre contenga el mismo texto.
    for ventana in gw.getWindowsWithTitle(titulo):
        if "Visual Studio Code" not in ventana.title:
            return ventana
    return None


def _esperar_pagina(titulo: str, nombre: str, preguntar, ventana=None):
    """Espera a que el título de la ventana sea el de la página; si sale la verificación, pide pasarla.

    Con `ventana` (abierta de antemano) solo se mira esa: otra puede estar en su propia verificación.
    """
    if ventana is None:
        time.sleep(1)  # La pestaña nueva tarda un instante en tomar el foco.
    fin = time.time() + ESPERA_MAXIMA
    avisado = False
    while time.time() < fin:
        verificando = _es_verificacion(ventana.title) if ventana else _hay_verificacion()
        if verificando and not avisado:
            avisado = True
            log(f"{nombre}: verificación de Cloudflare detectada")
            preguntar(f"{nombre}: pasa la verificación en el navegador y pulsa Enter... ")
            fin = time.time() + ESPERA_MAXIMA
        if ventana is None:
            encontrada = _buscar_ventana(titulo)
        else:
            encontrada = ventana if titulo in ventana.title else None
        if encontrada:
            return encontrada
        time.sleep(0.2)
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


def abrir_en_ventana(url: str):
    """Abre `url` en una ventana nueva del navegador por defecto y la minimiza: carga mientras se hace lo demás.

    Devuelve la ventana; None si no se reconoce (la captura abrirá entonces la página en su momento).
    """
    ejecutable = _navegador_por_defecto()
    if not ejecutable:
        return None
    antes = {v._hWnd for v in gw.getAllWindows()}
    subprocess.Popen([ejecutable, "--new-window", url])
    fin = time.time() + 10
    while time.time() < fin:
        for ventana in gw.getAllWindows():
            if ventana._hWnd not in antes and ventana.title and _clase(ventana._hWnd) in CLASES_NAVEGADOR:
                ventana.minimize()
                return ventana
        time.sleep(0.2)
    return None


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


def capturar_navegador(
    url: str,
    titulo: str,
    nombre: str,
    prefijo: str,
    numero: str,
    paso_manual: str | None = None,
    espera_minima: float = ESPERA_MINIMA,
    preguntar=input,
    ventana=None,
    zoom: float = 1,
) -> Path:
    """Abre `url` en el navegador por defecto y captura la página de la ventana cuyo título contiene `titulo`.

    Con `ventana` (de `abrir_en_ventana`) la página ya está abierta y cargada: solo se muestra y se captura.
    La página se ve como con `zoom` en la pantalla de referencia, sin importar la pantalla del equipo.
    """
    with PANTALLA:
        return _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, ventana, zoom)


def _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, ventana, zoom) -> Path:
    numero = re.sub(r"\D", "", numero)
    abierta_antes = ventana is not None and ctypes.windll.user32.IsWindow(ventana._hWnd)
    if abierta_antes:
        # Cargó mientras se resolvían los captchas: el margen para el JavaScript de la página ya pasó.
        espera_minima = min(espera_minima, ESPERA_MINIMA)
        # Minimizada no se dibuja: se muestra como las demás capturas, maximizada.
        _encima(ventana)
        ventana = _esperar_pagina(titulo, nombre, preguntar, ventana)
    else:
        webbrowser.open(url, new=2)
        ventana = _esperar_pagina(titulo, nombre, preguntar)

    def mostrar():
        if abierta_antes:
            _encima(ventana)
            return
        if ventana.isMinimized:
            ventana.restore()
        # Windows puede negar el foco; la ventana se captura igual si está visible.
        _dar_foco(ventana._hWnd)

    if paso_manual and nombre not in _PASOS_HECHOS:
        mostrar()
        preguntar(f"{nombre}: {paso_manual} Pulsa Enter cuando la lista esté visible... ")
        _PASOS_HECHOS.add(nombre)

    consola = _minimizar_consola()
    hwnd = ventana._hWnd
    try:
        mostrar()
        time.sleep(0.3)

        with mss.mss() as sct:
            pantalla = sct.monitors[0]
            # Recién restaurada, Windows puede tardar en sacarla de minimizada (pasó con el FBI en un equipo cargado).
            fin = time.time() + 10
            while _region_en_pantalla(ventana, pantalla) is None:
                if time.time() > fin:
                    raise RuntimeError(f"la ventana de {nombre} no mostró la página; no se pudo capturar")
                mostrar()
                time.sleep(0.5)

            _fijar_encima(hwnd, True)
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
        if abierta_antes:
            # La abrió el bot: se cierra sin depender del foco.
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        else:
            # Antes de restaurar la consola, que al activarse quitaría el foco al navegador.
            _cerrar_pestana(ventana, titulo)
        return ruta
    finally:
        _fijar_encima(hwnd, False)
        if consola:
            ctypes.windll.user32.ShowWindow(consola, 9)  # SW_RESTORE
