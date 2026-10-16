"""Texto de la columna OBSERVACIÓN a partir de lo que respondió cada sitio."""
from dataclasses import dataclass

SIN_NOVEDAD = "Sin novedad"
NO_OBLIGADO = "NO OBLIGADO A DEFINIR SITUACIÓN MILITAR (MAYOR DE 50 AÑOS)"
NUMEROS = ("cero", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
           "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte")
# Lo que el RNMC escribe cuando no hubo descargos.
SIN_DESCARGOS = {"", "-", ".", "NA", "N/A", "NO APLICA", "NO REPORTADO", "SIN DESCARGOS", "NINGUNO", "NINGUNA"}


@dataclass
class Libreta:
    # "RESERVISTA - 2DA CLASE", "INSCRIPCIÓN - EN REGISTRO"...; None si el sitio no tiene a la persona.
    estado: str | None
    # Mujeres y documentos que la libreta no consulta (CE, PEP...): sin novedad.
    no_aplica: bool = False


@dataclass
class Simit:
    comparendos: int
    multas: int
    acuerdos: int
    total: str


@dataclass
class Anotacion:
    expediente: str
    articulo: str
    numeral: str
    relato: str
    descargos: str
    fecha: str


def limpio(texto: str) -> str:
    return " ".join(str(texto or "").split())


def cantidad(n: int, singular: str, plural: str, femenino: bool = False) -> str:
    """3 -> "tres multas"; 1 -> "una multa"."""
    if n == 1:
        palabra = "una" if femenino else "un"
    else:
        palabra = NUMEROS[n] if n < len(NUMEROS) else str(n)
    return f"{palabra} {singular if n == 1 else plural}"


def texto_libreta(libreta: Libreta, al_final: bool) -> str:
    """Vacío si no hay novedad. `al_final`: va después del RNMC o del SIMIT, como frase corta."""
    if libreta.no_aplica or libreta.estado == NO_OBLIGADO or (libreta.estado and "RESERVISTA" in libreta.estado.upper()):
        return ""
    if not libreta.estado:
        return ("NO REGISTRA ante el sistema de reclutamiento nacional." if al_final
                else "Tener en cuenta que el candidato NO REGISTRA ante el sistema de reclutamiento nacional.")
    estado = limpio(libreta.estado)
    return (f"Se encuentra {estado} ante el sistema de reclutamiento nacional." if al_final
            else f"Tener en cuenta que el candidato se encuentra {estado} ante el sistema de reclutamiento nacional.")


def texto_simit(simit: Simit) -> str:
    """Vacío si no tiene comparendos, multas ni acuerdos de pago."""
    pendientes = []
    if simit.comparendos:
        pendientes.append(cantidad(simit.comparendos, "comparendo", "comparendos"))
    if simit.multas:
        pendientes.append(cantidad(simit.multas, "multa", "multas", femenino=True))
    cosas = f"{' y '.join(pendientes)} sin acuerdo de pago" if pendientes else ""
    if simit.acuerdos:
        acuerdos = cantidad(simit.acuerdos, "acuerdo de pago", "acuerdos de pago")
        cosas = f"{cosas} y {acuerdos}" if cosas else acuerdos
    if not cosas:
        return ""
    return f"Posee en el simit {cosas}, con un valor a pagar de {simit.total}."


def texto_anotacion(anotacion: Anotacion) -> str:
    descargos = limpio(anotacion.descargos)
    partes = [
        ("Número de Expediente", anotacion.expediente),
        ("Artículo", anotacion.articulo),
        ("Numeral", anotacion.numeral),
        ("Relato Hechos", anotacion.relato),
        ("Descargos", "" if descargos.upper() in SIN_DESCARGOS else descargos),
        ("", anotacion.fecha),
    ]
    texto = " - ".join(f"{titulo}: {limpio(valor)}" if titulo else limpio(valor)
                       for titulo, valor in partes if limpio(valor))
    return texto.rstrip(". ") + "."


def texto_rnmc(anotaciones: list[Anotacion]) -> str:
    """Vacío si no tiene anotaciones; con varias, cada una numerada."""
    if not anotaciones:
        return ""
    cabeza = (f"Posee {cantidad(len(anotaciones), 'anotación', 'anotaciones', femenino=True)} en el Sistema Registro "
              "Nacional de Medidas Correctivas RNMC - ")
    if len(anotaciones) == 1:
        return cabeza + texto_anotacion(anotaciones[0])
    return cabeza + " ".join(f"{i}. {texto_anotacion(a)}" for i, a in enumerate(anotaciones, start=1))


def observacion(libreta: Libreta, simit: Simit, anotaciones: list[Anotacion]) -> str:
    """RNMC, luego SIMIT y al final la situación militar; "Sin novedad" si nada aplica."""
    partes = [texto for texto in (texto_rnmc(anotaciones), texto_simit(simit)) if texto]
    militar = texto_libreta(libreta, al_final=bool(partes))
    if militar:
        partes.append(militar)
    return " ".join(partes) or SIN_NOVEDAD
