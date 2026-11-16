"""A CLI `python3 -m ombro` contra um ombrod --demo de verdade (subprocesso, pasta temporária, sem tela USB)."""
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ombro import cli, ipc

RAIZ = Path(__file__).resolve().parent.parent


def rodar(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        codigo = cli.main(list(args))
    return codigo, out.getvalue(), err.getvalue()


class SemDaemon(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="omcli", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)
        p = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": self.pasta})
        p.start()
        self.addCleanup(p.stop)

    def test_mensagem_clara_e_codigo_3(self):
        for cmd in ("status", "providers", "sessions", "display", "health", "config"):
            codigo, out, err = rodar(cmd)
            self.assertEqual(codigo, cli.SEM_DAEMON, cmd)
            self.assertIn("O ombrod não está rodando.", err)
        codigo, out, _ = rodar("status", "--json")
        self.assertEqual(json.loads(out)["exit_code"], cli.SEM_DAEMON)
        self.assertFalse((Path(self.pasta) / "run").exists())  # consultar não cria nem inicia nada

    def test_argumentos_invalidos(self):
        with redirect_stderr(io.StringIO()):
            for args in (["display", "brightness"], ["display", "brightness", "x"], ["config", "set", "brilho"], ["nada"]):
                with self.assertRaises(SystemExit, msg=args):
                    cli.main(args)

    def test_roda_de_qualquer_pasta(self):
        r = subprocess.run([sys.executable, str(RAIZ / "ombro"), "--version"], cwd=self.pasta, capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("protocolo IPC 1", r.stdout)

    def test_formatacao(self):
        from datetime import datetime, timedelta

        agora = datetime(2026, 11, 14, 12, 0, 0)
        for delta, esperado in ((2, "2s"), (90, "1m"), (7200, "2h"), (200000, "2d")):
            self.assertEqual(cli.ha_quanto((agora - timedelta(seconds=delta)).isoformat(), agora), esperado)
        self.assertEqual(cli.ha_quanto(None), "-")
        self.assertEqual(cli.duracao(3725), "1h 2m 5s")


class ComDaemon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pasta = tempfile.mkdtemp(prefix="omcli", dir="/tmp" if os.path.isdir("/tmp") else None)
        with closing(socket.socket()) as s:
            s.bind(("127.0.0.1", 0))
            porta = s.getsockname()[1]
        env = dict(os.environ, OMBRO_SCREEN_DIR=cls.pasta, PYTHONDONTWRITEBYTECODE="1")
        cls.proc = subprocess.Popen([sys.executable, str(RAIZ / "ombrod.py"), "--demo", "--porta-sim", str(porta)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sock = Path(cls.pasta) / "run" / "ombro.sock"
        for _ in range(150):
            try:
                if ipc.chamar("providers.list", caminho=sock)["providers"] and ipc.chamar("display.preview", caminho=sock)["available"]:
                    break
            except ipc.DaemonIndisponivel:
                pass
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.send_signal(signal.SIGTERM)
        cls.proc.wait(10)
        shutil.rmtree(cls.pasta, ignore_errors=True)

    def setUp(self):
        p = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": self.pasta})
        p.start()
        self.addCleanup(p.stop)

    def test_status(self):
        codigo, out, _ = rodar("status")
        self.assertEqual(codigo, 0)
        for rotulo in ("Ombro Hub", "Daemon:", "Display:", "Layout:", "Providers:", "Sessões:", "Aguardando:",
                       "Armazenamento:", "IPC:"):
            self.assertIn(rotulo, out)
        self.assertIn("v1", out)
        codigo, out, _ = rodar("status", "--json")
        st = json.loads(out)
        self.assertEqual(st["providers"]["configured"], 3)
        self.assertGreater(st["sessions"], 0)

    def test_providers_e_sessions(self):
        codigo, out, _ = rodar("providers")
        self.assertEqual(codigo, 0)
        self.assertIn("PROVIDER", out)
        for nome in ("Claude", "Codex", "Cursor"):
            self.assertIn(nome, out)
        codigo, out, _ = rodar("sessions")
        self.assertIn("PROJETO", out)
        self.assertNotIn(str(Path.home()), out)  # nada de caminho pessoal completo
        s = json.loads(rodar("sessions", "--json", "--provider", "codex")[1])["sessions"]
        self.assertTrue(s and all(x["provider"] == "codex" for x in s))
        self.assertTrue(all(len(x["session_id"]) <= 8 for x in s))  # id curto, a não ser com --full

    def test_display(self):
        codigo, out, _ = rodar("display")
        self.assertEqual(codigo, 0)
        self.assertIn("simulator", out)
        destino = Path(self.pasta) / "q.png"
        codigo, out, _ = rodar("display", "preview", str(destino))
        self.assertEqual(codigo, 0, out)
        self.assertEqual(destino.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        codigo, out, _ = rodar("display", "brightness", "35")
        self.assertEqual(codigo, 0)
        codigo, _, err = rodar("display", "brightness", "300")
        self.assertEqual(codigo, 1)
        self.assertIn("VALIDATION_ERROR", err)
        codigo, out, _ = rodar("display", "layout", "classico", "--json")
        r = json.loads(out)  # o clássico também entra na hora (a orientação muda com o Ombro rodando)
        self.assertEqual((r["applied"], r["restart_required"]), (["layout"], []))
        codigo, out, _ = rodar("display", "status", "--json")
        self.assertEqual((json.loads(out)["layout"], json.loads(out)["orientation"]), ("classico", "portrait"))

    def test_config_e_health(self):
        codigo, out, _ = rodar("config", "--json")
        self.assertIn("brilho", json.loads(out)["editable"])
        codigo, out, _ = rodar("config", "set", "rotacao", "true", "--no-persist")
        self.assertEqual(codigo, 0)
        self.assertIn("aplicada agora", out)
        codigo, _, err = rodar("config", "set", "lib", "/tmp")
        self.assertEqual(codigo, 1)
        self.assertIn("não dá para mudar", err)
        codigo, _, err = rodar("config", "set", "providers.cursor", "false", "--no-persist")
        self.assertEqual(codigo, 1)
        self.assertIn("reiniciar", err)
        codigo, out, _ = rodar("config", "set", "providers.cursor", "false")
        self.assertEqual(codigo, 0)
        self.assertIn("vale depois de reiniciar", out)
        codigo, out, _ = rodar("config", "schema")
        self.assertEqual(codigo, 0)
        self.assertIn("providers.codex", out)
        self.assertIn("ao reiniciar", out)
        codigo, out, _ = rodar("health", "--json")
        self.assertIn("display", json.loads(out)["components"])


if __name__ == "__main__":
    unittest.main()
