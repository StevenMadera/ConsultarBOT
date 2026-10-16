import threading
from datetime import datetime
from pathlib import Path

# Igual al nombre del .pyw: el instalador da al acceso directo el id f"Soporte.{NOMBRE}".
NOMBRE = "AntecedentesBOT"
RAIZ = Path(__file__).parent
PLANTILLAS = RAIZ / "Plantillas"
SALIDAS = RAIZ / "salidas"
_archivo = threading.Lock()


def log(msg: str):
    with _archivo, open(RAIZ / "log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
