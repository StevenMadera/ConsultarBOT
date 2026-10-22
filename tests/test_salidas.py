import os
import sys
import tempfile
import time
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

    def test_borra_una_vez_al_dia_las_capturas_de_dias_anteriores(self):
        capturas = Path(self.temporal.name) / "capturas"
        capturas.mkdir()
        ayer = time.time() - 26 * 3600
        viejas = [capturas / nombre for nombre in ("runt_1_ayer.png", "rnmc_1_ayer.pdf", "error_runt.png")]
        otras = [capturas / "notas.txt", capturas / "runt_1_hoy.png"]
        for archivo in viejas + otras:
            archivo.write_bytes(b"x")
        for archivo in viejas + otras[:1]:
            os.utime(archivo, (ayer, ayer))
        with patch.object(main, "CAPTURAS", capturas), patch.object(main, "_capturas_revisadas", None), \
                patch.object(main, "log") as log:
            main.borrar_capturas_viejas()
            self.assertEqual(sorted(p.name for p in capturas.iterdir()), ["notas.txt", "runt_1_hoy.png"])
            self.assertIn("3 imágenes y PDF", log.call_args.args[0])
            # Ya revisada hoy: lo que quede de días anteriores espera a mañana.
            viejas[0].write_bytes(b"x")
            os.utime(viejas[0], (ayer, ayer))
            main.borrar_capturas_viejas()
            self.assertTrue(viejas[0].exists())


class FechaExpedicionTests(unittest.TestCase):
    FILA = ["Persona Prueba", "1000858557", "EMPRESA", "Bogotá", "21/10/2026", "STEVEN", "25/06/2020", "H"]

    def persona(self, expedicion, documento="1000858557"):
        return main.validar([*self.FILA[:1], documento, *self.FILA[2:6], expedicion, *self.FILA[7:]])

    def test_la_ciudad_va_despues_de_la_empresa(self):
        persona = self.persona("25/06/2020")
        self.assertEqual((persona["empresa"], persona["ciudad"], persona["fecha_consulta"]),
                         ("EMPRESA", "Bogotá", date(2026, 10, 21)))
        with self.assertRaisesRegex(ValueError, "Falta el dato 'ciudad'"):
            main.validar([*self.FILA[:3], "", *self.FILA[4:]])

    def test_reconoce_las_primeras_columnas_de_una_fila(self):
        fila = main.partir_fila("Carlos David Gil Casas\t1000143171\tFALABELLA DE COLOMBIA S.A.\tBogota\t2026-10-20\r\n")
        self.assertEqual(fila, ["Carlos David Gil Casas", "1000143171", "FALABELLA DE COLOMBIA S.A.", "Bogota",
                                "2026-10-20"])
        self.assertTrue(main.parece_fila(fila))
        self.assertTrue(main.parece_fila(self.FILA))
        self.assertEqual(main.leer_fecha(fila[4], "fecha de consulta"), date(2026, 10, 20))
        for otra in (["STEVEN", "25/06/2020", "H"], ["1000143171", "Bogota"], ["Carlos"], self.FILA + ["otra"]):
            with self.subTest(otra=otra):
                self.assertFalse(main.parece_fila(otra))

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
