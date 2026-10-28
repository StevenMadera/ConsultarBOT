"""Consulta de Consilium en Chrome visible, con verificación manual y consentimiento de cookies automático."""
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pygetwindow as gw
from playwright.sync_api import sync_playwright

from captura_navegador import PAGINA_REFERENCIA, PANTALLA, _es_verificacion
from carga_paginas import VERIFICACION_JS
from comun import CAPTURAS, log

URL_CONSILIUM = "https://www.consilium.europa.eu/es/search/?query={nombre}"
# Título de la página de resultados: la verificación previa del sitio ("Checking your browser") no lo tiene.
TITULO_CONSILIUM = "Search - Consilium"
PUERTO_CDP = 9223
PERFIL_CONSILIUM = Path(__file__).parent / "perfil_consilium"
COOKIES_NECESARIAS = r"solo.*necesarias|s[oó]lo.*necesarias|only.*necessary|necessary.*only|reject.*optional|rechazar.*opcionales"
COOKIES_ACEPTAR = r"aceptar.*cookies|accept.*cookies|accept all|aceptar todas"


def preparar_consilium(page, preguntar):
    page.wait_for_function(
        "() => /consilium/i.test(document.title) || (" + VERIFICACION_JS + ")()", timeout=60000)
    if page.evaluate(VERIFICACION_JS):
        page.bring_to_front()
        log("Consilium: verificación detectada; se espera a que el usuario la resuelva.")
        preguntar("Consilium: resuelve la verificación en el navegador y pulsa «Listo» cuando termine. ")
        page.wait_for_function("() => !(" + VERIFICACION_JS + ")()", timeout=300000, polling=500)
    page.locator("main").wait_for(state="visible", timeout=60000)
    page.wait_for_timeout(1500)
    for patron in (COOKIES_NECESARIAS, COOKIES_ACEPTAR):
        botones = page.get_by_role("button", name=re.compile(patron, re.I)).filter(visible=True)
        if botones.count():
            boton = botones.first
            boton.click(timeout=10000)
            boton.wait_for(state="hidden", timeout=10000)
            log("Consilium: consentimiento de cookies gestionado automáticamente.")
            break
    if page.evaluate(VERIFICACION_JS):
        raise ValueError("Consilium: la verificación sigue pendiente; no se tomará la captura.")


def url_consilium(persona: dict) -> str:
    return URL_CONSILIUM.format(nombre=quote(persona["nombre"]))


def consultar_consilium(persona: dict, preguntar=input, antes=None) -> Path:
    # Chrome normal (sin banderas de automatización) para que Cloudflare no deje la verificación en bucle.
    PERFIL_CONSILIUM.mkdir(parents=True, exist_ok=True)
    proceso = subprocess.Popen([
        _chrome(), f"--remote-debugging-port={PUERTO_CDP}", f"--user-data-dir={PERFIL_CONSILIUM}",
        "--no-first-run", "--no-default-browser-check", "--force-device-scale-factor=0.75",
        f"--window-size={round(PAGINA_REFERENCIA[0] / 0.75)},{round(PAGINA_REFERENCIA[1] / 0.75) + 120}",
        url_consilium(persona)])
    try:
        with PANTALLA, sync_playwright() as playwright:
            # Cloudflare detecta el control del navegador: no se conecta hasta que la verificación haya pasado.
            _esperar_verificacion(preguntar)
            navegador = _conectar(playwright)
            contexto = navegador.contexts[0]
            page = next(p for p in contexto.pages if "consilium" in p.url)
            preparar_consilium(page, preguntar)
            page.wait_for_timeout(2000)
            if page.evaluate(VERIFICACION_JS):
                raise ValueError("Consilium: volvió la verificación; no se tomará la captura.")
            ruta = CAPTURAS / f"consilium_{persona['numero']}_{datetime.now():%Y%m%d_%H%M%S}.png"
            page.screenshot(path=str(ruta), timeout=15000)
            # Cierre ordenado: guarda la cookie de Cloudflare en el perfil y la próxima consulta no se verifica.
            try:
                navegador.new_browser_cdp_session().send("Browser.close")
                proceso.wait(timeout=10)
            except Exception:
                pass
            return ruta
    finally:
        proceso.terminate()


def _titulo_consilium() -> str:
    for ventana in gw.getAllWindows():
        titulo = ventana.title
        if "Visual Studio Code" not in titulo and ("consilium" in titulo.lower() or _es_verificacion(titulo)):
            return titulo
    return ""


def _esperar_verificacion(preguntar):
    limite = time.monotonic() + 60
    avisado = False
    while time.monotonic() < limite or avisado:
        titulo = _titulo_consilium()
        if titulo and not _es_verificacion(titulo):
            time.sleep(2)
            if not _es_verificacion(_titulo_consilium()):
                return
        elif _es_verificacion(titulo) and not avisado and time.monotonic() > limite - 50:
            log("Consilium: verificación detectada; se espera a que el usuario la resuelva.")
            preguntar("Consilium: resuelve la verificación en el navegador y pulsa «Listo» cuando termine. ")
            avisado = True
            limite = time.monotonic() + 300
        elif avisado and time.monotonic() > limite:
            break
        time.sleep(0.5)
    raise ValueError("Consilium: la verificación no terminó; no se tomará la captura.")


def _chrome() -> str:
    for ruta in (shutil.which("chrome"),
                 os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                 os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                 os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")):
        if ruta and os.path.exists(ruta):
            return ruta
    raise FileNotFoundError("Consilium: no se encontró Google Chrome.")


def _conectar(playwright):
    for _ in range(40):
        try:
            return playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{PUERTO_CDP}")
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Consilium: no se pudo conectar con Chrome.")




if __name__ == "__main__":
    nombre_prueba = " ".join(sys.argv[1:]) or "Juan Perez Gomez"
    print(consultar_consilium({"numero": "0", "nombre": nombre_prueba}))
