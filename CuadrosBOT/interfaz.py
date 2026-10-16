"""Ventana de CuadrosBOT: recibe las filas copiadas de Excel, muestra el avance y entrega el Excel."""
import asyncio
import concurrent.futures
import ctypes
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
import traceback
import winsound
from tkinter import messagebox, ttk

from playwright.async_api import async_playwright

import comun
import tabla
from bot import abrir_navegadores, cerrar_navegadores, minutos, procesar, resumen
from comun import NOMBRE, RAIZ, SALIDAS, log

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
FONDO_ROJO = "#fde2e2"
FONDO_AMARILLO = "#fef3c7"
LETRA = ("Segoe UI", 10)
LETRA_PEQUENA = ("Segoe UI", 9)
LETRA_NEGRITA = ("Segoe UI Semibold", 10)
LETRA_TITULO = ("Segoe UI Semibold", 13)
ANCHO_TEXTO = 680

SITIOS = {"libreta": "Libreta", "simit": "SIMIT", "rnmc": "RNMC"}
MARCAS = {"pendiente": "○", "en_curso": "●", "listo": "✓", "no_aplica": "–", "omitida": "–", "error": "✕", "dato": "!"}
TERMINADOS = {"listo", "no_aplica", "omitida", "error", "dato"}
SEXOS = {"H": "Hombre", "M": "Mujer", "": "¿?"}

user32 = ctypes.windll.user32


def dar_foco(hwnd: int):
    """Windows solo deja que una ventana de atrás tome el foco justo después de pulsar Alt."""
    if user32.GetForegroundWindow() == hwnd:
        return
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def primera_linea(texto) -> str:
    return next(iter(str(texto).strip().splitlines()), "")


