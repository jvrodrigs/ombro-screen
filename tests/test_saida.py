"""TelaUSB com uma biblioteca falsa: envio por faixas e reconexão quando a tela some."""
import contextlib
import io
import sys
import tempfile
import types
import unittest
from enum import IntEnum
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

import saida


class Orientation(IntEnum):
    PORTRAIT = 0
    LANDSCAPE = 2
    REVERSE_PORTRAIT = 1
    REVERSE_LANDSCAPE = 3


class LcdFalso:
    presente = True  # a porta aparece na detecção automática?
    desistir = False  # a biblioteca chama sys.exit() ao abrir a porta?
    instancias: list = []

    @staticmethod
    def auto_detect_com_port():
        return "/dev/cu.falsa" if LcdFalso.presente else None

    def __init__(self, com_port, display_width, display_height):
        if LcdFalso.desistir:
            raise SystemExit(0)
        self.lcd_serial = object()
        self.chamadas = []
        self.falhar = False
        self.reabrir_no_envio = False
        LcdFalso.instancias.append(self)

    def Reset(self):
        self.chamadas.append("Reset")

    def InitializeComm(self):
        self.chamadas.append("Init")

    def SetBrightness(self, level):
        self.chamadas.append(("brilho", level))

    def SetOrientation(self, orientation):
        self.chamadas.append(("orientacao", orientation))

    def DisplayPILImage(self, img, x, y):
        if self.falhar:
            raise OSError(6, "Device not configured")
        if self.reabrir_no_envio:  # como a biblioteca faz ao recuperar de uma SerialException
            self.lcd_serial = object()
            self.reabrir_no_envio = False
        self.chamadas.append(("img", img.size, x, y))

    def closeSerial(self):
        self.chamadas.append("close")


def quadro(marca: int = 0) -> Image.Image:
    img = Image.new("RGB", (480, 320), (18, 17, 16))
    if marca:
        ImageDraw.Draw(img).rectangle((10, 50, 10 + marca, 60), fill=(255, 0, 0))
    return img


def imagens(lcd):
    return [c for c in lcd.chamadas if c[0] == "img"]


class TelaUSBComLibFalsa(unittest.TestCase):
    def setUp(self):
        LcdFalso.presente, LcdFalso.desistir, LcdFalso.instancias = True, False, []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pasta = Path(tmp.name)
        (self.pasta / "library").mkdir()
        mod_comm = types.ModuleType("library.lcd.lcd_comm")
        mod_comm.Orientation = Orientation
        mod_rev = types.ModuleType("library.lcd.lcd_comm_rev_a")
        mod_rev.LcdCommRevA = LcdFalso
        modulos = mock.patch.dict(sys.modules, {
            "library": types.ModuleType("library"), "library.lcd": types.ModuleType("library.lcd"),
            "library.lcd.lcd_comm": mod_comm, "library.lcd.lcd_comm_rev_a": mod_rev,
        })
        modulos.start()
        self.addCleanup(modulos.stop)
        caminho = mock.patch.object(sys, "path", list(sys.path))
        caminho.start()
        self.addCleanup(caminho.stop)
        self.relogio = 1000.0
        rel = mock.patch.object(saida.time, "monotonic", lambda: self.relogio)
        rel.start()
        self.addCleanup(rel.stop)
        self.stderr = io.StringIO()
        silencio = contextlib.redirect_stderr(self.stderr)
        silencio.__enter__()
        self.addCleanup(silencio.__exit__, None, None, None)

    def tela(self):
        return saida.TelaUSB("A", "AUTO", 30, self.pasta, paisagem=True)

    def test_inicia_configurando_a_tela(self):
        t = self.tela()
        self.assertEqual(t.lcd.chamadas, ["Reset", "Init", ("brilho", 30), ("orientacao", Orientation.LANDSCAPE)])

    def test_manda_o_quadro_inteiro_e_depois_so_o_que_mudou(self):
        t = self.tela()
        t.mostrar(quadro())
        t.mostrar(quadro())  # igual: nada vai pela serial
        t.mostrar(quadro(30))
        self.assertEqual(imagens(t.lcd), [("img", (480, 320), 0, 0), ("img", (31, 11), 10, 50)])

    def test_cabo_solto_nao_derruba_e_reconecta(self):
        t = self.tela()
        t.mostrar(quadro())
        t.lcd.falhar = True
        t.mostrar(quadro(5))  # não levanta exceção
        self.assertIsNone(t.lcd)
        self.assertIn("parou de responder", self.stderr.getvalue())

        LcdFalso.presente = False
        self.relogio += 1
        t.mostrar(quadro(5))  # antes do intervalo: nem tenta
        self.relogio += saida.RETENTAR_S
        t.mostrar(quadro(5))  # tenta, mas a porta não existe
        self.assertEqual(len(LcdFalso.instancias), 1)

        LcdFalso.presente = True
        self.relogio += saida.RETENTAR_S
        t.mostrar(quadro(5))
        self.assertEqual(len(LcdFalso.instancias), 2)
        self.assertEqual(imagens(t.lcd), [("img", (480, 320), 0, 0)])  # a tela reiniciou: quadro inteiro
        self.assertIn("tela USB conectada", self.stderr.getvalue())

    def test_comeca_sem_a_tela_e_conecta_quando_ela_aparece(self):
        LcdFalso.presente = False
        t = self.tela()
        self.assertIsNone(t.lcd)
        self.assertIn("não encontrada", self.stderr.getvalue())
        LcdFalso.presente = True
        self.relogio += saida.RETENTAR_S
        t.mostrar(quadro())
        self.assertEqual(imagens(t.lcd), [("img", (480, 320), 0, 0)])

    def test_sys_exit_da_biblioteca_e_contido(self):
        LcdFalso.desistir = True
        t = self.tela()  # não sai do programa
        self.assertIsNone(t.lcd)
        self.assertIn("porta indisponível", self.stderr.getvalue())

    def test_porta_reaberta_pela_biblioteca_reconfigura_e_redesenha(self):
        t = self.tela()
        t.mostrar(quadro())
        t.lcd.chamadas.clear()
        t.lcd.reabrir_no_envio = True
        t.mostrar(quadro(8))
        self.assertIn(("brilho", 30), t.lcd.chamadas)
        self.assertIn(("orientacao", Orientation.LANDSCAPE), t.lcd.chamadas)
        t.mostrar(quadro(8))  # mesmo quadro, mas a tela pode ter perdido tudo: vai inteiro
        self.assertEqual(imagens(t.lcd)[-1], ("img", (480, 320), 0, 0))

    def test_fechar_solta_a_porta(self):
        t = self.tela()
        lcd = t.lcd
        t.fechar()
        self.assertEqual(lcd.chamadas[-1], "close")
        self.assertIsNone(t.lcd)


if __name__ == "__main__":
    unittest.main()
