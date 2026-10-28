import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PaginasBOT"))
try:
    import equipo
finally:
    sys.path.pop(0)


class EquipoTests(unittest.IsolatedAsyncioTestCase):
    def test_ocupacion_es_una_fraccion(self):
        equipo.ocupacion()
        time.sleep(equipo.MUESTRA + 0.05)
        self.assertTrue(0 <= equipo.ocupacion() <= 1)

    async def test_con_respiro_no_espera(self):
        with patch.object(equipo, "saturado", return_value=False), patch.object(equipo, "_medido_en", time.monotonic()):
            self.assertLess(await equipo.esperar_respiro(5), 0.5)

    async def test_saturado_espera_hasta_el_tope_y_sigue(self):
        with patch.object(equipo, "saturado", return_value=True), patch.object(equipo, "MUESTRA", 0.02), \
                patch.object(equipo, "_medido_en", time.monotonic()):
            esperado = await equipo.esperar_respiro(0.1)
        self.assertGreaterEqual(esperado, 0.1)
        self.assertLess(esperado, 1)

    async def test_espera_hasta_que_se_libera(self):
        estados = iter([True, True, False])
        with patch.object(equipo, "saturado", side_effect=lambda: next(estados)), \
                patch.object(equipo, "MUESTRA", 0.02), patch.object(equipo, "_medido_en", time.monotonic()):
            esperado = await equipo.esperar_respiro(5)
        self.assertLess(esperado, 1)

    async def test_deja_de_esperar_si_ya_se_necesita(self):
        necesitado = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, necesitado.set)
        with patch.object(equipo, "saturado", return_value=True), patch.object(equipo, "MUESTRA", 0.02), \
                patch.object(equipo, "_medido_en", time.monotonic()):
            esperado = await equipo.esperar_respiro(5, seguir=lambda: not necesitado.is_set())
        self.assertLess(esperado, 1)


if __name__ == "__main__":
    unittest.main()
