"""A cor do corpo de cada avatar (config.cores_avatares): o arquivo, a IPC, o mascote da telinha e o "reduzir movimento".

O que vale: uma entrada por ID de avatar ("default", "rgb" ou "#RRGGBB"); sem entrada, o cor_mascote e o cor_modo de
sempre; com entrada, ela vale por cima. Só o corpo muda de cor: a forma, a transparência, os olhos e a pose não.
"""
import json
import os
import random
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import avatares
import mascote
from config import ConfigInvalida, OmbroConfig, ler_arquivo, resolver, validar
from ombro import movimento, preferencias
from ombro.ipc import ErroIPC
from ombro.movimento import MovimentoReduzido
from ombro.app import Aplicacao
from tests.test_config_ao_vivo import Rodando

IDS = tuple(avatares.catalogo().ids())


class NoArquivo(unittest.TestCase):
    def setUp(self):
        self.arq = Path(tempfile.mkdtemp()) / "config.json"

    def ler(self, dados):
        self.arq.write_text(json.dumps(dados))
        return ler_arquivo(self.arq)

    def test_config_antigo_sem_a_chave_nao_muda_nada(self):
        valores, avisos = self.ler({"versao": 1, "brilho": 40, "cor_mascote": "#3D7A4F", "cor_modo": "sorteada"})
        self.assertNotIn("cores_avatares", valores)
        cfg, _ = resolver({}, self.arq)
        self.assertEqual((cfg.cores_avatares, cfg.cor_avatar("sereno"), cfg.cor_mascote, cfg.cor_modo),
                         ((), None, "#3D7A4F", "sorteada"))
        self.assertEqual(avisos, [])

    def test_valores_normalizados(self):
        valores, avisos = self.ler({"cores_avatares": {"sereno": "3d7a4f", "cubo": "RGB", "pixel": " Default ",
                                                       "nova": "#abcdef"}})
        self.assertEqual(dict(valores["cores_avatares"]),
                         {"sereno": "#3D7A4F", "cubo": "rgb", "pixel": "default", "nova": "#ABCDEF"})
        self.assertEqual([k for k, _ in valores["cores_avatares"]], sorted(["sereno", "cubo", "pixel", "nova"]))
        self.assertEqual(avisos, [])

    def test_null_remove_a_entrada(self):
        valores, _ = self.ler({"cores_avatares": {"sereno": None, "cubo": "rgb"}})
        self.assertEqual(dict(valores["cores_avatares"]), {"cubo": "rgb"})

    def test_invalidos(self):
        for ruim in ({"cores_avatares": {"sereno": "vermelho"}}, {"cores_avatares": {"sereno": 12}},
                     {"cores_avatares": {"sereno": True}}, {"cores_avatares": {"sereno": "#12345"}},
                     {"cores_avatares": {"sereno": "#GGGGGG"}}, {"cores_avatares": {"Sereno!": "rgb"}},
                     {"cores_avatares": "rgb"}, {"cores_avatares": {"sereno": ["rgb"]}}, {"cores_avatares": [["a"]]}):
            with self.assertRaises(ConfigInvalida, msg=ruim):
                self.ler(ruim)

    def test_avatar_que_sumiu_avisa_e_nao_trava(self):
        valores, avisos = self.ler({"cores_avatares": {"apagado": "rgb", "sereno": "default"}})
        self.assertEqual(dict(valores["cores_avatares"]), {"apagado": "rgb", "sereno": "default"})
        self.assertEqual(len(avisos), 1)
        self.assertIn("apagado", avisos[0])

    def test_cor_mascote_da_linha_de_comando_segue_valendo(self):
        cfg, _ = resolver({"cor_mascote": "#123456"}, self.arq if self.arq.exists() else None)
        self.assertEqual((cfg.cor_mascote_rgb, cfg.cores_avatares), ((0x12, 0x34, 0x56), ()))
        self.assertEqual(validar("cores_avatares", {"cubo": "#abcdef"}), (("cubo", "#ABCDEF"),))


