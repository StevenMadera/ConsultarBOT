import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import captura_navegador as captura
finally:
    sys.path.pop(0)


class CierreNavegadorTests(unittest.TestCase):
    def test_cierra_solo_consulta_y_duplicadas_sin_depender_del_foco(self):
        ventana = SimpleNamespace(_hWnd=2, title="Fugitives - FBI")
        duplicada = SimpleNamespace(_hWnd=3, title="Fugitives - FBI")
        consilium = SimpleNamespace(_hWnd=4, title="Search - Consilium")
        user32 = Mock()
        abiertas = {1, 2, 3, 4}
        user32.IsWindow.side_effect = lambda hwnd: hwnd in abiertas
        user32.PostMessageW.side_effect = lambda hwnd, *_: abiertas.discard(hwnd)
        with patch.object(captura.ctypes.windll, "user32", user32), \
                patch.object(captura, "_clase", return_value="Chrome_WidgetWin_1"), \
                patch.object(captura, "_ventanas_nuevas", return_value=[ventana, duplicada, consilium]):
            captura._cerrar_ventanas_consultadas(ventana, "Fugitives", {1})
        self.assertEqual(abiertas, {1, 4})
        self.assertEqual(user32.PostMessageW.call_count, 2)
        user32.GetForegroundWindow.assert_not_called()

    def test_no_cierra_ventana_preexistente(self):
        ventana = SimpleNamespace(_hWnd=1, title="Fugitives")
        with patch.object(captura.ctypes.windll, "user32") as user32, \
                patch.object(captura, "_ventanas_nuevas", return_value=[]):
            captura._cerrar_ventanas_consultadas(ventana, "Fugitives", {1})
        user32.PostMessageW.assert_not_called()

    def test_registra_si_el_navegador_no_cierra(self):
        ventana = SimpleNamespace(_hWnd=2, title="Fugitives")
        with patch.object(captura.ctypes.windll, "user32") as user32, \
                patch.object(captura, "_clase", return_value="Chrome_WidgetWin_1"), \
                patch.object(captura, "_ventanas_nuevas", return_value=[ventana]), \
                patch.object(captura.time, "monotonic", side_effect=[0, 4]), \
                patch.object(captura, "log") as log:
            user32.IsWindow.return_value = True
            captura._cerrar_ventanas_consultadas(ventana, "Fugitives", {1})
        log.assert_called_once()

    def test_al_terminar_cierra_lo_que_el_bot_abrio_y_las_reabiertas_de_la_sesion_anterior(self):
        abiertas = {1, 2, 3, 4, 5, 6, 7}
        nuevas = [SimpleNamespace(_hWnd=hwnd, title=titulo) for hwnd, titulo in (
            (3, "Federal Bureau of Investigation - Google Chrome"), (4, "Reclutamiento | Ejercito Colombia"),
            (5, "Gmail - Google Chrome"), (6, "www.consilium.europa.eu - Google Chrome"))]
        user32 = Mock()
        user32.IsWindow.side_effect = lambda hwnd: hwnd in abiertas
        user32.PostMessageW.side_effect = lambda hwnd, *_: abiertas.discard(hwnd)
        clases = {7: "Notepad"}
        with patch.object(captura.ctypes.windll, "user32", user32), \
                patch.object(captura, "_clase", side_effect=lambda hwnd: clases.get(hwnd, "Chrome_WidgetWin_1")), \
                patch.object(captura, "_ventanas_nuevas", return_value=nuevas), \
                patch.object(captura, "_ABIERTAS", {2, 7}), \
                patch.object(captura, "_MARCAS", {"www.consilium.europa.eu", "Search - Consilium"}), \
                patch.object(captura, "_PREVIAS", {1}):
            captura.cerrar_ventanas_abiertas()
            self.assertEqual(captura._ABIERTAS, set())
            self.assertIsNone(captura._PREVIAS)
        # La 7 es el número de una ventana del bot que ya se cerró y ahora es de otro programa: no se toca.
        self.assertEqual(abiertas, {1, 5, 7})

    def test_sin_nada_abierto_no_toca_ninguna_ventana(self):
        with patch.object(captura.ctypes.windll, "user32") as user32, \
                patch.object(captura, "_ABIERTAS", set()), patch.object(captura, "_PREVIAS", None):
            captura.cerrar_ventanas_abiertas()
        user32.PostMessageW.assert_not_called()

    def test_cierra_si_falla_el_paso_manual(self):
        ventana = SimpleNamespace(_hWnd=2, title="Search - Consilium")
        preguntar = Mock(side_effect=RuntimeError("consulta cancelada"))
        with patch.object(captura, "_esperar_pagina", return_value=(ventana, None)), \
                patch.object(captura, "_encima"), \
                patch.object(captura, "_fijar_encima"), \
                patch.object(captura, "_PASOS_HECHOS", set()), \
                patch.object(captura, "_cerrar_ventanas_consultadas") as cerrar:
            with self.assertRaisesRegex(RuntimeError, "consulta cancelada"):
                captura._capturar("https://www.consilium.europa.eu", "Search - Consilium",
                                  "Consilium", "consilium", "123", "Acepta cookies", 2,
                                  preguntar, {1}, 0.75)
        cerrar.assert_called_once_with(ventana, "Search - Consilium", {1})

    def test_cierra_despues_de_guardar_y_tambien_si_falla_la_captura(self):
        for caida, fallo in ((None, None), ("sitio caído", None), (None, OSError("disco lleno"))):
            with self.subTest(caida=caida, fallo=fallo), ExitStack() as stack:
                carpeta = stack.enter_context(tempfile.TemporaryDirectory())
                ventana = SimpleNamespace(_hWnd=2, title="Fugitives")
                sct = MagicMock()
                sct.monitors = [{}]
                sct.grab.return_value = SimpleNamespace(rgb=b"imagen", size=(10, 10))
                gestor = stack.enter_context(patch.object(captura.mss, "mss"))
                gestor.return_value.__enter__.return_value = sct
                for nombre, valor in (
                    ("_esperar_pagina", (ventana, caida)), ("_minimizar_consola", None),
                    ("_region_en_pantalla", {"width": 10}), ("_en_blanco", False),
                    ("_colocacion", captura._Colocacion()),
                ):
                    stack.enter_context(patch.object(captura, nombre, return_value=valor))
                for nombre in ("_encima", "_fijar_encima", "_ajustar_ventana", "log"):
                    stack.enter_context(patch.object(captura, nombre))
                stack.enter_context(patch.object(captura, "CAPTURAS", Path(carpeta)))
                stack.enter_context(patch.object(captura, "ESPERA_ESTABLE_MAXIMA", 0))
                stack.enter_context(patch.object(captura.time, "sleep"))
                stack.enter_context(patch.object(captura.ctypes.windll, "user32"))
                guardar = stack.enter_context(patch.object(captura.mss.tools, "to_png", side_effect=fallo))
                cerrar = stack.enter_context(patch.object(captura, "_cerrar_ventanas_consultadas"))
                orden = Mock()
                orden.attach_mock(guardar, "guardar")
                orden.attach_mock(cerrar, "cerrar")

                def ejecutar():
                    return captura._capturar("https://www.fbi.gov", "Fugitives", "FBI", "fbi", "123",
                                              None, 0, Mock(), {1}, 1)

                if fallo:
                    with self.assertRaisesRegex(OSError, "disco lleno"):
                        ejecutar()
                elif caida:
                    with self.assertRaises(captura.FueraDeServicio):
                        ejecutar()
                else:
                    self.assertEqual(ejecutar().parent, Path(carpeta))
                self.assertEqual([llamada[0] for llamada in orden.mock_calls], ["guardar", "cerrar"])
                cerrar.assert_called_once_with(ventana, "Fugitives", {1})


if __name__ == "__main__":
    unittest.main()
