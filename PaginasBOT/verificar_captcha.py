"""Comprueba el lector con los 95 captchas reales de prueba. Uso: py verificar_captcha.py  (esperado: ~78 %)"""

from pathlib import Path

from PIL import Image

from captcha_registraduria import leer_captcha

carpeta = Path(__file__).resolve().parent / "prueba_captchas"
archivos = sorted(carpeta.glob("*.png"))
aciertos = sum(leer_captcha(Image.open(f).convert("RGB")) == f.name.split("_")[0] for f in archivos)
print(f"Acierto: {aciertos}/{len(archivos)} = {aciertos / len(archivos):.1%}")
print(f"LANAP de ejemplo -> {leer_captcha(Image.open(carpeta.parent / 'lanap_ejemplo.png').convert('RGB'))}")
