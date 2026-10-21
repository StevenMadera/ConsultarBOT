import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
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

    def test_si_nunca_vio_la_ventana_ve_todas_las_versiones(self):
        self.assertEqual([titulo for titulo, _ in interfaz.novedades_pendientes()],
                         ["22 de octubre de 2026", "21 de octubre de 2026", "20 de octubre de 2026"])

    def test_titulo_visto_que_ya_no_existe_solo_la_ultima_version(self):
        self.vistas.write_text("título cambiado", encoding="utf-8")
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

    def _abrir(self, instalacion_nueva: bool) -> list[tk.Toplevel]:
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        objeto = interfaz.Interfaz.__new__(interfaz.Interfaz)
        objeto.root, objeto.instalacion_nueva = root, instalacion_nueva
        objeto._mostrar_novedades()
        root.update_idletasks()
        return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]

    def test_muchas_versiones_se_ven_todas_con_barra_y_caben_en_la_pantalla(self):
        self.novedades.write_text("".join(f"== Versión {n} ==\n- Cambio uno.\n- Cambio dos.\n" for n in range(40, 0, -1)),
                                  encoding="utf-8")
        ventana, = self._abrir(instalacion_nueva=False)
        textos, barras, pila = [], [], [ventana]
        while pila:
            hijo = pila.pop()
            pila.extend(hijo.winfo_children())
            if isinstance(hijo, ttk.Label):
                textos.append(hijo.cget("text"))
            barras += [hijo] if isinstance(hijo, ttk.Scrollbar) else []
        self.assertEqual(sum(texto.startswith("Versión ") for texto in textos), 40)
        self.assertEqual(len(barras), 1)
        self.assertLessEqual(ventana.winfo_reqheight(), ventana.winfo_screenheight())
        self.assertEqual(self.vistas.read_text(encoding="utf-8"), "Versión 40")

    def test_pocas_versiones_sin_barra(self):
        ventana, = self._abrir(instalacion_nueva=False)
        pila, barras = [ventana], 0
        while pila:
            hijo = pila.pop()
            pila.extend(hijo.winfo_children())
            barras += isinstance(hijo, ttk.Scrollbar)
        self.assertEqual(barras, 0)

    def test_recien_instalado_no_muestra_la_ventana_y_las_da_por_vistas(self):
        self.assertEqual(self._abrir(instalacion_nueva=True), [])
        self.assertEqual(self.vistas.read_text(encoding="utf-8"), "22 de octubre de 2026")
        self.assertEqual(interfaz.novedades_pendientes(), [])


if __name__ == "__main__":
    unittest.main()
