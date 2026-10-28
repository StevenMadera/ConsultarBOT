"""Consulta de casos grandes de Justice.gov en el navegador por defecto."""
from pathlib import Path
from urllib.parse import quote

from captura_navegador import capturar_navegador

URL_JUSTICIA = (
    "https://www.justice.gov/information-victims-large-cases"
    "?search_api_fulltext={nombre}&sort_by=field_date"
)
TITULO_JUSTICIA = "Information for Victims in Large Cases"


def url_justicia(persona: dict) -> str:
    return URL_JUSTICIA.format(nombre=quote(" ".join(persona["nombre"].split())))


def consultar_justicia(persona: dict, preguntar=input, antes=None) -> Path:
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