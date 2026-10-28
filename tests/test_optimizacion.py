import asyncio
import sys
import time
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from weakref import WeakKeyDictionary

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import carga_paginas as carga
    import documentos
    import main
    import rendimiento
finally:
    sys.path.pop(0)


class OptimizacionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cada_sitio_tiene_cupo_y_no_espera_al_grupo(self):
        await self.verificar_cola("Hombre")

    async def test_gallagher_conserva_todos_los_sitios(self):
        await self.verificar_cola("Gallagher")

    async def test_sin_fecha_de_expedicion_omite_las_consultas_que_la_piden(self):
        await self.verificar_cola("Hombre", fecha_expedicion=None)

    async def test_los_formularios_con_captcha_se_llenan_antes_que_las_demas_consultas(self):
        llenos = {}

        def sitio(clave, demora):
            async def consulta(*args):
                turnos, listo = args[-2], args[-1]
                await asyncio.sleep(demora)
                llenos[clave] = time.monotonic()
                async with turnos.turno(clave, time.monotonic, AsyncMock()):
                    pass
                listo.set()
                return Path(f"{clave}.pdf")
            return consulta

        demoras = {"runt": 0.05, "registraduria": 0.1, "antecedentes": 0.15, "inhabilidades": 0.2}
        await self.verificar_cola("Hombre", por_sitio={c: sitio(c, d) for c, d in demoras.items()},
                                  espera_formularios=3)
        self.assertEqual(set(llenos), set(demoras))
        self.assertGreaterEqual(min(self.inicios), max(llenos.values()))

    async def test_si_un_formulario_tarda_lo_demas_no_lo_espera_mas_del_tope(self):
        async def lento(*args):
            await asyncio.sleep(1)
            args[-1].set()
            return Path("runt.png")

        desde = time.monotonic()
        await self.verificar_cola("Hombre", por_sitio={"runt": lento}, espera_formularios=0.1)
        self.assertLess(min(self.inicios) - desde, 0.6)

    async def test_fbi_y_consilium_se_capturan_apenas_cargan_sin_esperar_los_captchas(self):
        bucle = asyncio.get_running_loop()
        capturadas = asyncio.Event()
        claves = []

        def en_chrome(persona, preguntar, pedidas, antes, caidos):
            claves.extend(pedidas)
            if {"fbi", "consilium"} <= set(claves):
                bucle.call_soon_threadsafe(capturadas.set)
            return {clave: Path(f"{clave}.png") for clave in pedidas}

        async def captcha_lento(*args):
            # Un captcha que sigue cargando no detiene las capturas de Chrome.
            await asyncio.wait_for(capturadas.wait(), 3)
            return Path("captcha.pdf")

        async def runt(*args):
            return Path("runt.png")

        await self.verificar_cola("Hombre", por_sitio={"runt": runt}, captcha=captcha_lento, consultas_edge=en_chrome)
        self.assertEqual(sorted(claves), ["consilium", "fbi"])

    async def test_ventana_de_captcha_que_no_responde_se_cambia_y_se_repite_una_vez(self):
        paginas = []

        async def antecedentes(page, persona, turnos, listo):
            paginas.append(page)
            if len(paginas) == 1:
                raise main.FueraDeServicio(documentos.SIN_RESPUESTA)
            return Path("antecedentes.pdf")

        await self.verificar_cola("Hombre", por_sitio={"antecedentes": antecedentes})
        self.assertEqual(len(paginas), 2)
        self.r["ventana_nueva_de"].assert_awaited_once_with("antecedentes")

    async def test_sitio_caido_no_se_repite_en_otra_ventana(self):
        async def registraduria(*args):
            raise main.FueraDeServicio(documentos.SIN_RESPUESTA)

        # La Registraduría no se repite (cada visita de más acerca el bloqueo de su Cloudflare): queda caída.
        await self.verificar_cola("Hombre", por_sitio={"registraduria": registraduria},
                                  caidos_esperados={"registraduria"})
        self.r["ventana_nueva_de"].assert_not_awaited()

    async def test_si_la_precarga_no_abre_mientras_se_espera_se_carga_una_vez_mas(self):
        async def precarga_caida():
            await asyncio.sleep(0.05)
            raise main.FueraDeServicio("la página no abrió ni recargándola")

        def preparar(r):
            r["precargas"]["inhabilidades"] = asyncio.create_task(precarga_caida())
            r["formularios"] = {clave: ("https://sitio.test/", None) for clave in r["ventanas"]}

        # Sin la carga extra, Inhabilidades quedaría fuera de servicio y la cola lo notaría.
        with patch.object(documentos, "precargar", new_callable=AsyncMock) as precargar:
            await self.verificar_cola("Hombre", preparar=preparar)
        precargar.assert_awaited_once()

    async def test_si_el_ocr_tarda_se_pide_el_codigo_sin_esperarlo(self):
        with patch.object(main, "leer_captcha", side_effect=lambda imagen: time.sleep(0.5) or "abc12"), \
                patch.object(main, "ESPERA_OCR", 0.1), patch.object(main, "log"):
            desde = time.monotonic()
            self.assertIsNone(await main.leer_captcha_a_tiempo(b"png"))
            self.assertLess(time.monotonic() - desde, 0.4)
        with patch.object(main, "leer_captcha", return_value="abc12"):
            self.assertEqual(await main.leer_captcha_a_tiempo(b"png"), "abc12")

    async def verificar_cola(self, formato, fecha_expedicion=date(2020, 1, 1), por_sitio=None, captcha=None,
                             consultas_edge=None, preparar=None, espera_formularios=0.3, caidos_esperados=frozenset()):
        activos, maximo = 0, 0
        iniciados = []
        self.inicios = []
        govuk_listo = asyncio.Event()

        async def trabajo(clave, *args):
            nonlocal activos, maximo
            activos += 1
            maximo = max(maximo, activos)
            iniciados.append(clave)
            self.inicios.append(time.monotonic())
            try:
                if clave == "ofac":
                    await asyncio.wait_for(govuk_listo.wait(), 3)
                elif clave == "govuk":
                    govuk_listo.set()
                await asyncio.sleep(0.005)
                return Path(f"{clave}.png")
            finally:
                activos -= 1

        async def con_caida(page, clave, persona, consulta):
            return await consulta

        async def nueva(contexto, consulta, persona, clave):
            return await trabajo(clave)

        async def captcha_listo(*args):
            return Path("captcha.pdf")

        captcha = captcha or captcha_listo

        page = Mock()
        page.is_closed.return_value = False
        contexto = Mock()
        contexto.new_page = AsyncMock(return_value=page)
        contexto.close = AsyncMock()
        contexto.browser.new_context = AsyncMock(return_value=contexto)
        r = {clave: page for clave in ("procuraduria", "simit", "libreta")}
        r.update({clave: contexto for clave in ("grupo", "eeuu", "documentos", "gallagher")})
        r["navegadores"] = (contexto.browser, contexto.browser)
        r["ventanas"] = {clave: page for clave in documentos.ORDEN_TURNOS}
        r["precargas"] = {clave: asyncio.create_task(asyncio.sleep(0)) for clave in (*r["ventanas"], "simit")}
        r["usos"] = dict.fromkeys(main.CON_CASILLA, 0)
        r["renovar"] = AsyncMock()
        r["precargar"] = AsyncMock()
        r["precargar_simit"] = AsyncMock()
        r["ventana_nueva_de"] = AsyncMock(return_value=page)
        r["registrar"] = lambda clave, pagina: pagina
        self.r = r
        for nombre in ("consolidado", "consolium"):
            r[nombre] = SimpleNamespace(clave=nombre, page=page,
                                       buscar=lambda persona, nombre=nombre: trabajo(nombre))
        if preparar:
            preparar(r)

        async def automatico(page, *args):
            return await trabajo("automatica")

        async def eeuu(page, persona, clave, *args):
            return await trabajo(clave)

        async def insight(*args):
            return Path("insight.png")

        with ExitStack() as stack:
            for nombre in ("consultar_procuraduria", "consultar_simit", "consultar_libreta"):
                stack.enter_context(patch.object(main, nombre, side_effect=automatico))
            for modulo, nombre in ((main, "consultar_runt"), (documentos, "consultar_antecedentes"),
                                   (documentos, "consultar_registraduria"), (documentos, "consultar_inhabilidades")):
                clave = nombre.removeprefix("consultar_")
                propia = (por_sitio or {}).get(clave) or captcha
                stack.enter_context(patch.object(modulo, nombre, side_effect=propia))
            for nombre in ("log", "avisar", "abrir_paginas_chrome"):
                stack.enter_context(patch.object(main, nombre))
            cerrar_ventanas = stack.enter_context(patch.object(main, "cerrar_ventanas_abiertas"))
            stack.enter_context(patch.object(rendimiento, "log"))
            stack.enter_context(patch.object(main, "limite_concurrencia", return_value=2))
            stack.enter_context(patch.object(main, "con_caida", side_effect=con_caida))
            stack.enter_context(patch.object(main, "en_pestana_nueva", side_effect=nueva))
            stack.enter_context(patch.object(main, "capturar_sin_ventana", side_effect=eeuu))
            stack.enter_context(patch.object(main, "consultar_insight", side_effect=insight))
            stack.enter_context(patch.object(main, "estado_ventana", new_callable=AsyncMock))
            stack.enter_context(patch.object(main, "pagina_cargada", return_value=True))
            stack.enter_context(patch.object(main, "MARGEN_CHROME", 0))
            stack.enter_context(patch.object(main, "ESPERA_FORMULARIOS", espera_formularios))
            stack.enter_context(patch.object(main, "cerrar_ventana", new_callable=AsyncMock))
            stack.enter_context(patch.object(main, "consultas_edge", side_effect=consultas_edge,
                                            return_value={"fbi": Path("fbi.png"), "consilium": Path("consilium.png")}))
            capturas, caidos = await asyncio.wait_for(main.consultar_persona(
                {"formato": formato, "sexo": "Hombre", "nombre": "Prueba", "tipo": "CC",
                 "fecha_expedicion": fecha_expedicion},
                r, AsyncMock(), Mock()), 5)
        self.assertTrue(govuk_listo.is_set())
        self.assertEqual(maximo, 2)
        self.assertEqual(caidos, set(caidos_esperados))
        esperadas = {
            "procuraduria", "simit", "libreta", "consolidado", "consolium", "ofac", "govuk",
            "eumostwanted", "bdme", "contraloria", "rnmc", "terroristas", "justicia",
            "runt", "antecedentes", "registraduria", "inhabilidades", "fbi", "consilium",
        }
        if formato == "Gallagher":
            esperadas.update(("contadores", "dian", "samm", "bid", "canada", "tesoro", "insight"))
        if fecha_expedicion is None:
            esperadas -= {"registraduria", "rnmc", "inhabilidades"}
            self.assertNotIn("rnmc", iniciados)
        self.assertEqual(set(capturas), esperadas - set(caidos_esperados))
        # Al terminar: no queda nada abierto en el Chrome del usuario, y SIMIT y los formularios con captcha se dejan
        # listos para la siguiente.
        cerrar_ventanas.assert_called_once()
        r["precargar_simit"].assert_called_once()
        self.assertEqual({llamada.args[0] for llamada in r["precargar"].call_args_list}, set(main.PRECARGABLES))
        self.assertFalse(r["consultando"])

    @staticmethod
    def _sin_fondo():
        """Parches para abrir_navegadores sin Buster, sin lectores reales, sin mantenimiento en reposo y sin esperar a
        que el procesador del equipo de pruebas tenga respiro."""
        pila = ExitStack()
        pila.enter_context(patch.object(main, "abrir_buster", AsyncMock(return_value=None)))
        pila.enter_context(patch.object(main, "mantener_en_reposo", new_callable=AsyncMock))
        pila.enter_context(patch.object(main, "preparar_lectores"))
        pila.enter_context(patch.object(main.equipo, "esperar_respiro", AsyncMock(return_value=0)))
        return pila

    @staticmethod
    async def _cancelar(r):
        tareas = [*r["precargas"].values(), *r.get("fondo", ())]
        for tarea in tareas:
            tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)

    async def test_al_abrir_los_formularios_con_captcha_cargan_juntos_y_simit_espera_a_que_terminen(self):
        liberar = asyncio.Event()
        activos, maximo = 0, 0

        async def precargar(page, url, lista):
            nonlocal activos, maximo
            activos += 1
            maximo = max(maximo, activos)
            try:
                await liberar.wait()
            finally:
                activos -= 1

        with self._sin_fondo(), patch.object(main, "estado_ventana", new_callable=AsyncMock), \
                patch.object(main, "abrir_perfil_runt", AsyncMock(return_value=None)), \
                patch.object(documentos, "precargar", side_effect=precargar), \
                patch.object(main, "cargar_pagina", new_callable=AsyncMock) as cargar_pagina, \
                patch.object(main, "cerrar_aviso_simit", new_callable=AsyncMock), \
                patch.object(main, "Documento"), patch.object(rendimiento, "log"):
            r = await main.abrir_navegadores(self._playwright_falso())
            try:
                await asyncio.sleep(0.05)
                self.assertEqual(maximo, len(main.PRECARGABLES))
                # SIMIT (con su validación de seguridad, que ocupa el equipo) espera a los formularios.
                cargar_pagina.assert_not_awaited()
                liberar.set()
                await asyncio.wait_for(asyncio.gather(*r["precargas"].values()), 2)
                self.assertEqual(cargar_pagina.call_args.args[1:], (main.URL_SIMIT, main.formulario_simit_listo))
            finally:
                await self._cancelar(r)

    async def test_al_abrir_documentos_y_lectores_cargan_tras_los_formularios(self):
        cargado = asyncio.Event()
        orden = []

        async def precargar(page, url, lista):
            await asyncio.sleep(0.05)
            orden.append(url)

        async def cargar(page):
            orden.append("documento")
            cargado.set()

        with self._sin_fondo(), patch.object(main, "estado_ventana", new_callable=AsyncMock), \
                patch.object(main, "abrir_perfil_runt", AsyncMock(return_value=None)), \
                patch.object(main, "cargar_consolidado", cargar), \
                patch.object(documentos, "precargar", side_effect=precargar) as precargas, \
                patch.object(main, "cargar_pagina", new_callable=AsyncMock), \
                patch.object(main, "cerrar_aviso_simit", new_callable=AsyncMock), \
                patch.object(main, "Documento") as documento, patch.object(rendimiento, "log"):
            r = await main.abrir_navegadores(self._playwright_falso())
            try:
                carga_del_documento = documento.call_args_list[0].args[1]
                await asyncio.wait_for(carga_del_documento(Mock()), 2)
                self.assertTrue(cargado.is_set())
                self.assertEqual(orden[-1], "documento")
                await asyncio.wait_for(r["fondo"][0], 2)
                main.preparar_lectores.assert_called_once()
                # La Registraduría no se precarga: cada visita de más acerca el bloqueo de su Cloudflare.
                cargadas = {llamada.args[1] for llamada in precargas.call_args_list}
                self.assertNotIn(documentos.URL_REGISTRADURIA, cargadas)
                self.assertIn(documentos.URL_INHABILIDADES, cargadas)
            finally:
                await self._cancelar(r)

    @staticmethod
    def _playwright_falso():
        page = Mock()
        contexto = Mock()
        contexto.new_page = AsyncMock(return_value=page)
        browser = Mock()
        browser.new_context = AsyncMock(return_value=contexto)
        p = Mock()
        p.chromium.launch = AsyncMock(return_value=browser)
        return p

    async def test_ventanas_de_la_casilla_se_cambian_cada_tantas_consultas_o_si_tardo(self):
        renovadas = []

        async def renovar(clave):
            renovadas.append(clave)

        normal, lenta = Mock(), Mock()
        r = {"usos": dict.fromkeys(main.CON_CASILLA, 0), "renovar": renovar, "precargas": {}, "buster": None,
             "ventanas": {clave: Mock(context=normal) for clave in main.CON_CASILLA}}
        with patch.object(main, "log"):
            for _ in range(main.RENOVAR_CADA - 1):
                main.renovar_ventanas(r, set())
            self.assertEqual(r["precargas"], {})
            # Sin fecha de expedición, Inhabilidades no se consultó: no cuenta.
            main.renovar_ventanas(r, {"inhabilidades"})
            await asyncio.gather(*r["precargas"].values())
            self.assertEqual(renovadas, ["antecedentes"])
            self.assertEqual(r["usos"], {"antecedentes": 0, "inhabilidades": main.RENOVAR_CADA - 1})
            r["ventanas"]["antecedentes"] = Mock(context=lenta)
            documentos.casillas_lentas.add(lenta)
            main.renovar_ventanas(r, set())
            await asyncio.gather(*r["precargas"].values())
        self.assertEqual(renovadas, ["antecedentes", "antecedentes", "inhabilidades"])
        self.assertEqual(r["usos"], {"antecedentes": 0, "inhabilidades": 0})

    async def test_renovar_cambia_la_ventana_cierra_la_vieja_y_deja_el_formulario_listo(self):
        async def nueva_pagina():
            pagina = Mock()
            pagina.context.close = AsyncMock()
            return pagina

        precargas = []

        async def precargar(page, url, lista):
            precargas.append((page, url))

        p = self._playwright_falso()
        p.chromium.launch.return_value.new_context.return_value.new_page = AsyncMock(side_effect=nueva_pagina)
        with self._sin_fondo(), patch.object(main, "estado_ventana", new_callable=AsyncMock), \
                patch.object(main, "abrir_perfil_runt", AsyncMock(return_value=None)), \
                patch.object(documentos, "precargar", side_effect=precargar), \
                patch.object(main, "cargar_pagina", new_callable=AsyncMock), \
                patch.object(main, "cerrar_aviso_simit", new_callable=AsyncMock), \
                patch.object(main, "Documento"), patch.object(rendimiento, "log"):
            r = await main.abrir_navegadores(p)
            try:
                await asyncio.wait_for(asyncio.gather(*r["precargas"].values()), 2)
                vieja = r["ventanas"]["antecedentes"]
                await asyncio.wait_for(r["renovar"]("antecedentes"), 2)
            finally:
                await self._cancelar(r)
        nueva = r["ventanas"]["antecedentes"]
        self.assertIsNot(nueva, vieja)
        vieja.context.close.assert_awaited_once()
        nueva.context.close.assert_not_awaited()
        self.assertEqual(precargas[-1], (nueva, documentos.URL_ANTECEDENTES))

    async def test_ventanas_de_captcha_sin_marca_de_automatizado_y_con_respaldo(self):
        navegador = Mock()
        p = Mock()
        p.chromium.launch = AsyncMock(side_effect=[main.PlaywrightError("Chromium distribution 'chrome' is not found"),
                                                   navegador])
        with patch.object(main, "log") as log:
            self.assertIs(await main.abrir_visible(p), navegador)
        primera, segunda = p.chromium.launch.call_args_list
        self.assertEqual(primera.kwargs["channel"], "chrome")
        self.assertNotIn("channel", segunda.kwargs)
        for llamada in (primera, segunda):
            self.assertFalse(llamada.kwargs["headless"])
            self.assertIn("--disable-blink-features=AutomationControlled", llamada.kwargs["args"])
            self.assertEqual(llamada.kwargs["ignore_default_args"], ["--enable-automation"])
        self.assertIn("Chromium del bot", log.call_args.args[0])

    async def test_mediciones_separan_etapas_sin_contar_doble(self):
        with patch.object(rendimiento, "log") as log:
            with rendimiento.medir_sitio("prueba"):
                with rendimiento.etapa("captura"):
                    with rendimiento.etapa("usuario"):
                        await asyncio.sleep(0.02)
                with rendimiento.etapa("carga"):
                    await asyncio.sleep(0.01)
                with rendimiento.etapa("cola"):
                    await asyncio.sleep(0.01)
        linea = log.call_args.args[0]
        self.assertIn("Rendimiento prueba:", linea)
        self.assertIn("captura=0.00s", linea)
        for nombre in ("cola=", "carga=", "usuario=", "respuesta_procesamiento=", "total="):
            self.assertIn(nombre, linea)
        self.assertIsNone(rendimiento._medicion.get())

    async def test_mediciones_se_registran_al_fallar_y_no_se_mezclan(self):
        async def sitio(clave, etapa):
            with rendimiento.medir_sitio(clave):
                with rendimiento.etapa(etapa):
                    await asyncio.sleep(0.01)
                if clave == "fallo":
                    raise ValueError("fallo de prueba")
        with patch.object(rendimiento, "log") as log:
            resultados = await asyncio.gather(sitio("fallo", "carga"), sitio("otro", "usuario"),
                                             return_exceptions=True)
        self.assertIsInstance(resultados[0], ValueError)
        lineas = [llamada.args[0] for llamada in log.call_args_list]
        self.assertEqual(len(lineas), 2)
        self.assertIn("usuario=0.00s", next(l for l in lineas if "fallo:" in l))
        self.assertIn("carga=0.00s", next(l for l in lineas if "otro:" in l))
        self.assertIsNone(rendimiento._medicion.get())

    @staticmethod
    def _recursos_en_reposo():
        hecha = asyncio.get_running_loop().create_future()
        hecha.set_result(None)
        urls = {"runt": main.URL_RUNT, "antecedentes": documentos.URL_ANTECEDENTES,
                "inhabilidades": documentos.URL_INHABILIDADES, "registraduria": documentos.URL_REGISTRADURIA}
        return {
            "ventanas": {clave: Mock(url=url, is_closed=Mock(return_value=False)) for clave, url in urls.items()},
            "formularios": {clave: (url, None) for clave, url in urls.items()},
            "precargas": dict.fromkeys((*urls, "simit"), hecha),
            "precargar": AsyncMock(), "precargar_simit": AsyncMock(), "simit_cargado_en": time.monotonic(),
            "ultima_actividad": time.monotonic(),
        }

    async def test_al_copiar_una_fila_se_cargan_los_formularios_que_vencerian(self):
        r = self._recursos_en_reposo()
        with patch.object(carga, "_precargadas", WeakKeyDictionary()):
            # Inhabilidades, precargada hace 10 s, sirve 150 s; Antecedentes, hace 60 s, vencería antes de usarla.
            carga.marcar_precarga(r["ventanas"]["inhabilidades"], documentos.URL_INHABILIDADES)
            carga._precargadas[r["ventanas"]["antecedentes"]] = (documentos.URL_ANTECEDENTES, time.monotonic() - 60)
            r["consultando"] = True
            main.anticipar(r)
            r["precargar"].assert_not_called()
            r["consultando"] = False
            main.anticipar(r)
        # El RUNT, con su aplicación abierta, vuelve al formulario en 1-2 s; la Registraduría no se precarga.
        self.assertEqual([llamada.args[0] for llamada in r["precargar"].call_args_list], ["antecedentes"])
        r["precargar_simit"].assert_not_called()
        await asyncio.gather(*r["precargas"].values())
        # SIMIT a punto de vencer también se deja listo.
        r["simit_cargado_en"] = time.monotonic() - main.VIGENCIA_SIMIT
        main.anticipar(r)
        r["precargar_simit"].assert_called_once()
        await asyncio.gather(*r["precargas"].values())

    async def test_en_reposo_simit_se_recarga_antes_de_vencer_y_nunca_durante_una_consulta(self):
        r = self._recursos_en_reposo()
        r["precargar_simit"] = AsyncMock(side_effect=lambda: r.update(simit_cargado_en=time.monotonic()))
        r["simit_cargado_en"] = time.monotonic() - main.VIGENCIA_SIMIT
        r["consultando"] = True
        with patch.object(main, "PERIODO_REPOSO", 0.01), patch.object(main.equipo, "saturado", return_value=False):
            tarea = asyncio.create_task(main.mantener_en_reposo(r))
            try:
                await asyncio.sleep(0.05)
                r["precargar_simit"].assert_not_called()
                r["consultando"] = False
                await asyncio.sleep(0.05)
                # Una sola vez: recién cargado, no vuelve a cargarse hasta que esté por vencer.
                r["precargar_simit"].assert_called_once()
                # Sin actividad en mucho tiempo ya no se mantiene.
                r["simit_cargado_en"] = time.monotonic() - main.VIGENCIA_SIMIT
                r["ultima_actividad"] = time.monotonic() - main.REPOSO_MAXIMO - 1
                await asyncio.sleep(0.05)
                r["precargar_simit"].assert_called_once()
            finally:
                tarea.cancel()
                await asyncio.gather(tarea, r["precargas"]["simit"], return_exceptions=True)

    async def test_escalonado_espera_los_formularios_llenos_con_tope(self):
        turnos = documentos.Turnos()
        asyncio.get_running_loop().call_later(0.02, turnos.preparado, "runt")
        self.assertTrue(await turnos.esperar_preparados(["runt"], 1))
        self.assertFalse(await turnos.esperar_preparados(["runt", "antecedentes"], 0.05))
        # Pedir turno es tener el formulario lleno.
        async with turnos.turno("antecedentes", time.monotonic, AsyncMock()):
            pass
        self.assertTrue(await turnos.esperar_preparados(["runt", "antecedentes"], 0.05))
        self.assertTrue(await turnos.esperar_preparados([], 0))

    async def test_casilla_atascada_con_aviso_de_cuota_se_recarga_sin_esperar(self):
        page = Mock(evaluate=AsyncMock(return_value=False), on=Mock(), remove_listener=Mock())
        page.locator.return_value.count = AsyncMock(return_value=1)
        recuadro = Mock()
        recuadro.content_frame.locator.return_value.is_visible = AsyncMock(return_value=False)
        with patch.object(documentos, "casilla_atascada", AsyncMock(return_value=True)), \
                patch.object(documentos, "aviso_de_cuota", AsyncMock(return_value=True)), \
                patch.object(documentos, "ESPERA_CON_CUOTA", 0.1), \
                patch.object(documentos, "avisar"), patch.object(documentos, "log"):
            inicio = time.monotonic()
            with self.assertRaisesRegex(documentos.FueraDeServicio, "cuota"):
                await documentos.esperar_recaptcha(page, "Prueba", recuadro)
        # Sin esperar los ESPERA_CASILLA s de una casilla atascada sin ese aviso.
        self.assertLess(time.monotonic() - inicio, documentos.ESPERA_CASILLA / 3)

    def test_vigencia_de_precarga_por_sitio(self):
        self.assertEqual(documentos.vigencia_precarga("registraduria"), carga.VIGENCIA_PRECARGA)
        margen = documentos.VIGENCIA_CAPTCHA - carga.VIGENCIA_PRECARGA
        for clave in ("antecedentes", "inhabilidades"):
            self.assertEqual(documentos.vigencia_precarga(clave), documentos.VIGENCIAS[clave] - margen)
        # La sesión de Antecedentes vence a los 2 minutos: su vigencia queda por debajo.
        self.assertLess(documentos.vigencia("antecedentes"), 120)

    def test_concurrencia_moderada_segun_equipo(self):
        for nucleos, ram, esperado in ((16, 16000, 4), (8, 1500, 3), (2, 4000, 2)):
            with patch.object(main.os, "cpu_count", return_value=nucleos), \
                    patch.object(main, "memoria_libre_mb", return_value=ram):
                self.assertEqual(main.limite_concurrencia(), esperado)


