import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import main
finally:
    sys.path.pop(0)


class SalidasTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.base = Path(self.temporal.name) / "Documentos redirigidos" / "PAGINAS"
        self.parche = patch.object(main, "SALIDAS", self.base)
        self.parche.start()
        self.addCleanup(self.parche.stop)

    def test_carpetas_por_mes_y_dia_incluso_al_cambiar_de_ano(self):
        for hoy, mes in ((date(2026, 10, 20), "OCTUBRE"),
                         (date(2026, 12, 31), "DICIEMBRE"),
                         (date(2027, 1, 1), "ENERO"),
                         (date(2027, 10, 20), "OCTUBRE")):
            with self.subTest(hoy=hoy), patch.object(main, "date") as reloj:
                reloj.today.return_value = hoy
                carpeta = main.carpeta_salidas()
                self.assertEqual(carpeta, self.base / mes / hoy.strftime("%d-%m-%Y"))
                self.assertTrue(carpeta.is_dir())
                self.assertEqual(main.carpeta_salidas(), carpeta)

    def test_pdf_real_se_guarda_en_el_dia_actual_sin_sobrescribir(self):
        plantilla = Path(self.temporal.name) / "plantilla.pdf"
        with pymupdf.open() as doc:
            doc.new_page().insert_text((72, 72), "Plantilla de prueba")
            doc.save(plantilla)
        persona = {"formato": "Prueba", "numero": "123456", "nombre": "Persona Prueba",
                   "fecha_consulta": date(2020, 1, 1)}
        with patch.object(main, "date") as reloj, \
                patch.object(main, "PLANTILLAS", {"Prueba": plantilla}), \
                patch.object(main, "UBICACION_PDF", {"Prueba": {}}), \
                patch.object(main, "rellenar_portada"), patch.object(main, "mover_chulos"), \
                patch.object(main, "sitios_de", return_value=()), patch.object(main.documentos, "ORDEN", ()):
            reloj.today.return_value = date(2026, 10, 20)
            primera = main.insertar_en_pdf({}, persona)
            original = primera.read_bytes()
            segunda = main.insertar_en_pdf({}, persona)
        self.assertEqual(primera, self.base / "OCTUBRE" / "20-10-2026" / "123456 - Persona Prueba.pdf")
        self.assertEqual(segunda.name, "123456 - Persona Prueba (2).pdf")
        self.assertEqual(primera.read_bytes(), original)
        with pymupdf.open(segunda) as doc:
            self.assertEqual(len(doc), 1)
            self.assertIn("Plantilla de prueba", doc[0].get_text())

    def test_no_oculta_error_de_permisos(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("sin permiso")):
            with self.assertRaisesRegex(PermissionError, "sin permiso"):
                main.carpeta_salidas()


if __name__ == "__main__":
    unittest.main()
