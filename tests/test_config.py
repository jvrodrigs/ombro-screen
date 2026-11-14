"""config.py: padrões, arquivo opcional, precedência e validação, sem tocar em tela nem USB."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from config import ConfigInvalida, OmbroConfig, ler_arquivo, resolver, validar


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pasta = Path(tmp.name)
        p = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta)})
        p.start()
        self.addCleanup(p.stop)

    def arquivo(self, dados, nome="config.json"):
        arq = self.pasta / nome
        arq.write_text(dados if isinstance(dados, str) else json.dumps(dados), encoding="utf-8")
        return arq


class Padroes(Base):
    def test_sem_arquivo_valem_os_padroes_de_sempre(self):
        cfg, avisos = resolver({})
        self.assertEqual(avisos, [])
        self.assertIsNone(cfg.arquivo)
        esperado = dict(usb=False, revisao="A", porta="AUTO", transporte="auto", brilho=30, paisagem=False, girar=False,
                        demo=False, sem_macmon=False, claude_dir=None, layout="auto", intervalo=None, demo_ciclo=0.0,
                        cor_mascote=None, porta_sim=5680, nao_abrir=False, png=None, debug=False)
        self.assertEqual({k: getattr(cfg, k) for k in esperado}, esperado)
        self.assertEqual(cfg.lib, config.RAIZ / "turing-smart-screen-python")

    def test_valores_derivados(self):
        self.assertTrue(OmbroConfig().paisagem_efetiva)  # o layout do Ombro é sempre deitado
        self.assertEqual(OmbroConfig().intervalo_efetivo, 0.2)
        classico = OmbroConfig(layout="classico")
        self.assertFalse(classico.paisagem_efetiva)
        self.assertEqual(classico.intervalo_efetivo, 1.0)
        self.assertEqual(OmbroConfig(layout="classico", intervalo=0.5).intervalo_efetivo, 0.5)
        self.assertEqual(OmbroConfig(intervalo=0.0).intervalo_efetivo, 0.0)
        self.assertEqual(OmbroConfig(cor_mascote="#3D7A4F").cor_mascote_rgb, (61, 122, 79))
        self.assertIsNone(OmbroConfig().cor_mascote_rgb)

    def test_resumo_vira_json(self):
        json.dumps(OmbroConfig(png=Path("x.png")).resumo())

    def test_listas_iguais_as_da_saida(self):
        import saida

        self.assertEqual(set(config.REVISOES), set(saida.TelaUSB.REVISOES))
        self.assertEqual(config.TRANSPORTES, saida.TRANSPORTES)


class Precedencia(Base):
    def test_arquivo_muda_o_padrao(self):
        arq = self.arquivo({"versao": 1, "usb": True, "brilho": 45, "cor_mascote": "3d7a4f"})
        cfg, avisos = resolver({})
        self.assertEqual((cfg.usb, cfg.brilho, cfg.cor_mascote, cfg.arquivo), (True, 45, "#3D7A4F", arq))
        self.assertEqual(avisos, [])

    def test_linha_de_comando_vence_o_arquivo(self):
        self.arquivo({"usb": True, "brilho": 45, "layout": "classico"})
        cfg, _ = resolver({"usb": False, "brilho": 20})
        self.assertEqual((cfg.usb, cfg.brilho, cfg.layout), (False, 20, "classico"))

    def test_opcao_nao_dada_nao_apaga_o_arquivo(self):
        self.arquivo({"paisagem": True, "intervalo": 0.5})
        cfg, _ = resolver({"paisagem": None, "intervalo": None, "brilho": None})
        self.assertEqual((cfg.paisagem, cfg.intervalo, cfg.brilho), (True, 0.5, 30))

    def test_arquivo_de_config_explicito(self):
        outro = self.arquivo({"porta_sim": 6000}, nome="outro.json")
        cfg, _ = resolver({}, outro)
        self.assertEqual((cfg.porta_sim, cfg.arquivo), (6000, outro))

    def test_arquivo_explicito_que_nao_existe_e_erro(self):
        with self.assertRaisesRegex(ConfigInvalida, "não encontrado"):
            resolver({}, self.pasta / "sumiu.json")

    def test_segue_ombro_screen_dir(self):
        with mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta / "outra")}):
            self.assertEqual(config.arquivo_padrao(), self.pasta / "outra" / "config.json")


class Arquivo(Base):
    def test_vazio_vale_como_sem_opcoes(self):
        arq = self.arquivo("  \n")
        cfg, _ = resolver({})
        self.assertEqual((cfg.brilho, cfg.arquivo), (30, arq))

    def test_json_quebrado_diz_onde(self):
        self.arquivo('{"brilho": 40,\n "usb": }')
        with self.assertRaisesRegex(ConfigInvalida, r"linha 2"):
            resolver({})

    def test_precisa_ser_um_objeto(self):
        self.arquivo("[1, 2]")
        with self.assertRaisesRegex(ConfigInvalida, "objeto"):
            resolver({})

    def test_opcao_desconhecida_vira_aviso(self):
        self.arquivo({"brilho": 40, "tema": "escuro", "png": "x.png"})
        cfg, avisos = resolver({})
        self.assertEqual(cfg.brilho, 40)
        self.assertIsNone(cfg.png)
        self.assertEqual(len(avisos), 2)
        self.assertTrue(any("tema" in a for a in avisos))
        self.assertTrue(any("png" in a and "linha de comando" in a for a in avisos))

    def test_versao_mais_nova_vira_aviso(self):
        self.arquivo({"versao": 2, "brilho": 40, "novidade": 1})
        cfg, avisos = resolver({})
        self.assertEqual(cfg.brilho, 40)
        self.assertTrue(any("versão 2" in a for a in avisos))

    def test_versao_que_nao_e_numero_e_erro(self):
        self.arquivo({"versao": "1"})
        with self.assertRaisesRegex(ConfigInvalida, "versao"):
            resolver({})

    def test_valor_invalido_no_arquivo_diz_o_arquivo_e_a_opcao(self):
        arq = self.arquivo({"brilho": 150})
        with self.assertRaises(ConfigInvalida) as e:
            resolver({})
        self.assertIn(str(arq), str(e.exception))
        self.assertIn("brilho deve ser de 0 a 100", str(e.exception))

    def test_caminho_relativo_e_relativo_ao_arquivo(self):
        arq = self.arquivo({"lib": "libs/turing", "claude_dir": "~/.claude-conta2"})
        valores, _ = ler_arquivo(arq)
        self.assertEqual(valores["lib"], self.pasta / "libs" / "turing")
        self.assertEqual(valores["claude_dir"], "~/.claude-conta2")  # o coletor expande o ~
        self.assertEqual(validar("lib", "~/x"), Path("~/x").expanduser())

    def test_arquivo_ilegivel(self):
        (self.pasta / "config.json").mkdir()  # uma pasta no lugar do arquivo
        with self.assertRaisesRegex(ConfigInvalida, "não consegui ler"):
            resolver({})


class Validacao(unittest.TestCase):
    def invalido(self, nome, valor, trecho):
        with self.assertRaises(ConfigInvalida) as e:
            validar(nome, valor)
        self.assertIn(trecho, str(e.exception))

    def test_brilho(self):
        self.assertEqual(validar("brilho", 0), 0)
        self.assertEqual(validar("brilho", 100.0), 100)
        for v in (-1, 101, 40.5, "40", True):
            with self.assertRaises(ConfigInvalida):
                validar("brilho", v)

    def test_escolhas(self):
        self.assertEqual(validar("revisao", "weact_a"), "WEACT_A")
        self.invalido("revisao", "Z", "A, B, C, D, WEACT_A")
        self.invalido("transporte", "bluetooth", "auto, usb, serial")
        self.invalido("layout", "Ombro", "auto, ombro, classico")
        self.invalido("revisao", 1, "texto")

    def test_numeros(self):
        self.assertEqual(validar("intervalo", 0), 0.0)
        self.assertIsNone(validar("intervalo", None))
        self.invalido("intervalo", -0.1, "maior ou igual a 0")
        self.invalido("demo_ciclo", -1, "maior ou igual a 0")
        self.invalido("porta_sim", 0, "de 1 a 65535")
        self.invalido("porta_sim", 70000, "de 1 a 65535")

    def test_cor(self):
        self.assertEqual(validar("cor_mascote", "#3d7a4f"), "#3D7A4F")
        for v in ("#abc", "zzzzzz", "#3D7A4F0", 123):
            self.invalido("cor_mascote", v, "#3D7A4F")

    def test_textos_e_booleanos(self):
        self.invalido("porta", "  ", "não vazio")
        self.invalido("usb", "sim", "true ou false")
        self.invalido("claude_dir", 3, "texto")
        self.invalido("tema", 1, "desconhecida")


class LinhaDeComando(Base):
    def setUp(self):
        super().setUp()
        import tela

        self.tela = tela

    def test_flags_de_antes_continuam_iguais(self):
        cfg, _ = self.tela.argumentos(["--usb", "--revisao", "c", "--brilho", "40", "--paisagem", "--girar", "--sem-macmon",
                                       "--layout", "classico", "--intervalo", "0.5", "--cor-mascote", "#112233",
                                       "--porta-sim", "6001", "--nao-abrir", "--demo", "--demo-ciclo", "7", "--png", "q.png"])
        self.assertEqual((cfg.usb, cfg.revisao, cfg.brilho, cfg.paisagem, cfg.girar, cfg.sem_macmon, cfg.layout),
                         (True, "C", 40, True, True, True, "classico"))
        self.assertEqual((cfg.intervalo, cfg.cor_mascote, cfg.porta_sim, cfg.nao_abrir, cfg.demo, cfg.demo_ciclo, cfg.png),
                         (0.5, "#112233", 6001, True, True, 7.0, Path("q.png")))

    def test_sem_opcoes(self):
        cfg, _ = self.tela.argumentos([])
        self.assertEqual(cfg, OmbroConfig())

    def test_no_usb_desliga_o_que_o_arquivo_ligou(self):
        self.arquivo({"usb": True})
        self.assertTrue(self.tela.argumentos([])[0].usb)
        self.assertFalse(self.tela.argumentos(["--no-usb"])[0].usb)

    def test_valor_invalido_sai_com_mensagem_clara(self):
        erro = io.StringIO()
        with contextlib.redirect_stderr(erro), self.assertRaises(SystemExit) as e:
            self.tela.argumentos(["--brilho", "150"])
        self.assertEqual(e.exception.code, 2)
        self.assertIn("brilho deve ser de 0 a 100", erro.getvalue())


if __name__ == "__main__":
    unittest.main()
