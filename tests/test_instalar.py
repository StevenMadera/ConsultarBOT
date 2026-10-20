import hashlib
import io
import os
import subprocess
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import instalar
from PaginasBOT.ocr import buscar_tesseract


class InstalacionTesseractTests(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporal.cleanup)
        self.base = Path(self.temporal.name) / "usuario con espacios"
        self.base.mkdir()
        self.ejecutable = self.base / "Programs" / "Tesseract-OCR" / "tesseract.exe"
        self.datos = b"instalador de prueba"

    def instalar(self):
        with redirect_stdout(io.StringIO()) as salida:
            resultado = instalar.instalar_tesseract()
        return resultado, salida.getvalue()

    def test_reutiliza_instalacion_existente_sin_descargar(self):
        with patch("instalar.buscar_tesseract", return_value=self.ejecutable), \
                patch("instalar.verificar_tesseract") as verificar, \
                patch("instalar.urllib.request.urlopen") as descargar:
            resultado, _ = self.instalar()
        self.assertTrue(resultado)
        verificar.assert_called_once_with(self.ejecutable)
        descargar.assert_not_called()

    def test_descarga_verifica_e_instala_para_usuario_sin_winget(self):
        def ejecutar(argumentos, **opciones):
            self.assertIsInstance(argumentos, str)
            self.assertTrue(argumentos.endswith(f" /S /CurrentUser /D={self.ejecutable.parent}"))
            self.ejecutable.parent.mkdir(parents=True)
            self.ejecutable.touch()
            return subprocess.CompletedProcess(argumentos, 0, "", "")

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base)}), \
                patch("instalar.buscar_tesseract", return_value=None), \
                patch("instalar.urllib.request.urlopen", return_value=io.BytesIO(self.datos)), \
                patch("instalar.TESSERACT_SHA256", hashlib.sha256(self.datos).hexdigest()), \
                patch("instalar.subprocess.run", side_effect=ejecutar), \
                patch("instalar.verificar_tesseract") as verificar:
            resultado, _ = self.instalar()
        self.assertTrue(resultado)
        verificar.assert_called_once_with(self.ejecutable)

    def test_no_ejecuta_descarga_con_hash_incorrecto(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base)}), \
                patch("instalar.buscar_tesseract", return_value=None), \
                patch("instalar.urllib.request.urlopen", return_value=io.BytesIO(self.datos)), \
                patch("instalar.subprocess.run") as ejecutar:
            resultado, salida = self.instalar()
        self.assertFalse(resultado)
        self.assertIn("SHA-256", salida)
        ejecutar.assert_not_called()

    def test_error_de_red_se_muestra(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base)}), \
                patch("instalar.buscar_tesseract", return_value=None), \
                patch("instalar.urllib.request.urlopen", side_effect=urllib.error.URLError("sin red")):
            resultado, salida = self.instalar()
        self.assertFalse(resultado)
        self.assertIn("sin red", salida)

    def test_error_del_instalador_se_muestra(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base)}), \
                patch("instalar.buscar_tesseract", return_value=None), \
                patch("instalar.urllib.request.urlopen", return_value=io.BytesIO(self.datos)), \
                patch("instalar.TESSERACT_SHA256", hashlib.sha256(self.datos).hexdigest()), \
                patch("instalar.subprocess.run", return_value=subprocess.CompletedProcess([], 5, "", "denegado")):
            resultado, salida = self.instalar()
        self.assertFalse(resultado)
        self.assertIn("código 5", salida)
        self.assertIn("denegado", salida)

    def test_rechaza_instalacion_sin_idioma_eng(self):
        with patch("instalar.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "spa\n", "")):
            with self.assertRaisesRegex(RuntimeError, "idioma eng"):
                instalar.verificar_tesseract(self.ejecutable)

    def test_instalador_que_no_crea_ejecutable_no_da_exito(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base)}), \
                patch("instalar.buscar_tesseract", return_value=None), \
                patch("instalar.urllib.request.urlopen", return_value=io.BytesIO(self.datos)), \
                patch("instalar.TESSERACT_SHA256", hashlib.sha256(self.datos).hexdigest()), \
                patch("instalar.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            resultado, salida = self.instalar()
        self.assertFalse(resultado)
        self.assertIn("no creó", salida)

    def test_tiempo_agotado_se_muestra(self):
        with patch("instalar.buscar_tesseract", return_value=self.ejecutable), \
                patch("instalar.subprocess.run", side_effect=subprocess.TimeoutExpired("tesseract", 30)):
            resultado, salida = self.instalar()
        self.assertFalse(resultado)
        self.assertIn("30", salida)

    def test_verifica_idioma_eng(self):
        with patch("instalar.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "List of available languages (1):\neng\n", "")):
            instalar.verificar_tesseract(self.ejecutable)

    def test_detecta_ruta_del_usuario_sin_path(self):
        self.ejecutable.parent.mkdir(parents=True)
        self.ejecutable.touch()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.base)}, clear=True), \
                patch("PaginasBOT.ocr.shutil.which", return_value=None):
            self.assertEqual(buscar_tesseract(), self.ejecutable)

    def test_detecta_ruta_de_program_files(self):
        ejecutable = self.base / "Tesseract-OCR" / "tesseract.exe"
        ejecutable.parent.mkdir()
        ejecutable.touch()
        with patch.dict(os.environ, {"ProgramFiles": str(self.base)}, clear=True), \
                patch("PaginasBOT.ocr.shutil.which", return_value=None):
            self.assertEqual(buscar_tesseract(), ejecutable)

    def test_detecta_path(self):
        with patch("PaginasBOT.ocr.shutil.which", return_value=str(self.ejecutable)):
            self.assertEqual(buscar_tesseract(), self.ejecutable)

    def test_sin_tesseract_devuelve_none(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("PaginasBOT.ocr.shutil.which", return_value=None):
            self.assertIsNone(buscar_tesseract())


if __name__ == "__main__":
    unittest.main()
