"""Lector automático del captcha de la Registraduría (certvigenciacedula.registraduria.gov.co).

Combina un modelo propio (ONNX, entrenado con dddd_trainer) y varias configuraciones de ddddocr, y vota.
Acierto medido en 95 captchas reales nunca vistos al entrenar: ~78 %. "LANAP" se reconoce con una regla fija.

Uso:
    from captcha_registraduria import leer_captcha, JS_IMAGEN_CAPTCHA, imagen_desde_data_url
    data_url = await page.locator("#datos_contentplaceholder1_captcha1_CaptchaImage").evaluate(JS_IMAGEN_CAPTCHA)
    texto = await asyncio.to_thread(leer_captcha, imagen_desde_data_url(data_url))   # "" si no hay lectura confiable

Dependencias: ddddocr, onnxruntime, numpy, pillow.
"""

import base64
import io
import json
import re
import threading
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

RUTA_MODELO = Path(__file__).resolve().parent / "modelos" / "registraduria"
CARACTERES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
LARGO = 6

# Copia la imagen del captcha a tamaño natural (180x40) desde el <img> ya cargado; no hace otra petición al sitio.
JS_IMAGEN_CAPTCHA = """e => { const c = document.createElement('canvas'); c.width = e.naturalWidth; c.height = e.naturalHeight;
    c.getContext('2d').drawImage(e, 0, 0); return c.toDataURL('image/png'); }"""

_cargado = None
_candado = threading.Lock()


def _cargar():
    """Carga perezosa (ddddocr y onnxruntime tardan unos segundos en iniciar)."""
    global _cargado
    with _candado:
        if _cargado is None:
            import ddddocr
            import onnxruntime as ort

            beta = ddddocr.DdddOcr(show_ad=False, beta=True)
            beta.set_ranges(CARACTERES)
            normal = ddddocr.DdddOcr(show_ad=False)
            configs = [  # (umbral, escala, dilatación, erosión, desenfoque, modelo)
                (150, 3, 3, 0, 1, beta),
                (150, 2, 5, 5, 1, beta),
                (170, 3, 3, 0, 1, beta),
                (190, 2, 3, 3, 1, beta),
                (150, 3, 0, 0, 0, normal),
            ]
            sesion = conf = None
            if (RUTA_MODELO / "modelo.onnx").exists():
                sesion = ort.InferenceSession(str(RUTA_MODELO / "modelo.onnx"), providers=["CPUExecutionProvider"])
                conf = json.loads((RUTA_MODELO / "charsets.json").read_text(encoding="utf-8"))
            _cargado = (configs, sesion, conf)
    return _cargado


def preparar():
    """Carga los lectores al abrir el bot: la primera lectura no los carga en plena consulta, con el equipo ocupado."""
    _cargar()


def imagen_desde_data_url(data_url: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGB")


def _preprocesar(imagen: Image.Image, umbral: int, escala: int, dil: int, ero: int, blur: int) -> bytes:
    g = imagen.convert("L").resize((imagen.width * escala, imagen.height * escala), Image.BICUBIC)
    g = g.point(lambda px: 0 if px < umbral else 255)
    if dil:
        g = g.filter(ImageFilter.MinFilter(dil))
    if ero:
        g = g.filter(ImageFilter.MaxFilter(ero))
    if blur:
        g = g.filter(ImageFilter.GaussianBlur(blur))
    salida = io.BytesIO()
    g.save(salida, "PNG")
    return salida.getvalue()


def _leer_modelo(sesion, conf, imagen: Image.Image) -> str:
    # Mismo preprocesado que dddd_trainer: gris, alto fijo, normalización mean=0.456 std=0.224
    alto, charset = conf["image"][1], conf["charset"]
    g = imagen.convert("L")
    g = g.resize((int(g.width * alto / g.height), alto))
    x = (np.asarray(g, np.float32) / 255 - 0.456) / 0.224
    texto, ultimo = [], 0
    for i in sesion.run(None, {"input1": x[None, None]})[0].reshape(-1).tolist():
        if i != ultimo and i != 0:
            texto.append(charset[i])
        ultimo = i
    return "".join(texto)


def lecturas(imagen: Image.Image) -> tuple[list[str], list[str]]:
    """Devuelve (lecturas del modelo propio, lecturas de ddddocr). Útil para registrar en el log."""
    configs, sesion, conf = _cargar()
    ocr = [re.sub(r"[^A-Z0-9]", "", m.classification(_preprocesar(imagen, *c)).upper()) for *c, m in configs]
    modelo = []
    if sesion is not None:
        w, h = imagen.size
        variantes = (imagen, imagen.crop((2, 0, w, h)), imagen.crop((0, 0, w - 2, h)),
                     imagen.resize((int(w * 1.1), h)), imagen.resize((int(w * 0.9), h)))
        modelo = [_leer_modelo(sesion, conf, v) for v in variantes]
    return modelo, ocr


def leer_captcha(imagen: Image.Image) -> str:
    """Texto del captcha (6 caracteres A-Z/0-9, o "LANAP"); "" si ninguna lectura es confiable."""
    modelo, ocr = lecturas(imagen)
    # "LANAP" (nombre del componente) es una respuesta que el portal acepta.
    if "LANAP" in ocr or sum(t.startswith("LA") and t.endswith("AP") for t in ocr) >= 2:
        return "LANAP"
    validas = [t for t in modelo + ocr if len(t) == LARGO]
    return Counter(validas).most_common(1)[0][0] if validas else ""
