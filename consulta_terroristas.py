"""Búsqueda de nombre en la lista de designaciones terroristas de State.gov."""
from pathlib import Path
from urllib.parse import quote

from captura_navegador import capturar_navegador

URL_TERRORISTAS = (
    "https://findit.state.gov/search?affiliate=dos_stategov&sort_by="
    "&query={nombre}&commit=Search"
)
TITULO_TERRORISTAS = "U.S. Department of State Search Results"


def url_terroristas(persona: dict) -> str:
    return URL_TERRORISTAS.format(nombre=quote(" ".join(persona["nombre"].split()), safe=""))


def consultar_terroristas(persona: dict, preguntar=input, ventana=None) -> Path:
    return capturar_navegador(
        url_terroristas(persona),
        TITULO_TERRORISTAS,
        "Terrorist designations",
        "terroristas",
        persona["numero"],
        preguntar=preguntar,
        ventana=ventana,
    )