class PelaIPC(unittest.TestCase):
    def setUp(self):
        self.arq = Path(tempfile.mkdtemp()) / "config.json"

    def test_o_esquema_tem_uma_opcao_por_avatar(self):
        op = {o["key"]: o for o in preferencias.esquema()["options"]}
        for aid in IDS:
            o = op[f"cores_avatares.{aid}"]
            self.assertEqual((o["type"], o["live"], o["nullable"], o["group"]), ("string", True, True, "avatars"))
        self.assertEqual(len(IDS), 12)

    def test_valida_normaliza_e_recusa(self):
        ok = preferencias.validar_mudancas({"cores_avatares.sereno": "3d7a4f", "cores_avatares.cubo": "rgb",
                                            "cores_avatares.pixel": None, "cores_avatares.nova": "default"})
        self.assertEqual(ok, {"cores_avatares.sereno": "#3D7A4F", "cores_avatares.cubo": "rgb",
                              "cores_avatares.pixel": None, "cores_avatares.nova": "default"})
        for ruim in ({"cores_avatares.sereno": "azul"}, {"cores_avatares.sereno": 5}, {"cores_avatares.apagado": "rgb"},
                     {"cores_avatares.sereno": "#12"}, {"cores_avatares": {"sereno": "rgb"}}):
            with self.assertRaises(ConfigInvalida, msg=ruim):
                preferencias.validar_mudancas(ruim)

    def test_aplicar_em_isola_por_id_e_null_remove(self):
        cfg = OmbroConfig()
        cfg = preferencias.aplicar_em(cfg, {"cores_avatares.sereno": "#112233", "cores_avatares.cubo": "rgb"})
        self.assertEqual(cfg.cores_avatares, (("cubo", "rgb"), ("sereno", "#112233")))
        cfg = preferencias.aplicar_em(cfg, {"cores_avatares.sereno": "default", "cores_avatares.cubo": None})
        self.assertEqual(cfg.cores_avatares, (("sereno", "default"),))
        self.assertEqual(preferencias.valor(cfg, "cores_avatares.sereno"), "default")
        self.assertIsNone(preferencias.valor(cfg, "cores_avatares.cubo"))

    def test_gravar_e_ler_de_volta_sem_estragar_o_resto(self):
        self.arq.write_text(json.dumps({"versao": 1, "brilho": 40, "avatars": {"claude": "sereno"}}))
        preferencias.gravar(self.arq, {"cores_avatares.sereno": "#3D7A4F", "cores_avatares.cubo": "rgb"})
        dados = json.loads(self.arq.read_text())
        self.assertEqual(dados["cores_avatares"], {"sereno": "#3D7A4F", "cubo": "rgb"})
        self.assertEqual((dados["brilho"], dados["avatars"]), (40, {"claude": "sereno"}))
        cfg, _ = resolver({}, self.arq)  # o que a IPC grava, o arquivo lê igual (o reinício)
        self.assertEqual(dict(cfg.cores_avatares), {"sereno": "#3D7A4F", "cubo": "rgb"})
        preferencias.gravar(self.arq, {"cores_avatares.sereno": None})
        self.assertEqual(json.loads(self.arq.read_text())["cores_avatares"], {"cubo": "rgb"})
        preferencias.gravar(self.arq, {"cores_avatares.cubo": None})  # esvaziou: a chave some
        self.assertNotIn("cores_avatares", json.loads(self.arq.read_text()))
        preferencias.gravar(self.arq, {"cores_avatares.cubo": None})  # remover o que não há não quebra
        self.assertEqual(json.loads(self.arq.read_text())["brilho"], 40)


class OCiclo(unittest.TestCase):
    def test_volta_a_cada_12_s_com_valores_fixos(self):
        esperado = {0: (231, 64, 64), 1: (231, 148, 64), 3: (148, 231, 64), 6: (64, 231, 231), 9: (148, 64, 231),
                    12: (231, 64, 64), 30: (64, 231, 231)}  # o mesmo quadro de app/src/features/avatars/cicloRgb.test.ts
        for t, cor in esperado.items():
            self.assertEqual(mascote.cor_do_ciclo(t), cor, t)
        self.assertEqual(mascote.CICLO_RGB_S, 12.0)

    def test_lê_bem_no_fundo_escuro(self):
        for i in range(120):
            r, g, b = mascote.cor_do_ciclo(i * 0.1)
            self.assertTrue(55 <= min(r, g, b) and max(r, g, b) <= 240)


def _mascote(av="sereno", entrada=None, cor=(10, 200, 30), **kw):
    m = mascote.Mascote(**kw)
    if entrada is not None:
        m.definir_entradas({av: entrada})
    m.usar(avatares.catalogo().avatares[av], cor, 0.0, ja=True)
    return m


