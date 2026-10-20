"""Instala los BOT de esta carpeta: sus librerías, Chromium si lo usan y un acceso directo con su ícono en el
Escritorio y en el menú Inicio. Se abre con Instalar.bat."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell

from PaginasBOT.ocr import buscar_tesseract

RAIZ = Path(__file__).parent
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
# Las que se instalan en los equipos; AntecedentesBOT y CuadrosBOT todavía no.
PUBLICADAS = ("PaginasBOT",)
# Instalador de UB Mannheim y SHA-256 publicado en microsoft/winget-pkgs.
TESSERACT_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/"
    "tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)
TESSERACT_SHA256 = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"


def buscar_apps() -> list[Path]:
    """El .pyw de cada app publicada: en esta carpeta o en sus subcarpetas, junto a su requirements.txt."""
    carpetas = [RAIZ, *sorted(c for c in RAIZ.iterdir() if c.is_dir())]
    return [pyw for c in carpetas if (c / "requirements.txt").exists() for pyw in sorted(c.glob("*.pyw"))
            if pyw.stem in PUBLICADAS]


def python(*argumentos: str) -> bool:
    return subprocess.run([sys.executable, *argumentos]).returncode == 0


def verificar_tesseract(ruta: Path) -> None:
    resultado = subprocess.run(
        [str(ruta), "--list-langs"], capture_output=True, text=True, timeout=30,
    )
    if resultado.returncode or "eng" not in resultado.stdout.splitlines():
        detalle = (resultado.stderr or resultado.stdout).strip()
        raise RuntimeError(f"Tesseract no funciona o le falta el idioma eng: {detalle}")


def instalar_tesseract() -> bool:
    try:
        tesseract = buscar_tesseract()
        if tesseract:
            verificar_tesseract(tesseract)
            print(f"Tesseract OCR ya está instalado: {tesseract}")
            return True

        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("Windows no definió LOCALAPPDATA; no se puede instalar para este usuario.")
        destino = Path(local) / "Programs" / "Tesseract-OCR"
        print("\nDescargando Tesseract OCR desde UB Mannheim (sin WinGet)…")
        with tempfile.TemporaryDirectory(prefix="BOTs-tesseract-") as temporal:
            instalador = Path(temporal) / "tesseract-setup.exe"
            pedido = urllib.request.Request(TESSERACT_URL, headers={"User-Agent": "BOTs-instalar"})
            with urllib.request.urlopen(pedido, timeout=120) as respuesta, instalador.open("wb") as archivo:
                shutil.copyfileobj(respuesta, archivo)
            with instalador.open("rb") as archivo:
                resumen = hashlib.file_digest(archivo, "sha256").hexdigest()
            if resumen != TESSERACT_SHA256:
                raise RuntimeError("El SHA-256 del instalador no coincide; no se ejecutó la descarga.")

            print(f"Instalando Tesseract OCR en {destino}…")
            # NSIS exige /D al final y sin comillas, incluso si la ruta contiene espacios.
            comando = subprocess.list2cmdline([str(instalador), "/S", "/CurrentUser"])
            resultado = subprocess.run(
                f"{comando} /D={destino}",
                capture_output=True, text=True, timeout=600,
            )
            if resultado.returncode:
                detalle = (resultado.stderr or resultado.stdout).strip()
                raise RuntimeError(f"El instalador terminó con código {resultado.returncode}: {detalle}")

        tesseract = destino / "tesseract.exe"
        if not tesseract.is_file():
            raise RuntimeError(f"El instalador no creó {tesseract}.")
        verificar_tesseract(tesseract)
        print(f"Tesseract OCR quedó instalado: {tesseract}")
        return True
    except (OSError, urllib.error.URLError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"No se pudo instalar Tesseract OCR: {error}")
        return False


def crear_acceso(ruta: Path, pyw: Path):
    enlace = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                        shell.IID_IShellLink)
    enlace.SetPath(str(PYTHONW))
    enlace.SetArguments(f'"{pyw}"')
    enlace.SetWorkingDirectory(str(pyw.parent))
    enlace.SetIconLocation(str(pyw.with_name("icono.ico")), 0)
    # El mismo id que fija la app al abrir: si se ancla, su ventana cae en ese mismo botón de la barra de tareas.
    propiedades = enlace.QueryInterface(propsys.IID_IPropertyStore)
    propiedades.SetValue(pscon.PKEY_AppUserModel_ID,
                         propsys.PROPVARIANTType(f"Soporte.{pyw.stem}", pythoncom.VT_LPWSTR))
    propiedades.Commit()
    enlace.QueryInterface(pythoncom.IID_IPersistFile).Save(str(ruta), 0)


def main() -> int:
    apps = buscar_apps()
    if not apps:
        print(f"No hay ninguna app (.pyw con su requirements.txt) en {RAIZ}.")
        return 1
    nombres = ", ".join(pyw.stem for pyw in apps)
    print(f"Apps: {nombres}")
    requisitos = [pyw.with_name("requirements.txt") for pyw in apps]
    fallos = []

    print("\nInstalando librerías…")
    if not python("-m", "pip", "install", "--disable-pip-version-check",
                  *(argumento for r in requisitos for argumento in ("-r", str(r)))):
        fallos.append("las librerías")
    if any("playwright" in r.read_text(encoding="utf-8").lower() for r in requisitos):
        print("\nInstalando Chromium para Playwright…")
        if not python("-m", "playwright", "install", "chromium"):
            fallos.append("Chromium")
    if any(pyw.stem == "PaginasBOT" for pyw in apps) and not instalar_tesseract():
        fallos.append("Tesseract OCR")

    try:
        documentos = Path(shell.SHGetKnownFolderPath(shell.FOLDERID_Documents)) / "PAGINAS"
        documentos.mkdir(parents=True, exist_ok=True)
        print(f"\nCarpeta de informes: {documentos}")
    except OSError as error:
        print(f"No se pudo crear la carpeta de informes: {error}")
        fallos.append("la carpeta de informes")

    print("\nCreando accesos directos…")
    for carpeta in (shell.FOLDERID_Desktop, shell.FOLDERID_Programs):
        destino = Path(shell.SHGetKnownFolderPath(carpeta))
        for pyw in apps:
            crear_acceso(destino / f"{pyw.stem}.lnk", pyw)
    print(f"   {nombres}: en el Escritorio y en el menú Inicio.")

    if fallos:
        print(f"\nFalló la instalación de {' y '.join(fallos)}: revisa la conexión a internet y vuelve a abrir "
              "Instalar.bat.")
        return 1
    print(f"\nListo: abre {nombres} desde el acceso directo del Escritorio o desde el menú Inicio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
