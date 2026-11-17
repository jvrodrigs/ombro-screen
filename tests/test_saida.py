"""TelaUSB com uma biblioteca falsa: envio por faixas e reconexão quando a tela some."""
import io
import logging
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
    quebrar_ao_abrir = False  # a porta aparece, mas abrir dá erro (ocupada, sem permissão)
    instancias: list = []

    @staticmethod
    def auto_detect_com_port():
        return "/dev/cu.falsa" if LcdFalso.presente else None

    def __init__(self, com_port, display_width, display_height):
        if LcdFalso.desistir:
            raise SystemExit(0)
        if LcdFalso.quebrar_ao_abrir:
            raise OSError(16, "Resource busy")
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
        if getattr(self, "quebrar_ao_fechar", False):
            raise OSError("já estava fechada")


def quadro(marca: int = 0) -> Image.Image:
    img = Image.new("RGB", (480, 320), (18, 17, 16))
    if marca:
        ImageDraw.Draw(img).rectangle((10, 50, 10 + marca, 60), fill=(255, 0, 0))
    return img


def imagens(lcd):
    return [c for c in lcd.chamadas if c[0] == "img"]


def inteiro(w: int = 480, h: int = 320) -> list:
    """Um quadro inteiro, como vai para a tela: em faixas (o envio pode parar entre duas para atender um pedido)."""
    return [("img", (w, min(saida.FAIXA, h - y)), 0, y) for y in range(0, h, saida.FAIXA)]