class NoMascote(unittest.TestCase):
    def test_sem_entrada_e_o_de_sempre(self):
        for kw in (dict(), dict(cor_modo="sorteada"), dict(cor_fixa=(1, 2, 3))):
            random.seed(7)
            a = _mascote(**kw)
            random.seed(7)
            b = mascote.Mascote(**kw)
            b.usar(avatares.catalogo().avatares["sereno"], (10, 200, 30), 0.0, ja=True)
            self.assertEqual((a.cor, a.cor_atual(1.0)), (b.cor, b.cor_atual(1.0)))
            self.assertEqual(a.camada(1.0).tobytes(), b.camada(1.0).tobytes())

    def test_entrada_vale_por_cima_das_globais(self):
        for kw in (dict(), dict(cor_modo="sorteada"), dict(cor_fixa=(1, 2, 3))):
            m = _mascote(entrada=("fixa", (9, 8, 7)), **kw)
            self.assertEqual((m.cor, m.cor_atual(0.1), m.cor_atual(7.3)), ((9, 8, 7),) * 3, kw)
            m.ir_para("trabalhando", 2.0)  # a troca de estado não sorteia nem clareia o que o usuário escolheu
            self.assertEqual(m.cor_atual(2.1), (9, 8, 7), kw)
            p = _mascote(entrada=("padrao",), **kw)
            self.assertEqual(p.cor, (10, 200, 30), kw)  # a original (a do agente), não a global

    def test_default_explicito_mantem_a_cor_base_em_todo_quadro_e_a_ausencia_conserva_o_realce(self):
        """R8-05: "default" explícito = a cor-base em todo quadro (sem o pulso de meio segundo da troca de estado); sem
        entrada, o caminho legado (com o realce) é o de sempre. O app faz o mesmo (cores.test.tsx)."""
        base = (10, 200, 30)
        explicito = _mascote(entrada=("padrao",))
        legado = _mascote()
        explicito.ir_para("trabalhando", 2.0)
        legado.ir_para("trabalhando", 2.0)
        for t in (2.0, 2.1, 2.25, 2.4, 2.49):
            self.assertEqual(explicito.cor_atual(t), base, t)
        self.assertNotEqual(legado.cor_atual(2.1), base)  # o realce: a cor clareia logo depois da troca de estado
        self.assertEqual(legado.cor_atual(2.6), base)

    def test_rgb_gira_sem_depender_do_estado_e_para_no_reduzir_movimento(self):
        m = _mascote(entrada=("rgb",))
        self.assertTrue(m.em_ciclo_rgb)
        antes = [m.cor_atual(t) for t in (0.0, 1.0, 5.0)]
        self.assertEqual(antes, [mascote.cor_do_ciclo(t) for t in (0.0, 1.0, 5.0)])
        m.ir_para("trabalhando", 3.0)  # trocar de estado não recomeça a fase
        m.ir_para("esperando", 4.0)
        self.assertEqual([m.cor_atual(t) for t in (0.0, 1.0, 5.0)], antes)
        m.movimento_reduzido = True  # parado na cor-base; o "rgb" salvo continua na entrada
        self.assertEqual({m.cor_atual(t) for t in (0.0, 1.0, 5.0, 99.0)}, {(10, 200, 30)})
        self.assertTrue(m.em_ciclo_rgb)
        m.movimento_reduzido = False
        self.assertEqual(m.cor_atual(1.0), mascote.cor_do_ciclo(1.0))

    def test_a_entrada_e_a_da_forma_desenhada_e_muda_junto_com_ela(self):
        cat = avatares.catalogo().avatares
        m = _mascote("sereno", entrada=None, cor=(10, 200, 30))
        m.definir_entradas({"cubo": ("fixa", (200, 0, 0))})
        m.usar(cat["cubo"], (1, 2, 3), 1.0)  # forma nova: a cor do destino entra no meio da troca
        self.assertEqual((m.forma.avatar, m.cor_atual(1.1)), ("sereno", (10, 200, 30)))
        m.camada(1.0 + mascote.TRANSICAO * 0.75)
        self.assertEqual((m.forma.avatar, m.cor_atual(2.0)), ("cubo", (200, 0, 0)))
        m.camada(3.0)  # a transição acabou
        m.definir_entradas({"cubo": ("rgb",)})  # mesma forma: só a entrada, na hora, sem transição
        self.assertTrue(m.em_ciclo_rgb)
        self.assertIsNone(m._troca)
        m.definir_entradas({})  # remove: volta a herdar
        self.assertEqual((m.em_ciclo_rgb, m.cor, m.cor_atual(4.5)), (False, (1, 2, 3), (1, 2, 3)))

    def test_usa_ciclo_rgb_inclui_o_destino_da_troca(self):
        cat = avatares.catalogo().avatares
        m = _mascote("sereno")
        m.definir_entradas({"cubo": ("rgb",)})
        self.assertFalse(m.usa_ciclo_rgb)
        m.usar(cat["cubo"], (1, 2, 3), 1.0)
        self.assertFalse(m.em_ciclo_rgb)
        self.assertTrue(m.usa_ciclo_rgb)  # o ciclo começa no ponto médio: a leitura do sistema já tem de estar feita

    def test_so_o_corpo_muda_nos_doze_avatares_e_nos_dois_pacotes(self):
        """A mesma pose e os mesmos olhos/extras; a cor do corpo é a pedida; a forma e a transparência não mudam."""
        for pacote in mascote.PACOTES:
            for aid, av in avatares.catalogo().avatares.items():
                quadros = {}
                for nome, entrada, cor in (("a", ("fixa", (200, 40, 40)), (200, 40, 40)),
                                           ("b", ("fixa", (40, 40, 200)), (40, 40, 200))):
                    m = mascote.Mascote()
                    m.usar_pacote(pacote, 0.0, ja=True)
                    m.definir_entradas({aid: entrada})
                    m.usar(av, cor, 0.0, ja=True)
                    m.ir_para("trabalhando", 0.0)
                    quadros[nome] = m.camada(1.5)
                fixo = mascote.Mascote()
                fixo.usar_pacote(pacote, 0.0, ja=True)
                fixo.usar(av, (200, 40, 40), 0.0, ja=True)
                fixo.cor_fixa = (200, 40, 40)
                fixo.cor = fixo._cor_do_estado()
                fixo.ir_para("trabalhando", 0.0)
                self.assertEqual(quadros["a"].tobytes(), fixo.camada(1.5).tobytes(), f"{aid}/{pacote}: igual ao cor_mascote")
                self.assertEqual(quadros["a"].getchannel("A").tobytes(), quadros["b"].getchannel("A").tobytes(),
                                 f"{aid}/{pacote}: a forma e a transparência não podem mudar")
                self.assertNotEqual(quadros["a"].tobytes(), quadros["b"].tobytes(), f"{aid}/{pacote}")

    def test_olhos_e_extras_ficam_com_a_paleta(self):
        """Trocar a cor do corpo não toca a paleta: os olhos (escuros) são os mesmos nos dois quadros."""
        av = avatares.catalogo().avatares["ombro-cloud"]
        escuros = []
        for cor in ((230, 90, 90), (90, 90, 230)):
            m = mascote.Mascote()
            m.definir_entradas({"ombro-cloud": ("fixa", cor)})
            m.usar(av, cor, 0.0, ja=True)
            img = m.camada(1.0).convert("RGBA")
            escuros.append({(x, y) for x in range(img.width) for y in range(img.height)
                            if img.getpixel((x, y))[3] and sum(img.getpixel((x, y))[:3]) < 120})
        self.assertTrue(escuros[0])
        self.assertEqual(escuros[0], escuros[1])

    def test_quadro_do_ciclo_muda_a_cada_instante_sem_refazer_nada(self):
        m = _mascote("orbita", entrada=("rgb",))
        a, b = m.camada(1.0), m.camada(4.0)
        self.assertNotEqual(a.tobytes(), b.tobytes())
        fixo = _mascote("orbita", entrada=("fixa", mascote.cor_do_ciclo(4.0)), cor=mascote.cor_do_ciclo(4.0))
        self.assertEqual(b.tobytes(), fixo.camada(4.0).tobytes())  # o ciclo em t é a cor fixa em t: só o corpo muda


