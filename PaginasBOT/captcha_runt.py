"""Lectura del captcha del RUNT con el modelo propio (entrenamiento_runt/), en ONNX.

Se lee con onnxruntime directamente y no con ddddocr: ddddocr no aplica la normalización del entrenador
(Normalize([0.456], [0.224])) a los modelos importados y así lee casi todo vacío.
"""
import io
import json
import re
import threading
from pathlib import Path

import numpy as np
from PIL import Image

CARPETA = Path(__file__).parent / "modelo_runt"
# El captcha del RUNT siempre trae 5 letras o números; otra lectura se descarta como poco confiable.
VALIDO = re.compile(r"^[A-Za-z0-9]{5}$")
_cargado = None
_candado = threading.Lock()


def _modelo():
    global _cargado
    with _candado:
        if _cargado is None:
            import onnxruntime
            sesion = onnxruntime.InferenceSession(str(CARPETA / "modelo.onnx"), providers=["CPUExecutionProvider"])
            datos = json.loads((CARPETA / "charsets.json").read_text(encoding="utf-8"))
            _cargado = (sesion, datos["charset"], int(datos["image"][1]))
        return _cargado


def preparar():
    """Carga el modelo al abrir el bot: la primera lectura no lo carga en plena consulta, con el equipo ocupado."""
    _modelo()


def leer(png: bytes) -> str | None:
    """El texto del captcha, o None si el modelo no da 5 caracteres alfanuméricos."""
    sesion, charset, alto = _modelo()
    imagen = Image.open(io.BytesIO(png)).convert("L")
    imagen = imagen.resize((int(imagen.width * alto / imagen.height), alto))
    entrada = ((np.asarray(imagen, dtype=np.float32) / 255 - 0.456) / 0.224)[None, None]
    indices = sesion.run(None, {sesion.get_inputs()[0].name: entrada})[0].reshape(-1).tolist()
    # CTC: se quitan los repetidos seguidos y el 0 (vacío).
    texto, anterior = [], 0
    for i in indices:
        if i != anterior and i != 0:
            texto.append(charset[i])
        anterior = i
    texto = "".join(texto)
    return texto if VALIDO.match(texto) else None
