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
# Pasos manuales ya hechos en esta sesión: Edge recuerda, por ejemplo, el consentimiento de cookies.
_PASOS_HECHOS: set[str] = set()
# Una captura necesita el foco y la pantalla: no puede coincidir con otra ni con una pregunta en la consola.
# Quien lo tiene y pregunta al usuario debe hacerlo sin volver a pedirlo.
PANTALLA = threading.Lock()


def _es_verificacion(titulo: str) -> bool:
    return any(t in titulo.lower() for t in TITULOS_VERIFICACION)


def _hay_verificacion() -> bool:
    return any(_es_verificacion(titulo) for titulo in gw.getAllTitles())


def _en_blanco(rgb: bytes) -> bool:
    # Sin la franja de arriba (el aviso del FBI se dibuja antes que el resto), todo blanco puro.
    resto = rgb[len(rgb) // 10:]
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
            preguntar("Pasa la verificación en el navegador y pulsa Enter... ")
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


def _encima(ventana):
    """Maximiza la ventana y la pone encima de las demás, aunque Windows no deje darle el foco a otro programa."""
    SWP_NOSIZE_NOMOVE, SW_MAXIMIZE = 0x0003, 3
    user32 = ctypes.windll.user32
    hwnd = wintypes.HWND(ventana._hWnd)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    # Con un -1 simple (sin el tipo HWND) Windows ignora la orden.
    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, SWP_NOSIZE_NOMOVE)  # HWND_TOPMOST
    user32.SetWindowPos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, SWP_NOSIZE_NOMOVE)  # HWND_NOTOPMOST
    try:
        ventana.activate()
    except Exception:
        pass


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


def _area_pagina(ventana) -> tuple[int, int, int, int]:
    """Rectángulo (izq, arr, der, abajo) de la página, sin pestañas ni barra de direcciones.

    Chrome y Edge la exponen como subventana; si no aparece, se usa toda la ventana.
    """
    areas = []

    def revisar(hwnd, _):
        if _clase(hwnd) == "Chrome_RenderWidgetHostHWND" and ctypes.windll.user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
            areas.append((r.left, r.top, r.right, r.bottom))
        return True

    ctypes.windll.user32.EnumChildWindows(ventana._hWnd, _ENUM_VENTANAS(revisar), 0)
    if areas:
        return max(areas, key=lambda a: (a[2] - a[0]) * (a[3] - a[1]))
    return ventana.left, ventana.top, ventana.right, ventana.bottom


def _region_en_pantalla(ventana, pantalla: dict) -> dict | None:
    """Área de la página recortada a la pantalla; None mientras la ventana siga minimizada (fuera de la pantalla)."""
    izq, arr, der, aba = _area_pagina(ventana)
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
    VK_CONTROL, VK_W, KEYEVENTF_KEYUP = 0x11, 0x57, 0x0002
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
) -> Path:
    """Abre `url` en el navegador por defecto y captura la página de la ventana cuyo título contiene `titulo`.

    Con `ventana` (de `abrir_en_ventana`) la página ya está abierta y cargada: solo se muestra y se captura.
    """
    with PANTALLA:
        return _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, ventana)


def _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar, ventana) -> Path:
    numero = re.sub(r"\D", "", numero)
    abierta_antes = ventana is not None and ctypes.windll.user32.IsWindow(ventana._hWnd)
    if abierta_antes:
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
        try:
            ventana.activate()
        except Exception:
            pass  # Windows puede negar el foco; la ventana se captura igual si está visible.

    if paso_manual and nombre not in _PASOS_HECHOS:
        mostrar()
        preguntar(f"{nombre}: {paso_manual} Pulsa Enter cuando la lista esté visible... ")
        _PASOS_HECHOS.add(nombre)

    consola = _minimizar_consola()
    try:
        mostrar()
        time.sleep(0.3)

        with mss.mss() as sct:
            pantalla = sct.monitors[0]
            # Recién restaurada, Windows puede tardar en sacarla de minimizada (pasó con el FBI en un equipo cargado).
            fin = time.time() + 10
            while (region := _region_en_pantalla(ventana, pantalla)) is None:
                if time.time() > fin:
                    raise RuntimeError(f"la ventana de {nombre} siguió minimizada; no se pudo capturar")
                mostrar()
                time.sleep(0.5)

            # Los resultados se aplican después de cargar: se captura cuando la página deja de cambiar.
            inicio, previo, iguales = time.time(), None, 0
            while True:
                actual = sct.grab(region).rgb
                iguales = iguales + 1 if actual == previo else 0
                previo = actual
                transcurrido = time.time() - inicio
                if _en_blanco(actual):
                    # A veces la página tarda en dibujar el contenido (el FBI): en blanco no se captura.
                    if transcurrido >= ESPERA_MAXIMA:
                        log(f"{nombre}: la página siguió en blanco {ESPERA_MAXIMA} s; se captura así.")
                        break
                elif transcurrido >= ESPERA_ESTABLE_MAXIMA or (transcurrido >= espera_minima and iguales >= 2):
                    break
                time.sleep(0.4)
            imagen = sct.grab(region)

        CAPTURAS.mkdir(exist_ok=True)
        ruta = CAPTURAS / f"{prefijo}_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
        mss.tools.to_png(imagen.rgb, imagen.size, output=str(ruta))
        if abierta_antes:
            # La abrió el bot: se cierra sin depender del foco.
            ctypes.windll.user32.PostMessageW(ventana._hWnd, 0x0010, 0, 0)  # WM_CLOSE
        else:
            # Antes de restaurar la consola, que al activarse quitaría el foco al navegador.
            _cerrar_pestana(ventana, titulo)
        return ruta
    finally:
        if consola:
            ctypes.windll.user32.ShowWindow(consola, 9)  # SW_RESTORE