class LeituraDoSistema(unittest.TestCase):
    def test_guarda_a_leitura_e_nao_consulta_por_quadro(self):
        t, chamadas = [0.0], []
        r = MovimentoReduzido(leitor=lambda: chamadas.append(1) or False, relogio=lambda: t[0], ttl=5.0)
        for _ in range(500):
            self.assertFalse(r.reduzido())
        self.assertEqual(len(chamadas), 1)
        t[0] = 5.1
        r.reduzido()
        self.assertEqual(len(chamadas), 2)

    def test_ligado_reduz_e_desligado_anima(self):
        self.assertTrue(MovimentoReduzido(leitor=lambda: True).reduzido())
        self.assertFalse(MovimentoReduzido(leitor=lambda: False).reduzido())

    def test_sem_leitura_fica_parado(self):
        self.assertTrue(MovimentoReduzido(leitor=lambda: None).reduzido())

        def quebra():
            raise OSError("sem sistema")

        self.assertTrue(MovimentoReduzido(leitor=quebra).reduzido())

    def test_fora_do_macos_nao_le(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIsNone(movimento.ler_do_sistema())

    @unittest.skipUnless(sys.platform == "darwin", "a leitura nativa é do macOS")
    def test_leitura_nativa_devolve_booleano_no_macos(self):
        self.assertIn(movimento.ler_do_sistema(), (True, False))


class SemLaco(unittest.TestCase):
    """O Aplicacao sem o laço (os quadros e o tempo são do teste): a transição de avatar e a exportação de PNG."""

    def _app(self, **opcoes):
        amb = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": tempfile.mkdtemp()})
        amb.start()
        self.addCleanup(amb.stop)
        cfg, _ = resolver({"demo": True, "sem_macmon": True, "nao_abrir": True, "layout": "minimal-v2", **opcoes})
        app = Aplicacao(cfg)
        self.addCleanup(app.fechar)
        app.ler_claude()
        return app

    def test_editar_outro_id_nao_corta_a_transicao_e_o_destino_vale_no_ponto_medio(self):
        """R4-03: Sereno -> Cubo em andamento; editar Pixel (alheio), Sereno (o de agora) e Cubo (o destino)."""
        cat = avatares.catalogo().avatares
        app = self._app()
        m = app.mascote
        m.usar(cat["sereno"], (10, 200, 30), 0.0, ja=True)
        m.usar(cat["cubo"], (1, 2, 3), 1.0)  # a transição começa
        troca = m._troca
        self.assertIsNotNone(troca)
        app.aplicar_cfg(replace(app.cfg, cores_avatares=(("pixel", "#FF0000"),)))  # um id alheio
        self.assertIs(m._troca, troca)  # a mesma transição, sem corte
        self.assertEqual(m.forma.avatar, "sereno")
        self.assertEqual(m.entradas["pixel"], ("fixa", (255, 0, 0)))
        app.aplicar_cfg(replace(app.cfg, cores_avatares=(("pixel", "#FF0000"), ("sereno", "#00FF00"))))  # o de agora
        self.assertIs(m._troca, troca)
        self.assertEqual((m.forma.avatar, m.cor_atual(1.1)), ("sereno", (0, 255, 0)))
        app.aplicar_cfg(replace(app.cfg, cores_avatares=(("cubo", "rgb"), ("sereno", "#00FF00"))))  # o destino
        self.assertIs(m._troca, troca)
        self.assertTrue(m.usa_ciclo_rgb and not m.em_ciclo_rgb)
        m.camada(1.0 + mascote.TRANSICAO * 0.75)  # passou do ponto médio: a forma nova e a entrada dela
        self.assertEqual((m.forma.avatar, m.em_ciclo_rgb), ("cubo", True))

    def _png(self, reduzido):
        app = self._app(cores_avatares=(("ombro-cloud", "rgb"),))
        leituras = []
        app.movimento = MovimentoReduzido(leitor=lambda: leituras.append(1) or reduzido)
        vistos = []
        real = app.desenhar_animado

        def espia(h, uso, agora):
            vistos.append((agora, app.mascote.movimento_reduzido, app.mascote.cor_atual(agora)))
            return real(h, uso, agora)

        app.desenhar_animado = espia
        app.salvar_png(Path(tempfile.mkdtemp()) / "q.png")
        return app, vistos, leituras

    def test_png_respeita_o_reduzir_movimento_no_quadro_final(self):
        """R4-04: o PNG (o quadro de agora + 1 s) lê a preferência (guardada) e, ligada, sai na cor-base do rgb."""
        app, vistos, leituras = self._png(True)
        agora, reduzido, cor = vistos[-1]
        self.assertTrue(reduzido)
        self.assertEqual(cor, app.mascote.cor_base)
        self.assertNotEqual(cor, mascote.cor_do_ciclo(agora))
        self.assertEqual(len(leituras), 1)  # lida uma vez, guardada: a do quadro de antes e a do final são a mesma

    def test_png_sem_reduzir_movimento_sai_no_ciclo(self):
        app, vistos, leituras = self._png(False)
        agora, reduzido, cor = vistos[-1]
        self.assertFalse(reduzido)
        self.assertEqual(cor, mascote.cor_do_ciclo(agora))
        self.assertEqual(len(leituras), 1)


class NaAplicacao(Rodando):
    """O ombrod com a tela falsa: a entrada vale depois de resolver o avatar, ao vivo, gravada e desfeita se falhar."""

    def test_valor_por_id_e_aleatorio_por_sessao(self):
        app = self.app(layout="minimal-v2", cores_avatares=(("sereno", "#112233"),))
        app.definir_avatar("avatares.claude", "sereno")
        self.assertEqual((app.mascote.forma.avatar, app.mascote._entrada), ("sereno", ("fixa", (0x11, 0x22, 0x33))))
        app.definir_avatar("avatares.claude", "cubo")  # outro avatar: a cor do sereno não vai junto
        self.assertEqual((app.mascote.forma.avatar, app.mascote._entrada), ("cubo", None))
        app.definir_avatar("cores_avatares.cubo", "rgb")
        self.assertTrue(app.mascote.em_ciclo_rgb)
        # aleatório por sessão: a entrada é a do avatar que saiu, não a do slot
        cfg = app.cfg
        app.aplicar_cfg(OmbroConfig(**{**cfg.__dict__, "avatar_sorteio": "sessao",
                                       "avatares": (("claude", "aleatorio"),), "cores_avatares": ()}))
        sessao = app._sessao_relevante("claude")
        sorteado = app.avatar_de("claude", sessao).id
        app.definir_avatar(f"cores_avatares.{sorteado}", "#AA0000")
        self.assertEqual((app.mascote.forma.avatar, app.mascote._entrada), (sorteado, ("fixa", (0xAA, 0, 0))))

    def test_entrada_vale_por_cima_do_cor_mascote_global(self):
        app = self.app(layout="minimal-v2", cor_mascote="#123456", cor_modo="sorteada")
        self.assertEqual(app.mascote.cor_fixa, (0x12, 0x34, 0x56))
        app.definir_avatar("cores_avatares.ombro-cloud", "default")
        self.assertEqual(app.mascote.cor_atual(1.0), avatares.cor_hex(avatares.catalogo().cor_agente["claude"]))
        app.definir_avatar("cores_avatares.ombro-cloud", None)  # remove: volta a herdar o global
        self.assertEqual(app.mascote.cor_atual(1.0), (0x12, 0x34, 0x56))

    def test_ipc_grava_responde_publica_e_reinicia(self):
        d, app = self.daemon(layout="minimal-v2")
        r = d.m_config_update({"changes": {"cores_avatares.ombro-cloud": "3d7a4f", "cores_avatares.cubo": "rgb"}})
        self.assertEqual(sorted(r["applied"]), ["cores_avatares.cubo", "cores_avatares.ombro-cloud"])
        self.assertEqual(r["values"], {"cores_avatares.ombro-cloud": "#3D7A4F", "cores_avatares.cubo": "rgb"})
        self.assertEqual(self.no_arquivo()["cores_avatares"], {"ombro-cloud": "#3D7A4F", "cubo": "rgb"})
        self.assertEqual(d.m_config_get({})["values"]["cores_avatares"], {"cubo": "rgb", "ombro-cloud": "#3D7A4F"})
        self.assertEqual(d.m_providers({})["avatar_colors"], {"cubo": "rgb", "ombro-cloud": "#3D7A4F"})
        self.assertEqual(app.mascote._entrada, ("fixa", (0x3D, 0x7A, 0x4F)))
        cfg, _ = resolver({}, self.arquivo)  # reiniciar: o arquivo dá a mesma configuração
        self.assertEqual(cfg.cores_avatares, app.cfg.cores_avatares)
        r = d.m_config_update({"changes": {"cores_avatares.ombro-cloud": None}})
        self.assertEqual(r["values"], {"cores_avatares.ombro-cloud": None})
        self.assertEqual(d.m_providers({})["avatar_colors"], {"cubo": "rgb"})
        self.assertIsNone(app.mascote._entrada)

    def test_falha_ao_gravar_desfaz_a_cor(self):
        d, app = self.daemon(layout="minimal-v2")
        with mock.patch.object(preferencias, "gravar", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(ErroIPC) as e:
                d.m_config_update({"changes": {"cores_avatares.ombro-cloud": "#FF0000"}})
        self.assertIn("Nada mudou", str(e.exception))
        self.assertEqual((app.cfg.cores_avatares, app.mascote._entrada), ((), None))
        self.assertFalse(self.arquivo.exists())
        with self.assertRaises(ErroIPC):
            d.m_config_update({"changes": {"cores_avatares.ombro-cloud": "vermelho"}})
        self.assertEqual(app.cfg.cores_avatares, ())

    def test_providers_so_publica_o_confirmado_mesmo_com_a_gravacao_em_curso_e_falhando(self):
        """R4-05: o app.cfg é provisório durante o config.update; providers.list só vê o confirmado, e o laço segue livre."""
        d, app = self.daemon(layout="minimal-v2")
        d.m_config_update({"changes": {"cores_avatares.sereno": "#112233"}, "persist": False})  # valida, não persistente
        self.assertEqual(d.m_providers({})["avatar_colors"], {"sereno": "#112233"})
        entrou, soltar, resultado = threading.Event(), threading.Event(), []

        def gravar_travado(*a, **k):
            entrou.set()
            soltar.wait(5)
            raise OSError(28, "No space left on device")

        def atualizar():
            try:
                d.m_config_update({"changes": {"cores_avatares.cubo": "rgb"}})
            except ErroIPC as e:
                resultado.append(e)

        with mock.patch.object(preferencias, "gravar", gravar_travado):
            t = threading.Thread(target=atualizar)
            t.start()
            self.assertTrue(entrou.wait(5))
            self.assertEqual(dict(app.cfg.cores_avatares), {"cubo": "rgb", "sereno": "#112233"})  # em uso, provisório
            self.assertEqual(d.m_providers({})["avatar_colors"], {"sereno": "#112233"})  # ainda o confirmado
            self.quadro_novo(app)  # o laço da aplicação não ficou preso na gravação
            soltar.set()
            t.join(5)
        self.assertEqual(len(resultado), 1)
        self.assertIn("Nada mudou", str(resultado[0]))
        self.assertEqual(dict(app.cfg.cores_avatares), {"sereno": "#112233"})  # o desfazer preservou o não persistente
        self.assertEqual(d.m_providers({})["avatar_colors"], {"sereno": "#112233"})
        d.m_config_update({"changes": {"cores_avatares.cubo": "rgb"}, "persist": False})  # e um válido depois confirma
        self.assertEqual(d.m_providers({})["avatar_colors"], {"cubo": "rgb", "sereno": "#112233"})

    def _leituras_durante(self, d, atualizar):
        """Roda `atualizar` (uma thread, com a gravação travada) e lê config.get e providers.list sem parar até ela acabar:
        devolve todas as leituras (mapa de config.get, revisão, instância, mapa de providers.list)."""
        leituras, fim = [], threading.Event()

        def ler():
            while not fim.is_set():
                c, p = d.m_config_get({}), d.m_providers({})
                leituras.append((dict(c["values"]["cores_avatares"]), c["revision"], c["instance"], dict(p["avatar_colors"])))

        leitor = threading.Thread(target=ler)
        leitor.start()
        try:
            atualizar()
        finally:
            fim.set()
            leitor.join(5)
        return leituras

    def test_config_get_e_providers_publicam_o_mesmo_estado_confirmado_durante_a_gravacao(self):
        """R8-03: com a gravação travada (e depois com sucesso ou falha), nenhuma leitura mostra o mapa provisório com a
        revisão de antes, nem config.get diverge de providers.list; o mapa e a revisão são sempre do mesmo estado."""
        d, app = self.daemon(layout="minimal-v2")
        instancia = d.m_config_get({})["instance"]
        antes = d.m_config_get({})
        self.assertEqual((antes["values"]["cores_avatares"], antes["revision"]), ({}, 0))
        for falha in (False, True):
            entrou, soltar = threading.Event(), threading.Event()
            real = preferencias.gravar

            def gravar_travado(*a, **k):
                entrou.set()
                soltar.wait(5)
                if falha:
                    raise OSError(28, "No space left on device")
                return real(*a, **k)

            def atualizar():
                with mock.patch.object(preferencias, "gravar", gravar_travado):
                    t = threading.Thread(target=lambda: self._tentar(d, {"cores_avatares.cubo": "rgb"}))
                    t.start()
                    self.assertTrue(entrou.wait(5))
                    self.assertEqual(dict(app.cfg.cores_avatares), {"cubo": "rgb"})  # o provisório está em uso no app
                    c, p = d.m_config_get({}), d.m_providers({})  # e mesmo assim os leitores veem o confirmado
                    self.assertEqual((c["values"]["cores_avatares"], c["revision"], p["avatar_colors"]), ({}, 0 if not falha else d._revisao, {}))
                    soltar.set()
                    t.join(5)

            rev0 = d.m_config_get({})["revision"]
            leituras = self._leituras_durante(d, atualizar)
            self.assertTrue(leituras)
            depois = d.m_config_get({})
            if falha:
                self.assertEqual((depois["values"]["cores_avatares"], depois["revision"]), ({}, rev0))  # nada mudou
                permitidos = [({}, rev0)]
            else:
                self.assertEqual((depois["values"]["cores_avatares"], depois["revision"]), ({"cubo": "rgb"}, rev0 + 1))
                permitidos = [({}, rev0), ({"cubo": "rgb"}, rev0 + 1)]
            for mapa, rev, inst, publicado in leituras:
                self.assertIn((mapa, rev), permitidos)  # o mapa e a revisão nunca vêm de estados diferentes
                # providers.list é lido depois (dois pedidos, não um): é o mesmo estado ou o seguinte, nunca um provisório
                self.assertEqual(inst, instancia)
                self.assertIn(publicado, [m for m, _ in permitidos])
                if mapa == {"cubo": "rgb"}:
                    self.assertEqual(publicado, mapa)
            soltar.clear()
            entrou.clear()
            d.m_config_update({"changes": {"cores_avatares.cubo": None}})  # volta ao vazio para a próxima volta
            d.m_config_update({"changes": {"cores_avatares.cubo": None}, "persist": False})

    def _tentar(self, d, mudancas):
        try:
            d.m_config_update({"changes": mudancas})
        except ErroIPC:
            pass

    def test_persist_false_confirma_na_hora_e_preferencias_alheias_seguem(self):
        d, app = self.daemon(layout="minimal-v2")
        r = d.m_config_update({"changes": {"cores_avatares.sereno": "#112233", "girar": True}, "persist": False})
        c = d.m_config_get({})
        self.assertEqual((c["values"]["cores_avatares"], c["values"]["girar"], c["revision"]), ({"sereno": "#112233"}, True, r["revision"]))
        self.assertEqual(d.m_providers({})["avatar_colors"], {"sereno": "#112233"})
        self.assertEqual(c["pending"], {})

    def test_leitura_do_sistema_so_com_ciclo_e_nunca_por_quadro(self):
        d, app = self.daemon(layout="minimal-v2")
        chamadas = []
        app.movimento = MovimentoReduzido(leitor=lambda: chamadas.append(1) or False)
        n = app.quadros
        self.esperar(lambda: app.quadros > n + 5)
        self.assertEqual(chamadas, [])  # sem avatar em "rgb", o sistema nem é consultado
        d.m_config_update({"changes": {"cores_avatares.ombro-cloud": "rgb"}})
        n = app.quadros
        self.esperar(lambda: app.quadros > n + 20)
        self.assertEqual(len(chamadas), 1)  # vários quadros, uma leitura guardada
        self.assertFalse(app.mascote.movimento_reduzido)
        app.movimento = MovimentoReduzido(leitor=lambda: True)
        n = app.quadros
        self.esperar(lambda: app.quadros > n + 3)
        self.assertTrue(app.mascote.movimento_reduzido)
        self.assertEqual(app.mascote.cor_atual(3.0), app.mascote.cor_base)


if __name__ == "__main__":
    unittest.main()
