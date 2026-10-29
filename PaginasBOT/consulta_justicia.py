"""Consulta de casos grandes de Justice.gov en el navegador por defecto."""
from pathlib import Path
from urllib.parse import quote

from captura_navegador import capturar_navegador
from comun import FueraDeServicio

URL_JUSTICIA = (
    "https://www.justice.gov/information-victims-large-cases"
    "?search_api_fulltext={nombre}&sort_by=field_date"
)
TITULO_JUSTICIA = "Information for Victims in Large Cases"
# Desde el 28/10/2026 su cuenta regresiva no termina desde ninguna red: se da por caído sin abrirlo.
DESCANSO = True
MOTIVO_DESCANSO = "en descanso: el sitio repite sin fin su cuenta regresiva desde cualquier conexión"


def url_justicia(persona: dict) -> str:
    return URL_JUSTICIA.format(nombre=quote(" ".join(persona["nombre"].split())))


def consultar_justicia(persona: dict, preguntar=input, antes=None) -> Path:
    if DESCANSO:
        raise FueraDeServicio(MOTIVO_DESCANSO)
    return capturar_navegador(
        url_justicia(persona),
        TITULO_JUSTICIA,
        "Justice.gov",
        "justicia",
        persona["numero"],
        preguntar=preguntar,
        antes=antes,
        zoom=2 / 3,
    )