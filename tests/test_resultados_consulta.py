import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import consulta_consilium as consilium
    import documentos
finally:
    sys.path.pop(0)


class ConsiliumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.preguntar = Mock()

    def test_sin_verificacion_ni_cookies_no_pregunta(self):
        self.page.set_content("<title>Search - Consilium</title><main>Resultados</main>")
        consilium.preparar_consilium(self.page, self.preguntar)
        self.preguntar.assert_not_called()

    def test_cookies_necesarias_se_aceptan_sin_preguntar(self):
        self.page.set_content("""<title>Search - Consilium</title><main>Resultados</main>
            <aside id="cookies"><button onclick="this.parentElement.remove()">
            Accept only necessary cookies</button><button>Aceptar todas las cookies</button></aside>""")
        consilium.preparar_consilium(self.page, self.preguntar)
        self.assertEqual(self.page.locator("#cookies").count(), 0)
        self.preguntar.assert_not_called()

    def test_verificacion_se_resuelve_antes_de_continuar(self):
        self.page.set_content("<title>Just a moment</title><main>Verify you are human</main>")
        self.preguntar.side_effect = lambda _: self.page.set_content(
            "<title>Search - Consilium</title><main>Resultados</main>")
        consilium.preparar_consilium(self.page, self.preguntar)
        self.preguntar.assert_called_once()
        self.assertEqual(self.page.locator("main").inner_text(), "Resultados")

    def test_verificacion_con_titulo_del_sitio_tambien_se_detecta(self):
        self.page.set_content("<title>Search - Consilium</title><main>Checking your browser</main>")
        self.preguntar.side_effect = lambda _: self.page.set_content(
            "<title>Search - Consilium</title><main>Resultados</main>")
        consilium.preparar_consilium(self.page, self.preguntar)
        self.preguntar.assert_called_once()


class RegistraduriaTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((50, 50), "Certificado de prueba")
            cls.pdf = pdf.tobytes()

        class Sitio(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", "attachment" if self.path == "/descarga" else "inline")
                self.end_headers()
                self.wfile.write(cls.pdf)

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Sitio)
        cls.hilo = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.hilo.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.hilo.join()

    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.temporal = tempfile.TemporaryDirectory()
        self.ruta = Path(self.temporal.name) / "certificado.pdf"

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        self.temporal.cleanup()

    async def test_guarda_pdf_original_inline_descarga_y_pestana(self):
        for destino, target in (("/inline", ""), ("/descarga", ""), ("/inline", "_blank")):
            with self.subTest(destino=destino, target=target):
                await self.page.set_content(
                    f'<a href="{self.url}{destino}" target="{target}">Descargar certificado</a>')
                boton = self.page.get_by_role("link", name="Descargar certificado")
                await documentos.descargar_pdf_resultado(self.page, boton, self.ruta, espera=5000)
                self.assertEqual(self.ruta.read_bytes(), self.pdf)

    async def test_sin_enlace_no_imprime_la_pagina(self):
        await self.page.set_content("<main>Formulario sin certificado</main>")
        with patch.object(documentos, "pagina_a_pdf") as imprimir:
            with self.assertRaisesRegex(ValueError, "no apareció el enlace"):
                await documentos.pdf_del_sitio_o_captura(
                    self.page, self.page.locator("button"), self.ruta, exigir_pdf=True)
            imprimir.assert_not_called()
        self.assertFalse(self.ruta.exists())

    async def test_enlace_oculto_no_tapa_la_descarga_visible(self):
        await self.page.set_content(
            f'<button hidden>Descargar</button><a href="{self.url}/descarga">Descargar</a>')
        botones = self.page.get_by_role("button", include_hidden=True).or_(self.page.get_by_role("link"))
        await documentos.pdf_del_sitio_o_captura(self.page, botones, self.ruta, exigir_pdf=True)
        self.assertEqual(self.ruta.read_bytes(), self.pdf)


if __name__ == "__main__":
    unittest.main()