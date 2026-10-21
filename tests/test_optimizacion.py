import asyncio
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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

    async def verificar_cola(self, formato):
        activos, maximo = 0, 0
        iniciados = []
        govuk_listo = asyncio.Event()

        async def trabajo(clave, *args):
            nonlocal activos, maximo
            activos += 1
            maximo = max(maximo, activos)
            iniciados.append(clave)
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

        async def captcha(*args):
            return Path("captcha.pdf")

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
        r["precargas"] = {clave: asyncio.create_task(asyncio.sleep(0)) for clave in r["ventanas"]}
        for nombre in ("consolidado", "consolium"):
            r[nombre] = SimpleNamespace(clave=nombre, page=page,
                                       buscar=lambda persona, nombre=nombre: trabajo(nombre))

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
                stack.enter_context(patch.object(modulo, nombre, side_effect=captcha))
            for nombre in ("log", "avisar", "abrir_paginas_chrome"):
                stack.enter_context(patch.object(main, nombre))
            stack.enter_context(patch.object(rendimiento, "log"))
            stack.enter_context(patch.object(main, "limite_concurrencia", return_value=2))
            stack.enter_context(patch.object(main, "con_caida", side_effect=con_caida))
            stack.enter_context(patch.object(main, "en_pestana_nueva", side_effect=nueva))
            stack.enter_context(patch.object(main, "capturar_sin_ventana", side_effect=eeuu))
            stack.enter_context(patch.object(main, "consultar_insight", side_effect=insight))
            stack.enter_context(patch.object(main, "estado_ventana", new_callable=AsyncMock))
            stack.enter_context(patch.object(main, "consultas_edge",
                                            return_value={"fbi": Path("fbi.png"), "consilium": Path("consilium.png")}))
            capturas, caidos = await asyncio.wait_for(main.consultar_persona(
                {"formato": formato, "sexo": "Hombre", "nombre": "Prueba"},
                r, AsyncMock(), Mock()), 5)
        self.assertTrue(govuk_listo.is_set())
        self.assertEqual(maximo, 2)
        self.assertFalse(caidos)
        esperadas = {
            "procuraduria", "simit", "libreta", "consolidado", "consolium", "ofac", "govuk",
            "eumostwanted", "bdme", "contraloria", "rnmc", "terroristas", "justicia",
            "runt", "antecedentes", "registraduria", "inhabilidades", "fbi", "consilium",
        }
        if formato == "Gallagher":
            esperadas.update(("contadores", "dian", "samm", "bid", "canada", "tesoro", "insight"))
        self.assertEqual(set(capturas), esperadas)

    async def test_precargas_esperan_al_runt_un_tiempo_y_luego_van_de_dos_en_dos(self):
        runt_liberado = asyncio.Event()
        otra_iniciada = asyncio.Event()
        activos, maximo = 0, 0
        async def precargar(page, url, lista):
            nonlocal activos, maximo
            activos += 1
            maximo = max(maximo, activos)
            try:
                if url == main.URL_RUNT:
                    await runt_liberado.wait()
                else:
                    otra_iniciada.set()
                    await asyncio.sleep(0.005)
            finally:
                activos -= 1

        with patch.object(main, "PRIORIDAD_RUNT", 0.3), \
                patch.object(main, "estado_ventana", new_callable=AsyncMock), \
                patch.object(main, "abrir_perfil_runt", AsyncMock(return_value=None)), \
                patch.object(documentos, "precargar", side_effect=precargar), \
                patch.object(main, "Documento"), patch.object(rendimiento, "log"):
            r = await main.abrir_navegadores(self._playwright_falso())
            try:
                await asyncio.sleep(0.05)
                self.assertFalse(otra_iniciada.is_set())
                # Si el RUNT tarda más que su prioridad, las demás ya no lo esperan.
                await asyncio.wait_for(otra_iniciada.wait(), 2)
                self.assertFalse(r["precargas"]["runt"].done())
                runt_liberado.set()
                await asyncio.gather(*r["precargas"].values())
            finally:
                for tarea in r["precargas"].values():
                    tarea.cancel()
                await asyncio.gather(*r["precargas"].values(), return_exceptions=True)
        self.assertEqual(maximo, 2)

    async def test_precargas_y_documentos_siguen_apenas_termina_el_runt(self):
        cargado = asyncio.Event()
        async def precargar(page, url, lista):
            await asyncio.sleep(0.01)

        async def cargar(page):
            cargado.set()

        with patch.object(main, "PRIORIDAD_RUNT", 30), \
                patch.object(main, "estado_ventana", new_callable=AsyncMock), \
                patch.object(main, "abrir_perfil_runt", AsyncMock(return_value=None)), \
                patch.object(main, "cargar_consolidado", cargar), \
                patch.object(documentos, "precargar", side_effect=precargar), \
                patch.object(main, "Documento") as documento, patch.object(rendimiento, "log"):
            r = await main.abrir_navegadores(self._playwright_falso())
            try:
                await asyncio.wait_for(asyncio.gather(*r["precargas"].values()), 2)
                carga_del_documento = documento.call_args_list[0].args[1]
                await asyncio.wait_for(carga_del_documento(Mock()), 2)
                self.assertTrue(cargado.is_set())
            finally:
                for tarea in r["precargas"].values():
                    tarea.cancel()
                await asyncio.gather(*r["precargas"].values(), return_exceptions=True)

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

    def test_concurrencia_moderada_segun_equipo(self):
        for nucleos, ram, esperado in ((16, 16000, 4), (8, 1500, 3), (2, 4000, 2)):
            with patch.object(main.os, "cpu_count", return_value=nucleos), \
                    patch.object(main, "memoria_libre_mb", return_value=ram):
                self.assertEqual(main.limite_concurrencia(), esperado)


class EstabilidadTests(unittest.IsolatedAsyncioTestCase):
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
