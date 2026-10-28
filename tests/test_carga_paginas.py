import asyncio
import sys
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from weakref import WeakKeyDictionary

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import carga_paginas as carga
    import main
finally:
    sys.path.pop(0)

URL = "https://bot.test/"


async def sin_fin(*_):
    await asyncio.Event().wait()


def abre_al_segundo():
    """Formulario que no aparece en la primera carga y sí en la siguiente."""
    llamadas = []

    async def lista(page):
        llamadas.append(page)
        if len(llamadas) == 1:
            await sin_fin()

    return lista


class CargaPaginasTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.parches = ExitStack()
        self.addCleanup(self.parches.close)
        self.parches.enter_context(patch.object(carga, "ESPERA_CARGA", 0.1))
        self.log = self.parches.enter_context(patch.object(carga, "log"))
        self.detener = self.parches.enter_context(patch.object(carga, "_detener", new_callable=AsyncMock))
        self.parches.enter_context(patch.object(carga, "_precargadas", WeakKeyDictionary()))
        self.parches.enter_context(patch.object(carga, "_respaldos", WeakKeyDictionary()))
        self.parches.enter_context(patch.object(carga, "_reemplazos", WeakKeyDictionary()))
        self.parches.enter_context(patch.object(carga, "_duraciones", {}))
        # El procesador del equipo de pruebas no decide: por defecto, con respiro.
        self.saturado = self.parches.enter_context(patch.object(carga.equipo, "saturado", return_value=False))
        self.orden = []
        self.estados = []
        self.page = self.pagina()

    def pagina(self, url="about:blank"):
        # Sin aviso de bloqueo ni verificación de Cloudflare.
        page = Mock(url=url, is_closed=Mock(return_value=False), wait_for_load_state=AsyncMock(),
                    evaluate=AsyncMock(return_value=False))

        async def navegar(destino, **opciones):
            self.orden.append(("goto", destino, opciones.get("wait_until")))
            page.url = destino
            return SimpleNamespace(status=self.estados.pop(0) if self.estados else 200)

        async def recargar(**opciones):
            self.orden.append(("reload", page.url, opciones.get("wait_until")))
            return SimpleNamespace(status=200)

        page.goto = AsyncMock(side_effect=navegar)
        page.reload = AsyncMock(side_effect=recargar)
        return page

    async def test_abre_a_la_primera_en_la_misma_pagina(self):
        self.assertIs(await carga.cargar_pagina(self.page, URL, AsyncMock()), self.page)
        self.assertEqual(self.orden, [("goto", URL, "domcontentloaded")])
        self.detener.assert_not_awaited()

    async def test_si_no_abre_se_detiene_y_se_carga_de_nuevo_una_vez(self):
        self.assertIs(await carga.cargar_pagina(self.page, URL, abre_al_segundo()), self.page)
        self.assertEqual(self.orden, [("goto", URL, "domcontentloaded")] * 2)
        self.detener.assert_awaited_once_with(self.page)
        self.assertIn("se recarga", self.log.call_args.args[0])

    async def test_sin_respuesta_tras_recargar_queda_fuera_de_servicio(self):
        inicio = time.monotonic()
        with self.assertRaises(carga.FueraDeServicio) as error:
            await carga.cargar_pagina(self.page, URL, sin_fin)
        self.assertGreaterEqual(time.monotonic() - inicio, 0.2)
        self.assertEqual(error.exception.direccion, URL)
        self.assertIn("no abrió en", str(error.exception))
        self.assertEqual(len(self.orden), 2)

    async def test_error_de_red_se_reintenta_e_informa(self):
        self.page.goto.side_effect = PlaywrightError("net::ERR_CONNECTION_RESET at https://bot.test/")
        with self.assertRaisesRegex(carga.FueraDeServicio, "ERR_CONNECTION_RESET"):
            await carga.cargar_pagina(self.page, URL, AsyncMock())
        self.assertEqual(self.page.goto.await_count, 2)

    async def test_error_del_servidor_se_reintenta(self):
        self.estados = [503]
        self.assertIs(await carga.cargar_pagina(self.page, URL, AsyncMock()), self.page)
        self.assertIn("HTTP 503", self.log.call_args.args[0])

    async def test_spa_abierta_vuelve_al_formulario_antes_de_recargar(self):
        # SIMIT y RUNT: tras una consulta la página queda en el resultado; solo recargar lo volvería a mostrar.
        self.page.url = "https://bot.test/#/estado-cuenta"
        await carga.cargar_pagina(self.page, "https://bot.test/#/home", AsyncMock(), recargar=True)
        self.assertEqual(self.orden, [("goto", "https://bot.test/#/home", "commit"),
                                      ("reload", "https://bot.test/#/home", "domcontentloaded")])

    async def test_precarga_se_reutiliza_una_sola_vez_sin_recargar(self):
        carga.marcar_precarga(self.page, URL)
        self.assertIs(await carga.cargar_pagina(self.page, URL, AsyncMock(), reutilizar=True), self.page)
        self.assertEqual(self.orden, [])
        await carga.cargar_pagina(self.page, URL, AsyncMock(), reutilizar=True)
        self.assertEqual(len(self.orden), 1)

    async def test_precarga_vencida_o_de_otra_direccion_no_se_reutiliza(self):
        for url, edad in ((URL, 60), ("https://otro.test/", 0)):
            carga._precargadas[self.page] = (url, time.monotonic() - edad)
            await carga.cargar_pagina(self.page, URL, AsyncMock(), reutilizar=True)
        self.assertEqual(len(self.orden), 2)

    async def test_precarga_que_caducaria_antes_de_su_turno_se_recarga_al_empezar(self):
        # Con lo que tarda llenar el formulario, el turno del captcha la recargaría después, con todo cargando.
        limite = main.documentos.VIGENCIA_CAPTCHA - main.documentos.MARGEN_RECARGA
        carga._precargadas[self.page] = (URL, time.monotonic() - (limite - 10))
        await carga.cargar_pagina(self.page, URL, AsyncMock(), reutilizar=True)
        self.assertEqual(len(self.orden), 1)

    async def test_precarga_que_perdio_el_formulario_se_vuelve_a_cargar(self):
        carga.marcar_precarga(self.page, URL)
        lista = AsyncMock(side_effect=[PlaywrightError("formulario perdido"), None])
        await carga.cargar_pagina(self.page, URL, lista, reutilizar=True)
        self.assertEqual(len(self.orden), 1)

    async def test_configuracion_se_aplica_una_vez(self):
        configurar = AsyncMock()
        await carga.cargar_pagina(self.page, URL, abre_al_segundo(), configurar=configurar)
        configurar.assert_awaited_once_with(self.page)

    async def test_cancelar_corta_la_espera(self):
        tarea = asyncio.create_task(carga.cargar_pagina(self.page, URL, sin_fin, espera=60))
        await asyncio.sleep(0.05)
        tarea.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tarea

    def respaldo(self, crear=None):
        """Otra ventana del mismo navegador para la carga de respaldo de self.page."""
        otra = self.pagina()
        contexto = Mock(pages=[self.page, otra], browser=None)
        for pagina in (self.page, otra):
            pagina.context = contexto
            pagina.close = AsyncMock()
        carga.con_respaldo(self.page, crear or AsyncMock(return_value=otra))
        return otra

    async def test_si_no_abre_a_tiempo_se_carga_a_la_vez_en_otra_ventana_y_vale_la_primera(self):
        otra = self.respaldo()

        async def lista(page):
            if page is self.page:
                await sin_fin()

        with patch.object(carga, "ESPERA_RESPALDO", 0.02):
            self.assertIs(await carga.cargar_pagina(self.page, URL, lista), otra)
        # La consulta sigue en la ventana nueva; la primera, que seguía cargando, se cierra.
        self.assertIs(carga.vigente(self.page), otra)
        self.page.close.assert_awaited_once()
        otra.close.assert_not_awaited()
        self.detener.assert_not_awaited()
        self.assertIn("respaldo", self.log.call_args.args[0])

    async def test_si_la_primera_abre_antes_que_el_respaldo_se_cierra_el_respaldo(self):
        liberar = asyncio.Event()

        async def crear():
            asyncio.get_running_loop().call_later(0.02, liberar.set)
            return otra

        otra = self.respaldo(crear)

        async def lista(page):
            await (liberar.wait() if page is self.page else sin_fin())

        with patch.object(carga, "ESPERA_RESPALDO", 0.02):
            self.assertIs(await carga.cargar_pagina(self.page, URL, lista), self.page)
        self.assertIs(carga.vigente(self.page), self.page)
        otra.close.assert_awaited_once()
        self.page.close.assert_not_awaited()

    async def test_con_el_equipo_saturado_no_se_abre_otra_ventana_y_se_espera_la_primera(self):
        crear = AsyncMock()
        self.respaldo(crear)
        self.saturado.return_value = True
        liberar = asyncio.Event()
        asyncio.get_running_loop().call_later(0.1, liberar.set)

        async def lista(page):
            await liberar.wait()

        with patch.object(carga, "ESPERA_RESPALDO", 0.02), patch.object(carga.equipo, "MUESTRA", 0.01), \
                patch.object(carga, "ESPERA_CARGA", 2):
            self.assertIs(await carga.cargar_pagina(self.page, URL, lista), self.page)
        crear.assert_not_awaited()
        self.assertTrue(any("saturado" in llamada.args[0] for llamada in self.log.call_args_list))

    async def test_cancelar_durante_la_espera_del_respaldo_no_deja_cargas_sueltas(self):
        self.respaldo()
        tarea = asyncio.create_task(carga.cargar_pagina(self.page, URL, sin_fin, espera=60))
        await asyncio.sleep(0.05)
        tarea.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tarea
        await asyncio.sleep(0)
        sueltas = [t for t in asyncio.all_tasks() if "_abrir" in repr(t.get_coro())]
        self.assertEqual(sueltas, [])

    async def test_sin_ventana_de_respaldo_registrada_no_se_abre_otra(self):
        self.assertIs(await carga.cargar_pagina(self.page, URL, abre_al_segundo()), self.page)
        self.assertEqual(self.orden, [("goto", URL, "domcontentloaded")] * 2)

    def test_espera_del_respaldo_se_adapta_al_equipo(self):
        self.assertEqual(carga._espera_respaldo(URL), carga.ESPERA_RESPALDO)
        for ultima, esperada in ((4, carga.ESPERA_RESPALDO_MINIMA), (18, 18 * carga.RESPALDO_VECES),
                                 (100, carga.ESPERA_RESPALDO_MAXIMA)):
            carga._duraciones["bot.test"] = ultima
            self.assertEqual(carga._espera_respaldo(URL), esperada)

    async def test_aplicacion_abierta_vuelve_al_formulario_con_su_atajo_sin_recargar(self):
        self.page.url = "https://bot.test/#/resultado"
        atajo = AsyncMock()
        with patch.object(carga, "_atajos", {"https://bot.test/#/home": atajo}):
            self.assertIs(await carga.cargar_pagina(self.page, "https://bot.test/#/home", AsyncMock()), self.page)
        atajo.assert_awaited_once_with(self.page)
        self.assertEqual(self.orden, [])

    async def test_si_el_atajo_falla_se_recarga_la_aplicacion(self):
        self.page.url = "https://bot.test/#/resultado"
        atajo = AsyncMock(side_effect=PlaywrightError("sin captcha nuevo"))
        with patch.object(carga, "_atajos", {"https://bot.test/#/home": atajo}):
            await carga.cargar_pagina(self.page, "https://bot.test/#/home", AsyncMock())
        self.assertEqual(self.orden, [("goto", "https://bot.test/#/home", "commit"),
                                      ("reload", "https://bot.test/#/home", "domcontentloaded")])

    async def test_pagina_caida_se_captura_en_la_misma_pagina(self):
        async def fallar():
            raise carga.FueraDeServicio("sin respuesta")

        with patch.object(main, "capturar_caida", new_callable=AsyncMock, return_value=Path("caida.png")) as capturar:
            with self.assertRaises(carga.FueraDeServicio) as error:
                await main.con_caida(self.page, "runt", {}, fallar())
        capturar.assert_awaited_once_with(self.page, "runt", {})
        self.assertEqual(error.exception.captura, Path("caida.png"))

    async def test_documento_fijo_no_reinicia_en_la_misma_consulta(self):
        cargar = AsyncMock(side_effect=carga.FueraDeServicio("no abrió"))
        documento = main.Documento(self.page, cargar, "consolium", "EUR-Lex")
        with self.assertRaises(carga.FueraDeServicio):
            await documento.buscar({})
        cargar.assert_awaited_once()
        with self.assertRaises(carga.FueraDeServicio):
            await documento.buscar({})
        self.assertEqual(cargar.await_count, 2)


