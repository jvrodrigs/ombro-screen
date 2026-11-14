"""tela.py de ponta a ponta, como o usuário roda: sem tela USB, sem Claude Code e sem macmon."""
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent


class Tela(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pasta = Path(tmp.name)
        # pastas vazias: nada do Claude Code nem da configuração de quem roda o teste entra aqui
        self.env = dict(os.environ, OMBRO_SCREEN_DIR=str(self.pasta / "ombro"), CLAUDE_CONFIG_DIR=str(self.pasta / "claude"),
                        PYTHONDONTWRITEBYTECODE="1")

    def rodar(self, *args):
        return subprocess.run([sys.executable, str(RAIZ / "tela.py"), *args], env=self.env, cwd=self.pasta,
                              capture_output=True, text=True, timeout=60)

    def test_png_nos_tres_layouts(self):
        for args, tamanho in ((["--demo"], (480, 320)), (["--demo", "--layout", "classico"], (320, 480)),
                              (["--layout", "classico", "--paisagem", "--sem-macmon"], (480, 320))):
            arq = self.pasta / "q.png"
            r = self.rodar(*args, "--png", str(arq))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(Image.open(arq).size, tamanho, args)
            arq.unlink()

    def test_opcao_invalida_sai_com_erro_do_argparse(self):
        r = self.rodar("--layout", "outro")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--layout", r.stderr)


    def test_arquivo_de_configuracao_vale_e_a_linha_de_comando_vence(self):
        (self.pasta / "ombro").mkdir()
        (self.pasta / "ombro" / "config.json").write_text('{"layout": "classico", "paisagem": true}')
        arq = self.pasta / "q.png"
        self.assertEqual(self.rodar("--demo", "--png", str(arq)).returncode, 0)
        self.assertEqual(Image.open(arq).size, (480, 320))
        self.assertEqual(self.rodar("--demo", "--no-paisagem", "--png", str(arq)).returncode, 0)
        self.assertEqual(Image.open(arq).size, (320, 480))

    def test_configuracao_invalida_nao_inicia(self):
        (self.pasta / "ombro").mkdir()
        (self.pasta / "ombro" / "config.json").write_text('{"brilho": "alto"}')
        r = self.rodar("--demo", "--png", str(self.pasta / "q.png"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("brilho deve ser um número inteiro", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_simulador_com_dados_reais_grava_o_estado_e_o_demo_nao(self):
        claude = self.pasta / "claude" / "sessions"
        claude.mkdir(parents=True)
        (claude / "1.json").write_text('{"pid": %d, "sessionId": "s1", "status": "busy"}' % os.getpid())
        for args, grava in ((["--demo"], False), ([], True)):
            with socket.socket() as sk:  # uma porta livre para o simulador
                sk.bind(("127.0.0.1", 0))
                porta = str(sk.getsockname()[1])
            p = subprocess.Popen([sys.executable, str(RAIZ / "tela.py"), *args, "--nao-abrir", "--porta-sim", porta,
                                  "--sem-macmon"], env=self.env, cwd=self.pasta, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            time.sleep(2.5)
            p.send_signal(signal.SIGINT)
            _, err = p.communicate(timeout=20)
            self.assertEqual(p.returncode, 0, err)
            banco = self.pasta / "ombro" / "state.db"
            self.assertEqual(banco.exists(), grava, args)
        with sqlite3.connect(str(banco)) as c:
            self.assertEqual(c.execute("SELECT provider_id, session_id, activity FROM sessions").fetchall(),
                             [("claude", "s1", "working")])

    def test_banco_de_estado(self):
        import tela
        from config import OmbroConfig

        self.assertIsNone(tela.banco_de_estado(OmbroConfig(demo=True)))
        self.assertIsNone(tela.banco_de_estado(OmbroConfig(png=Path("q.png"))))
        with mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta / "x")}):
            self.assertEqual(tela.banco_de_estado(OmbroConfig()), self.pasta / "x" / "state.db")

if __name__ == "__main__":
    unittest.main()
