"""Ventana de AntecedentesBOT: recibe las filas copiadas de Excel y genera un PDF por persona con su plantilla."""
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
from datetime import date, datetime
from tkinter import messagebox, ttk

import pywintypes

import tabla
from comun import NOMBRE, PLANTILLAS, RAIZ, SALIDAS, log
from generar import Word, fecha_larga

FONDO = "#ffffff"
TEXTO = "#1f2937"
SUAVE = "#6b7280"
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
LETRA_TITULO = ("Segoe UI Semibold", 13)
ANCHO_TEXTO = 720

PLANTILLAS_NECESARIAS = ("000 - AAA - FALABELLA . COM", "000 - AAA - FALABELLA CEDI", "000 - BBB - FALABELLA . COM",
                         "000 - BBB - FALABELLA CEDI")
MARCAS = {"pendiente": "○", "en_curso": "●", "listo": "✓", "error": "✕"}

user32 = ctypes.windll.user32


def dar_foco(hwnd: int):
    """Windows solo deja que una ventana de atrás tome el foco justo después de pulsar Alt."""
    if user32.GetForegroundWindow() == hwnd:
        return
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def explicar(error: Exception) -> str:
    """El mensaje de Word dentro de un error COM, o el del error."""
    if isinstance(error, pywintypes.com_error):
        detalle = error.args[2][2] if len(error.args) > 2 and error.args[2] and error.args[2][2] else error.args[1]
        return f"Word: {' '.join(str(detalle).split())}"
    return str(error)


def minutos(segundos: float) -> str:
    segundos = round(segundos)
    return f"{segundos // 60}:{segundos % 60:02d}"


def plantillas_faltantes() -> list[str]:
    return [p for p in PLANTILLAS_NECESARIAS if not (PLANTILLAS / f"{p}.odt").exists()]


