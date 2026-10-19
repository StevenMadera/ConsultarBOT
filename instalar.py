"""Instala los BOT de esta carpeta: sus librerías, Chromium si lo usan y un acceso directo con su ícono en el
Escritorio y en el menú Inicio. Se abre con Instalar.bat."""
import subprocess
import sys
from pathlib import Path

import pythoncom
from win32com.propsys import propsys, pscon
from win32com.shell import shell

RAIZ = Path(__file__).parent
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
# Las que se instalan en los equipos; AntecedentesBOT y CuadrosBOT todavía no.
PUBLICADAS = ("PaginasBOT",)


def buscar_apps() -> list[Path]:
    """El .pyw de cada app publicada: en esta carpeta o en sus subcarpetas, junto a su requirements.txt."""
    carpetas = [RAIZ, *sorted(c for c in RAIZ.iterdir() if c.is_dir())]
    return [pyw for c in carpetas if (c / "requirements.txt").exists() for pyw in sorted(c.glob("*.pyw"))
            if pyw.stem in PUBLICADAS]


def python(*argumentos: str) -> bool:
    return subprocess.run([sys.executable, *argumentos]).returncode == 0


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