class Interfaz:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(NOMBRE)
        self.root.configure(background=FONDO)
        self.root.minsize(700, 460)
        self.root.report_callback_exception = self._error_en_ventana
        self.root.iconbitmap(default=str(RAIZ / "icono.ico"))
        self._estilos()

        self.vivo = True
        self.eventos = queue.Queue()
        self.estado = "datos"
        self.preguntas: list[dict] = []
        self.tabla: tabla.Tabla | None = None
        self.estados: dict[tuple[int, str], str] = {}
        self.inicio = 0.0
        self.ruta = None
        self._vista = None
        self._reloj = None
        self._secuencia = user32.GetClipboardSequenceNumber()

        self.v_datos = self._armar_datos()
        self.v_consultando = self._armar_progreso()
        self.v_resultado = self._armar_resultado()
        self.v_pregunta = ttk.Frame(self.root, padding=(22, 18, 22, 16))
        self.v_cerrando = ttk.Frame(self.root, padding=26)
        ttk.Label(self.v_cerrando, text="Cerrando los navegadores…", style="Suave.TLabel").pack()

        self.root.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        for tecla in ("<Control-v>", "<Control-V>"):
            self.root.bind(tecla, lambda _: self._pegar())
        for tecla in ("<Return>", "<KP_Enter>"):
            self.root.bind(tecla, lambda _: self._consultar() if self._vista == "datos" else None)
        self._mostrar()
        ancho, alto = 780, 580
        self.root.geometry(f"{ancho}x{alto}+{(self.root.winfo_screenwidth() - ancho) // 2}"
                           f"+{max(0, (self.root.winfo_screenheight() - alto) // 2 - 30)}")
        self.root.deiconify()
        self.root.update_idletasks()
        self.hwnd = int(self.root.wm_frame(), 16)
        # Si ya había filas copiadas al abrir, se cargan.
        self._cargar_portapapeles(automatico=True)

        self.bucle = asyncio.new_event_loop()
        self.lotes = asyncio.Queue()
        self.sesion = None
        comun.escuchar(lambda tipo, datos: self.eventos.put((tipo, datos)))
        threading.Thread(target=self._hilo_bot, daemon=True).start()
        self.root.after(200, self._atender)
        self.root.after(600, self._vigilar_portapapeles)

    # ---------- Aspecto ----------

    def _estilos(self):
        estilo = ttk.Style(self.root)
        estilo.theme_use("clam")
        estilo.configure(".", background=FONDO, foreground=TEXTO, font=LETRA, bordercolor=BORDE)
        estilo.configure("Titulo.TLabel", font=LETRA_TITULO)
        estilo.configure("Suave.TLabel", foreground=SUAVE)
        estilo.configure("Pie.TLabel", foreground=SUAVE, font=LETRA_PEQUENA)
        estilo.configure("Error.TLabel", foreground=ROJO)
        estilo.configure("Enlace.TLabel", foreground=ACENTO, font=LETRA_PEQUENA)
        estilo.configure("TButton", background=GRIS, lightcolor=GRIS, darkcolor=GRIS, padding=(14, 5))
        estilo.map("TButton", background=[("pressed", BORDE), ("active", "#e5e7eb")],
                   lightcolor=[("active", "#e5e7eb")], darkcolor=[("active", "#e5e7eb")])
        estilo.configure("Acento.TButton", background=ACENTO, foreground=FONDO, bordercolor=ACENTO,
                         lightcolor=ACENTO, darkcolor=ACENTO)
        estilo.map("Acento.TButton", background=[("pressed", ACENTO_OSCURO), ("active", ACENTO_OSCURO)],
                   lightcolor=[("active", ACENTO_OSCURO)], darkcolor=[("active", ACENTO_OSCURO)],
                   bordercolor=[("active", ACENTO_OSCURO)])
        estilo.configure("Treeview", background=FONDO, fieldbackground=FONDO, rowheight=22, font=LETRA_PEQUENA)
        estilo.configure("Treeview.Heading", background=GRIS, font=("Segoe UI Semibold", 9), relief="flat")
        estilo.map("Treeview.Heading", background=[("active", "#e5e7eb")])
        estilo.configure("TSeparator", background=BORDE)

    def _arbol(self, padre, columnas) -> tuple[ttk.Frame, ttk.Treeview]:
        marco = ttk.Frame(padre)
        arbol = ttk.Treeview(marco, columns=[c for c, _, _ in columnas], show="headings", height=12,
                             selectmode="none")
        for clave, titulo, ancho in columnas:
            arbol.heading(clave, text=titulo, anchor="w")
            arbol.column(clave, width=ancho, minwidth=30, anchor="w", stretch=clave in ("nombre", "revisar"))
        barra = ttk.Scrollbar(marco, orient="vertical", command=arbol.yview)
        arbol.configure(yscrollcommand=barra.set)
        arbol.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        arbol.tag_configure("rojo", background=FONDO_ROJO)
        arbol.tag_configure("amarillo", background=FONDO_AMARILLO)
        return marco, arbol

    def _armar_datos(self) -> ttk.Frame:
        v = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        cabeza = ttk.Frame(v)
        cabeza.pack(fill="x")
        ttk.Label(cabeza, text="Nueva consulta", style="Titulo.TLabel").pack(side="left")
        ttk.Button(cabeza, text="Pegar filas", command=self._pegar).pack(side="right")
        ttk.Label(v, text="Copia en Excel solo 3 columnas: cédula, fecha de expedición y nombre (en ese orden, con o sin "
                          "la fila de títulos). Se pegan solas. Se consultan la libreta militar, el SIMIT y el RNMC.",
                  style="Suave.TLabel", wraplength=ANCHO_TEXTO, justify="left").pack(anchor="w", pady=(2, 8))
        self.columnas_texto = ttk.Label(v, wraplength=ANCHO_TEXTO, justify="left")
        self.columnas_texto.pack(anchor="w")
        marco, self.arbol_datos = self._arbol(v, (("n", "#", 36), ("cedula", "Cédula", 105), ("nombre", "Nombre", 240),
                                                  ("expedicion", "F. expedición", 95), ("sexo", "Sexo", 70),
                                                  ("revisar", "Revisar", 230)))
        marco.pack(fill="both", expand=True, pady=(8, 0))
        self.arbol_datos.bind("<Double-1>", self._cambiar_sexo)
        ttk.Label(v, text="El sexo se deduce del nombre; doble clic en él para cambiarlo (a las mujeres no se les consulta "
                          "la libreta militar). Las filas en rojo tienen un dato por corregir y no se consultan.",
                  style="Pie.TLabel", wraplength=ANCHO_TEXTO, justify="left").pack(anchor="w", pady=(6, 0))
        self.error = ttk.Label(v, style="Error.TLabel", wraplength=ANCHO_TEXTO, justify="left")
        self.error.pack(anchor="w")
        pie = ttk.Frame(v)
        pie.pack(fill="x", pady=(8, 0))
        self.punto = tk.Label(pie, text="●", fg=AMBAR, bg=FONDO, font=LETRA_PEQUENA)
        self.punto.pack(side="left")
        self.texto_navegadores = ttk.Label(pie, text="Preparando los navegadores…", style="Pie.TLabel")
        self.texto_navegadores.pack(side="left", padx=(4, 0))
        ttk.Button(pie, text="Consultar", style="Acento.TButton", command=self._consultar).pack(side="right")
        return v

    def _armar_progreso(self) -> ttk.Frame:
        v = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        cabeza = ttk.Frame(v)
        cabeza.pack(fill="x")
        ttk.Label(cabeza, text="Consultando", style="Titulo.TLabel").pack(side="left")
        self.reloj = ttk.Label(cabeza, style="Suave.TLabel")
        self.reloj.pack(side="right")
        ttk.Label(v, text="Los tres sitios trabajan a la vez, cada uno fila por fila.", style="Suave.TLabel").pack(
            anchor="w")
        fila = ttk.Frame(v)
        fila.pack(fill="x", pady=10)
        self.barra = tk.Canvas(fila, height=6, bg="#e5e7eb", highlightthickness=0, bd=0)
        self.barra.pack(side="left", fill="x", expand=True)
        self.relleno = self.barra.create_rectangle(0, 0, 0, 6, fill=ACENTO, width=0)
        self.cuenta = ttk.Label(fila, style="Suave.TLabel")
        self.cuenta.pack(side="left", padx=(10, 0))
        marco, self.arbol_avance = self._arbol(v, (("n", "#", 36), ("cedula", "Cédula", 105), ("nombre", "Nombre", 300),
                                                   ("libreta", "Libreta", 70), ("simit", "SIMIT", 70),
                                                   ("rnmc", "RNMC", 70)))
        marco.pack(fill="both", expand=True)
        for clave in SITIOS:
            self.arbol_avance.column(clave, anchor="center")
            self.arbol_avance.heading(clave, anchor="center")
        ttk.Label(v, text="○ en espera   ● consultando   ✓ listo   – no aplica   ✕ el sitio no respondió   ! dato errado",
                  style="Pie.TLabel").pack(anchor="w", pady=(6, 0))
        # Dos renglones fijos: la ventana no cambia de alto con cada mensaje.
        self.linea_estado = tk.Label(v, bg=FONDO, fg=SUAVE, font=LETRA_PEQUENA, height=2, anchor="nw",
                                     justify="left", wraplength=ANCHO_TEXTO)
        self.linea_estado.pack(fill="x", pady=(6, 0))
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
        self.archivo = ttk.Label(v, wraplength=ANCHO_TEXTO, justify="left")
        self.archivo.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        botones = ttk.Frame(v)
        botones.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.boton_excel = ttk.Button(botones, text="Abrir Excel", style="Acento.TButton",
                                      command=lambda: os.startfile(self.ruta))
        self.boton_carpeta = ttk.Button(botones, text="Abrir carpeta", command=self._abrir_carpeta)
        self.boton_carpeta.pack(side="left")
        self.detalle = ttk.Label(v, wraplength=ANCHO_TEXTO, justify="left")
        self.detalle.grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Separator(v).grid(row=4, column=0, columnspan=2, sticky="we", pady=(16, 10))
        pie = ttk.Frame(v)
        pie.grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Button(pie, text="Nueva consulta", command=self._nueva).pack(side="left")
        ttk.Label(pie, text="o copia otras filas en Excel", style="Pie.TLabel").pack(side="left", padx=(10, 0))
        return v

    def _armar_pregunta(self, pregunta: dict):
        v = self.v_pregunta
        for hijo in v.winfo_children():
            hijo.destroy()
        ttk.Label(v, text="SIMIT: varias personas con la misma cédula", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(v, text=pregunta["mensaje"], wraplength=ANCHO_TEXTO, justify="left").pack(anchor="w", pady=(4, 10))
        for i, opcion in enumerate(pregunta["opciones"]):
            ttk.Button(v, text=opcion, command=lambda i=i: self._responder(i)).pack(fill="x", pady=2)
        ttk.Button(v, text="No sé: marcarla para revisar", command=lambda: self._responder(None)).pack(
            anchor="e", pady=(12, 0))

    def _mostrar(self):
        """Muestra la pregunta pendiente o la pantalla del estado actual."""
        if self.estado == "cerrando":
            clave = "cerrando"
        elif self.preguntas:
            clave = ("pregunta", id(self.preguntas[0]))
        else:
            clave = self.estado
        if clave == self._vista:
            return
        self._vista = clave
        for hijo in self.root.pack_slaves():
            hijo.pack_forget()
        if isinstance(clave, tuple):
            self._armar_pregunta(self.preguntas[0])
            vista = self.v_pregunta
        else:
            vista = getattr(self, f"v_{clave}")
        vista.pack(fill="both", expand=True)

    def _llamar_atencion(self):
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        if self.root.state() == "iconic":
            self.root.deiconify()
        dar_foco(self.hwnd)
        self.root.focus_force()

    # ---------- Filas ----------

    def _pegar(self):
        if self.estado in ("datos", "resultado") and not self.preguntas:
            self._cargar_portapapeles(automatico=False)

    def _vigilar_portapapeles(self):
        # Windows numera cada cambio del portapapeles: solo se lee cuando cambió.
        secuencia = user32.GetClipboardSequenceNumber()
        if secuencia != self._secuencia:
            self._secuencia = secuencia
            if self.estado in ("datos", "resultado") and not self.preguntas:
                self._cargar_portapapeles(automatico=True)
        self.root.after(600, self._vigilar_portapapeles)

    def _cargar_portapapeles(self, automatico: bool):
        """Carga las filas copiadas; en automático, solo si parecen filas de Excel con cédulas."""
        try:
            texto = self.root.clipboard_get()
        except tk.TclError:
            texto = ""
        if automatico and ("\t" not in texto or not re.search(r"\d{6}", texto.replace(".", ""))):
            return
        try:
            nueva = tabla.armar(texto)
        except ValueError as e:
            # No debe quedar a la vista (ni consultarse) lo pegado antes.
            self._nueva()
            self._mostrar_error(str(e))
            return
        if automatico and not any(f.cedula for f in nueva.filas):
            return
        self.tabla = nueva
        self.estado = "datos"
        self._llenar_datos()
        self._mostrar_error("")
        self._mostrar()
        if automatico:
            if self.root.state() == "iconic":
                self.root.deiconify()
            dar_foco(self.hwnd)
            self.root.focus_force()

    def _llenar_datos(self):
        filas = self.tabla.filas
        self.arbol_datos.delete(*self.arbol_datos.get_children())
        for f in filas:
            if f.vacia:
                self.arbol_datos.insert("", "end", iid=str(f.numero), values=(f.numero, "", "(fila en blanco)", "", "", ""))
                continue
            revisar = "; ".join(f.errores) or ("indica el sexo (doble clic)" if not f.sexo else "")
            revisar = revisar[:1].upper() + revisar[1:]
            expedicion = f"{f.expedicion:%d/%m/%Y}" if f.expedicion else f.celdas[1]
            etiqueta = "rojo" if f.errores else "amarillo" if not f.sexo else ""
            self.arbol_datos.insert("", "end", iid=str(f.numero), tags=(etiqueta,) if etiqueta else (), values=(
                f.numero, f.cedula or f.celdas[0], f.nombre, expedicion, SEXOS[f.sexo], revisar))
        personas = [f for f in filas if not f.vacia]
        partes = [f"{len(personas)} persona{'s' if len(personas) != 1 else ''}"]
        rojas = sum(1 for f in personas if f.errores)
        if rojas:
            partes.append(f"{rojas} por corregir")
        sin_sexo = sum(1 for f in personas if not f.sexo and not f.errores)
        if sin_sexo:
            partes.append(f"{sin_sexo} sin sexo deducido")
        self.columnas_texto.configure(text=" · ".join(partes))

    def _cambiar_sexo(self, evento):
        if self.estado != "datos" or not self.tabla:
            return
        iid = self.arbol_datos.identify_row(evento.y)
        if not iid or self.arbol_datos.identify_column(evento.x) != "#5":
            return
        fila = self.tabla.filas[int(iid) - 1]
        if fila.vacia:
            return
        fila.sexo = "M" if fila.sexo == "H" else "H"
        self._llenar_datos()

    def _mostrar_error(self, texto: str):
        self.error.configure(text=texto)

    def _consultar(self):
        if self.estado != "datos" or self.preguntas:
            return
        if not self.tabla:
            self._mostrar_error("Primero copia en Excel la cédula, la fecha de expedición y el nombre de las personas.")
            return
        validas = [f for f in self.tabla.filas if f.consultable]
        if not validas:
            self._mostrar_error("Ninguna fila tiene cédula y fecha de expedición válidas: corrígelas y vuelve a copiarlas.")
            return
        sin_sexo = [f for f in validas if not f.sexo]
        if sin_sexo:
            self._mostrar_error(f"Falta el sexo en {len(sin_sexo)} fila(s), en amarillo: doble clic en su columna Sexo.")
            return
        try:
            self.bucle.call_soon_threadsafe(self.lotes.put_nowait, self.tabla)
        except RuntimeError:
            self._mostrar_error(f"El bot se detuvo. Cierra {NOMBRE} y vuelve a abrirlo; el motivo está en el registro.")
            return
        self.estado = "consultando"
        self._preparar_progreso()
        self._mostrar()

    def _nueva(self):
        self.tabla = None
        self.arbol_datos.delete(*self.arbol_datos.get_children())
        self.columnas_texto.configure(text="")
        self._mostrar_error("")
        self.estado = "datos"
        self._mostrar()

    # ---------- Avance ----------

    def _preparar_progreso(self):
        self.arbol_avance.delete(*self.arbol_avance.get_children())
        self.estados = {}
        for f in self.tabla.filas:
            if f.consultable:
                self.estados.update({(f.numero, sitio): "pendiente" for sitio in SITIOS})
            marca = MARCAS["pendiente"] if f.consultable else "" if f.vacia else "–"
            etiqueta = () if f.consultable or f.vacia else ("rojo",)
            self.arbol_avance.insert("", "end", iid=str(f.numero), tags=etiqueta,
                                     values=(f.numero, f.cedula, "(fila en blanco)" if f.vacia else f.nombre,
                                             marca, marca, marca))
        self.linea_estado.configure(text="Abriendo las páginas…")
        self._contar()
        self.inicio = time.monotonic()
        if self._reloj:
            self.root.after_cancel(self._reloj)
        self._tic()

    def _contar(self):
        hechos, total = sum(e in TERMINADOS for e in self.estados.values()), max(1, len(self.estados))
        self.barra.update_idletasks()
        self.barra.coords(self.relleno, 0, 0, self.barra.winfo_width() * hechos / total, 6)
        self.cuenta.configure(text=f"{hechos} de {total}")
        self.root.title(f"{NOMBRE} · {round(100 * hechos / total)} %")
        if hechos == total:
            self.linea_estado.configure(text="Armando el Excel…")

    def _tic(self):
        self.reloj.configure(text=minutos(time.monotonic() - self.inicio))
        self._reloj = self.root.after(1000, self._tic) if self.estado == "consultando" else None

    def _abrir_carpeta(self):
        if self.ruta and self.ruta.exists():
            subprocess.Popen(["explorer", "/select,", str(self.ruta)])
        else:
            SALIDAS.mkdir(exist_ok=True)
            os.startfile(SALIDAS)

    # ---------- Comunicación con el bot ----------

    def _hilo_bot(self):
        asyncio.set_event_loop(self.bucle)
        try:
            self.bucle.run_until_complete(self._sesion())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"ERROR: {e!r}")
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
                    lote = await self.lotes.get()
                    try:
                        ruta = await procesar(lote, apertura, self.elegir)
                        self.eventos.put(("fin", {"ruta": ruta, "datos": resumen(lote)}))
                    except Exception as e:
                        log(f"ERROR: {e!r}")
                        self.eventos.put(("fin", {"ruta": None, "datos": None, "error": primera_linea(e)}))
            finally:
                await cerrar_navegadores(apertura)

    def _navegadores_listos(self, tarea: asyncio.Task):
        if not tarea.cancelled():
            error = tarea.exception()
            self.eventos.put(("navegadores", {"error": primera_linea(error) if error else None}))

    def _cancelar_sesion(self):
        if self.sesion:
            self.sesion.cancel()

    async def elegir(self, mensaje: str, opciones: list[str]) -> int | None:
        return await asyncio.to_thread(self._preguntar, mensaje, opciones)

    def _preguntar(self, mensaje: str, opciones: list[str]) -> int | None:
        futuro = concurrent.futures.Future()
        self.eventos.put(("pregunta", {"mensaje": mensaje, "opciones": opciones, "futuro": futuro}))
        return futuro.result()

    def _responder(self, valor: int | None):
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
        if self.estado == "consultando" and linea and not linea.startswith(("Tiempo", "Excel:", "Lote:")):
            self.linea_estado.configure(text=linea if len(linea) <= 160 else linea[:159] + "…")

    def _al_fila(self, n: int, sitio: str, estado: str, detalle: str = ""):
        if self.estado != "consultando" or (n, sitio) not in self.estados:
            return
        self.estados[(n, sitio)] = estado
        self.arbol_avance.set(str(n), sitio, MARCAS[estado])
        fila = self.tabla.filas[n - 1]
        if fila.errores or fila.avisos:
            etiqueta = "rojo"
        elif any(self.estados.get((n, s)) == "error" for s in SITIOS):
            etiqueta = "amarillo"
        else:
            etiqueta = ""
        self.arbol_avance.item(str(n), tags=(etiqueta,) if etiqueta else ())
        self._contar()

    def _al_pregunta(self, **pregunta):
        if self.estado == "cerrando":
            pregunta["futuro"].set_exception(RuntimeError(f"Se cerró {NOMBRE}."))
            return
        self.preguntas.append(pregunta)
        self._mostrar()
        self._llamar_atencion()

    def _al_navegadores(self, error: str | None):
        self.punto.configure(fg=ROJO if error else VERDE)
        self.texto_navegadores.configure(
            text=f"No se pudieron abrir los navegadores: {error}" if error else "Navegadores listos")

    def _al_fin(self, ruta, datos: dict | None, error: str | None = None):
        if self.estado != "consultando":
            return
        self.estado = "resultado"
        self.ruta = ruta
        self.tiempo.configure(text=minutos(time.monotonic() - self.inicio))
        if ruta:
            self.titulo_resultado.configure(text="✓  Excel listo", foreground=VERDE)
            self.archivo.configure(text=ruta.name)
            self.boton_excel.pack(side="left", padx=(0, 8), before=self.boton_carpeta)
        else:
            self.titulo_resultado.configure(text="No se generó el Excel", foreground=ROJO)
            self.archivo.configure(text=error or "Ocurrió un error; el motivo está en el registro.")
            self.boton_excel.pack_forget()
        lineas = []
        if datos:
            con_observacion = datos["listas"] - datos["sin_novedad"]
            lineas.append(f"Trae la cédula y la observación de cada persona, en el orden de las filas pegadas: "
                          f"{datos['total']} personas, {datos['listas']} listas ({datos['sin_novedad']} sin novedad, "
                          f"{con_observacion} con observación).")
            if datos["rojas"]:
                lineas.append(f"{datos['rojas']} en rojo: un dato por corregir (lo dice la observación) o el nombre no "
                              "coincide (va en una nota de la celda). Corrígelas y vuelve a copiarlas.")
            if datos["amarillas"]:
                lineas.append(f"{datos['amarillas']} en amarillo: algún sitio no respondió. Vuelve a copiarlas para "
                              "repetirlas.")
        self.detalle.configure(text="\n".join(lineas))
        self.root.title(NOMBRE)
        self._mostrar()
        winsound.MessageBeep(winsound.MB_OK)
        if self.root.state() == "iconic":
            self.root.deiconify()

    def _al_caido(self, error: str):
        self._al_navegadores(error)
        self._al_fin(None, None, error)

    def _al_terminado(self):
        if self.estado == "cerrando":
            self._cerrar_ya()

    # ---------- Cierre ----------

    def _al_cerrar(self):
        if self.estado == "cerrando":
            return
        if self.estado == "consultando" and not messagebox.askyesno(
                NOMBRE, "Hay una consulta en curso. ¿Cancelarla y cerrar?", parent=self.root):
            return
        self.estado = "cerrando"
        for pregunta in self.preguntas:
            pregunta["futuro"].set_exception(RuntimeError(f"Se cerró {NOMBRE}."))
        self.preguntas.clear()
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
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    # Botón e ícono propios en la barra de tareas (si no, sale el de Python).
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"Soporte.{NOMBRE}")
    Interfaz().root.mainloop()
    os._exit(0)


if __name__ == "__main__":
    main()