class Interfaz:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("AntecedentesBOT")
        self.root.configure(background=FONDO)
        self.root.minsize(740, 480)
        self.root.report_callback_exception = self._error_en_ventana
        self.root.iconbitmap(default=str(RAIZ / "icono.ico"))
        self._estilos()

        self.vivo = True
        self.eventos = queue.Queue()
        self.estado = "datos"
        self.tabla: tabla.Tabla | None = None
        self.estados: dict[int, str] = {}
        self.detener = threading.Event()
        self.carpeta = None
        self.inicio = 0.0
        self._vista = None
        self._reloj = None
        self._secuencia = user32.GetClipboardSequenceNumber()

        self.v_datos = self._armar_datos()
        self.v_generando = self._armar_progreso()
        self.v_resultado = self._armar_resultado()
        self.v_cerrando = ttk.Frame(self.root, padding=26)
        ttk.Label(self.v_cerrando, text="Terminando el documento en curso y cerrando Word…", style="Suave.TLabel").pack()

        self.root.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        for tecla in ("<Control-v>", "<Control-V>"):
            self.root.bind(tecla, lambda _: self._pegar())
        self._mostrar()
        ancho, alto = 860, 600
        self.root.geometry(f"{ancho}x{alto}+{(self.root.winfo_screenwidth() - ancho) // 2}"
                           f"+{max(0, (self.root.winfo_screenheight() - alto) // 2 - 30)}")
        self.root.deiconify()
        self.root.update_idletasks()
        self.hwnd = int(self.root.wm_frame(), 16)
        self._revisar_plantillas()
        # Si ya había filas copiadas al abrir, se cargan.
        self._cargar_portapapeles(automatico=True)
        self.root.after(150, self._atender)
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
            arbol.column(clave, width=ancho, minwidth=30, anchor="w",
                         stretch=clave in ("nombre", "concepto", "revisar", "archivo"))
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
        ttk.Label(cabeza, text="Nuevos documentos", style="Titulo.TLabel").pack(side="left")
        ttk.Button(cabeza, text="Pegar filas", command=self._pegar).pack(side="right")
        ttk.Label(v, text="Copia en Excel las filas de las personas junto con su fila de títulos (NOMBRE, CEDULA, PERFIL, "
                          "CONCEPTO, RAMA…): se pegan solas. Se genera un PDF por persona con su plantilla.",
                  style="Suave.TLabel", wraplength=ANCHO_TEXTO, justify="left").pack(anchor="w", pady=(2, 8))
        self.columnas_texto = ttk.Label(v, wraplength=ANCHO_TEXTO, justify="left")
        self.columnas_texto.pack(anchor="w")
        marco, self.arbol_datos = self._arbol(v, (
            ("n", "#", 36), ("cedula", "Cédula", 95), ("nombre", "Nombre", 210), ("perfil", "Perfil", 45),
            ("rama", "Rama", 55), ("concepto", "Concepto", 200), ("revisar", "Revisar", 170)))
        marco.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(v, text="Perfil A: plantilla AAA. Perfiles B y C: plantilla BBB (al C se le cambia «PERFIL B» por "
                          "«PERFIL C»). Rama que diga COM: plantilla . COM; si no, CEDI. Las filas en rojo tienen un dato "
                          "por corregir y no se generan.",
                  style="Pie.TLabel", wraplength=ANCHO_TEXTO, justify="left").pack(anchor="w", pady=(6, 0))
        self.error = ttk.Label(v, style="Error.TLabel", wraplength=ANCHO_TEXTO, justify="left")
        self.error.pack(anchor="w")
        pie = ttk.Frame(v)
        pie.pack(fill="x", pady=(8, 0))
        self.punto = tk.Label(pie, text="●", fg=AMBAR, bg=FONDO, font=LETRA_PEQUENA)
        self.punto.pack(side="left")
        self.texto_plantillas = ttk.Label(pie, style="Pie.TLabel")
        self.texto_plantillas.pack(side="left", padx=(4, 0))
        enlace = ttk.Label(pie, text="Abrir plantillas", style="Enlace.TLabel", cursor="hand2")
        enlace.pack(side="left", padx=(10, 0))
        enlace.bind("<Button-1>", lambda _: os.startfile(PLANTILLAS))
        self.boton_generar = ttk.Button(pie, text="Generar PDF", style="Acento.TButton", command=self._generar)
        self.boton_generar.pack(side="right")
        return v

    def _armar_progreso(self) -> ttk.Frame:
        v = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        cabeza = ttk.Frame(v)
        cabeza.pack(fill="x")
        ttk.Label(cabeza, text="Generando los PDF", style="Titulo.TLabel").pack(side="left")
        self.reloj = ttk.Label(cabeza, style="Suave.TLabel")
        self.reloj.pack(side="right")
        fila = ttk.Frame(v)
        fila.pack(fill="x", pady=10)
        self.barra = tk.Canvas(fila, height=6, bg="#e5e7eb", highlightthickness=0, bd=0)
        self.barra.pack(side="left", fill="x", expand=True)
        self.relleno = self.barra.create_rectangle(0, 0, 0, 6, fill=ACENTO, width=0)
        self.cuenta = ttk.Label(fila, style="Suave.TLabel")
        self.cuenta.pack(side="left", padx=(10, 0))
        marco, self.arbol_avance = self._arbol(v, (
            ("n", "#", 36), ("estado", "", 30), ("cedula", "Cédula", 95), ("archivo", "Archivo", 520)))
        marco.pack(fill="both", expand=True)
        self.arbol_avance.column("estado", anchor="center")
        self.linea_estado = tk.Label(v, bg=FONDO, fg=SUAVE, font=LETRA_PEQUENA, height=2, anchor="nw",
                                     justify="left", wraplength=ANCHO_TEXTO)
        self.linea_estado.pack(fill="x", pady=(8, 0))
        pie = ttk.Frame(v)
        pie.pack(fill="x")
        self.boton_detener = ttk.Button(pie, text="Detener", command=self._detener)
        self.boton_detener.pack(side="right")
        ttk.Label(pie, text="Word trabaja oculto y no toca los documentos que tengas abiertos.",
                  style="Pie.TLabel").pack(side="left")
        return v

    def _armar_resultado(self) -> ttk.Frame:
        v = ttk.Frame(self.root, padding=(22, 18, 22, 14))
        v.columnconfigure(0, weight=1)
        self.titulo_resultado = ttk.Label(v, style="Titulo.TLabel")
        self.titulo_resultado.grid(row=0, column=0, sticky="w")
        self.tiempo = ttk.Label(v, style="Suave.TLabel")
        self.tiempo.grid(row=0, column=1, sticky="e")
        self.texto_carpeta = ttk.Label(v, wraplength=ANCHO_TEXTO, justify="left")
        self.texto_carpeta.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        botones = ttk.Frame(v)
        botones.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Button(botones, text="Abrir carpeta", style="Acento.TButton", command=self._abrir_carpeta).pack(side="left")
        self.detalle = ttk.Label(v, wraplength=ANCHO_TEXTO, justify="left")
        self.detalle.grid(row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))
        self.fallos = tk.Text(v, height=10, wrap="word", font=LETRA_PEQUENA, fg=ROJO, bg=FONDO, relief="flat",
                              highlightthickness=1, highlightbackground=BORDE)
        self.fallos.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        v.rowconfigure(4, weight=1)
        ttk.Separator(v).grid(row=5, column=0, columnspan=2, sticky="we", pady=(14, 10))
        pie = ttk.Frame(v)
        pie.grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Button(pie, text="Nuevos documentos", command=self._nueva).pack(side="left")
        ttk.Label(pie, text="o copia otras filas en Excel", style="Pie.TLabel").pack(side="left", padx=(10, 0))
        return v

    def _mostrar(self):
        clave = "cerrando" if self.estado == "cerrando" else self.estado
        if clave == self._vista:
            return
        self._vista = clave
        for hijo in self.root.pack_slaves():
            hijo.pack_forget()
        getattr(self, f"v_{clave}").pack(fill="both", expand=True)

    # ---------- Filas ----------

    def _revisar_plantillas(self) -> bool:
        faltan = plantillas_faltantes()
        self.punto.configure(fg=ROJO if faltan else VERDE)
        self.texto_plantillas.configure(
            text=f"Falta en Plantillas: {', '.join(f + '.odt' for f in faltan)}" if faltan
            else f"Plantillas listas ({len(PLANTILLAS_NECESARIAS)})")
        return not faltan

    def _pegar(self):
        if self.estado in ("datos", "resultado"):
            self._cargar_portapapeles(automatico=False)

    def _vigilar_portapapeles(self):
        # Windows numera cada cambio del portapapeles: solo se lee cuando cambió.
        secuencia = user32.GetClipboardSequenceNumber()
        if secuencia != self._secuencia:
            self._secuencia = secuencia
            if self.estado in ("datos", "resultado"):
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
            # No debe quedar a la vista (ni generarse) lo pegado antes.
            self._nueva()
            self._mostrar_error(str(e))
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
        t = self.tabla
        self.arbol_datos.delete(*self.arbol_datos.get_children())
        for f in t.filas:
            revisar = "; ".join(f.errores + f.avisos)
            etiqueta = "rojo" if f.errores else "amarillo" if f.avisos else ""
            concepto = " ".join(f.concepto.split())
            self.arbol_datos.insert("", "end", iid=str(f.numero), tags=(etiqueta,) if etiqueta else (), values=(
                f.numero, f.documento, f.nombre, f.perfil, ". COM" if f.rama == "COM" else "CEDI",
                concepto if len(concepto) <= 90 else concepto[:89] + "…", revisar[:1].upper() + revisar[1:]))
        generables = sum(f.generable for f in t.filas)
        partes = [f"{len(t.filas)} personas: {generables} para generar"]
        rojas = len(t.filas) - generables
        if rojas:
            partes.append(f"{rojas} por corregir")
        usadas = [f"{nombre}: «{t.titulos[rol]}»" for rol, nombre in (
            ("nombre", "nombre"), ("cedula", "cédula"), ("perfil", "perfil"), ("concepto", "concepto"), ("rama", "rama"))
            if t.titulos[rol]]
        self.columnas_texto.configure(text=" · ".join(partes) + "\nColumnas: " + " · ".join(usadas)
                                      + f"\nFecha en los documentos: {fecha_larga(date.today())} (la de hoy)"
                                      + "".join(f"\n{a}" for a in t.avisos))
        self.boton_generar.configure(text=f"Generar {generables} PDF" if generables else "Generar PDF")

    def _mostrar_error(self, texto: str):
        self.error.configure(text=texto)

    def _generar(self):
        if self.estado != "datos":
            return
        if not self.tabla:
            self._mostrar_error("Primero copia en Excel las filas de las personas, con su fila de títulos.")
            return
        filas = [f for f in self.tabla.filas if f.generable]
        if not filas:
            self._mostrar_error("Ninguna fila se puede generar: corrige las que están en rojo y vuelve a copiarlas.")
            return
        if not self._revisar_plantillas():
            self._mostrar_error("Faltan plantillas en la carpeta Plantillas (abajo dice cuáles).")
            return
        SALIDAS.mkdir(exist_ok=True)
        base = f"Antecedentes {datetime.now():%Y-%m-%d %H.%M}"
        self.carpeta, contador = SALIDAS / base, 2
        while self.carpeta.exists():
            self.carpeta, contador = SALIDAS / f"{base} ({contador})", contador + 1
        self.carpeta.mkdir()
        for f in self.tabla.filas:
            f.ruta, f.falla = None, ""
        self.detener.clear()
        self.estado = "generando"
        self._preparar_progreso(filas)
        self._mostrar()
        log(f"Lote: {len(self.tabla.filas)} filas, {len(filas)} para generar en «{self.carpeta.name}».")
        threading.Thread(target=self._trabajar, args=(filas, self.carpeta), daemon=True).start()

    def _nueva(self):
        self.tabla = None
        self.arbol_datos.delete(*self.arbol_datos.get_children())
        self.columnas_texto.configure(text="")
        self.boton_generar.configure(text="Generar PDF")
        self._mostrar_error("")
        self.estado = "datos"
        self._mostrar()

    # ---------- Generación (hilo aparte: Word tarda ~1 s por documento) ----------

    def _trabajar(self, filas: list[tabla.Fila], carpeta):
        word = None
        error = None
        # Una sola fecha para todo el lote, aunque termine pasada la medianoche.
        fecha = fecha_larga(date.today())
        try:
            self.eventos.put(("estado", {"texto": "Abriendo Word…"}))
            word = Word()
            for fila in filas:
                if self.detener.is_set():
                    break
                self.eventos.put(("fila", {"n": fila.numero, "estado": "en_curso"}))
                for intento in range(2):
                    try:
                        fila.ruta = word.generar(fila, carpeta, fecha)
                        fila.falla = ""
                        break
                    except ValueError as e:
                        fila.falla = str(e)
                        break
                    except pywintypes.com_error as e:
                        fila.falla = explicar(e)
                        log(f"Fila {fila.numero} ({fila.cedula}), intento {intento + 1}: {fila.falla}")
                if fila.falla:
                    log(f"Fila {fila.numero} ({fila.cedula}): no se generó ({fila.falla}).")
                self.eventos.put(("fila", {"n": fila.numero, "estado": "listo" if fila.ruta else "error"}))
        except Exception as e:
            error = explicar(e)
            log(f"ERROR: {e!r}")
        finally:
            if word:
                word.cerrar()
            self.eventos.put(("fin", {"error": error}))

    def _preparar_progreso(self, filas: list[tabla.Fila]):
        self.arbol_avance.delete(*self.arbol_avance.get_children())
        self.estados = {f.numero: "pendiente" for f in filas}
        for f in filas:
            self.arbol_avance.insert("", "end", iid=str(f.numero),
                                     values=(f.numero, MARCAS["pendiente"], f.documento, f.archivo))
        self.linea_estado.configure(text="Abriendo Word…")
        self.boton_detener.state(["!disabled"])
        self._contar()
        self.inicio = time.monotonic()
        if self._reloj:
            self.root.after_cancel(self._reloj)
        self._tic()

    def _contar(self):
        hechos = sum(e in ("listo", "error") for e in self.estados.values())
        total = max(1, len(self.estados))
        self.barra.update_idletasks()
        self.barra.coords(self.relleno, 0, 0, self.barra.winfo_width() * hechos / total, 6)
        self.cuenta.configure(text=f"{hechos} de {total}")
        self.root.title(f"AntecedentesBOT · {round(100 * hechos / total)} %")

    def _tic(self):
        self.reloj.configure(text=minutos(time.monotonic() - self.inicio))
        self._reloj = self.root.after(1000, self._tic) if self.estado == "generando" else None

    def _detener(self):
        # Un clic o una tecla por error no debe cortar un lote de cientos de documentos.
        if not messagebox.askyesno("AntecedentesBOT", "¿Detener la generación? Los PDF ya hechos se conservan.",
                                   parent=self.root):
            return
        self.detener.set()
        self.boton_detener.state(["disabled"])
        self.linea_estado.configure(text="Deteniendo: se termina el documento en curso…")

    def _abrir_carpeta(self):
        if self.carpeta and self.carpeta.exists():
            os.startfile(self.carpeta)
        else:
            SALIDAS.mkdir(exist_ok=True)
            os.startfile(SALIDAS)

    def _atender(self):
        """Lo que llega del hilo de Word se atiende aquí: Tkinter solo se toca desde su propio hilo."""
        while self.vivo:
            try:
                tipo, datos = self.eventos.get_nowait()
            except queue.Empty:
                self.root.after(150, self._atender)
                return
            getattr(self, f"_al_{tipo}")(**datos)

    def _al_estado(self, texto: str):
        if self.estado == "generando":
            self.linea_estado.configure(text=texto)

    def _al_fila(self, n: int, estado: str):
        if n not in self.estados:
            return
        self.estados[n] = estado
        self.arbol_avance.set(str(n), "estado", MARCAS[estado])
        # Las filas unidas a la de arriba dejan huecos en la numeración.
        fila = next(f for f in self.tabla.filas if f.numero == n)
        if estado == "en_curso":
            self.arbol_avance.see(str(n))
            self.linea_estado.configure(text=f"Generando {fila.archivo}")
        elif estado == "error":
            self.arbol_avance.item(str(n), tags=("rojo",))
        self._contar()

    def _al_fin(self, error: str | None):
        if self.estado == "cerrando":
            self._cerrar_ya()
            return
        self.estado = "resultado"
        filas = self.tabla.filas
        listos = [f for f in filas if f.ruta]
        no_generados = [f for f in filas if not f.ruta]
        self.tiempo.configure(text=minutos(time.monotonic() - self.inicio))
        if listos:
            self.titulo_resultado.configure(text=f"✓  {len(listos)} PDF listos", foreground=VERDE)
        else:
            self.titulo_resultado.configure(text="No se generó ningún PDF", foreground=ROJO)
        self.texto_carpeta.configure(text=f"Carpeta: salidas\\{self.carpeta.name}")
        lineas = []
        if error:
            lineas.append(f"Se interrumpió: {error}")
        elif self.detener.is_set() and any(not f.ruta and not f.falla and f.generable for f in filas):
            lineas.append("Se detuvo antes de terminar.")
        if no_generados:
            lineas.append(f"{len(no_generados)} no se generaron (detalle abajo): corrígelas y vuelve a copiarlas.")
        self.detalle.configure(text="\n".join(lineas))
        self.fallos.configure(state="normal")
        self.fallos.delete("1.0", "end")
        for f in no_generados:
            motivo = "; ".join(f.errores) or f.falla or "no se alcanzó a generar"
            self.fallos.insert("end", f"Fila {f.numero} · {f.documento or 'sin cédula'} · {f.nombre or 'sin nombre'}: "
                                      f"{motivo}\n")
        self.fallos.configure(state="disabled")
        if no_generados:
            self.fallos.grid()
        else:
            self.fallos.grid_remove()
        log(f"Lote terminado en {minutos(time.monotonic() - self.inicio)}: {len(listos)} PDF, "
            f"{len(no_generados)} sin generar.")
        self.root.title("AntecedentesBOT")
        self._mostrar()
        winsound.MessageBeep(winsound.MB_OK)
        if self.root.state() == "iconic":
            self.root.deiconify()

    # ---------- Cierre ----------

    def _al_cerrar(self):
        if self.estado == "cerrando":
            return
        if self.estado == "generando":
            if not messagebox.askyesno("AntecedentesBOT", "Se están generando los PDF. ¿Detener y cerrar?",
                                       parent=self.root):
                return
            self.detener.set()
            self.estado = "cerrando"
            self._mostrar()
            # Por si Word se queda colgado.
            self.root.after(30000, self._cerrar_ya)
            return
        self._cerrar_ya()

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
