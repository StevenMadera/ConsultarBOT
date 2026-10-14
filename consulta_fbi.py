"""Consulta del FBI: abre la búsqueda en el navegador por defecto (sin controlarlo) y captura su ventana."""
import sys
from pathlib import Path
from urllib.parse import quote

from captura_navegador import capturar_navegador

URL_FBI = "https://www.fbi.gov/wanted/fugitives?SearchableText={nombre}"


def url_fbi(persona: dict) -> str:
    return URL_FBI.format(nombre=quote(persona["nombre"]))


def consultar_fbi(persona: dict, preguntar=input, ventana=None) -> Path:
    # El filtro de búsqueda se aplica con JavaScript después de cargar: necesita más margen.
    return capturar_navegador(
        url_fbi(persona), "Fugitives", "FBI", "fbi", persona["numero"],
        espera_minima=5, preguntar=preguntar, ventana=ventana,
    )


if __name__ == "__main__":
    nombre_prueba = " ".join(sys.argv[1:]) or "Juan Perez Gomez"
    print(consultar_fbi({"numero": "0", "nombre": nombre_prueba}))
