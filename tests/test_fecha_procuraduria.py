import sys
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    from documentos import FECHA_PROCURADURIA_JS
finally:
    sys.path.pop(0)


class FechaProcuraduriaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_cambia_solo_fecha_consulta_en_el_marco(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content('<p>Fecha de consulta: 20/10/2026</p><iframe></iframe>')
        marco = page.frames[1]
        marco.set_content("""
            <p>Fecha de consulta: <span>20/10/2026</span> 11:30:00</p>
            <p>Fecha de sanción: 01/02/2020</p>
            <input value="20/10/2026">
        """)
        self.assertEqual(marco.evaluate(FECHA_PROCURADURIA_JS, "15/10/2026"), 1)
        self.assertIn("15/10/2026 11:30:00", marco.locator("p").first.inner_text())
        self.assertIn("01/02/2020", marco.locator("p").nth(1).inner_text())
        self.assertEqual(marco.locator("input").input_value(), "20/10/2026")
        self.assertIn("20/10/2026", page.locator("p").inner_text())

    def test_fecha_fragmentada_y_etiqueta_sin_de(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content('<p>Fecha Consulta: <b>2/</b><span>10/2026</span> 12:00</p>')
        self.assertEqual(page.evaluate(FECHA_PROCURADURIA_JS, "15/10/2026"), 1)
        self.assertEqual(page.locator("p").inner_text(), "Fecha Consulta: 15/10/2026 12:00")
        self.assertEqual(page.locator("b").count(), 1)

    def test_sin_etiqueta_no_modifica_otras_fechas(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content("<p>Fecha de expedición: 20/10/2026</p>")
        self.assertEqual(page.evaluate(FECHA_PROCURADURIA_JS, "15/10/2026"), 0)
        self.assertIn("20/10/2026", page.locator("p").inner_text())

    def test_formato_real_conserva_hora_y_recalcula_dia_semana(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content("<iframe></iframe>")
        marco = page.frames[1]
        marco.set_content(
            "<p>Fecha de consulta: martes, octubre 20, 2026 - Hora de consulta: 11:38:23</p>"
            "<p>Fecha de expedición: martes, octubre 20, 2026</p>"
        )
        self.assertEqual(marco.evaluate(FECHA_PROCURADURIA_JS, "15/10/2026"), 1)
        self.assertEqual(
            marco.locator("p").first.inner_text(),
            "Fecha de consulta: jueves, octubre 15, 2026 - Hora de consulta: 11:38:23",
        )
        self.assertEqual(
            marco.locator("p").nth(1).inner_text(),
            "Fecha de expedición: martes, octubre 20, 2026",
        )

    def test_formato_largo_fragmentado_con_tildes_y_cambio_de_anio(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(
            "<p>Fecha de consulta: <span>miércoles, </span><b>octubre 21, </b>"
            "<span>2026</span> - Hora de consulta: 11:38:23</p>"
        )
        self.assertEqual(page.evaluate(FECHA_PROCURADURIA_JS, "01/01/2027"), 1)
        self.assertEqual(
            page.locator("p").inner_text(),
            "Fecha de consulta: viernes, enero 1, 2027 - Hora de consulta: 11:38:23",
        )


if __name__ == "__main__":
    unittest.main()
