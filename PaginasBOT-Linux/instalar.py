#!/usr/bin/env python3
"""Instala PaginasBOT en Linux Mint. En una terminal abierta en esta carpeta:

    python3 instalar.py

Copia el programa a ~/BOTs/PaginasBOT-Linux; instala lo que necesita del sistema (pide la contraseña), Google Chrome,
las librerías en un entorno propio y el Chromium de Playwright, y crea el acceso «PaginasBOT» en el menú y en el
Escritorio. Volver a ejecutarlo repara la instalación sin borrar los informes. Si este archivo se ejecuta solo, sin el
resto de la carpeta, baja el programa de GitHub.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NOMBRE = "PaginasBOT"
CARPETA = "PaginasBOT-Linux"
REPOSITORIO = "StevenMadera/ConsultarBOT"
RAMA = "main"
ORIGEN = Path(__file__).resolve().parent
DESTINO = Path.home() / "BOTs" / CARPETA
ENTORNO = DESTINO / ".venv"
# Lo que se crea al usar el programa: ni se copia ni se borra al reinstalar.
PROPIOS = {".venv", "capturas", "salidas", "log.txt", "__pycache__"}
NECESARIOS = ("python3-venv", "python3-tk", "tesseract-ocr", "tesseract-ocr-eng", "xdg-utils", "ca-certificates")
# Para ubicar la ventana, los sonidos de aviso y que las páginas usen letras con las medidas de Arial y Times.
OPCIONALES = ("x11-utils", "x11-xserver-utils", "sound-theme-freedesktop", "fonts-liberation")
CHROMES = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")
CHROME_DEB = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
_apt_actualizado = False


def paso(texto: str):
    print(f"\n== {texto}", flush=True)


def correr(comando, **opciones) -> bool:
    try:
        return subprocess.run([str(c) for c in comando], **opciones).returncode == 0
    except OSError:
        return False


def como_admin(comando) -> bool:
    return correr(["sudo", *comando])


def bajar(url: str, destino: Path | None = None) -> bytes:
    pedido = urllib.request.Request(url, headers={"User-Agent": "BOTs-instalar"})
    with urllib.request.urlopen(pedido, timeout=300) as respuesta:
        if destino is None:
            return respuesta.read()
        with open(destino, "wb") as archivo:
            shutil.copyfileobj(respuesta, archivo)
    return b""


def sistema() -> dict:
    datos = {}
    try:
        for linea in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            clave, _, valor = linea.partition("=")
            datos[clave] = valor.strip().strip('"')
    except OSError:
        pass
    return datos


def faltan(paquetes) -> list[str]:
    return [p for p in paquetes if subprocess.run(["dpkg-query", "-W", "-f=${Status}", p], capture_output=True,
                                                  text=True).stdout.strip() != "install ok installed"]


def instalar_paquetes(paquetes) -> bool:
    global _apt_actualizado
    pendientes = faltan(paquetes)
    if not pendientes:
        print("Ya están:", " ".join(paquetes))
        return True
    print("Instalando:", " ".join(pendientes))
    if not _apt_actualizado:
        # Aunque falle algún repositorio ajeno, se intenta instalar igual.
        como_admin(["apt-get", "update"])
        _apt_actualizado = True
    return como_admin(["apt-get", "install", "-y", *pendientes])


def version(python) -> tuple:
    try:
        r = subprocess.run([str(python), "-c", "import sys; print(*sys.version_info[:2])"], capture_output=True,
                           text=True, timeout=30)
        return tuple(int(n) for n in r.stdout.split())
    except (OSError, ValueError, subprocess.SubprocessError):
        return ()


def python_del_sistema() -> str | None:
    """Python 3.12 o superior (el programa usa f-strings de 3.12), con Tkinter, para crear el entorno del programa."""
    for nombre in ("python3", "python3.14", "python3.13", "python3.12"):
        ruta = shutil.which(nombre)
        if ruta and version(ruta) >= (3, 12) and correr([ruta, "-c", "import tkinter, venv"], capture_output=True):
            return ruta
    if sistema().get("UBUNTU_CODENAME") == "jammy":
        # Linux Mint 21 trae Python 3.10: se instala Python 3.12 de deadsnakes (paquetes para su base, Ubuntu 22.04).
        print("Este Linux Mint trae Python 3.10; se instala Python 3.12.")
        if (como_admin(["add-apt-repository", "-y", "ppa:deadsnakes/ppa"]) and como_admin(["apt-get", "update"])
                and como_admin(["apt-get", "install", "-y", "python3.12", "python3.12-venv", "python3.12-tk"])):
            return shutil.which("python3.12")
    return None


def instalar_chrome() -> bool:
    for nombre in CHROMES:
        if ruta := shutil.which(nombre):
            print(f"Ya está: {ruta}")
            return True
    if platform.machine().lower() not in ("x86_64", "amd64"):
        # Google Chrome solo existe para equipos de 64 bits de Intel o AMD.
        return instalar_paquetes(["chromium"])
    print("Descargando Google Chrome…")
    with tempfile.TemporaryDirectory(prefix="paginasbot-") as carpeta:
        # apt lo abre con su propio usuario: el archivo debe poder leerlo cualquiera.
        os.chmod(carpeta, 0o755)
        paquete = Path(carpeta) / "google-chrome-stable_current_amd64.deb"
        bajar(CHROME_DEB, paquete)
        os.chmod(paquete, 0o644)
        return como_admin(["apt-get", "install", "-y", paquete])


def copiar_programa():
    """Copia esta carpeta a DESTINO sin tocar los informes ni el entorno de una instalación anterior."""
    DESTINO.mkdir(parents=True, exist_ok=True)
    for item in ORIGEN.iterdir():
        if item.name in PROPIOS:
            continue
        if item.is_dir():
            shutil.copytree(item, DESTINO / item.name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, DESTINO / item.name)


def bajar_programa():
    """Baja la carpeta del programa desde GitHub (cuando este archivo se ejecuta solo)."""
    print("Descargando PaginasBOT desde GitHub…")
    datos = bajar(f"https://codeload.github.com/{REPOSITORIO}/zip/refs/heads/{RAMA}")
    with zipfile.ZipFile(io.BytesIO(datos)) as comprimido:
        nombres = comprimido.namelist()
        prefijo = next((n for n in nombres if n.endswith(f"/{CARPETA}/") and n.count("/") == 2), None)
        if prefijo is None:
            raise SystemExit(f"GitHub todavía no tiene la carpeta {CARPETA}: copia la carpeta completa a este equipo "
                             "y ejecuta instalar.py desde ella.")
        base = DESTINO.resolve()
        for nombre in nombres:
            if not nombre.startswith(prefijo) or nombre.endswith("/"):
                continue
            destino = (DESTINO / nombre[len(prefijo):]).resolve()
            if base not in destino.parents:
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(comprimido.read(nombre))


def crear_entorno(python: str) -> bool:
    """Entorno de Python propio del programa (Linux Mint no deja instalar librerías en el del sistema)."""
    py = ENTORNO / "bin" / "python"
    if py.exists() and version(py) >= (3, 12) and correr([py, "-c", "import tkinter"], capture_output=True):
        print(f"Ya existe: {ENTORNO}")
    else:
        shutil.rmtree(ENTORNO, ignore_errors=True)
        if not correr([python, "-m", "venv", ENTORNO]):
            return False
    correr([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    return correr([py, "-m", "pip", "install", "--disable-pip-version-check", "-r", DESTINO / "requirements.txt"])


def tesseract_listo() -> bool:
    try:
        r = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and "eng" in (r.stdout + r.stderr).split()


def carpeta_usuario(clave: str, por_defecto: str) -> Path:
    """Carpeta del usuario (DOCUMENTS, DESKTOP) con el nombre que tiene en su idioma."""
    try:
        ruta = subprocess.run(["xdg-user-dir", clave], capture_output=True, text=True, timeout=5).stdout.strip()
        if ruta and Path(ruta) != Path.home():
            return Path(ruta)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / por_defecto


def argumento(ruta) -> str:
    """Una ruta como argumento de Exec= en un archivo .desktop: entre comillas y con sus escapes."""
    texto = str(ruta).replace("%", "%%")
    for caracter in ("\\", '"', "`", "$"):
        texto = texto.replace(caracter, "\\" + caracter)
    return '"' + texto.replace("\\", "\\\\") + '"'


def contenido_acceso() -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NOMBRE}",
        "Comment=Consulta de antecedentes en páginas públicas",
        f"Exec={argumento(ENTORNO / 'bin' / 'python')} {argumento(DESTINO / f'{NOMBRE}.py')}",
        f"Path={DESTINO}",
        f"Icon={DESTINO / 'icono.png'}",
        "Terminal=false",
        "Categories=Office;",
        # La clase de su ventana: el panel la muestra con este ícono.
        f"StartupWMClass={NOMBRE}",
        "StartupNotify=true",
        "",
    ])


def crear_accesos() -> list[Path]:
    """El acceso en el menú (Oficina) y en el Escritorio."""
    aplicaciones = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
    destinos = [aplicaciones / "paginasbot.desktop"]
    escritorio = carpeta_usuario("DESKTOP", "Escritorio")
    if escritorio.is_dir():
        destinos.append(escritorio / f"{NOMBRE}.desktop")
    for destino in destinos:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido_acceso(), encoding="utf-8")
        destino.chmod(0o755)
    if len(destinos) > 1:
        # Linux Mint solo abre con doble clic los accesos del Escritorio marcados como de confianza.
        correr(["gio", "set", destinos[1], "metadata::trusted", "true"], capture_output=True)
    correr(["update-desktop-database", aplicaciones], capture_output=True)
    return destinos


def main() -> int:
    if not sys.platform.startswith("linux"):
        print("Este instalador es para Linux Mint. En Windows usa «Instalar PaginasBOT.bat».")
        return 1
    if os.geteuid() == 0:
        print("Ejecútalo sin sudo:  python3 instalar.py   (pedirá la contraseña cuando haga falta).")
        return 1
    print(f"Instalando {NOMBRE} para Linux en {DESTINO}")
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        print("Aviso: esta sesión es Wayland. PaginasBOT se usa en la sesión normal de Linux Mint (X11).")
    fallos = []

    paso("Programa")
    if (ORIGEN / "main.py").is_file():
        if ORIGEN != DESTINO.resolve():
            copiar_programa()
    else:
        bajar_programa()
    print(f"En {DESTINO}")

    paso("Programas del sistema (se pide tu contraseña de usuario)")
    if not como_admin(["-v"]):
        print("Sin la contraseña no se pueden instalar los programas del sistema.")
        return 1
    if not instalar_paquetes(NECESARIOS):
        fallos.append("los programas del sistema")
    instalar_paquetes(OPCIONALES)

    paso("Python")
    python = python_del_sistema()
    if not python:
        print("Se necesita Python 3.12 o superior con Tkinter: actualiza a Linux Mint 22 y vuelve a ejecutar esto.")
        return 1
    print(f"{python} ({'.'.join(map(str, version(python)))})")

    paso("Google Chrome (para FBI y Consilium)")
    if not instalar_chrome():
        fallos.append("Google Chrome")

    paso("Librerías")
    if crear_entorno(python):
        paso("Chromium de Playwright y lo que necesita del sistema")
        if not correr([ENTORNO / "bin" / "python", "-m", "playwright", "install", "--with-deps", "chromium"]):
            fallos.append("Chromium")
    else:
        fallos.append("las librerías")

    paso("Tesseract OCR")
    if tesseract_listo():
        print("Funciona, con el idioma eng.")
    else:
        fallos.append("Tesseract OCR")

    paso("Carpeta de informes")
    informes = carpeta_usuario("DOCUMENTS", "Documentos") / "PAGINAS"
    informes.mkdir(parents=True, exist_ok=True)
    print(informes)

    paso("Accesos")
    for acceso in crear_accesos():
        print(acceso)

    if fallos:
        print(f"\nFalló la instalación de {' y '.join(fallos)}: revisa la conexión a internet y vuelve a ejecutar "
              "python3 instalar.py")
        return 1
    print(f"\nListo: abre {NOMBRE} desde el menú (Oficina) o con el acceso del Escritorio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
