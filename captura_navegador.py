"""Captura de una página en el navegador por defecto, sin controlarlo.

Cloudflare bloquea los navegadores automatizados, así que la verificación la pasa una persona en su
navegador normal; este módulo solo abre la página y toma la captura de pantalla de su ventana.
"""
import ctypes
import re
import time
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import mss
import mss.tools
import pygetwindow as gw

from comun import CAPTURAS, log

ESPERA_MINIMA = 5
ESPERA_ESTABLE_MAXIMA = 12
ESPERA_MAXIMA = 30
TITULOS_VERIFICACION = ("just a moment", "un momento", "verifying", "verificando")
_ENUM_VENTANAS = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _hay_verificacion() -> bool:
    return any(t in titulo.lower() for titulo in gw.getAllTitles() for t in TITULOS_VERIFICACION)


def _buscar_ventana(titulo: str):
    # El editor puede tener abierto un archivo cuyo nombre contenga el mismo texto.
    for ventana in gw.getWindowsWithTitle(titulo):
        if "Visual Studio Code" not in ventana.title:
            return ventana
    return None


def _esperar_pagina(titulo: str, nombre: str):
    """Espera a que el título de la ventana sea el de la página; si sale la verificación, pide pasarla."""
    time.sleep(1)  # La pestaña nueva tarda un instante en tomar el foco.
    fin = time.time() + ESPERA_MAXIMA
    avisado = False
    while time.time() < fin:
        if _hay_verificacion() and not avisado:
            avisado = True
            log(f"{nombre}: verificación de Cloudflare detectada")
            input("Pasa la verificación en el navegador y pulsa Enter... ")
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


def capturar_navegador(url: str, titulo: str, nombre: str, prefijo: str, numero: str) -> Path:
    """Abre `url` en el navegador por defecto y captura la página de la ventana cuyo título contiene `titulo`."""
    numero = re.sub(r"\D", "", numero)
    webbrowser.open(url, new=2)
    ventana = _esperar_pagina(titulo, nombre)

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
                if time.time() - inicio >= ESPERA_MINIMA and iguales >= 2:
                    break
                time.sleep(0.4)
            imagen = sct.grab(region)

        CAPTURAS.mkdir(exist_ok=True)
        ruta = CAPTURAS / f"{prefijo}_{numero}_{datetime.now():%Y%m%d_%H%M%S}.png"
        mss.tools.to_png(imagen.rgb, imagen.size, output=str(ruta))
        return ruta
    finally:
        if consola:
            ctypes.windll.user32.ShowWindow(consola, 9)  # SW_RESTORE
