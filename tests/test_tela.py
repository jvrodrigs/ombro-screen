"""tela.py de ponta a ponta, como o usuário roda: sem tela USB, sem Claude Code e sem macmon."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

if __name__ == "__main__":
    unittest.main()