class EstabilidadTests(unittest.IsolatedAsyncioTestCase):
    async def test_busqueda_en_documento_como_ctrl_f(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content("""<p>12. CARLOS DAVID GIL CASÁS (alias El Gil), nacido en 1980.</p>
                    <p>Carlos   David\n Gil Casas, Bogotá.</p><p>GIL CASAS, Carlos David</p>
                    <table><tr><td>1000143171</td><td>10001431710</td></tr></table>""")
                self.assertEqual(await page.evaluate(main.BUSCAR_EN_DOCUMENTO_JS, "Carlos David Gil Casas"), 2)
                self.assertEqual(await page.evaluate("CSS.highlights.get('buscar').size"), 2)
                # Al buscar otra persona se quita la búsqueda anterior.
                self.assertEqual(await page.evaluate(main.BUSCAR_EN_DOCUMENTO_JS, "1000143171"), 2)
                self.assertEqual(await page.locator("#barra-buscar-bot").count(), 1)
                self.assertEqual(await page.evaluate(main.BUSCAR_EN_DOCUMENTO_JS, "Persona (Prueba)"), 0)
            finally:
                await browser.close()

    async def test_casilla_lenta_o_sin_resolver_marca_su_ventana(self):
        html = """<textarea name="g-recaptcha-response"></textarea>
            <iframe srcdoc="<div id='recaptcha-anchor' style='width:20px;height:20px'></div>"></iframe>
            <script>setTimeout(() => document.querySelector('textarea').value = 'TOKEN', 300)</script>"""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                with patch.object(documentos, "log"), patch.object(documentos, "avisar"):
                    for lenta, marcada in ((60, False), (0.1, True)):
                        contexto = await browser.new_context()
                        page = await contexto.new_page()
                        await page.set_content(html)
                        with patch.object(documentos, "CASILLA_LENTA", lenta):
                            self.assertTrue(await documentos.esperar_recaptcha(page, "Prueba", page.locator("iframe")))
                        self.assertEqual(contexto in documentos.casillas_lentas, marcada)
                    contexto = await browser.new_context()
                    page = await contexto.new_page()
                    await page.set_content(html.replace("TOKEN", ""))
                    with patch.object(documentos, "ESPERA_CAPTCHA", 1000), self.assertRaises(ValueError):
                        await documentos.esperar_recaptcha(page, "Prueba", page.locator("iframe"))
                    self.assertIn(contexto, documentos.casillas_lentas)
            finally:
                await browser.close()

    async def test_formulario_espera_a_que_el_captcha_termine_de_prepararse(self):
        casilla = """<select id="tipo"><option>CC</option></select>
            <iframe title="reCAPTCHA" srcdoc="<div id='recaptcha-anchor' style='width:20px;height:20px'></div>"></iframe>"""
        # Como Google: el recuadro de las imágenes llega después de que ya se ve la casilla.
        desafio = """<script>setTimeout(() => {
                const f = document.createElement('iframe');
                f.src = 'https://bot.test/recaptcha/api2/bframe';
                f.srcdoc = "<input type='hidden' id='recaptcha-token'>";
                f.style = 'visibility: hidden; position: absolute; top: -10000px';
                document.body.append(f);
            }, 1000)</script>"""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                with patch.object(documentos, "log") as log:
                    await page.set_content(casilla + desafio)
                    inicio = time.monotonic()
                    await documentos.formulario_inhabilidades_listo(page)
                    self.assertGreater(time.monotonic() - inicio, 0.5)
                    self.assertEqual(await page.locator(documentos.DESAFIO).count(), 1)
                    log.assert_not_called()
                    # Si no llega, tras el límite la casilla se muestra igual y queda en el registro.
                    await page.set_content(casilla)
                    with patch.object(documentos, "ESPERA_CAPTCHA_LISTO", 0.3):
                        await documentos.formulario_inhabilidades_listo(page)
                    self.assertIn("no terminó de prepararse", log.call_args.args[0])
            finally:
                await browser.close()

    async def test_casilla_atascada_dice_en_que_paso_se_quedo(self):
        girando = """<textarea name="g-recaptcha-response"></textarea>
            <iframe srcdoc="<div id='recaptcha-anchor' style='width:20px;height:20px'>
                <div class='recaptcha-checkbox-spinner'></div></div>"></iframe>"""
        desafio = """<iframe src="https://bot.test/recaptcha/api2/bframe" srcdoc="<p>.</p>"
            style="visibility: hidden; position: absolute; top: -10000px"></iframe>"""
        pulsada = "<script>setTimeout(() => fetch('https://bot.test/recaptcha/api2/reload', {method: 'POST'}), 200)</script>"
        casos = (
            (girando, "el captcha de Google no terminó de prepararse"),
            (girando + desafio, "la casilla no llegó a consultar a Google"),
            (girando + desafio + pulsada, "Google no respondió al pulsarla"),
        )

        async def sin_respuesta(route):
            pass

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                with patch.object(documentos, "log"), patch.object(documentos, "avisar"), \
                        patch.object(documentos, "ESPERA_CASILLA", 1):
                    for html, motivo in casos:
                        contexto = await browser.new_context()
                        await contexto.route("https://bot.test/recaptcha/api2/reload", sin_respuesta)
                        page = await contexto.new_page()
                        await page.set_content(html)
                        with self.assertRaises(documentos.FueraDeServicio) as error:
                            await documentos.esperar_recaptcha(page, "Prueba", page.locator("iframe").first)
                        self.assertIn(motivo, str(error.exception))
                        await contexto.close()
            finally:
                await browser.close()

    async def test_espera_resultado_y_no_espera_trafico_de_analitica(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                peticion = asyncio.Event()
                liberar = asyncio.Event()
                async def analitica(route):
                    peticion.set()
                    await liberar.wait()
                    await route.fulfill(body="ok")
                await page.route("https://metricas.test/**", analitica)
                await page.set_content("""
                    <div class="spinner">Cargando</div><p>Inicial</p>
                    <script>
                    fetch('https://metricas.test/evento');
                    setTimeout(() => {
                        document.querySelector('.spinner').remove();
                        document.querySelector('p').textContent = 'Resultado completo';
                    }, 600);
                    </script>
                """)
                await asyncio.wait_for(peticion.wait(), 2)
                inicio = asyncio.get_running_loop().time()
                await carga.esperar_estable(page)
                self.assertEqual(await page.locator("p").inner_text(), "Resultado completo")
                self.assertLess(asyncio.get_running_loop().time() - inicio, 2)
                self.assertFalse(liberar.is_set())
                liberar.set()
                await page.set_content('<div class="spinner">Cargando</div>')
                # Si la página no se queda quieta, se captura igual: no se pierde el sitio.
                with patch.object(carga, "log") as log:
                    await carga.esperar_estable(page, timeout=600)
                self.assertIn("se captura como está", log.call_args.args[0])
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
