"""Trae la versión nueva desde GitHub: con git si esta carpeta es un clon y, si no, bajando solo los archivos que
cambiaron. Después vuelve a ejecutar instalar.py (librerías nuevas, Chromium y accesos directos)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPOSITORIO = "StevenMadera/ConsultarBOT"
RAMA = "main"
RAIZ = Path(__file__).parent
GIT = shutil.which("git") or next((str(p) for p in [Path(r"C:\Program Files\Git\cmd\git.exe")] if p.exists()), None)
# Si el repositorio pasa a privado y el equipo no tiene sesión, Git no debe abrir su ventana de inicio de sesión.
SIN_PREGUNTAS = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def _git(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run([GIT, "-C", str(RAIZ), *argumentos], capture_output=True, text=True, timeout=120,
                          env=SIN_PREGUNTAS, creationflags=subprocess.CREATE_NO_WINDOW)


def _bajar(url: str) -> bytes:
    pedido = urllib.request.Request(url, headers={"User-Agent": "BOTs-actualizar"})
    with urllib.request.urlopen(pedido, timeout=60) as respuesta:
        return respuesta.read()


def _id_git(datos: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(datos) + datos).hexdigest()


def _distintos() -> tuple[str, list[tuple[str, str]]]:
    """El último commit de GitHub y los archivos (ruta, id) que aquí faltan o son distintos."""
    api = f"https://api.github.com/repos/{REPOSITORIO}"
    commit = json.loads(_bajar(f"{api}/commits/{RAMA}"))["sha"]
    distintos = []
    for archivo in json.loads(_bajar(f"{api}/git/trees/{commit}?recursive=1"))["tree"]:
        if archivo["type"] != "blob":
            continue
        local = RAIZ / archivo["path"]
        if local.is_file():
            datos = local.read_bytes()
            # Git guarda los textos con LF; en Windows pueden estar con CRLF.
            if archivo["sha"] in (_id_git(datos), _id_git(datos.replace(b"\r\n", b"\n"))):
                continue
        distintos.append((archivo["path"], archivo["sha"]))
    return commit, distintos


def hay_version_nueva() -> bool:
    """Sin conexión, o en la copia donde se programa (cambios sin commit o commits sin subir), es False."""
    try:
        if (RAIZ / ".git").exists():
            if not GIT:
                return False
            estado = _git("status", "--porcelain", "--untracked-files=no")
            if estado.returncode or estado.stdout.strip():
                return False
            _git("fetch", "--quiet", "origin", RAMA)
            nuevos = _git("rev-list", "--count", f"HEAD..origin/{RAMA}").stdout.strip()
            return (nuevos not in ("", "0")
                    and _git("merge-base", "--is-ancestor", "HEAD", f"origin/{RAMA}").returncode == 0)
        return bool(_distintos()[1])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False


def _bajar_cambios():
    commit, distintos = _distintos()
    nuevos = {}
    for ruta, id_git in distintos:
        datos = _bajar(f"https://raw.githubusercontent.com/{REPOSITORIO}/{commit}/{urllib.parse.quote(ruta)}")
        if _id_git(datos) != id_git:
            raise RuntimeError(f"{ruta} llegó incompleto; vuelve a intentarlo")
        nuevos[ruta] = datos
    # Se escribe cuando ya bajó todo: un corte de internet no deja la carpeta a medias.
    base = RAIZ.resolve()
    for ruta, datos in nuevos.items():
        destino = (RAIZ / ruta).resolve()
        if not destino.is_relative_to(base):
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            destino.write_bytes(datos)
        except PermissionError:
            raise RuntimeError(f"{ruta} está abierto en otro programa: ciérralo y vuelve a intentarlo") from None


def _reinstalar():
    python = Path(sys.executable).with_name("python.exe")
    r = subprocess.run([str(python), str(RAIZ / "instalar.py")], capture_output=True, text=True, timeout=3600,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        lineas = [linea for linea in r.stdout.splitlines() if linea.strip()]
        raise RuntimeError(lineas[-1] if lineas else "falló instalar.py")


def aplicar():
    """Trae la versión nueva y reinstala lo necesario; si algo falla, RuntimeError con el motivo."""
    if (RAIZ / ".git").exists():
        r = _git("merge", "--ff-only", "--quiet", f"origin/{RAMA}")
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip() or "git no pudo traer la versión nueva")
    else:
        _bajar_cambios()
    _reinstalar()
