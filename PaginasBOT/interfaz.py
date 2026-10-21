"""Ventana de PaginasBOT: pide los datos, muestra el avance y los captchas y entrega el informe."""
import asyncio
import base64
import concurrent.futures
import ctypes
import itertools
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import winsound
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, ttk

from playwright.async_api import async_playwright

import comun
from captura_navegador import PANTALLA
from comun import NOMBRE, RAIZ, log
from main import (CAMPOS, FORMATO, SEXOS, abrir_navegadores, carpeta_salidas, cerrar_navegadores, minutos, partir_fila,
                  procesar, sin_fecha_expedicion, sitios_de, validar)

# actualizar.py está en la carpeta que contiene las apps; si PaginasBOT se copió sola, no hay botón de actualizar.
sys.path.append(str(RAIZ.parent))
try:
    import actualizar
except ImportError:
    actualizar = None

FONDO = "#ffffff"
TEXTO = "#1f2937"
SUAVE = "#6b7280"
APAGADO = "#9ca3af"
BORDE = "#d1d5db"
GRIS = "#f3f4f6"
ACENTO = "#2563eb"
ACENTO_OSCURO = "#1d4ed8"
VERDE = "#16a34a"
ROJO = "#dc2626"
AMBAR = "#b45309"
LETRA = ("Segoe UI", 10)
LETRA_PEQUENA = ("Segoe UI", 9)
LETRA_NEGRITA = ("Segoe UI Semibold", 10)
LETRA_TITULO = ("Segoe UI Semibold", 13)
LETRA_CODIGO = ("Segoe UI", 15)
ANCHO = 410

# En el orden del informe.
SITIOS = {
    "procuraduria": "Procuraduría", "runt": "RUNT", "simit": "SIMIT", "libreta": "Libreta militar", "ofac": "OFAC",
    "consolidado": "ONU", "govuk": "GOV.UK", "fbi": "FBI", "justicia": "Justice", "terroristas": "State.gov",
    "consolium": "EUR-Lex", "eumostwanted": "EU Most Wanted", "consilium": "Consilium", "bdme": "BDME",
    "contraloria": "Contraloría", "antecedentes": "Antecedentes", "registraduria": "Registraduría", "rnmc": "RNMC",
    "inhabilidades": "Inhabilidades",
    # Solo en el formato de GALLAGHER.
    "insight": "InSight Crime", "contadores": "Junta de Contadores", "dian": "DIAN ficticios", "samm": "SAMM (armas)",
    "bid": "BID", "canada": "Canadá", "tesoro": "OFAC (Tesoro)",
}
ESTADOS = {
    "pendiente": ("○", APAGADO),
    "en_curso": ("●", ACENTO),
    "listo": ("✓", VERDE),
    "error": ("✕", ROJO),
    "no_aplica": ("–", APAGADO),
    "fuera_servicio": ("!", AMBAR),
}
TERMINADOS = {"listo", "error", "no_aplica", "fuera_servicio"}
ETIQUETAS = ("Nombre", "Documento", "Empresa", "Fecha consulta", "Quién consulta", "Fecha expedición")
# Lo que la ventana ya muestra de otra forma.
OMITIDOS = re.compile(r"Captura |Tiempo|Datos:|PDF: [A-Za-z]:\\")
NOVEDADES = RAIZ / "novedades.txt"
# Fuera de la carpeta del programa: un archivo nuevo ahí frenaría la actualización con git.
NOVEDADES_VISTAS = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / NOMBRE / "novedades_vistas.txt"

user32 = ctypes.windll.user32


def area_de_trabajo() -> tuple[int, int, int, int]:
    """La pantalla sin la barra de tareas."""
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


def dar_foco(hwnd: int):
    """Windows solo deja que una ventana de atrás tome el foco justo después de pulsar Alt."""
    if user32.GetForegroundWindow() == hwnd:
        return
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def excluir_de_capturas(hwnd: int, excluir: bool = True) -> bool:
    """Se ve en el monitor pero no sale en las capturas de pantalla (FBI y Consilium), aunque esté encima."""
    return bool(user32.SetWindowDisplayAffinity(hwnd, 0x11 if excluir else 0))  # WDA_EXCLUDEFROMCAPTURE / WDA_NONE


def foto(png: bytes) -> tk.PhotoImage:
    return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))


def primera_linea(texto) -> str:
    return next(iter(str(texto).strip().splitlines()), "")


def separar_titulo(mensaje: str) -> tuple[str, str]:
    """'Consilium: Acepta… Pulsa Enter cuando…' -> ('Consilium', 'Acepta…'): en la ventana se pulsa «Listo»."""
    titulo, _, texto = mensaje.partition(": ")
    if not texto:
        titulo, texto = NOMBRE, mensaje
    texto = re.sub(r"\s*(?:y\s+)?pulsa enter\b.*$", "", texto, flags=re.IGNORECASE | re.DOTALL).strip()
    texto = texto[:1].upper() + texto[1:]
    return titulo, texto if texto.endswith(".") else f"{texto}."


def explicar(detalle: str) -> str:
    if re.search(r"Timeout \d+ms exceeded", detalle):
        return "el sitio no respondió a tiempo"
    if "net::ERR_" in detalle:
        return "no se pudo conectar con el sitio"
    return detalle if len(detalle) <= 110 else detalle[:109] + "…"


