"""Trae la versión nueva de PaginasBOT para Linux desde GitHub: con git si esta carpeta está dentro de un clon y, si
no, bajando solo los archivos de la carpeta PaginasBOT-Linux que cambiaron. Después instala las librerías nuevas."""
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
CARPETA = "PaginasBOT-Linux"
RAIZ = Path(__file__).parent
CLON = RAIZ.parent if (RAIZ.parent / ".git").exists() else None
GIT = shutil.which("git")
# Si el repositorio pasa a privado, git no debe quedarse esperando usuario y contraseña.
SIN_PREGUNTAS = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _git(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run([GIT, "-C", str(CLON), *argumentos], capture_output=True, text=True, timeout=120,
                          env=SIN_PREGUNTAS)


def _bajar(url: str) -> bytes:
    pedido = urllib.request.Request(url, headers={"User-Agent": "BOTs-actualizar"})
    with urllib.request.urlopen(pedido, timeout=60) as respuesta:
        return respuesta.read()


def _id_git(datos: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(datos) + datos).hexdigest()


def _distintos() -> tuple[str, list[tuple[str, str]]]:
    """El último commit de GitHub y los archivos (ruta en GitHub, id) de esta carpeta que aquí faltan o son distintos."""
    api = f"https://api.github.com/repos/{REPOSITORIO}"
    commit = json.loads(_bajar(f"{api}/commits/{RAMA}"))["sha"]
    prefijo = f"{CARPETA}/"
    distintos = []
    for archivo in json.loads(_bajar(f"{api}/git/trees/{commit}?recursive=1"))["tree"]:
        if archivo["type"] != "blob" or not archivo["path"].startswith(prefijo):
            continue
        local = RAIZ / archivo["path"][len(prefijo):]
        if local.is_file():
            datos = local.read_bytes()
            # Git guarda los textos con LF; copiados desde Windows pueden venir con CRLF.
            if archivo["sha"] in (_id_git(datos), _id_git(datos.replace(b"\r\n", b"\n"))):
                continue
        distintos.append((archivo["path"], archivo["sha"]))
    return commit, distintos


def hay_version_nueva() -> bool:
    """Sin conexión, o en la copia donde se programa (cambios sin commit o commits sin subir), es False."""
    try:
        if CLON:
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
        nuevos[ruta[len(CARPETA) + 1:]] = datos
    # Se escribe cuando ya bajó todo: un corte de internet no deja la carpeta a medias.
    base = RAIZ.resolve()
    for ruta, datos in nuevos.items():
        destino = (RAIZ / ruta).resolve()
        if not destino.is_relative_to(base):
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(datos)


def _reinstalar():
    """Las librerías y el Chromium de Playwright de la versión nueva, en el mismo entorno de Python."""
    for argumentos in (("pip", "install", "--disable-pip-version-check", "-q", "-r", str(RAIZ / "requirements.txt")),
                       ("playwright", "install", "chromium")):
        r = subprocess.run([sys.executable, "-m", *argumentos], capture_output=True, text=True, timeout=3600)
        if r.returncode:
            lineas = [linea for linea in (r.stderr or r.stdout).splitlines() if linea.strip()]
            raise RuntimeError(lineas[-1] if lineas else f"falló {argumentos[0]}")


def aplicar():
    """Trae la versión nueva y reinstala lo necesario; si algo falla, RuntimeError con el motivo."""
    if CLON:
        r = _git("merge", "--ff-only", "--quiet", f"origin/{RAMA}")
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip() or "git no pudo traer la versión nueva")
    else:
        _bajar_cambios()
    _reinstalar()
