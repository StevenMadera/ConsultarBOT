"""Captura de una página en el navegador por defecto, sin controlarlo.

Cloudflare bloquea los navegadores automatizados, así que la verificación la pasa una persona en su
navegador normal; este módulo solo abre la página y toma la captura de pantalla de su ventana.
"""
import ctypes
import re
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
_ENUM_VENTANAS = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
# Pasos manuales ya hechos en esta sesión: Edge recuerda, por ejemplo, el consentimiento de cookies.
_PASOS_HECHOS: set[str] = set()
# Una captura necesita el foco y la pantalla: no puede coincidir con otra ni con una pregunta en la consola.
# Quien lo tiene y pregunta al usuario debe hacerlo sin volver a pedirlo.
PANTALLA = threading.Lock()


def _hay_verificacion() -> bool:
    return any(t in titulo.lower() for titulo in gw.getAllTitles() for t in TITULOS_VERIFICACION)


def _buscar_ventana(titulo: str):
    # El editor puede tener abierto un archivo cuyo nombre contenga el mismo texto.
    for ventana in gw.getWindowsWithTitle(titulo):
        if "Visual Studio Code" not in ventana.title:
            return ventana
    return None


def _esperar_pagina(titulo: str, nombre: str, preguntar):
    """Espera a que el título de la ventana sea el de la página; si sale la verificación, pide pasarla."""
    time.sleep(1)  # La pestaña nueva tarda un instante en tomar el foco.
    fin = time.time() + ESPERA_MAXIMA
    avisado = False
    while time.time() < fin:
        if _hay_verificacion() and not avisado:
            avisado = True
            log(f"{nombre}: verificación de Cloudflare detectada")
            preguntar("Pasa la verificación en el navegador y pulsa Enter... ")
            fin = time.time() + ESPERA_MAXIMA
        ventana = _buscar_ventana(titulo)
        if ventana:
            return ventana
        time.sleep(0.2)
    raise RuntimeError(f"No se encontró la ventana del navegador con la página de {nombre}")


def _area_pagina(ventana) -> tuple[int, int, int, int]:
    """Rectángulo (izq, arr, der, abajo) de la página, sin pestañas ni barra de direcciones.

    Chrome y Edge la exponen como subventana; si no aparece, se usa toda la ventana.
    """
    areas = []

    def revisar(hwnd, _):
        clase = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(hwnd, clase, 64)
        if clase.value == "Chrome_RenderWidgetHostHWND" and ctypes.windll.user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
            areas.append((r.left, r.top, r.right, r.bottom))
        return True

    ctypes.windll.user32.EnumChildWindows(ventana._hWnd, _ENUM_VENTANAS(revisar), 0)
    if areas:
        return max(areas, key=lambda a: (a[2] - a[0]) * (a[3] - a[1]))
    return ventana.left, ventana.top, ventana.right, ventana.bottom


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
) -> Path:
    """Abre `url` en el navegador por defecto y captura la página de la ventana cuyo título contiene `titulo`."""
    with PANTALLA:
        return _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar)


def _capturar(url, titulo, nombre, prefijo, numero, paso_manual, espera_minima, preguntar) -> Path:
    numero = re.sub(r"\D", "", numero)
    webbrowser.open(url, new=2)
    ventana = _esperar_pagina(titulo, nombre, preguntar)

    if paso_manual and nombre not in _PASOS_HECHOS:
        if ventana.isMinimized:
            ventana.restore()
        try:
            ventana.activate()
        except Exception:
            pass
        preguntar(f"{nombre}: {paso_manual} Pulsa Enter cuando la lista esté visible... ")
        _PASOS_HECHOS.add(nombre)

    consola = _minimizar_consola()
    try:
        if ventana.isMinimized:
            ventana.restore()
        try:
            ventana.activate()
        except Exception:
            pass  # Windows puede negar el foco; la ventana se captura igual si está visible.
        time.sleep(0.3)

        with mss.mss() as sct:
            pantalla = sct.monitors[0]
            izq, arr, der, aba = _area_pagina(ventana)
            region = {
                "left": max(izq, pantalla["left"]),
                "top": max(arr, pantalla["top"]),
            }
            region["width"] = min(der, pantalla["left"] + pantalla["width"]) - region["left"]
            region["height"] = min(aba, pantalla["top"] + pantalla["height"]) - region["top"]

            # Los resultados se aplican después de cargar: se captura cuando la página deja de cambiar.
            inicio, previo, iguales = time.time(), None, 0
            while time.time() - inicio < ESPERA_ESTABLE_MAXIMA:
                actual = sct.grab(region).rgb
                iguales = iguales + 1 if actual == previo else 0
                previo = actual
                if time.time() - inicio >= espera_minima and iguales >= 2:
                    break
                time.sleep(0.4)
            imagen = sct.grab(region)

        CAPTURAS.mkdir(exist_ok=True)
        ruta = CAPTURAS / f"{prefijo}_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
        mss.tools.to_png(imagen.rgb, imagen.size, output=str(ruta))
        # Antes de restaurar la consola, que al activarse quitaría el foco al navegador.
        _cerrar_pestana(ventana, titulo)
        return ruta
    finally:
        if consola:
            ctypes.windll.user32.ShowWindow(consola, 9)  # SW_RESTORE
