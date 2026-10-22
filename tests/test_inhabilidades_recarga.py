import asyncio
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import documentos
finally:
    sys.path.pop(0)


class InhabilidadesRecargaTests(unittest.IsolatedAsyncioTestCase):
    async def test_captcha_atascado_recarga_y_repone_todos_los_datos(self):
        persona = {"tipo": "CC", "numero": "123456", "fecha_expedicion": date(2020, 2, 1)}
        formulario = """
            <form action="/resultado">
                <select id="tipo"><option value="1">CC</option></select>
                <input id="nuip"><input id="fechaExpNuip">
                <input id="nombreEmpresa"><input id="nitEmpresa">
                <input type="checkbox" id="cbCondiciones">
                <label for="cbCondiciones">Acepto</label>
                <iframe title="reCAPTCHA" srcdoc="<input type='checkbox' id='recaptcha-anchor'>"></iframe>
                <iframe src="https://bot.test/recaptcha/api2/bframe" srcdoc="<input type='hidden' id='recaptcha-token'>"
                    style="visibility: hidden; position: absolute; top: -10000px"></iframe>
                <button id="btnConsultar">Consultar</button>
            </form>
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                contexto = await browser.new_context()
                page = await contexto.new_page()
                cargas = []
                async def responder(route):
                    cargas.append(route.request.url)
                    html = "<section id='res'>Resultado</section>" if "/resultado" in route.request.url else formulario
                    await route.fulfill(body=html, content_type="text/html")

                await contexto.route("https://bot.test/**", responder)
                datos = []
                async def captcha(pagina, sitio, recuadro):
                    datos.append({
                        campo: await pagina.locator(f"#{campo}").input_value()
                        for campo in ("tipo", "nuip", "fechaExpNuip", "nombreEmpresa", "nitEmpresa")
                    })
                    self.assertTrue(await pagina.locator("#cbCondiciones").is_checked())
                    if len(datos) == 1:
                        raise documentos.FueraDeServicio("la casilla no terminó de cargar")
                    return True

                listo = asyncio.Event()
                orden = Mock()
                with patch.object(documentos, "URL_INHABILIDADES", "https://bot.test/formulario"), \
                        patch.object(documentos, "TIPOS_INHABILIDADES", {"CC": "1"}), \
                        patch.object(documentos, "al_frente", new_callable=AsyncMock), \
                        patch.object(documentos, "esperar_recaptcha", side_effect=captcha), \
                        patch.object(documentos, "log"), \
                        patch.object(documentos, "esperar_estable", new_callable=AsyncMock) as estable, \
                        patch.object(documentos, "imprimir_pdf", new_callable=AsyncMock,
                                     return_value=Path("resultado.pdf")) as imprimir:
                    orden.attach_mock(estable, "estable")
                    orden.attach_mock(imprimir, "imprimir")
                    resultado = await documentos.consultar_inhabilidades(page, persona, documentos.Turnos(), listo)
                self.assertEqual(resultado, Path("resultado.pdf"))
                # El resultado se imprime cuando la página ya cargó y se quedó quieta.
                self.assertEqual([llamada[0] for llamada in orden.mock_calls], ["estable", "imprimir"])
                self.assertEqual(len(datos), 2)
                self.assertEqual(datos[0], datos[1])
                self.assertEqual(datos[1], {
                    "tipo": "1", "nuip": "123456", "fechaExpNuip": "01/02/2020",
                    "nombreEmpresa": documentos.EMPRESA_INHABILIDADES, "nitEmpresa": documentos.NIT_INHABILIDADES,
                })
                self.assertEqual(cargas.count("https://bot.test/formulario"), 2)
                self.assertTrue(listo.is_set())
            finally:
                await browser.close()


class RnmcTests(unittest.IsolatedAsyncioTestCase):
    async def test_revisa_la_fecha_escrita_y_repite_si_el_sitio_la_rechaza(self):
        persona = {"tipo": "CC", "numero": "123456", "fecha_expedicion": date(2014, 3, 5)}
        formulario = """
            <form action="/consulta">
                <select id="ctl00_ContentPlaceHolder3_ddlTipoDoc" name="tipo"><option value="55">CC</option></select>
                <input id="txtFechaexp" name="fecha">
                <input id="ctl00_ContentPlaceHolder3_txtExpediente" name="numero">
                <button id="ctl00_ContentPlaceHolder3_btnConsultar2">Buscar</button>
            </form>
            <script>
                // Como el selector de fecha del sitio: la primera vez que se sale de la casilla pone la fecha de hoy.
                const f = document.querySelector('#txtFechaexp');
                f.addEventListener('blur', () => {
                    if (!sessionStorage.cambiada) { sessionStorage.cambiada = 1; f.value = '21/10/2026'; }
                });
            </script>
        """
        rechazo = """<div class="modal in"><div class="modal-body">La fecha de expedición de la Cedula de Ciudadania
            no es correcta, por favor verifique.</div></div><button id="ctl00_ContentPlaceHolder3_btn_salir_modal">Salir
            </button>"""
        fechas = []

        async def responder(route):
            if "/consulta" in route.request.url:
                fechas.append(parse_qs(urlsplit(route.request.url).query)["fecha"][0])
                html = rechazo if len(fechas) == 1 else "<h1>Resultado RNMC</h1>"
            else:
                html = formulario
            await route.fulfill(body=html, content_type="text/html")

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                contexto = await browser.new_context()
                await contexto.route("https://bot.test/**", responder)
                page = await contexto.new_page()
                with patch.object(documentos, "URL_RNMC", "https://bot.test/formulario"), \
                        patch.object(documentos, "log") as log, \
                        patch.object(documentos, "imprimir_pdf", new_callable=AsyncMock,
                                     return_value=Path("rnmc.pdf")) as imprimir:
                    self.assertEqual(await documentos.consultar_rnmc(page, persona), Path("rnmc.pdf"))
                self.assertEqual(fechas, ["05/03/2014", "05/03/2014"])
                self.assertIn("Resultado RNMC", await imprimir.call_args.args[0].inner_text("body"))
                avisos = " ".join(llamada.args[0] for llamada in log.call_args_list)
                self.assertIn("se escribe otra vez", avisos)
                self.assertIn("se consulta otra vez", avisos)
            finally:
                await browser.close()


class RegistraduriaBloqueoTests(unittest.IsolatedAsyncioTestCase):
    async def test_tras_el_bloqueo_no_se_vuelve_a_cargar_mientras_siga_el_aviso(self):
        persona = {"tipo": "CC", "numero": "123456", "fecha_expedicion": date(2020, 2, 1)}
        aviso = "<h1>Acceso bloqueado</h1><p>Cloudflare detectó tráfico sospechoso desde tu dirección IP.</p>"
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page()
                consulta = AsyncMock(side_effect=documentos.SitioBloqueado)
                with patch.object(documentos, "_consultar_registraduria", consulta), \
                        patch.object(documentos, "_bloqueada_hasta", 0.0), patch.object(documentos, "log"):
                    await page.set_content(aviso)
                    with self.assertRaises(documentos.SitioBloqueado):
                        await documentos.consultar_registraduria(page, persona, None, None, None)
                    # La consulta siguiente, con el aviso aún en su ventana, no visita el sitio.
                    with self.assertRaises(documentos.SitioBloqueado):
                        await documentos.consultar_registraduria(page, persona, None, None, None)
                    self.assertEqual(consulta.await_count, 1)
                    # Si la ventana ya no muestra el aviso, o pasó la pausa, se consulta normal.
                    consulta.side_effect = None
                    consulta.return_value = Path("registraduria.pdf")
                    await page.set_content("<form></form>")
                    self.assertEqual(await documentos.consultar_registraduria(page, persona, None, None, None),
                                     Path("registraduria.pdf"))
                    await page.set_content(aviso)
                    documentos._bloqueada_hasta = 0.0
                    await documentos.consultar_registraduria(page, persona, None, None, None)
                    self.assertEqual(consulta.await_count, 3)
            finally:
                await browser.close()


if __name__ == "__main__":
    unittest.main()