def novedades_pendientes() -> list[tuple[str, list[str]]]:
    """Las versiones de novedades.txt que este equipo aún no vio, la más nueva primero."""
    try:
        lineas = NOVEDADES.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    versiones = []
    for linea in map(str.strip, lineas):
        if linea.startswith("==") and linea.endswith("=="):
            versiones.append((linea.strip("= "), []))
        elif linea.startswith("-") and versiones:
            versiones[-1][1].append(linea.lstrip("- "))
    try:
        vista = NOVEDADES_VISTAS.read_text(encoding="utf-8").strip()
    except OSError:
        vista = None
    titulos = [titulo for titulo, _ in versiones]
    if vista is None:
        # Nunca vio esta ventana: se actualizó desde una versión anterior a las novedades y le faltan todas.
        return versiones
    return versiones[:titulos.index(vista)] if vista in titulos else versiones[:1]


class Interfaz:
    def __init__(self):
        # Sin registro, es la primera vez que se abre en este equipo: no hay novedades que contar.
        self.instalacion_nueva = not (RAIZ / "log.txt").exists()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(NOMBRE)
        self.root.resizable(False, False)
        self.root.configure(background=FONDO)
        self.root.report_callback_exception = self._error_en_ventana
        self.root.iconbitmap(default=str(RAIZ / "icono.ico"))
        self._estilos()

        self.vivo = True
        self.reiniciar = False
        self.eventos = queue.Queue()
        self.estado = "datos"
        self.preguntas: list[dict] = []
        self.accion: tuple[str, str] | None = None
        self.capturando: tuple[str, ...] = ()
        self.sitios: dict[str, str] = {}
        self.detalles: dict[str, str] = {}
        self.omitidos: list[str] = []
        self.chips = {}
        self.inicio = 0.0
        self.ruta = None
        self._numero = itertools.count()
        self._vista = None
        self._con_enter = None
        self._foco = None
        self._imagen = None
        self._reloj = None
        self._secuencia = user32.GetClipboardSequenceNumber()
        self._intentos = 0
        self._ultimo_error = None

        self.v_datos = self._armar_datos()
        self.v_consultando = self._armar_progreso()
        self.v_resultado = self._armar_resultado()
        self.v_aviso = ttk.Frame(self.root)
        self.v_cerrando = ttk.Frame(self.root, padding=26)
        ttk.Label(self.v_cerrando, text="Cerrando los navegadores…", style="Suave.TLabel").pack()
        self.v_actualizando = ttk.Frame(self.root, padding=26)
        ttk.Label(self.v_actualizando, text="Actualizando…", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(self.v_actualizando, text=f"{NOMBRE} se volverá a abrir al terminar. Si hay librerías nuevas, puede "
                  "tardar unos minutos.", style="Suave.TLabel", wraplength=360, justify="left").pack(anchor="w",
                                                                                                     pady=(6, 0))

        self.root.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        for tecla in ("<Return>", "<KP_Enter>"):
            self.root.bind(tecla, lambda _: self._con_enter and self._con_enter())
        self._mostrar()
        self.root.deiconify()
        self.root.update_idletasks()
        self.hwnd = int(self.root.wm_frame(), 16)
        self.excluida = excluir_de_capturas(self.hwnd)
        if not self.excluida:
            log("Ventana: Windows no permitió excluirla de las capturas; podría salir en las de FBI y Consilium.")
        self.root.bind("<F9>", lambda _: self._alternar_exclusion())
        self.entradas[0].focus_set()

        self.bucle = asyncio.new_event_loop()
        self.personas = asyncio.Queue()
        self.sesion = None
        comun.escuchar(lambda tipo, datos: self.eventos.put((tipo, datos)))
        threading.Thread(target=self._hilo_bot, daemon=True).start()
        if actualizar:
            threading.Thread(target=self._buscar_version, daemon=True).start()
            self.root.after(700, self._mostrar_novedades)
        self.root.after(200, self._atender)
        self.root.after(600, self._vigilar_portapapeles)

    def _alternar_exclusion(self):
        """F9: deja ver el bot por AnyDesk/Meet (pero saldría en las capturas de FBI y Consilium) o lo vuelve a ocultar."""
        if excluir_de_capturas(self.hwnd, not self.excluida):
            self.excluida = not self.excluida
            log(f"Ventana: {'oculta de' if self.excluida else 'visible en'} capturas y escritorio remoto (F9).")

    # ---------- Aspecto ----------

    def _estilos(self):
        estilo = ttk.Style(self.root)
        estilo.theme_use("clam")
        estilo.configure(".", background=FONDO, foreground=TEXTO, font=LETRA, bordercolor=BORDE)
        estilo.configure("Titulo.TLabel", font=LETRA_TITULO)
        estilo.configure("Negrita.TLabel", font=LETRA_NEGRITA)
        estilo.configure("Suave.TLabel", foreground=SUAVE)
        estilo.configure("Pie.TLabel", foreground=SUAVE, font=LETRA_PEQUENA)
        estilo.configure("Error.TLabel", foreground=ROJO)
        estilo.configure("Aviso.TLabel", foreground=AMBAR)
        estilo.configure("Enlace.TLabel", foreground=ACENTO, font=LETRA_PEQUENA)
        estilo.configure("TEntry", fieldbackground=FONDO, lightcolor=BORDE, darkcolor=BORDE, padding=(6, 4))
        estilo.map("TEntry", bordercolor=[("focus", ACENTO)], lightcolor=[("focus", ACENTO)])
        estilo.configure("TButton", background=GRIS, lightcolor=GRIS, darkcolor=GRIS, padding=(14, 5))
        estilo.map("TButton", background=[("pressed", BORDE), ("active", "#e5e7eb")],
                   lightcolor=[("active", "#e5e7eb")], darkcolor=[("active", "#e5e7eb")])
        estilo.configure("Acento.TButton", background=ACENTO, foreground=FONDO, bordercolor=ACENTO,
                         lightcolor=ACENTO, darkcolor=ACENTO)
        estilo.map("Acento.TButton", background=[("pressed", ACENTO_OSCURO), ("active", ACENTO_OSCURO)],
                   lightcolor=[("active", ACENTO_OSCURO)], darkcolor=[("active", ACENTO_OSCURO)],
                   bordercolor=[("active", ACENTO_OSCURO)])
        estilo.map("TRadiobutton", background=[("active", FONDO)])
        estilo.configure("TSeparator", background=BORDE)

    def _armar_datos(self) -> ttk.Frame:
        v = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        v.columnconfigure(1, weight=1)
        ttk.Label(v, text="Nueva consulta", style="Titulo.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(v, text="Copia la fila en Excel o LibreOffice y los datos se llenan solos.", style="Suave.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.entradas = []
        for fila, etiqueta in enumerate(ETIQUETAS, start=2):
            ttk.Label(v, text=etiqueta, style="Suave.TLabel").grid(row=fila, column=0, sticky="w", padx=(0, 14), pady=3)
            entrada = ttk.Entry(v, font=LETRA, width=32)
            entrada.grid(row=fila, column=1, sticky="we", pady=3)
            entrada.bind("<<Paste>>", self._pegar)
            self.entradas.append(entrada)
        ttk.Label(v, text="Sexo", style="Suave.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 14), pady=3)
        self.sexo = tk.StringVar()
        opciones = ttk.Frame(v)
        opciones.grid(row=8, column=1, sticky="w", pady=3)
        for valor, texto in (("H", "Hombre"), ("M", "Mujer")):
            ttk.Radiobutton(opciones, text=texto, value=valor, variable=self.sexo).pack(side="left", padx=(0, 18))
        self.error = ttk.Label(v, style="Error.TLabel", wraplength=360, justify="left")
        self.error.grid(row=9, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.error.grid_remove()
        ttk.Button(v, text="Consultar", style="Acento.TButton", command=self._consultar).grid(
            row=10, column=1, sticky="e", pady=(14, 0))
        pie = ttk.Frame(v)
        pie.grid(row=11, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.punto = tk.Label(pie, text="●", fg=AMBAR, bg=FONDO, font=LETRA_PEQUENA)
        self.punto.pack(side="left")
        self.texto_navegadores = ttk.Label(pie, text="Preparando los navegadores…", style="Pie.TLabel", wraplength=340)
        self.texto_navegadores.pack(side="left", padx=(4, 0))
        self.texto_version = ttk.Label(v, style="Error.TLabel", wraplength=360, justify="left")
        self.texto_version.grid(row=12, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.texto_version.grid_remove()
        return v

    def _armar_progreso(self) -> ttk.Frame:
        v = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        cabeza = ttk.Frame(v)
        cabeza.pack(fill="x")
        ttk.Label(cabeza, text="Consultando", style="Titulo.TLabel").pack(side="left")
        self.reloj = ttk.Label(cabeza, style="Suave.TLabel")
        self.reloj.pack(side="right")
        self.persona_texto = ttk.Label(v, style="Suave.TLabel")
        self.persona_texto.pack(anchor="w")
        fila = ttk.Frame(v)
        fila.pack(fill="x", pady=12)
        self.barra = tk.Canvas(fila, height=6, bg="#e5e7eb", highlightthickness=0, bd=0)
        self.barra.pack(side="left", fill="x", expand=True)
        self.relleno = self.barra.create_rectangle(0, 0, 0, 6, fill=ACENTO, width=0)
        self.cuenta = ttk.Label(fila, style="Suave.TLabel")
        self.cuenta.pack(side="left", padx=(10, 0))
        self.rejilla = ttk.Frame(v)
        self.rejilla.pack(fill="x")
        # Dos renglones fijos: la ventana no cambia de alto con cada mensaje.
        self.linea_estado = tk.Label(v, bg=FONDO, fg=SUAVE, font=LETRA_PEQUENA, height=2, anchor="nw",
                                     justify="left", wraplength=360)
        self.linea_estado.pack(fill="x", pady=(12, 0))
        registro = ttk.Label(v, text="Ver registro", style="Enlace.TLabel", cursor="hand2")
        registro.pack(anchor="e")
        registro.bind("<Button-1>", lambda _: os.startfile(RAIZ / "log.txt"))
        return v

    def _armar_resultado(self) -> ttk.Frame:
        v = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        v.columnconfigure(0, weight=1)
        self.titulo_resultado = ttk.Label(v, style="Titulo.TLabel")
        self.titulo_resultado.grid(row=0, column=0, sticky="w")
        self.tiempo = ttk.Label(v, style="Suave.TLabel")
        self.tiempo.grid(row=0, column=1, sticky="e")
        self.archivo = ttk.Label(v, wraplength=360, justify="left")
        self.archivo.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        botones = ttk.Frame(v)
        botones.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.boton_pdf = ttk.Button(botones, text="Abrir PDF", style="Acento.TButton",
                                    command=lambda: os.startfile(self.ruta))
        self.boton_carpeta = ttk.Button(botones, text="Abrir carpeta", command=self._abrir_carpeta)
        self.boton_carpeta.pack(side="left")
        self.fallos = ttk.Label(v, style="Error.TLabel", wraplength=360, justify="left")
        self.fallos.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.caidos = ttk.Label(v, style="Aviso.TLabel", wraplength=360, justify="left")
        self.caidos.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.no_aplica = ttk.Label(v, style="Suave.TLabel", wraplength=360, justify="left")
        self.no_aplica.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.texto_omitidos = ttk.Label(v, style="Aviso.TLabel", wraplength=360, justify="left")
        self.texto_omitidos.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Separator(v).grid(row=7, column=0, columnspan=2, sticky="we", pady=(14, 10))
        pie = ttk.Frame(v)
        pie.grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Button(pie, text="Nueva consulta", command=self._nueva).pack(side="left")
        ttk.Label(pie, text="o copia la siguiente fila en Excel", style="Pie.TLabel").pack(side="left", padx=(10, 0))
        return v

    def _armar_pregunta(self, pregunta: dict):
        v = self.v_aviso
        if pregunta["modo"] == "esquina":
            titulo, texto = separar_titulo(pregunta["mensaje"])
            self._armar_tarjeta(titulo, f"{texto} Luego pulsa «Listo».", listo=lambda: self._responder(""))
            return
        v.configure(padding=(22, 18, 22, 16))
        titulo, texto = separar_titulo(pregunta["mensaje"])
        if pregunta.get("titulo"):
            titulo, texto = pregunta["titulo"], ""
        cabeza = ttk.Frame(v)
        cabeza.pack(fill="x")
        ttk.Label(cabeza, text=titulo, style="Titulo.TLabel").pack(side="left")
        if pregunta.get("intento", 1) > 1:
            ttk.Label(cabeza, text=f"Intento {pregunta['intento']} de 3", style="Aviso.TLabel").pack(side="right")
        if pregunta.get("opciones"):
            ttk.Label(v, text="Elige la que corresponde a la persona consultada:", style="Suave.TLabel").pack(
                anchor="w", pady=(4, 10))
            for i, opcion in enumerate(pregunta["opciones"]):
                ttk.Button(v, text=opcion, command=lambda i=i: self._responder(str(i + 1))).pack(fill="x", pady=2)
            return
        if texto:
            ttk.Label(v, text=texto, wraplength=360, justify="left").pack(anchor="w", pady=(4, 0))
        if pregunta.get("imagen"):
            self._imagen = foto(pregunta["imagen"])
            # La de la Registraduría es pequeña; la del RUNT ya se lee bien.
            if self._imagen.width() < 200:
                self._imagen = self._imagen.zoom(2)
            elif self._imagen.width() > 360:
                self._imagen = self._imagen.subsample(2)
            marco = tk.Frame(v, bg=BORDE, padx=1, pady=1)
            marco.pack(pady=(14, 0))
            tk.Label(marco, image=self._imagen, bg=FONDO).pack()
            ayuda = "Escribe lo que ves en la imagen y pulsa Enter."
        else:
            ayuda = "Míralo en la ventana del navegador que quedó al frente, escríbelo aquí y pulsa Enter."
        fila = ttk.Frame(v)
        fila.pack(fill="x", pady=(14, 0))
        entrada = ttk.Entry(fila, font=LETRA_CODIGO, width=14, justify="center")
        entrada.pack(side="left", fill="x", expand=True)

        def enviar():
            if entrada.get().strip():
                self._responder(entrada.get().strip())
            else:
                self.root.bell()

        ttk.Button(fila, text="Enviar", style="Acento.TButton", command=enviar).pack(side="left", padx=(10, 0))
        ttk.Label(v, text=ayuda, style="Pie.TLabel").pack(anchor="w", pady=(8, 0))
        self._con_enter, self._foco = enviar, entrada

    def _armar_tarjeta(self, titulo: str, texto: str = "", listo=None, color: str = AMBAR):
        """Aviso pequeño para lo que pasa en el navegador."""
        v = self.v_aviso
        # Sin texto ni botón, lo más baja posible: arriba solo debe tapar la barra del navegador.
        v.configure(padding=(16, 10, 16, 12) if texto or listo else (16, 5, 16, 7))
        izquierda = ttk.Frame(v)
        izquierda.pack(side="left", fill="both", expand=True)
        ttk.Label(izquierda, text=titulo, style="Negrita.TLabel", foreground=color).pack(anchor="w")
        if texto:
            ttk.Label(izquierda, text=texto, wraplength=240 if listo else 320, justify="left").pack(
                anchor="w", pady=(2, 0))
        if listo:
            ttk.Button(v, text="Listo", style="Acento.TButton", command=listo).pack(side="right", padx=(14, 0))
            self._con_enter = listo

    def _mostrar(self):
        """Muestra lo que toca: una pregunta, un paso en el navegador o la pantalla del estado actual."""
        if self.estado == "cerrando":
            clave, esquina = "cerrando", False
        elif self.preguntas:
            clave, esquina = ("pregunta", self.preguntas[0]["n"]), self.preguntas[0]["modo"] == "esquina"
        elif self.accion:
            clave, esquina = ("accion", *self.accion), True
        elif self.capturando:
            # Una sola línea arriba a la derecha: queda sobre la barra del navegador, fuera de la captura.
            clave, esquina = ("capturando", *self.capturando), True
        else:
            clave, esquina = self.estado, False
        # Si no cambió, no se rehace: se perdería lo que se está escribiendo.
        if clave == self._vista:
            return
        self._vista = clave
        for hijo in self.root.pack_slaves():
            hijo.pack_forget()
        self._con_enter = self._consultar if clave == "datos" else None
        self._foco = None
        if isinstance(clave, tuple):
            vista = self.v_aviso
            for hijo in vista.winfo_children():
                hijo.destroy()
            if clave[0] == "pregunta":
                self._armar_pregunta(self.preguntas[0])
            elif clave[0] == "accion":
                self._armar_tarjeta(clave[1], clave[2])
            else:
                self._armar_tarjeta(f"Capturando en Chrome: {', '.join(clave[1:])}…", color=ACENTO)
        else:
            vista = getattr(self, f"v_{clave}")
        vista.pack(fill="both", expand=True)
        self._colocar(esquina)
        if isinstance(clave, tuple) and clave[0] != "capturando":
            self._llamar_atencion(enfocar=not esquina)

    def _colocar(self, esquina: bool):
        self.root.minsize(0 if esquina else ANCHO, 0)
        self.root.update_idletasks()
        ancho = max(self.root.winfo_reqwidth(), 0 if esquina else ANCHO)
        alto = self.root.winfo_reqheight()
        izquierda, arriba, derecha, abajo = area_de_trabajo()
        if esquina:
            # Arriba a la derecha solo tapa la barra del navegador, no la página.
            x, y = derecha - ancho - 24, arriba
        else:
            x, y = (izquierda + derecha - ancho) // 2, (arriba + abajo - alto) // 2 - 20
        self.root.geometry(f"+{x}+{y}")
        self.root.attributes("-topmost", True)

    def _llamar_atencion(self, enfocar: bool):
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        if self.root.state() == "iconic":
            self.root.deiconify()
        if enfocar:
            dar_foco(self.hwnd)
            self.root.focus_force()
            if self._foco:
                self._foco.focus_set()

    # ---------- Datos ----------

    def _pegar(self, _evento):
        """Ctrl+V con la fila entera de Excel en cualquier casilla la reparte en todas."""
        try:
            texto = self.root.clipboard_get()
        except tk.TclError:
            return None
        campos = partir_fila(texto)
        if len(campos) != len(CAMPOS) or not re.search(r"[\t;]", texto):
            return None
        self._llenar(campos)
        return "break"

    def _vigilar_portapapeles(self):
        # Windows numera cada cambio del portapapeles: solo se lee cuando cambió.
        secuencia = user32.GetClipboardSequenceNumber()
        if secuencia != self._secuencia:
            self._secuencia = secuencia
            # LibreOffice entrega el texto un momento después de copiar: mientras no se pueda leer, se reintenta.
            self._intentos = 15 if self.estado in ("datos", "resultado") and not self.preguntas else 0
        if self._intentos:
            self._intentos = 0 if self._fila_copiada() else self._intentos - 1
            if not self._intentos and self._ultimo_error:
                log(f"Portapapeles: no se pudo leer lo copiado ({self._ultimo_error}).")
        self.root.after(200 if self._intentos else 600, self._vigilar_portapapeles)

    def _fila_copiada(self) -> bool:
        """Llena los datos si lo copiado es una fila; False si todavía no se pudo leer."""
        try:
            texto = self.root.clipboard_get()
        except tk.TclError as e:
            self._ultimo_error = str(e)
            return False
        self._ultimo_error = None
        if not texto.strip():
            self._ultimo_error = "vacío"
            return False
        campos = partir_fila(texto)
        if "\t" not in texto or len(campos) != len(CAMPOS):
            if "\t" in texto:
                log(f"Portapapeles: la fila copiada tiene {len(campos)} datos y se esperan {len(CAMPOS)}.")
            return True
        if self.estado == "resultado":
            self._nueva()
        self._llenar(campos)
        if self.root.state() == "iconic":
            self.root.deiconify()
        dar_foco(self.hwnd)
        self.root.focus_force()
        return True

    def _llenar(self, campos: list[str]):
        for entrada, valor in zip(self.entradas, campos):
            entrada.delete(0, "end")
            entrada.insert(0, valor)
        self.sexo.set(SEXOS.get(campos[-1].upper(), "")[:1])
        self._validar()

    def _validar(self) -> dict | None:
        campos = [" ".join(entrada.get().split()) for entrada in self.entradas] + [self.sexo.get()]
        try:
            persona = validar(campos)
        except ValueError as e:
            self._mostrar_error(str(e).replace(FORMATO, "").strip())
            return None
        self._mostrar_error("")
        return persona

    def _mostrar_error(self, texto: str):
        self.error.configure(text=texto)
        if texto:
            self.error.grid()
        else:
            self.error.grid_remove()
        if self._vista == "datos":
            self._colocar(False)

    def _consultar(self):
        persona = self._validar()
        if persona is None:
            return
        try:
            self.bucle.call_soon_threadsafe(self.personas.put_nowait, persona)
        except RuntimeError:
            self._mostrar_error(f"El bot se detuvo. Cierra {NOMBRE} y vuelve a abrirlo; el motivo está en el registro.")
            return
        self.estado = "consultando"
        self.accion, self.capturando = None, ()
        self._preparar_progreso(persona)
        self._mostrar()

    def _nueva(self):
        for entrada in self.entradas:
            entrada.delete(0, "end")
        self.sexo.set("")
        self._mostrar_error("")
        self.estado = "datos"
        self._mostrar()
        self.entradas[0].focus_set()

    # ---------- Avance ----------

    def _preparar_progreso(self, persona: dict):
        for hijo in self.rejilla.winfo_children():
            hijo.destroy()
        claves = sitios_de(persona)
        self.sitios = dict.fromkeys(claves, "pendiente")
        self.detalles = {}
        self.chips = {}
        omitidos = sin_fecha_expedicion(persona)
        self.omitidos = [nombre for clave, nombre in SITIOS.items() if clave in omitidos]
        filas = -(-len(claves) // 3)
        for i, clave in enumerate(claves):
            marco = ttk.Frame(self.rejilla)
            marco.grid(row=i % filas, column=i // filas, sticky="w", padx=(0, 16), pady=1)
            simbolo = tk.Label(marco, bg=FONDO, font=LETRA_PEQUENA, width=2, anchor="w")
            simbolo.pack(side="left")
            nombre = tk.Label(marco, text=SITIOS[clave], bg=FONDO, font=LETRA_PEQUENA)
            nombre.pack(side="left")
            self.chips[clave] = (simbolo, nombre)
            self._pintar(clave)
        self.persona_texto.configure(text=f"{persona['nombre']} · {persona['tipo']} {persona['numero']}")
        self.linea_estado.configure(text="Abriendo las páginas…")
        self._contar()
        self.inicio = time.monotonic()
        if self._reloj:
            self.root.after_cancel(self._reloj)
        self._tic()

    def _pintar(self, clave: str):
        simbolo, nombre = self.chips[clave]
        marca, color = ESTADOS[self.sitios[clave]]
        simbolo.configure(text=marca, fg=color)
        nombre.configure(fg=APAGADO if self.sitios[clave] == "no_aplica" else TEXTO)

    def _contar(self):
        hechos, total = sum(estado in TERMINADOS for estado in self.sitios.values()), len(self.sitios)
        self.barra.update_idletasks()
        self.barra.coords(self.relleno, 0, 0, self.barra.winfo_width() * hechos / total, 6)
        self.cuenta.configure(text=f"{hechos} de {total}")
        self.root.title(f"{NOMBRE} · {hechos}/{total}")
        if hechos == total:
            self.linea_estado.configure(text="Armando el PDF…")

    def _tic(self):
        self.reloj.configure(text=minutos(time.monotonic() - self.inicio))
        self._reloj = self.root.after(1000, self._tic) if self.estado == "consultando" else None

    def _abrir_carpeta(self):
        if self.ruta and self.ruta.exists():
            subprocess.Popen(["explorer", "/select,", str(self.ruta)])
        else:
            os.startfile(carpeta_salidas())

    # ---------- Comunicación con el bot ----------

    def _hilo_bot(self):
        asyncio.set_event_loop(self.bucle)
        try:
            self.bucle.run_until_complete(self._sesion())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"ERROR: {e}")
            self.eventos.put(("caido", {"error": primera_linea(e)}))
        finally:
            self.bucle.close()
            self.eventos.put(("terminado", {}))

    async def _sesion(self):
        self.sesion = asyncio.current_task()
        async with async_playwright() as p:
            apertura = asyncio.create_task(abrir_navegadores(p))
            apertura.add_done_callback(self._navegadores_listos)
            try:
                while True:
                    persona = await self.personas.get()
                    try:
                        ruta = await procesar(persona, apertura, self.preguntar, self.preguntar_edge)
                        self.eventos.put(("fin", {"ruta": ruta}))
                    except Exception as e:
                        log(f"ERROR: {e}")
                        self.eventos.put(("fin", {"ruta": None, "error": primera_linea(e)}))
            finally:
                await cerrar_navegadores(apertura)

    def _navegadores_listos(self, tarea: asyncio.Task):
        if not tarea.cancelled():
            error = tarea.exception()
            self.eventos.put(("navegadores", {"error": primera_linea(error) if error else None}))

    def _cancelar_sesion(self):
        if self.sesion:
            self.sesion.cancel()

    async def preguntar(self, mensaje: str, **datos) -> str:
        return await asyncio.to_thread(self._preguntar_con_pantalla, mensaje, datos)

    def _preguntar_con_pantalla(self, mensaje: str, datos: dict) -> str:
        # Como en la consola: una pregunta no coincide con una captura de Chrome.
        with PANTALLA:
            return self._preguntar(mensaje, "centro", datos)

    def preguntar_edge(self, mensaje: str) -> str:
        """Pasos en el navegador durante las capturas de Chrome (verificación, cookies): basta pulsar «Listo»."""
        return self._preguntar(mensaje, "esquina", {})

    def _preguntar(self, mensaje: str, modo: str, datos: dict) -> str:
        futuro = concurrent.futures.Future()
        self.eventos.put(("pregunta", {**datos, "mensaje": mensaje, "modo": modo, "futuro": futuro}))
        return futuro.result()

    def _responder(self, valor: str):
        self.preguntas.pop(0)["futuro"].set_result(valor)
        self._mostrar()

    def _atender(self):
        """Lo que llega del bot se atiende aquí: Tkinter solo se toca desde su propio hilo."""
        while self.vivo:
            try:
                tipo, datos = self.eventos.get_nowait()
            except queue.Empty:
                self.root.after(200, self._atender)
                return
            getattr(self, f"_al_{tipo}")(**datos)

    def _al_log(self, texto: str):
        linea = primera_linea(texto)
        if self.estado == "consultando" and linea and not OMITIDOS.match(linea):
            self.linea_estado.configure(text=linea if len(linea) <= 150 else linea[:149] + "…")

    def _al_sitio(self, clave: str, estado: str, detalle: str = ""):
        if self.estado != "consultando" or clave not in self.sitios:
            return
        self.sitios[clave] = estado
        self.detalles[clave] = primera_linea(detalle)
        self._pintar(clave)
        self._contar()

    def _al_accion(self, titulo: str, texto: str):
        self.accion = (titulo, texto)
        self._mostrar()

    def _al_accion_fin(self):
        self.accion = None
        self._mostrar()

    def _al_capturando(self, claves: list[str]):
        self.capturando = tuple(SITIOS.get(clave, clave) for clave in claves)
        self._mostrar()

    def _al_pregunta(self, **pregunta):
        if self.estado == "cerrando":
            pregunta["futuro"].set_exception(RuntimeError(f"Se cerró {NOMBRE}."))
            return
        pregunta["n"] = next(self._numero)
        self.preguntas.append(pregunta)
        self._mostrar()

    def _al_navegadores(self, error: str | None):
        self.punto.configure(fg=ROJO if error else VERDE)
        self.texto_navegadores.configure(
            text=f"No se pudieron abrir los navegadores: {error}" if error else "Navegadores listos")

    def _al_fin(self, ruta, error: str | None = None):
        if self.estado != "consultando":
            return
        self.estado = "resultado"
        self.ruta = ruta
        self.accion, self.capturando = None, ()
        self.tiempo.configure(text=minutos(time.monotonic() - self.inicio))
        if ruta:
            self.titulo_resultado.configure(text="✓  Informe listo", foreground=VERDE)
            self.archivo.configure(text=ruta.name)
            self.boton_pdf.pack(side="left", padx=(0, 8), before=self.boton_carpeta)
        else:
            self.titulo_resultado.configure(text="No se generó el informe", foreground=ROJO)
            self.archivo.configure(text=error or "Ningún sitio entregó resultado.")
            self.boton_pdf.pack_forget()
        fallos = [f"✕  {SITIOS[clave]}: {explicar(self.detalles.get(clave) or 'falló')}"
                  for clave, estado in self.sitios.items() if estado == "error"]
        caidos = [f"!  {SITIOS[clave]}: fuera de servicio, {explicar(self.detalles.get(clave) or 'no respondió')}."
                  for clave, estado in self.sitios.items() if estado == "fuera_servicio"]
        if caidos:
            caidos.append("En el informe van sus páginas caídas, marcadas en la portada como «Fuera de servicio».")
        no_aplica = [SITIOS[clave] for clave, estado in self.sitios.items() if estado == "no_aplica"]
        omitidos = (f"Sin fecha de expedición se omitieron: {', '.join(self.omitidos)}." if self.omitidos else "")
        for etiqueta, texto in ((self.fallos, "\n".join(fallos)), (self.caidos, "\n".join(caidos)),
                                (self.no_aplica, f"No aplica: {', '.join(no_aplica)}." if no_aplica else ""),
                                (self.texto_omitidos, omitidos)):
            etiqueta.configure(text=texto)
            if texto:
                etiqueta.grid()
            else:
                etiqueta.grid_remove()
        self.root.title(NOMBRE)
        self._mostrar()
        winsound.MessageBeep(winsound.MB_OK)
        if self.root.state() == "iconic":
            self.root.deiconify()

    def _al_caido(self, error: str):
        self._al_navegadores(error)
        self._al_fin(None, error)

    def _al_terminado(self):
        if self.estado == "cerrando":
            self._cerrar_ya()

    # ---------- Actualización ----------

    def _mostrar_novedades(self):
        """Tras actualizarse, todo lo que cambió desde la versión que tenía (novedades.txt), una sola vez."""
        versiones = novedades_pendientes()
        if not versiones:
            return
        if not self.instalacion_nueva:
            self._ventana_novedades(versiones)
        try:
            NOVEDADES_VISTAS.parent.mkdir(parents=True, exist_ok=True)
            NOVEDADES_VISTAS.write_text(versiones[0][0], encoding="utf-8")
        except OSError as e:
            log(f"Novedades: no se pudo guardar que ya se vieron ({e}).")

    def _ventana_novedades(self, versiones: list[tuple[str, list[str]]]):
        ventana = tk.Toplevel(self.root)
        ventana.withdraw()
        ventana.title(f"{NOMBRE} · Novedades")
        ventana.configure(background=FONDO)
        ventana.resizable(False, False)
        ventana.transient(self.root)
        v = ttk.Frame(ventana, padding=(22, 18, 22, 14))
        v.pack(fill="both", expand=True)
        ttk.Label(v, text=f"{NOMBRE} se actualizó", style="Titulo.TLabel").pack(anchor="w")
        # Si pasó mucho sin actualizarse, la lista puede no caber en la pantalla: va en un recuadro con barra.
        recuadro = ttk.Frame(v)
        recuadro.pack(fill="both", expand=True)
        lienzo = tk.Canvas(recuadro, background=FONDO, highlightthickness=0)
        lista = ttk.Frame(lienzo)
        lienzo.create_window(0, 0, window=lista, anchor="nw")
        for titulo, cambios in versiones:
            ttk.Label(lista, text=titulo, style="Negrita.TLabel").pack(anchor="w", pady=(12, 2))
            for cambio in cambios:
                ttk.Label(lista, text=f"•  {cambio}", wraplength=400, justify="left").pack(anchor="w", pady=1)
        boton = ttk.Button(v, text="Entendido", style="Acento.TButton", command=ventana.destroy)
        boton.pack(anchor="e", pady=(14, 0))
        ventana.bind("<Return>", lambda _: ventana.destroy())
        ventana.update_idletasks()
        izquierda, arriba, derecha, abajo = area_de_trabajo()
        ancho, alto = lista.winfo_reqwidth(), lista.winfo_reqheight()
        # También deben caber el título, el botón, los márgenes y la barra de título de Windows.
        visible = min(alto, abajo - arriba - v.winfo_reqheight() - 60)
        lienzo.configure(width=ancho, height=visible, scrollregion=(0, 0, ancho, alto))
        lienzo.pack(side="left", fill="both", expand=True)
        if visible < alto:
            barra = ttk.Scrollbar(recuadro, orient="vertical", command=lienzo.yview)
            barra.pack(side="right", fill="y")
            lienzo.configure(yscrollcommand=barra.set)
            ventana.bind("<MouseWheel>", lambda e: lienzo.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        ventana.update_idletasks()
        ventana.geometry(f"+{(izquierda + derecha - ventana.winfo_reqwidth()) // 2}"
                         f"+{max(arriba, (arriba + abajo - ventana.winfo_reqheight()) // 2)}")
        ventana.attributes("-topmost", True)
        ventana.deiconify()
        boton.focus_set()

    def _buscar_version(self):
        if actualizar.hay_version_nueva():
            self.eventos.put(("version", {}))

    def _al_version(self):
        # Si ya empezó una consulta no se interrumpe: se actualizará la próxima vez que se abra.
        if self.estado == "datos":
            log("Actualización: hay una versión nueva.")
            self._actualizar()

    def _actualizar(self):
        if self.estado != "datos":
            return
        self.estado = "actualizando"
        self._mostrar()
        threading.Thread(target=self._hilo_actualizar, daemon=True).start()

    def _hilo_actualizar(self):
        try:
            actualizar.aplicar()
            self.eventos.put(("actualizado", {"error": None}))
        except Exception as e:
            self.eventos.put(("actualizado", {"error": primera_linea(e)}))

    def _al_actualizado(self, error: str | None):
        if self.estado != "actualizando":
            return
        self.estado = "datos"
        if error:
            log(f"Actualización: {error}")
            self.texto_version.configure(text=f"No se pudo actualizar ({error}); se intentará al volver a abrir.")
            self.texto_version.grid()
            self._mostrar()
            return
        log("Actualización lista: se vuelve a abrir.")
        self.reiniciar = True
        self._al_cerrar()

    # ---------- Cierre ----------

    def _al_cerrar(self):
        if self.estado == "cerrando":
            return
        if self.estado == "actualizando" and not messagebox.askyesno(
                NOMBRE, "Se está actualizando. Si cierras ahora y después no abre, vuelve a ejecutar el instalador. "
                "¿Cerrar de todas formas?", parent=self.root):
            return
        if self.estado == "consultando" and not messagebox.askyesno(
                NOMBRE, "Hay una consulta en curso. ¿Cancelarla y cerrar?", parent=self.root):
            return
        self.estado = "cerrando"
        for pregunta in self.preguntas:
            pregunta["futuro"].set_exception(RuntimeError(f"Se cerró {NOMBRE}."))
        self.preguntas.clear()
        self.accion = None
        self._mostrar()
        try:
            self.bucle.call_soon_threadsafe(self._cancelar_sesion)
        except RuntimeError:
            # El bot ya se había detenido.
            self._cerrar_ya()
            return
        # Por si cerrar los navegadores se queda colgado.
        self.root.after(20000, self._cerrar_ya)

    def _cerrar_ya(self):
        if self.vivo:
            self.vivo = False
            self.root.destroy()

    def _error_en_ventana(self, tipo, valor, rastro):
        log("ERROR en la ventana: " + "".join(traceback.format_exception(tipo, valor, rastro)).strip())


def main():
    # Nítida con cualquier escala de Windows; mss hace lo mismo en su primera captura.
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    # Botón e ícono propios en la barra de tareas (si no, sale el de Python).
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"Soporte.{NOMBRE}")
    interfaz = Interfaz()
    interfaz.root.mainloop()
    if interfaz.reiniciar:
        subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "pythonw.exe"), str(RAIZ / f"{NOMBRE}.pyw")])
    # Tras cancelar, una captura de Chrome puede seguir en su hilo: no se la espera.
    os._exit(0)


if __name__ == "__main__":
    main()
