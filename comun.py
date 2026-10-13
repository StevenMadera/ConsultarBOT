from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).parent
CAPTURAS = RAIZ / "capturas"


def log(msg: str):
    print(msg)
    with open(RAIZ / "log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
