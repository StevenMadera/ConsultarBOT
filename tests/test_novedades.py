import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import interfaz
finally:
    sys.path.pop(0)

TEXTO = """# comentario que no se muestra
== 22 de octubre de 2026 ==
- Cambio nuevo.
== 21 de octubre de 2026 ==
- Cambio uno.
- Cambio dos.
== 20 de octubre de 2026 ==
- Cambio viejo.
"""


class NovedadesTests(unittest.TestCase):
    def setUp(self):
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        carpeta = Path(temporal.name)
        self.novedades, self.vistas = carpeta / "novedades.txt", carpeta / "vistas.txt"
        self.novedades.write_text(TEXTO, encoding="utf-8")
        for nombre, valor in (("NOVEDADES", self.novedades), ("NOVEDADES_VISTAS", self.vistas)):
            parche = patch.object(interfaz, nombre, valor)
            parche.start()
            self.addCleanup(parche.stop)

    def test_primera_vez_solo_la_ultima_version(self):
        self.assertEqual(interfaz.novedades_pendientes(), [("22 de octubre de 2026", ["Cambio nuevo."])])

    def test_muestra_las_versiones_que_no_se_vieron(self):
        self.vistas.write_text("20 de octubre de 2026", encoding="utf-8")
        self.assertEqual([titulo for titulo, _ in interfaz.novedades_pendientes()],
                         ["22 de octubre de 2026", "21 de octubre de 2026"])
        self.assertEqual(interfaz.novedades_pendientes()[1][1], ["Cambio uno.", "Cambio dos."])

    def test_ya_vista_no_se_muestra(self):
        self.vistas.write_text("22 de octubre de 2026\n", encoding="utf-8")
        self.assertEqual(interfaz.novedades_pendientes(), [])

    def test_sin_archivo_no_se_muestra_nada(self):
        self.novedades.unlink()
        self.assertEqual(interfaz.novedades_pendientes(), [])

    def test_el_archivo_real_se_lee(self):
        with patch.object(interfaz, "NOVEDADES", Path(interfaz.__file__).with_name("novedades.txt")):
            titulo, cambios = interfaz.novedades_pendientes()[0]
        self.assertTrue(titulo)
        self.assertTrue(cambios)


if __name__ == "__main__":
    unittest.main()