class TelaUSBComLibFalsa(unittest.TestCase):
    def setUp(self):
        LcdFalso.presente, LcdFalso.desistir, LcdFalso.quebrar_ao_abrir, LcdFalso.instancias = True, False, False, []
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
        self.stderr = io.StringIO()  # o que sairia no log
        self.log = logging.getLogger("ombro.usb")
        h = logging.StreamHandler(self.stderr)
        self.log.addHandler(h)
        nivel, propaga = self.log.level, self.log.propagate
        self.log.setLevel(logging.DEBUG)
        self.log.propagate = False
        self.addCleanup(lambda: (self.log.removeHandler(h), self.log.setLevel(nivel), setattr(self.log, "propagate", propaga)))

    def tela(self, revisao="A", porta="AUTO", paisagem=True, **kw):
        return saida.TelaUSB(revisao, porta, 30, self.pasta, paisagem=paisagem, **kw)

    def test_inicia_configurando_a_tela(self):
        t = self.tela()
        self.assertEqual(t.lcd.chamadas, ["Reset", "Init", ("brilho", 30), ("orientacao", Orientation.LANDSCAPE)])

    def test_manda_o_quadro_inteiro_e_depois_so_o_que_mudou(self):
        t = self.tela()
        t.mostrar(quadro())
        t.mostrar(quadro())  # igual: nada vai pela serial
        t.mostrar(quadro(30))
        self.assertEqual(imagens(t.lcd), inteiro() + [("img", (31, 11), 10, 50)])

    # ---------- orientação com o Ombro rodando (girar 180°, clássico em pé ou deitado) ----------
    def test_girar_muda_a_orientacao_na_conexao_aberta(self):
        t = self.tela()
        t.mostrar(quadro())
        lcd = t.lcd
        self.assertTrue(t.definir_orientacao(paisagem=True, girar=True))
        self.assertIs(t.lcd, lcd)  # a mesma conexão: nada de fechar e reabrir o USB
        self.assertNotIn("close", lcd.chamadas)
        self.assertEqual(lcd.chamadas[-1], ("orientacao", Orientation.REVERSE_LANDSCAPE))
        self.assertEqual(t.estado()["orientation"], "reverse_landscape")
        t.mostrar(quadro())  # o quadro é o mesmo, mas a tela girou: vai inteiro
        self.assertEqual(imagens(lcd)[-8:], inteiro())
        self.assertTrue(t.definir_orientacao(paisagem=True, girar=False))  # desligar volta
        self.assertEqual(t.estado()["orientation"], "landscape")
        n = len(lcd.chamadas)
        self.assertTrue(t.definir_orientacao(paisagem=True, girar=False))  # a mesma de novo: nada vai para a tela
        self.assertEqual(len(lcd.chamadas), n)

    def test_classico_em_pe_e_deitado_trocam_o_tamanho_do_quadro(self):
        t = self.tela()
        t.mostrar(quadro())
        self.assertTrue(t.definir_orientacao(paisagem=False, girar=False))
        self.assertEqual(t.estado()["orientation"], "portrait")
        t.mostrar(Image.new("RGB", (320, 480), (18, 17, 16)))
        self.assertEqual(imagens(t.lcd)[-12:], inteiro(320, 480))
        self.assertTrue(t.definir_orientacao(paisagem=False, girar=True))
        self.assertEqual(t.estado()["orientation"], "reverse_portrait")

    def test_orientacao_com_a_tela_fora_vale_ao_reconectar(self):
        t = self.tela()
        t.mostrar(quadro())
        t.lcd.falhar = True
        t.mostrar(quadro(5))
        self.assertIsNone(t.lcd)
        self.assertFalse(t.definir_orientacao(paisagem=True, girar=True))  # guardada: a tela não está lá
        self.assertEqual(t.estado()["orientation"], "reverse_landscape")
        self.relogio += saida.RETENTAR_S + 0.1
        t.mostrar(quadro(5))  # reconecta e configura com a orientação nova
        self.assertIn(("orientacao", Orientation.REVERSE_LANDSCAPE), LcdFalso.instancias[-1].chamadas)

    def test_tela_que_recusa_a_orientacao_vira_reconexao(self):
        t = self.tela()
        t.lcd.SetOrientation = mock.Mock(side_effect=OSError(5, "Input/output error"))
        self.assertFalse(t.definir_orientacao(paisagem=True, girar=True))
        self.assertIsNone(t.lcd)
        self.assertEqual(t.conexao, "error")
        self.assertIn("não aceitou a orientação", self.stderr.getvalue())
        self.relogio += saida.RETENTAR_S + 0.1
        t.mostrar(quadro())
        self.assertIn(("orientacao", Orientation.REVERSE_LANDSCAPE), LcdFalso.instancias[-1].chamadas)

    # ---------- o envio para entre duas faixas quando há um pedido esperando ----------
    def test_envio_interrompido_continua_no_proximo_quadro(self):
        t = self.tela()
        pedidos = iter([False, True])  # depois da 2ª faixa chega um pedido
        t.interromper = lambda: next(pedidos, False)
        t.mostrar(quadro())
        self.assertEqual(imagens(t.lcd), inteiro()[:2])  # parou: o laço vai atender o pedido
        t.interromper = lambda: False
        t.mostrar(quadro())  # o mesmo quadro: só o que faltava
        self.assertEqual(imagens(t.lcd), inteiro())
        t.mostrar(quadro())  # agora a tela tem tudo: nada a mandar
        self.assertEqual(len(imagens(t.lcd)), 8)

    def test_interrompido_e_o_quadro_seguinte_e_outro(self):
        t = self.tela()
        t.interromper = lambda: False
        t.mostrar(quadro())
        n = len(imagens(t.lcd))
        outro = Image.new("RGB", (480, 320), (200, 30, 30))  # outro layout: todas as faixas mudam
        vezes = iter([True])
        t.interromper = lambda: next(vezes, False)
        t.mostrar(outro)  # manda a 1ª faixa e para
        self.assertEqual(imagens(t.lcd)[n:], inteiro()[:1])
        terceiro = Image.new("RGB", (480, 320), (30, 200, 30))  # e o pedido trocou o layout de novo
        t.mostrar(terceiro)
        self.assertEqual(imagens(t.lcd)[n + 1:], inteiro())  # todas as faixas: a 1ª tinha o outro, o resto o antigo
        t.mostrar(terceiro)
        self.assertEqual(len(imagens(t.lcd)), n + 9)  # e a tela ficou igual ao último quadro

    def test_sem_pedido_esperando_o_quadro_vai_ate_o_fim(self):
        t = self.tela()
        consultas = []
        t.interromper = lambda: consultas.append(1) and False
        t.mostrar(quadro())
        self.assertEqual(imagens(t.lcd), inteiro())
        self.assertEqual(len(consultas), 7)  # entre as faixas; depois da última não há o que interromper

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
        self.assertEqual(imagens(t.lcd), inteiro())  # a tela reiniciou: quadro inteiro
        self.assertIn("tela USB reconectada", self.stderr.getvalue())

    def test_comeca_sem_a_tela_e_conecta_quando_ela_aparece(self):
        LcdFalso.presente = False
        t = self.tela()
        self.assertIsNone(t.lcd)
        self.assertIn("não encontrada", self.stderr.getvalue())
        LcdFalso.presente = True
        self.relogio += saida.RETENTAR_S
        t.mostrar(quadro())
        self.assertEqual(imagens(t.lcd), inteiro())

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
        self.assertEqual(imagens(t.lcd)[-8:], inteiro())

    def test_fechar_solta_a_porta(self):
        t = self.tela()
        lcd = t.lcd
        t.fechar()
        self.assertEqual(lcd.chamadas[-1], "close")
        self.assertIsNone(t.lcd)


    def test_porta_ocupada_vira_aviso_e_tenta_de_novo(self):
        LcdFalso.quebrar_ao_abrir = True
        t = self.tela()
        self.assertIsNone(t.lcd)
        self.assertIn("Resource busy", self.stderr.getvalue())
        LcdFalso.quebrar_ao_abrir = False
        self.relogio += saida.RETENTAR_S
        t.mostrar(quadro())
        self.assertIsNotNone(t.lcd)

    def test_reconectar_reconfigura_a_tela_do_zero(self):
        t = self.tela()
        t.mostrar(quadro())
        t.lcd.falhar = True
        t.mostrar(quadro(3))
        self.relogio += saida.RETENTAR_S
        t.mostrar(quadro(3))
        nova = LcdFalso.instancias[-1]
        self.assertEqual(nova.chamadas[:4], ["Reset", "Init", ("brilho", 30), ("orientacao", Orientation.LANDSCAPE)])
        self.assertEqual(LcdFalso.instancias[0].chamadas[-1], "close")  # a conexão antiga foi solta

    def test_falha_ao_fechar_nao_atrapalha_a_reconexao(self):
        t = self.tela()
        t.lcd.quebrar_ao_fechar = True
        t.lcd.falhar = True
        t.mostrar(quadro())
        self.assertIsNone(t.lcd)

    def test_orientacoes(self):
        casos = {(True, False): Orientation.LANDSCAPE, (True, True): Orientation.REVERSE_LANDSCAPE,
                 (False, False): Orientation.PORTRAIT, (False, True): Orientation.REVERSE_PORTRAIT}
        for (paisagem, girar), esperada in casos.items():
            t = self.tela(paisagem=paisagem, girar=girar)
            self.assertEqual(t.lcd.chamadas[-1], ("orientacao", esperada))

    def test_porta_informada_que_nao_existe_nem_tenta_abrir(self):
        t = self.tela(porta=str(self.pasta / "cu.nao-existe"))
        self.assertIsNone(t.lcd)
        self.assertEqual(LcdFalso.instancias, [])

    def test_revisao_desconhecida_encerra(self):
        with self.assertRaises(SystemExit) as e:
            self.tela(revisao="Z")
        self.assertIn("Revisão desconhecida", str(e.exception))

    def test_sem_a_biblioteca_encerra_com_instrucao(self):
        with self.assertRaises(SystemExit) as e:
            saida.TelaUSB("A", "AUTO", 30, self.pasta / "outra", transporte="serial")
        self.assertIn("git clone", str(e.exception))

    def test_pronta_so_quando_vale_desenhar(self):
        t = self.tela()
        self.assertTrue(t.pronta())
        t.lcd.falhar = True
        t.mostrar(quadro())
        self.assertFalse(t.pronta())  # fora, e ainda não é hora de tentar de novo
        self.relogio += saida.RETENTAR_S
        self.assertTrue(t.pronta())  # hora de tentar: o próximo quadro serve para reconectar

    def test_conta_os_bytes_mandados(self):
        t = self.tela()
        t.mostrar(quadro())
        t.mostrar(quadro(30))
        self.assertEqual(t.bytes_enviados, (480 * 320 + 31 * 11) * 2)


