"""Consulta de la lista de la UE de personas catalogadas como terroristas (Consilium), en el navegador por defecto."""
import sys
from pathlib import Path
from urllib.parse import quote

from captura_navegador import capturar_navegador

URL_CONSILIUM = "https://www.consilium.europa.eu/es/search/?query={nombre}"
# Título de la página de resultados: la verificación previa del sitio ("Checking your browser") no lo tiene.
TITULO_CONSILIUM = "Search - Consilium"


def url_consilium(persona: dict) -> str:
    return URL_CONSILIUM.format(nombre=quote(persona["nombre"]))


def consultar_consilium(persona: dict, preguntar=input, antes=None) -> Path:
    return capturar_navegador(
        url_consilium(persona),
        TITULO_CONSILIUM,
        "Consilium",
        "consilium",
        persona["numero"],
        paso_manual="Acepta solo las cookies necesarias y espera a que aparezca la lista.",
        # Sin el paso manual (desde la segunda consulta), la lista se carga con JavaScript.
        espera_minima=5,
        preguntar=preguntar,
        antes=antes,
        zoom=0.75,
    )


if __name__ == "__main__":
    nombre_prueba = " ".join(sys.argv[1:]) or "Juan Perez Gomez"
    print(consultar_consilium({"numero": "0", "nombre": nombre_prueba}))
