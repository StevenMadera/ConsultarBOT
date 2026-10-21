import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

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
                   "fecha_consulta": date(2020, 1, 1), "fecha_expedicion": date(2015, 1, 1)}
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


class FechaExpedicionTests(unittest.TestCase):
    FILA = ["Persona Prueba", "1000858557", "EMPRESA", "21/10/2026", "STEVEN", "25/06/2020", "H"]

    def persona(self, expedicion, documento="1000858557"):
        return main.validar([*self.FILA[:1], documento, *self.FILA[2:5], expedicion, *self.FILA[6:]])

    def test_cero_es_sin_fecha_y_omite_las_consultas_que_la_piden(self):
        persona = self.persona("0")
        self.assertIsNone(persona["fecha_expedicion"])
        self.assertEqual(main.sin_fecha_expedicion(persona), {"registraduria", "rnmc", "inhabilidades"})
        self.assertFalse({"registraduria", "rnmc", "inhabilidades"} & set(main.sitios_de(persona)))
        self.assertIn("antecedentes", main.sitios_de(persona))

    def test_con_fecha_no_se_omite_nada(self):
        persona = self.persona("25/06/2020")
        self.assertEqual(persona["fecha_expedicion"], date(2020, 6, 25))
        self.assertEqual(main.sin_fecha_expedicion(persona), set())

    def test_el_rnmc_no_pide_la_fecha_a_un_nit(self):
        self.assertEqual(main.sin_fecha_expedicion(self.persona("0", "NIT 900123456")),
                         {"registraduria", "inhabilidades"})

    def test_fecha_invalida_sugiere_el_cero(self):
        with self.assertRaisesRegex(ValueError, "o 0 si no se tiene"):
            self.persona("31/02/2020")

    def test_portada_no_marca_nada_sin_fecha(self):
        persona = {**self.persona("0"), "formato": "Hombre"}
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        with patch.object(main, "mover_chulos") as mover, patch.object(main, "rellenar_portada"), \
                patch.object(main, "log") as log, patch.object(main, "carpeta_salidas", return_value=Path(temporal.name)):
            main.insertar_en_pdf({}, persona)
        columnas = {llamada.args[3]: llamada.args[2] for llamada in mover.call_args_list}
        self.assertEqual(columnas["No Aplica"], set())
        avisos = " ".join(llamada.args[0] for llamada in log.call_args_list)
        self.assertNotIn("falta el documento de rnmc", avisos)
        self.assertIn("falta el documento de bdme", avisos)


class ConsultasSinFechaTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_tocan_la_pagina(self):
        persona = {"tipo": "CC", "numero": "123", "fecha_expedicion": None}
        page = Mock()
        for consulta in (main.documentos.consultar_rnmc(page, persona),
                         main.documentos.consultar_registraduria(page, persona, None, None, None),
                         main.documentos.consultar_inhabilidades(page, persona, None, None)):
            with self.subTest(consulta=consulta.__name__), self.assertRaisesRegex(ValueError, "se omite"):
                await consulta
        page.goto.assert_not_called()


if __name__ == "__main__":
    unittest.main()
