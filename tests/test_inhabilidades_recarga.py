import asyncio
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
                with patch.object(documentos, "URL_INHABILIDADES", "https://bot.test/formulario"), \
                        patch.object(documentos, "TIPOS_INHABILIDADES", {"CC": "1"}), \
                        patch.object(documentos, "al_frente", new_callable=AsyncMock), \
                        patch.object(documentos, "esperar_recaptcha", side_effect=captcha), \
                        patch.object(documentos, "log"), \
                        patch.object(documentos, "imprimir_pdf", new_callable=AsyncMock,
                                     return_value=Path("resultado.pdf")):
                    resultado = await documentos.consultar_inhabilidades(page, persona, documentos.Turnos(), listo)
                self.assertEqual(resultado, Path("resultado.pdf"))
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


if __name__ == "__main__":
    unittest.main()
