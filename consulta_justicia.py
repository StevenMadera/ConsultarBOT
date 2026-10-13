"""Consulta de casos grandes de Justice.gov en el navegador por defecto."""
from pathlib import Path
from urllib.parse import quote

from captura_navegador import capturar_navegador

URL_JUSTICIA = (
    "https://www.justice.gov/information-victims-large-cases"
    "?search_api_fulltext={nombre}&sort_by=field_date"
)


def consultar_justicia(persona: dict) -> Path:
    nombre = " ".join(persona["nombre"].split())
    return capturar_navegador(
        URL_JUSTICIA.format(nombre=quote(nombre)),
        "Information for Victims in Large Cases",
        "Justice.gov",
        "justicia",
        persona["numero"],
    )