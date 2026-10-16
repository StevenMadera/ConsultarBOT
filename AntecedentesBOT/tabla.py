"""Las filas pegadas desde Excel, con su fila de títulos: nombre, cédula, perfil, concepto y rama de cada persona."""
import csv
import io
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Encabezados que llevan "nombre" pero no son el de la persona.
OTROS_NOMBRES = {"EMPRESA", "CLIENTE", "CARGO", "SEDE", "CIUDAD", "JEFE", "ANALISTA", "USUARIO", "CONTACTO",
                 "REFERENCIA", "PROYECTO", "AREA", "EPS", "ARL", "BANCO", "PADRE", "MADRE", "CONSULTOR", "RESPONSABLE"}
# Conceptos que no agregan nada al documento.
SIN_CONCEPTO = {"", "SIN NOVEDAD"}
PROHIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def normalizar(texto) -> str:
    """Sin tildes, en mayúsculas y solo letras y números separados por un espacio."""
    t = "".join(c for c in unicodedata.normalize("NFD", str(texto or "")) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^A-Z0-9Ñ]+", " ", t.upper()).split())


def rol_de_titulo(titulo: str) -> str | None:
    palabras = normalizar(titulo).split()
    if not palabras:
        return None
    if "PERFIL" in palabras:
        return "perfil"
    if "CONCEPTO" in palabras:
        return "concepto"
    if palabras[0].startswith("OBSERVACION"):
        return "observacion"
    if "RAMA" in palabras:
        return "rama"
    if "TIPO" in palabras:
        # "Tipo de documento" sí; "Tipo de contrato" no.
        if len(palabras) == 1 or any(p == "ID" or p.startswith(("DOC", "IDENT")) for p in palabras):
            return "tipo"
        return None
    # "Fecha de expedición del documento": no es la cédula.
    if any(p in ("FECHA", "LUGAR") or p.startswith(("EXPED", "NACIM")) for p in palabras):
        return None
    if any(p in ("CC", "NUIP", "ID") or p.startswith(("CEDULA", "DOCUMENT", "IDENTIFICACI")) for p in palabras):
        return "cedula"
    if any(p.startswith(("NOMBRE", "APELLIDO")) for p in palabras) or palabras[0] in ("CANDIDATO", "ASPIRANTE"):
        return None if any(p in OTROS_NOMBRES for p in palabras) else "nombre"
    return None


@dataclass
class Fila:
    numero: int
    nombre: str = ""
    cedula: str = ""
    # "CC" o "PPT".
    tipo: str = "CC"
    perfil: str = ""
    # "COM" o "CEDI".
    rama: str = "CEDI"
    concepto: str = ""
    # Datos mal: no se genera el documento.
    errores: list[str] = field(default_factory=list)
    # Se genera igual, pero conviene revisarlo.
    avisos: list[str] = field(default_factory=list)
    ruta: Path | None = None
    falla: str = ""

    @property
    def generable(self) -> bool:
        return not self.errores

    @property
    def plantilla(self) -> str:
        """Nombre de la plantilla (sin extensión): AAA para el perfil A, BBB para B y C; . COM o CEDI según la rama."""
        return f"000 - {'AAA' if self.perfil == 'A' else 'BBB'} - FALABELLA {'. COM' if self.rama == 'COM' else 'CEDI'}"

    @property
    def documento(self) -> str:
        """"1234567890" o "PPT 1234567"."""
        return f"PPT {self.cedula}" if self.tipo == "PPT" else self.cedula

    @property
    def archivo(self) -> str:
        """Como la plantilla, con el documento y el nombre: "PPT 1234567 - NOMBRE APELLIDO - FALABELLA CEDI.pdf"."""
        marca = "AAA" if self.perfil == "A" else "BBB"
        return self.plantilla.replace("000", self.documento, 1).replace(marca, PROHIBIDOS.sub("", self.nombre), 1) + ".pdf"


@dataclass
class Tabla:
    filas: list[Fila]
    # Rol -> título de la columna de donde se toma.
    titulos: dict[str, str]
    avisos: list[str] = field(default_factory=list)


def separar(texto: str) -> list[list[str]]:
    """Celdas separadas por tabulaciones, como las copia Excel (las que tienen saltos de línea van entre comillas)."""
    filas = [[c.strip() for c in f] for f in csv.reader(io.StringIO(texto.strip("\r\n")), delimiter="\t")]
    return [f for f in filas if any(f)]


def limpiar_concepto(texto: str) -> str:
    """Los renglones del concepto, sin renglones vacíos; "Sin novedad" no agrega nada."""
    renglones = [" ".join(r.split()) for r in str(texto).splitlines()]
    concepto = "\n".join(r for r in renglones if r)
    return "" if normalizar(concepto) in SIN_CONCEPTO else concepto


def tipo_de_documento(texto: str) -> str | None:
    """"CC" o "PPT" según lo que diga la celda (vacía: CC); None si es otro tipo."""
    palabras = normalizar(texto).split()
    letras = " ".join(p for p in palabras if not p.isdigit())
    if "PPT" in palabras or "PROTECCION" in palabras or letras.replace(" ", "") == "PPT":
        return "PPT"
    if not letras or letras.replace(" ", "") == "CC" or ("CEDULA" in palabras and "EXTRANJERIA" not in palabras):
        return "CC"
    return None