class CargaNavegadorTests(unittest.IsolatedAsyncioTestCase):
    def test_esperas_configuradas(self):
        self.assertEqual((carga.ESPERA_CARGA, carga.INTENTOS), (120, 2))

    async def test_no_abre_a_la_primera_y_abre_al_recargar_sin_otra_pestana(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                contexto = await browser.new_context()
                page = await contexto.new_page()
                pedidos = []

                async def responder(route):
                    pedidos.append(route.request.url)
                    html = "<p>Cargando</p>" if len(pedidos) == 1 else '<input id="documento">'
                    await route.fulfill(body=html, content_type="text/html")

                await contexto.route("https://bot.test/**", responder)
                with patch.object(carga, "ESPERA_CARGA", 1), patch.object(carga, "log"):
                    cargada = await carga.cargar_pagina(
                        page, URL, lambda p: p.locator("#documento").wait_for(timeout=60000))
                self.assertIs(cargada, page)
                self.assertEqual(pedidos, [URL, URL])
                self.assertEqual(contexto.pages, [page])
                await page.locator("#documento").fill("123456")
            finally:
                await browser.close()

    async def test_spa_real_vuelve_al_formulario(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                pedidos = []
                html = """<body><script>
                    document.body.innerHTML = location.hash === '#/home' ? '<input id="documento">' : '<p>Resultado</p>';
                </script></body>"""

                async def responder(route):
                    pedidos.append(route.request.url)
                    await route.fulfill(body=html, content_type="text/html")

                await page.context.route("https://bot.test/**", responder)
                await page.goto("https://bot.test/#/resultado")
                with patch.object(carga, "log"):
                    await carga.cargar_pagina(page, "https://bot.test/#/home",
                                              lambda p: p.locator("#documento").wait_for(timeout=5000), recargar=True)
                self.assertEqual(len(pedidos), 2)
                self.assertTrue(page.url.endswith("#/home"))
            finally:
                await browser.close()

    async def test_runt_vuelve_al_formulario_con_captcha_nuevo_sin_recargar(self):
        # Como el RUNT: aplicación de una sola página que pide un captcha nuevo cada vez que se entra al formulario.
        html = """<body><script>
            let n = 0;
            function pintar() {
                if (!location.hash.startsWith('#/consulta')) { document.body.innerHTML = '<p>Inicio</p>'; return; }
                const c = document.createElement('canvas');
                c.width = 80; c.height = 20;
                c.getContext('2d').fillText('captcha ' + (++n), 5, 15);
                document.body.innerHTML = '<input formcontrolname="documento"><img class="img-fluid" src="' +
                    c.toDataURL() + '">';
            }
            addEventListener('hashchange', pintar);
            pintar();
        </script></body>"""
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                pedidos = []

                async def responder(route):
                    pedidos.append(route.request.url)
                    await route.fulfill(body=html, content_type="text/html")

                await page.context.route("https://bot.test/**", responder)
                await page.goto("https://bot.test/" + main.RUTA_RUNT)
                antes = await page.locator("img.img-fluid").get_attribute("src")
                await page.locator("input").fill("123")
                await main.volver_al_formulario_runt(page)
                self.assertNotEqual(await page.locator("img.img-fluid").get_attribute("src"), antes)
                self.assertEqual(await page.locator("input").input_value(), "")
                self.assertEqual(len(pedidos), 1)
            finally:
                await browser.close()

    async def test_precarga_real_reutilizada_conserva_antiguedad(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                navegaciones = []

                async def responder(route):
                    navegaciones.append(route.request.url)
                    await route.fulfill(body='<input id="documento">', content_type="text/html")

                await page.context.route("https://bot.test/**", responder)
                lista = lambda p: p.locator("#documento").wait_for(timeout=60000)
                with patch.object(main.documentos, "estado_ventana", new_callable=AsyncMock), \
                        patch.object(carga, "log"):
                    await main.documentos.precargar(page, URL, lista)
                    instante = carga.instante_carga(page)
                    await asyncio.sleep(0.05)
                    reutilizada = await carga.cargar_pagina(page, URL, lista, recargar=True, reutilizar=True)
                    self.assertIs(reutilizada, page)
                    self.assertEqual(carga.instante_carga(page), instante)
                    await page.locator("#documento").fill("123")
                    self.assertEqual(navegaciones, [URL])
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