class SimuladorHttp(unittest.TestCase):
    def test_serve_a_pagina_e_o_quadro_atual(self):
        import urllib.request

        sim = saida.Simulador(0, paisagem=True)  # porta 0: o sistema escolhe uma livre
        self.addCleanup(sim.fechar)
        self.assertTrue(sim.pronta())
        base = f"http://127.0.0.1:{sim.servidor.server_address[1]}"
        with urllib.request.urlopen(base + "/") as resp:
            self.assertIn("480x320", resp.read().decode())
        sim.mostrar(quadro(4))
        with urllib.request.urlopen(base + "/quadro.png?123") as resp:
            self.assertEqual(resp.headers["Content-Type"], "image/png")
            self.assertEqual(Image.open(io.BytesIO(resp.read())).size, (480, 320))

    def test_abre_sem_consultar_o_dns(self):
        # o getfqdn do http.server travava o ombrod por dezenas de segundos nos runners macOS do GitHub Actions
        with mock.patch("socket.getfqdn", side_effect=AssertionError("consultou o DNS reverso")) as getfqdn:
            sim = saida.Simulador(0)
        self.addCleanup(sim.fechar)
        getfqdn.assert_not_called()
        self.assertEqual(sim.servidor.server_name, "localhost")
        self.assertEqual(sim.servidor.server_port, sim.servidor.server_address[1])

if __name__ == "__main__":
    unittest.main()
