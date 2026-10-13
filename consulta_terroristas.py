"""Búsqueda de nombre en la lista de designaciones terroristas de State.gov."""
from pathlib import Path
from urllib.parse import quote

from captura_navegador import capturar_navegador

URL_TERRORISTAS = (
    "https://findit.state.gov/search?affiliate=dos_stategov&sort_by="
    "&query={nombre}&commit=Search"
)


def consultar_terroristas(persona: dict) -> Path:
    nombre = " ".join(persona["nombre"].split())
    return capturar_navegador(
        URL_TERRORISTAS.format(nombre=quote(nombre, safe="")),
        "U.S. Department of State Search Results",
        "Terrorist designations",
        "terroristas",
        persona["numero"],
    )