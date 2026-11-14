"""statusline_tap.py: grava só os percentuais do plano e nunca quebra o statusline."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import statusline_tap


class Capturar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name) / "ombro"
        p = mock.patch.object(statusline_tap, "PASTA", self.pasta)
        p.start()
        self.addCleanup(p.stop)

    def test_grava_so_os_percentuais(self):
        entrada = {"model": {"id": "x"}, "cwd": "/segredo", "rate_limits": {
            "five_hour": {"used_percentage": 42.4, "resets_at": 1_900_000_000},
            "seven_day": {"used_percentage": 130},
        }}
        rec = statusline_tap.capturar(json.dumps(entrada))
        salvo = json.loads((self.pasta / "uso.json").read_text())
        self.assertEqual(salvo, rec)
        self.assertEqual(salvo["five_hour"], {"utilization": 42.4, "resets_at": 1_900_000_000})
        self.assertEqual(salvo["seven_day"], {"utilization": 100.0})
        self.assertNotIn("cwd", json.dumps(salvo))
        self.assertEqual((self.pasta / "uso.json").stat().st_mode & 0o777, 0o600)

    def test_sem_rate_limits_nao_grava(self):
        self.assertIsNone(statusline_tap.capturar(json.dumps({"model": {}})))
        self.assertIsNone(statusline_tap.capturar(json.dumps({"rate_limits": {"five_hour": {"used_percentage": "?"}}})))
        self.assertFalse((self.pasta / "uso.json").exists())

    def test_json_quebrado_e_ignorado(self):
        self.assertIsNone(statusline_tap.capturar('{"rate_limits": '))


    def test_percentual_negativo_vira_zero_e_reset_e_mantido(self):
        rec = statusline_tap.capturar(json.dumps({"rate_limits": {"five_hour": {"used_percentage": -3, "resets_at": 123}}}))
        self.assertEqual(rec["five_hour"], {"utilization": 0.0, "resets_at": 123})

    def test_sem_statusline_original_imprime_um_resumo(self):
        entrada = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 42}, "seven_day": {"used_percentage": 61}}})
        stdin = mock.Mock(isatty=lambda: False, buffer=io.BytesIO(entrada.encode()))
        saida = io.StringIO()
        with mock.patch.object(statusline_tap.sys, "stdin", stdin), mock.patch.object(statusline_tap.sys, "argv", ["tap"]), \
                mock.patch.object(statusline_tap.sys, "stdout", saida):
            statusline_tap.main()
        self.assertEqual(saida.getvalue(), "5h 42% · semana 61%\n")

    def test_repassa_o_stdin_ao_statusline_original(self):
        entrada = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 5}}})
        stdin = mock.Mock(isatty=lambda: False, buffer=io.BytesIO(entrada.encode()))
        destino = Path(self.tmp.name) / "recebido.json"
        with mock.patch.object(statusline_tap.sys, "stdin", stdin), \
                mock.patch.object(statusline_tap.sys, "argv", ["tap", "--", f"cat > '{destino}'"]):
            with self.assertRaises(SystemExit) as e:
                statusline_tap.main()
        self.assertEqual(e.exception.code, 0)
        self.assertEqual(destino.read_text(), entrada)
        self.assertTrue((self.pasta / "uso.json").exists())

if __name__ == "__main__":
    unittest.main()