def armar(texto: str) -> Tabla:
    """Las filas a partir del texto copiado. ValueError si falta la fila de títulos o una columna obligatoria."""
    crudas = separar(texto)
    if not crudas:
        raise ValueError("No hay filas: copia en Excel las filas de las personas, con su fila de títulos.")
    titulos = crudas[0]
    roles = [rol_de_titulo(t) for t in titulos]
    columnas: dict[str, list[int]] = defaultdict(list)
    for i, rol in enumerate(roles):
        if rol:
            columnas[rol].append(i)
    faltan = [nombre for rol, nombre in (("nombre", "NOMBRE"), ("cedula", "CEDULA"), ("perfil", "PERFIL"))
              if not columnas[rol]]
    if faltan:
        raise ValueError(f"No encuentro la columna {', '.join(f'«{f}»' for f in faltan)} en la primera fila. La primera "
                         "fila copiada debe ser la de títulos (NOMBRE, CEDULA, PERFIL, CONCEPTO, RAMA).")
    if len(crudas) < 2:
        raise ValueError("Solo llegó la fila de títulos: copia también las filas de las personas.")
    columna_concepto = (columnas["concepto"] or columnas["observacion"] or [None])[0]
    columna_rama = (columnas["rama"] or [None])[0]
    columna_tipo = (columnas["tipo"] or [None])[0]
    tabla = Tabla([], {
        "nombre": " + ".join(titulos[i] for i in columnas["nombre"]),
        "cedula": titulos[columnas["cedula"][0]],
        "perfil": titulos[columnas["perfil"][0]],
        "concepto": titulos[columna_concepto] if columna_concepto is not None else "",
        "rama": titulos[columna_rama] if columna_rama is not None else "",
    })
    if columna_concepto is None:
        tabla.avisos.append("No hay columna CONCEPTO: los documentos saldrán sin concepto.")
    if columna_rama is None:
        tabla.avisos.append("No hay columna RAMA: todos saldrán con la plantilla CEDI.")
    ancho = len(titulos)
    clave = [*columnas["nombre"], columnas["cedula"][0], columnas["perfil"][0]]
    # (número de fila, celdas, filas de continuación unidas)
    datos: list[tuple[int, list[str], list[int]]] = []
    for n, cruda in enumerate(crudas[1:], start=1):
        celdas = (cruda + [""] * ancho)[:ancho]
        # Sin cédula, nombre ni perfil: es el resto del texto de la fila de arriba (un salto de línea en Excel).
        if datos and not any(celdas[i] for i in clave):
            _, previas, unidas = datos[-1]
            for i, valor in enumerate(celdas):
                if valor:
                    previas[i] = f"{previas[i]} {valor}" if previas[i] else valor
            unidas.append(n)
            continue
        datos.append((n, celdas, []))
    for n, celdas, unidas in datos:
        fila = Fila(n)
        if len(unidas) == 1:
            fila.avisos.append(f"se le unió la fila {unidas[0]}, que traía el resto de su texto")
        elif unidas:
            fila.avisos.append(f"se le unieron las filas {', '.join(map(str, unidas[:-1]))} y {unidas[-1]}, que traían "
                               "el resto de su texto")
        fila.nombre = " ".join(" ".join(celdas[i] for i in columnas["nombre"]).split()).upper()
        if not fila.nombre:
            fila.errores.append("falta el nombre")
        cedula = celdas[columnas["cedula"][0]]
        fila.cedula = re.sub(r"\D", "", cedula)
        if not 4 <= len(fila.cedula) <= 11:
            fila.errores.append(f"la cédula «{cedula}» no es válida" if cedula else "falta la cédula")
        # El tipo puede venir en la celda del documento ("PPT 1234567") o en su propia columna.
        tipo, origen = tipo_de_documento(cedula), cedula
        if tipo == "CC" and columna_tipo is not None and celdas[columna_tipo]:
            tipo, origen = tipo_de_documento(celdas[columna_tipo]), celdas[columna_tipo]
        if tipo is None:
            fila.errores.append(f"el tipo de documento «{origen}» no está en las plantillas (solo C.C y PPT)")
        else:
            fila.tipo = tipo
        perfil = celdas[columnas["perfil"][0]]
        fila.perfil = " ".join(w for w in normalizar(perfil).split() if w != "PERFIL")
        if fila.perfil not in ("A", "B", "C"):
            fila.errores.append(f"el perfil «{perfil}» no es A, B ni C" if perfil else "falta el perfil")
        if columna_rama is not None and re.search(r"\bCOM\b", normalizar(celdas[columna_rama])):
            fila.rama = "COM"
        if columna_concepto is not None:
            fila.concepto = limpiar_concepto(celdas[columna_concepto])
            if normalizar(fila.concepto).startswith("REVISAR"):
                fila.errores.append("el concepto dice «REVISAR»: resuélvelo antes de generar el documento")
        tabla.filas.append(fila)
    repetidas = defaultdict(list)
    for fila in tabla.filas:
        if fila.cedula:
            repetidas[fila.documento].append(fila.numero)
    for fila in tabla.filas:
        otras = [n for n in repetidas.get(fila.documento, []) if n != fila.numero]
        if otras:
            fila.avisos.append(f"documento repetido (fila {', '.join(map(str, otras))})")
    return tabla
