import asyncio
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import carga_paginas as carga
    import main
finally:
    sys.path.pop(0)


class CargaPaginasTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.parches = ExitStack()
        self.addCleanup(self.parches.close)
        for nombre, valor in (("SEGUNDA_PESTANA", 0.05), ("RECARGA", 0.1), ("LIMITE_CARGA", 0.2)):
            self.parches.enter_context(patch.object(carga, nombre, valor))
        self.parches.enter_context(patch.object(carga, "log"))
        self.detener = self.parches.enter_context(patch.object(carga, "_detener", new_callable=AsyncMock))
        self.inicio = asyncio.get_running_loop().time()
        self.eventos = []
        self.contexto = SimpleNamespace(new_page=AsyncMock())
        self.primera = self.pagina("primera")
        self.segunda = self.pagina("segunda")
        self.contexto.new_page.return_value = self.segunda

        async def abrir(original):
            return await self.contexto.new_page()

        self.parches.enter_context(patch.object(
            carga, "abrir_pestana_en_fondo", side_effect=abrir))
        self.parches.enter_context(patch.object(carga, "_sustitutas", {}))
        self.parches.enter_context(patch.object(carga, "_precargadas", {}))

    def pagina(self, nombre):
        async def navegar(*args, **kwargs):
            self.eventos.append((nombre, "goto", asyncio.get_running_loop().time() - self.inicio))
            return SimpleNamespace(status=200)

        async def recargar(*args, **kwargs):
            self.eventos.append((nombre, "reload", asyncio.get_running_loop().time() - self.inicio))
            return SimpleNamespace(status=200)

        return Mock(
            nombre=nombre, context=self.contexto, viewport_size={"width": 1000, "height": 700},
            goto=AsyncMock(side_effect=navegar), reload=AsyncMock(side_effect=recargar),
            close=AsyncMock(), is_closed=Mock(return_value=False), url="https://bot.test/",
            set_viewport_size=AsyncMock(), wait_for_load_state=AsyncMock(),
        )

    async def test_primera_lista_no_abre_otra(self):
        ganadora = await carga.cargar_pagina(self.primera, "https://bot.test/", AsyncMock())
        self.assertIs(ganadora, self.primera)
        self.contexto.new_page.assert_not_awaited()

    async def test_segunda_gana_a_los_15_y_se_usa_para_la_consulta(self):
        async def lista(page):
            if page is self.primera:
                await asyncio.Event().wait()

        ganadora = await carga.cargar_pagina(self.primera, "https://bot.test/", lista)
        self.assertIs(ganadora, self.segunda)
        self.assertIs(carga.pagina_actual(self.primera), self.segunda)
        self.assertGreaterEqual(self.eventos[1][2], 0.05)
        self.primera.reload.assert_not_awaited()
        self.detener.assert_awaited_with(self.primera)
        await carga.cerrar_secundarias(self.primera)
        self.segunda.close.assert_awaited_once()
        self.assertIs(carga.pagina_actual(self.primera), self.primera)

    async def test_primera_puede_ganar_despues_de_abrir_segunda(self):
        async def lista(page):
            if page is self.primera:
                await asyncio.sleep(0.075)
            else:
                await asyncio.Event().wait()

        self.assertIs(await carga.cargar_pagina(self.primera, "https://bot.test/", lista), self.primera)
        self.segunda.close.assert_awaited_once()
        self.assertFalse(carga._sustitutas)

    async def test_recarga_solo_secundaria_y_conserva_original(self):
        async def lista(page):
            if page is self.primera:
                await asyncio.Event().wait()
            if not page.reload.await_count:
                await asyncio.Event().wait()

        self.assertIs(await carga.cargar_pagina(self.primera, "https://bot.test/", lista), self.segunda)
        recargas = [evento for evento in self.eventos if evento[1] == "reload"]
        self.assertEqual({evento[0] for evento in recargas}, {"segunda"})
        self.assertTrue(all(evento[2] >= 0.1 for evento in recargas))
        self.primera.reload.assert_not_awaited()
        await carga.cerrar_secundarias(self.primera)

    async def test_original_puede_terminar_despues_del_hito_30(self):
        async def lista(page):
            if page is self.primera:
                await asyncio.sleep(0.13)
            else:
                await asyncio.Event().wait()
        self.assertIs(await carga.cargar_pagina(self.primera, "https://bot.test/", lista), self.primera)
        self.primera.reload.assert_not_awaited()
        self.primera.goto.assert_awaited_once()
        self.segunda.close.assert_awaited_once()

    async def test_precarga_se_reutiliza_una_sola_vez_sin_recargar(self):
        carga.marcar_precarga(self.primera, "https://bot.test/")
        self.assertIs(
            await carga.cargar_pagina(self.primera, "https://bot.test/", AsyncMock(),
                                     recargar=True, reutilizar=True), self.primera)
        self.primera.goto.assert_not_awaited()
        self.primera.reload.assert_not_awaited()
        await carga.cargar_pagina(self.primera, "https://bot.test/", AsyncMock(), recargar=True, reutilizar=True)
        self.primera.reload.assert_awaited_once()

    async def test_precarga_vencida_o_de_otra_url_no_se_reutiliza(self):
        for url, edad in (("https://bot.test/", 60), ("https://otro.test/", 0)):
            carga._precargadas[self.primera] = (url, carga.time.monotonic() - edad)
            await carga.cargar_pagina(self.primera, "https://bot.test/", AsyncMock(),
                                     recargar=True, reutilizar=True)
        self.assertEqual(self.primera.reload.await_count, 2)

    async def test_precarga_que_perdio_formulario_se_recarga(self):
        carga.marcar_precarga(self.primera, "https://bot.test/")
        lista = AsyncMock(side_effect=[PlaywrightError("formulario perdido"), None])
        await carga.cargar_pagina(self.primera, "https://bot.test/", lista, recargar=True, reutilizar=True)
        self.primera.reload.assert_awaited_once()

    async def test_limiter_no_abre_otra_pestana_y_no_bloquea_original(self):
        limite = asyncio.Semaphore(0)
        async def lista(page):
            await asyncio.sleep(0.13)
        with patch.object(carga, "limite_secundarias", return_value=limite):
            self.assertIs(await carga.cargar_pagina(self.primera, "https://bot.test/", lista), self.primera)
        self.contexto.new_page.assert_not_awaited()

    async def test_cancelacion_devuelve_permiso_de_secundaria(self):
        limite = asyncio.Semaphore(1)
        async def lista(page):
            await asyncio.Event().wait()
        with patch.object(carga, "limite_secundarias", return_value=limite):
            tarea = asyncio.create_task(carga.cargar_pagina(self.primera, "https://bot.test/", lista))
            await asyncio.sleep(0.075)
            self.assertTrue(limite.locked())
            tarea.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tarea
            self.assertFalse(limite.locked())
        self.segunda.close.assert_awaited_once()

    async def test_limite_60_no_responde_y_limpia_tareas(self):
        async def lista(page):
            await asyncio.Event().wait()

        with self.assertRaises(carga.FueraDeServicio) as error:
            await carga.cargar_pagina(self.primera, "https://bot.test/", lista)
        transcurrido = asyncio.get_running_loop().time() - self.inicio
        self.assertGreaterEqual(transcurrido, 0.2)
        self.assertLess(transcurrido, 0.4)
        self.assertEqual(error.exception.direccion, "https://bot.test/")
        self.segunda.close.assert_awaited_once()
        self.assertFalse(carga._sustitutas)

    async def test_error_de_red_no_se_considera_carga_exitosa(self):
        for page in (self.primera, self.segunda):
            page.goto.side_effect = PlaywrightError("net::ERR_CONNECTION_RESET")
            page.reload.side_effect = PlaywrightError("net::ERR_CONNECTION_RESET")
        with self.assertRaisesRegex(carga.FueraDeServicio, "ERR_CONNECTION_RESET"):
            await carga.cargar_pagina(self.primera, "https://bot.test/")

    async def test_cancelacion_cierra_secundaria(self):
        async def lista(page):
            await asyncio.Event().wait()

        tarea = asyncio.create_task(carga.cargar_pagina(self.primera, "https://bot.test/", lista))
        await asyncio.sleep(0.075)
        tarea.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tarea
        self.segunda.close.assert_awaited_once()

    async def test_documento_fijo_conserva_ganadora_y_libera_original(self):
        async def lista(page):
            if page is self.primera:
                await asyncio.Event().wait()

        await carga.cargar_pagina(self.primera, "https://bot.test/", lista)
        self.assertIs(await carga.conservar_pagina(self.primera), self.segunda)
        self.primera.close.assert_awaited_once()
        self.segunda.close.assert_not_awaited()
        self.assertFalse(carga._sustitutas)

    async def test_configuracion_se_aplica_a_ambas_pestanas(self):
        configurar = AsyncMock()
        async def lista(page):
            if page is self.primera:
                await asyncio.Event().wait()

        await carga.cargar_pagina(self.primera, "https://bot.test/", lista, configurar=configurar)
        self.assertEqual([llamada.args[0] for llamada in configurar.await_args_list], [self.primera, self.segunda])
        await carga.cerrar_secundarias(self.primera)

    async def test_recarga_inicial_de_spa_no_reutiliza_resultado_anterior(self):
        self.assertIs(
            await carga.cargar_pagina(self.primera, "https://bot.test/", AsyncMock(), recargar=True),
            self.primera,
        )
        self.primera.reload.assert_awaited_once()
        self.primera.goto.assert_not_awaited()

    async def test_error_se_captura_en_ganadora_antes_de_cerrarla(self):
        carga._sustitutas[self.primera] = self.segunda
        async def fallar():
            raise carga.FueraDeServicio("sin respuesta")

        with patch.object(main, "capturar_caida", new_callable=AsyncMock, return_value=Path("caida.png")) as capturar:
            with self.assertRaises(carga.FueraDeServicio) as error:
                await main.con_caida(self.primera, "runt", {}, fallar())
        capturar.assert_awaited_once_with(self.segunda, "runt", {})
        self.assertEqual(error.exception.captura, Path("caida.png"))
        self.segunda.close.assert_awaited_once()
        self.assertFalse(carga._sustitutas)

    async def test_documento_fijo_no_reinicia_limite_en_la_misma_consulta(self):
        cargar = AsyncMock(side_effect=carga.FueraDeServicio("no respondió en 60 s"))
        documento = main.Documento(self.primera, cargar, "consolium", "EUR-Lex")
        with self.assertRaises(carga.FueraDeServicio):
            await documento.buscar({})
        cargar.assert_awaited_once()
        with self.assertRaises(carga.FueraDeServicio):
            await documento.buscar({})
        self.assertEqual(cargar.await_count, 2)


class CargaNavegadorTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(sys.platform == "win32", "Comprueba el foco de ventanas de Windows")
    async def test_recuperacion_no_tapa_el_captcha_activo(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            try:
                contexto = await browser.new_context(no_viewport=True)
                original = await contexto.new_page()
                await main.documentos.estado_ventana(original, "minimized")
                activo = await (await browser.new_context(no_viewport=True)).new_page()
                await activo.set_content("<title>Captcha activo</title><input id='respuesta'>")
                await main.documentos.al_frente(activo)
                self.assertTrue(await activo.evaluate("document.hasFocus()"))
                ventana_activa = user32.GetForegroundWindow()

                async def responder(route):
                    html = "<p>Cargando</p>" if route.request.frame.page is original else '<input id="documento">'
                    await route.fulfill(body=html, content_type="text/html")

                await contexto.route("https://bot.test/**", responder)
                with patch.object(carga, "SEGUNDA_PESTANA", 0.2), \
                        patch.object(carga, "RECARGA", 10), patch.object(carga, "LIMITE_CARGA", 20), \
                        patch.object(carga, "log"):
                    ganadora = await carga.cargar_pagina(
                        original, "https://bot.test/",
                        lambda page: page.locator("#documento").wait_for(timeout=60000))
                self.assertIsNot(ganadora, original)
                self.assertIs(ganadora.context, contexto)
                await ganadora.locator("#documento").fill("123456")
                self.assertEqual(user32.GetForegroundWindow(), ventana_activa)
                self.assertTrue(await activo.evaluate("document.hasFocus()"))
                await carga.cerrar_secundarias(original)
                self.assertEqual(user32.GetForegroundWindow(), ventana_activa)
                self.assertTrue(await activo.evaluate("document.hasFocus()"))
            finally:
                await browser.close()

    async def test_error_al_crear_pestana_se_propaga_y_libera_cdp(self):
        cdp = Mock(send=AsyncMock(side_effect=PlaywrightError("fallo CDP")), detach=AsyncMock())
        contexto = Mock(new_cdp_session=AsyncMock(return_value=cdp))
        original = Mock(context=contexto)
        with self.assertRaisesRegex(PlaywrightError, "fallo CDP"):
            await carga.abrir_pestana_en_fondo(original)
        cdp.detach.assert_awaited_once()
        contexto.new_page.assert_not_called()

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
                    await main.documentos.precargar(page, "https://bot.test/", lista)
                    instante = carga.instante_carga(page)
                    await asyncio.sleep(0.05)
                    reutilizada = await carga.cargar_pagina(
                        page, "https://bot.test/", lista, recargar=True, reutilizar=True)
                    self.assertIs(reutilizada, page)
                    self.assertEqual(carga.instante_carga(page), instante)
                    await page.locator("#documento").fill("123")
                    self.assertEqual(navegaciones, ["https://bot.test/"])
                    await carga.cerrar_secundarias(page)
            finally:
                await browser.close()

    async def test_segunda_lista_se_rellena_solo_una_vez_y_se_cierra(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                contexto = await browser.new_context()
                original = await contexto.new_page()
                async def responder(route):
                    html = "<p>Cargando</p>" if route.request.frame.page is original else '<input id="documento">'
                    await route.fulfill(body=html, content_type="text/html")

                await contexto.route("https://bot.test/**", responder)
                with patch.object(carga, "SEGUNDA_PESTANA", 0.2), \
                        patch.object(carga, "RECARGA", 2), patch.object(carga, "LIMITE_CARGA", 4), \
                        patch.object(carga, "log"):
                    ganadora = await carga.cargar_pagina(
                        original, "https://bot.test/", lambda page: page.locator("#documento").wait_for(timeout=60000))
                    self.assertIsNot(ganadora, original)
                    await ganadora.locator("#documento").fill("123456")
                    self.assertEqual(await ganadora.locator("#documento").input_value(), "123456")
                    self.assertEqual(await original.locator("#documento").count(), 0)
                    await carga.cerrar_secundarias(original)
                    self.assertEqual(contexto.pages, [original])
            finally:
                await browser.close()

    def test_cronologia_configurada(self):
        self.assertEqual((carga.SEGUNDA_PESTANA, carga.RECARGA, carga.LIMITE_CARGA), (15, 30, 60))


if __name__ == "__main__":
    unittest.main()
