"""Llena la plantilla de cada persona con Word y la guarda en PDF."""
import re
from datetime import date
from pathlib import Path

import pythoncom
import pywintypes
import win32com.client

from comun import PLANTILLAS, log
from tabla import Fila

NO_GUARDAR = 0
FORMATO_PDF = 17
REEMPLAZAR_UNO = 1
REEMPLAZAR_TODO = 2
CONTINUAR_AL_INICIO = 1
FRASE_CONCEPTO = "Dentro de la verificación realizada"
MESES = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE",
         "NOVIEMBRE", "DICIEMBRE")
# La fecha de la plantilla, sea cual sea: "15 DE OCTUBRE DE 2026".
FECHA_PLANTILLA = re.compile(r"\b\d{1,2} DE [A-ZÁÉÍÓÚ]+ DE \d{4}\b", re.IGNORECASE)


def fecha_larga(dia: date) -> str:
    return f"{dia.day} DE {MESES[dia.month - 1]} DE {dia.year}"


def ruta_libre(ruta: Path) -> Path:
    """La ruta, o con " (2)", " (3)"... si ya existe (personas repetidas en el lote)."""
    contador = 2
    libre = ruta
    while libre.exists():
        libre = ruta.with_name(f"{ruta.stem} ({contador}){ruta.suffix}")
        contador += 1
    return libre


def reemplazar(doc, buscar: str, poner: str, palabra_completa: bool = True) -> bool:
    """Reemplaza el texto en todo el documento; False si no estaba."""
    rango = doc.Content
    rango.Find.ClearFormatting()
    rango.Find.Replacement.ClearFormatting()
    # En el texto de reemplazo de Word, "^" es un carácter especial.
    return bool(rango.Find.Execute(
        FindText=buscar, MatchCase=True, MatchWholeWord=palabra_completa, MatchWildcards=False, MatchSoundsLike=False,
        MatchAllWordForms=False, Forward=True, Wrap=CONTINUAR_AL_INICIO, Format=False,
        ReplaceWith=poner.replace("^", "^^"), Replace=REEMPLAZAR_TODO))


def poner_fecha(doc, fecha: str) -> bool:
    """Cambia la fecha de la plantilla (la primera que aparezca) por `fecha`; False si no la encontró."""
    # Los comodines de Word fallan con este patrón ("expresión demasiado compleja"): se busca en Python.
    hallada = FECHA_PLANTILLA.search(doc.Content.Text)
    if not hallada:
        return False
    rango = doc.Content
    rango.Find.ClearFormatting()
    rango.Find.Replacement.ClearFormatting()
    return bool(rango.Find.Execute(
        FindText=hallada.group(0), MatchCase=True, MatchWholeWord=False, MatchWildcards=False, MatchSoundsLike=False,
        MatchAllWordForms=False, Forward=True, Wrap=CONTINUAR_AL_INICIO, Format=False, ReplaceWith=fecha,
        Replace=REEMPLAZAR_UNO))


def agregar_concepto(doc, concepto: str):
    """El concepto va después del párrafo "Dentro de la verificación realizada...", con un renglón vacío en medio."""
    rango = doc.Content
    rango.Find.ClearFormatting()
    if not rango.Find.Execute(FindText=FRASE_CONCEPTO, MatchCase=False, Forward=True, Wrap=0):
        raise ValueError(f"la plantilla no trae la frase «{FRASE_CONCEPTO}…»")
    parrafo = rango.Paragraphs(1).Range
    final = doc.Range(parrafo.End - 1, parrafo.End - 1)
    final.InsertAfter("\r\r" + concepto.replace("\n", "\r"))


class Word:
    """Una instancia propia de Word, oculta: no toca los documentos que el usuario tenga abiertos."""

    def __init__(self):
        pythoncom.CoInitialize()
        self.app = None
        self.abrir()

    def abrir(self):
        self.app = win32com.client.DispatchEx("Word.Application")
        self.app.Visible = False
        self.app.DisplayAlerts = 0

    def sigue_viva(self) -> bool:
        try:
            self.app.Version
            return True
        except pywintypes.com_error:
            return False

    def cerrar(self):
        try:
            self.app.Quit(SaveChanges=NO_GUARDAR)
        except pywintypes.com_error:
            pass
        self.app = None
        pythoncom.CoUninitialize()

    def generar(self, fila: Fila, carpeta: Path, fecha: str) -> Path:
        plantilla = PLANTILLAS / f"{fila.plantilla}.odt"
        if not plantilla.exists():
            raise ValueError(f"falta la plantilla «{plantilla.name}» en la carpeta Plantillas")
        if not self.sigue_viva():
            log("Word se cerró; se abre de nuevo.")
            self.abrir()
        ruta = ruta_libre(carpeta / fila.archivo)
        doc = self.app.Documents.Open(FileName=str(plantilla), ConfirmConversions=False, ReadOnly=True,
                                      AddToRecentFiles=False, Visible=False)
        try:
            marca = "AAA" if fila.perfil == "A" else "BBB"
            # Antes del concepto: un "000" dentro de él (por ejemplo, "$ 1.000.000") no es la cédula.
            for buscar, poner in ((marca, fila.nombre), ("000", fila.cedula)):
                if not reemplazar(doc, buscar, poner):
                    raise ValueError(f"la plantilla «{plantilla.name}» no trae «{buscar}»")
            if fila.perfil == "C" and not reemplazar(doc, "PERFIL B", "PERFIL C"):
                raise ValueError(f"la plantilla «{plantilla.name}» no trae «PERFIL B»")
            # "C.C" con puntos: Word no lo toma como palabra completa.
            if fila.tipo == "PPT" and not reemplazar(doc, "C.C", "PPT", palabra_completa=False):
                raise ValueError(f"la plantilla «{plantilla.name}» no trae «C.C»")
            if not poner_fecha(doc, fecha):
                raise ValueError(f"la plantilla «{plantilla.name}» no trae una fecha como «15 DE OCTUBRE DE 2026»")
            if fila.concepto:
                agregar_concepto(doc, fila.concepto)
            doc.ExportAsFixedFormat(OutputFileName=str(ruta), ExportFormat=FORMATO_PDF, OpenAfterExport=False,
                                    OptimizeFor=0)
        finally:
            doc.Close(SaveChanges=NO_GUARDAR)
        return ruta
