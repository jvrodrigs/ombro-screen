"""O pacote de animações: a preferência, a troca segura (ombro/animacoes.py), o mascote da telinha e a IPC.

A regra (ver o módulo): troca só com o Ombro já tendo lido os agentes, nenhuma sessão de nenhum agente em andamento
(trabalhando, esperando você ou com erro), tudo quieto há FIM_DA_CONCLUSAO s e o mascote da telinha num ponto seguro.
Só a última escolha vale; o que está em uso é gravado à parte do que foi escolhido.
"""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import mascotinhos as oa

import avatares
import config
import mascote
from config import ConfigInvalida, OmbroConfig, ler_arquivo, resolver, validar
from core.models import Activity, AgentSession, Attention
from ombro import preferencias
from ombro.animacoes import FIM_DA_CONCLUSAO, Animacoes, Atividade, gravar_aplicado, ler_aplicado
from ombro.app import Aplicacao

try:
    from .test_config_ao_vivo import Rodando
except ImportError:  # unittest discover -s tests: os testes são módulos soltos
    from test_config_ao_vivo import Rodando

PASTA_OA = Path(oa.__file__).resolve().parent
QUIETO = Atividade(True, ())


def sessao(pid, sid, activity=Activity.IDLE, attention=Attention.NONE):
    return AgentSession(pid, sid, activity=activity, attention=attention)


def atividade(*por_agente):
    """Atividade.das_sessoes a partir de (agente, [sessões])."""
    return Atividade.das_sessoes(True, por_agente)


def pronto(desejado="vivo", aplicado="vivo", arquivo=None):
    """Um coordenador que já viu tudo quieto há tempo (o laço chama pode_trocar a cada quadro)."""
    a = Animacoes(desejado, aplicado, arquivo)
    a.pode_trocar(0.0, QUIETO, lambda: True)
    return a


class Temporaria(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="om"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.pasta, ignore_errors=True))


# ---------- a preferência ----------
class Preferencia(Temporaria):
    def arquivo(self, dados) -> Path:
        arq = self.pasta / "config.json"
        arq.write_text(json.dumps(dados))
        return arq

    def test_padrao_e_o_vivo(self):
        self.assertEqual(OmbroConfig().pacote_animacao, "vivo")
        self.assertEqual(config.PACOTES_ANIMACAO, ("vivo", "classico"))
        self.assertEqual(mascote.PACOTE, "vivo")

    def test_config_antigo_sem_a_chave_fica_no_vivo_e_preserva_o_resto(self):
        antigo = {"versao": 1, "brilho": 40, "layout": "multiagente", "cor_mascote": "#3D7A4F", "cor_modo": "sorteada",
                  "girar": True, "avatars": {"codex": "orbita", "default": "sereno", "random": "sessao"},
                  "providers": {"cursor": {"enabled": False}}, "multi_agent": {"view": "focus", "focus_provider": "codex"}}
        cfg, avisos = resolver({}, self.arquivo(antigo))
        self.assertEqual(avisos, [])
        self.assertEqual(cfg.pacote_animacao, "vivo")
        self.assertEqual((cfg.brilho, cfg.layout, cfg.cor_mascote, cfg.cor_modo, cfg.girar),
                         (40, "multiagente", "#3D7A4F", "sorteada", True))
        self.assertEqual((cfg.avatar("codex"), cfg.avatar("padrao"), cfg.avatar_sorteio), ("orbita", "sereno", "sessao"))
        self.assertEqual((cfg.providers, cfg.vista, cfg.foco), (("claude", "codex"), "focus", "codex"))

    def test_classico_no_arquivo(self):
        cfg, avisos = resolver({}, self.arquivo({"pacote_animacao": "classico"}))
        self.assertEqual((cfg.pacote_animacao, avisos), ("classico", []))

    def test_pacote_em_avaliacao_ou_desconhecido_no_arquivo_vale_o_padrao_com_aviso(self):
        for valor, trecho in (("3d", "em avaliação"), ("terminal", "em avaliação"), ("vivoo", "não existe")):
            with self.subTest(valor):
                cfg, avisos = resolver({}, self.arquivo({"pacote_animacao": valor, "brilho": 20}))
                self.assertEqual((cfg.pacote_animacao, cfg.brilho), ("vivo", 20))  # o Ombro abre e o resto vale
                self.assertEqual(len(avisos), 1)
                self.assertIn(trecho, avisos[0])

    def test_tipo_errado_no_arquivo_e_recusado(self):
        for valor in (42, None, True, ["vivo"]):
            with self.subTest(valor), self.assertRaises(ConfigInvalida):
                ler_arquivo(self.arquivo({"pacote_animacao": valor}))

    def test_pela_ipc_e_pela_linha_de_comando_so_os_oferecidos(self):
        self.assertEqual(validar("pacote_animacao", "classico"), "classico")
        for valor in ("3d", "terminal", "VIVO", "", 1):
            with self.subTest(valor), self.assertRaises(ConfigInvalida):
                preferencias.validar_mudancas({"pacote_animacao": valor})

    def test_esquema(self):
        o = preferencias.POR_CHAVE["pacote_animacao"]
        self.assertEqual((o.grupo, o.tipo, o.ao_vivo, o.opcoes, o.nulo), ("appearance", "enum", True, ("vivo", "classico"), False))
        self.assertIn("pacote_animacao", preferencias.AO_VIVO)

    def test_gravar_so_muda_a_chave(self):
        arq = self.arquivo({"versao": 1, "brilho": 40, "avatars": {"codex": "orbita"}, "chave_de_outra_versao": 1})
        preferencias.gravar(arq, {"pacote_animacao": "classico"})
        dados = json.loads(arq.read_text())
        self.assertEqual(dados, {"versao": 1, "brilho": 40, "avatars": {"codex": "orbita"}, "chave_de_outra_versao": 1,
                                 "pacote_animacao": "classico"})


# ---------- o que está em uso, gravado à parte ----------
class Aplicado(Temporaria):
    def test_grava_e_le(self):
        arq = self.pasta / "animacao.json"
        self.assertIsNone(ler_aplicado(arq))  # primeira vez
        gravar_aplicado(arq, "classico")
        self.assertEqual(ler_aplicado(arq), "classico")
        self.assertEqual(json.loads(arq.read_text()), {"versao": 1, "aplicado": "classico"})
        self.assertEqual(oct(arq.stat().st_mode & 0o777), "0o600")

    def test_arquivo_estragado_ou_desconhecido_volta_ao_escolhido(self):
        arq = self.pasta / "animacao.json"
        for texto in ("{", "[]", '{"aplicado": "3d"}', '{"aplicado": 7}'):
            with self.subTest(texto):
                arq.write_text(texto)
                self.assertIsNone(ler_aplicado(arq))

    def test_falha_ao_gravar_nao_derruba(self):
        gravar_aplicado(self.pasta / "nao" / "existe" / "\0" / "x.json", "vivo")  # só vai para o log


# ---------- a regra da troca ----------
class TrocaSegura(unittest.TestCase):
    def test_cenario_a_ocioso_troca_na_hora(self):
        a = pronto()
        a.pedir("classico")
        self.assertTrue(a.pendente)
        self.assertTrue(a.pode_trocar(10.0, QUIETO, lambda: True))
        a.confirmar("classico")
        self.assertFalse(a.pendente)
        self.assertEqual(a.como_dict()["applied"], "classico")

    def test_cenario_b_trabalhando_fica_pendente(self):
        a = pronto()
        a.pedir("classico")
        ativ = atividade(("claude", [sessao("claude", "s1", Activity.WORKING)]))
        self.assertFalse(a.pode_trocar(10.0, ativ, lambda: True))
        d = a.como_dict()
        self.assertEqual((d["desired"], d["applied"], d["pending"], d["waiting_for"], d["busy_providers"]),
                         ("classico", "vivo", True, "activity", ["claude"]))

    def test_cenario_c_esperando_resposta_aprovacao_ou_com_erro_fica_pendente(self):
        for att in (Attention.WAITING_USER, Attention.WAITING_APPROVAL, Attention.ERROR):
            with self.subTest(att):
                a = pronto()
                a.pedir("classico")
                ativ = atividade(("codex", [sessao("codex", "s", Activity.IDLE, att)]))
                self.assertFalse(a.pode_trocar(10.0, ativ, lambda: True))
                self.assertEqual(a.motivo, "activity")

    def test_cenario_d_depois_da_atividade_espera_a_comemoracao(self):
        a = pronto()
        a.pedir("classico")
        trabalhando = atividade(("claude", [sessao("claude", "s1", Activity.WORKING)]))
        self.assertFalse(a.pode_trocar(10.0, trabalhando, lambda: True))
        self.assertFalse(a.pode_trocar(11.0, QUIETO, lambda: True))  # acabou de terminar
        self.assertEqual(a.motivo, "settling")
        self.assertFalse(a.pode_trocar(11.0 + FIM_DA_CONCLUSAO - .1, QUIETO, lambda: True))
        self.assertFalse(a.pode_trocar(11.0 + FIM_DA_CONCLUSAO, QUIETO, lambda: False))  # o mascote ainda comemora
        self.assertEqual(a.motivo, "mascot")
        self.assertTrue(a.pode_trocar(11.0 + FIM_DA_CONCLUSAO + 1, QUIETO, lambda: True))

    def test_cenario_e_varios_agentes(self):
        casos = {
            "claude trabalhando, codex ocioso": [("claude", [sessao("claude", "a", Activity.WORKING)]),
                                                 ("codex", [sessao("codex", "b")])],
            "claude esperando, cursor trabalhando": [("claude", [sessao("claude", "a", attention=Attention.WAITING_USER)]),
                                                     ("cursor", [sessao("cursor", "c", Activity.WORKING)])],
            "duas sessões ativas no mesmo agente": [("claude", [sessao("claude", "a", Activity.WORKING),
                                                                sessao("claude", "b", Activity.WORKING)])],
            "uma terminou, a outra segue": [("claude", [sessao("claude", "a"), sessao("claude", "b", Activity.WORKING)])],
            "só o codex (o cartão em foco pode ser o do claude)": [("claude", []),
                                                                   ("codex", [sessao("codex", "b", Activity.WORKING)])],
        }
        for nome, por_agente in casos.items():
            with self.subTest(nome):
                a = pronto()
                a.pedir("classico")
                ativ = atividade(*por_agente)
                self.assertFalse(a.pode_trocar(50.0, ativ, lambda: True))
                self.assertEqual(a.motivo, "activity")
        ativ = atividade(("claude", [sessao("claude", "a", attention=Attention.WAITING_USER)]),
                         ("cursor", [sessao("cursor", "c", Activity.WORKING)]))
        self.assertEqual(ativ.agentes, (("claude", 1), ("cursor", 1)))

    def test_a_troca_so_vem_quando_todos_param(self):
        a = pronto()
        a.pedir("classico")
        claude = [sessao("claude", "a", Activity.WORKING)]
        codex = [sessao("codex", "b", Activity.WORKING)]
        self.assertFalse(a.pode_trocar(1, atividade(("claude", claude), ("codex", codex)), lambda: True))
        self.assertFalse(a.pode_trocar(2, atividade(("claude", []), ("codex", codex)), lambda: True))  # claude acabou
        self.assertEqual(a.agentes_ocupados, ("codex",))
        self.assertFalse(a.pode_trocar(3, atividade(("claude", []), ("codex", [])), lambda: True))  # codex acabou agora
        self.assertTrue(a.pode_trocar(3 + FIM_DA_CONCLUSAO, atividade(("claude", []), ("codex", [])), lambda: True))

    def test_cenario_f_so_a_ultima_escolha_vale(self):
        a = pronto()
        ocupado = atividade(("claude", [sessao("claude", "a", Activity.WORKING)]))
        for p in ("classico", "vivo", "classico", "vivo"):
            a.pedir(p)
            a.pode_trocar(5.0, ocupado, lambda: True)
        self.assertFalse(a.pendente)  # voltou ao que está em uso: nada a trocar
        self.assertIsNone(a.como_dict()["waiting_for"])
        a.pedir("classico")
        a.pode_trocar(6.0, ocupado, lambda: True)
        self.assertEqual((a.desejado, a.aplicado, a.pendente), ("classico", "vivo", True))

    def test_antes_da_primeira_leitura_nao_troca(self):
        a = Animacoes("classico", "vivo")
        self.assertFalse(a.pode_trocar(100.0, Atividade(False, ()), lambda: True))
        self.assertEqual(a.motivo, "starting")

    def test_pacote_que_nao_carrega_nao_e_tentado_de_novo(self):
        a = pronto()
        a.pedir("classico")
        a.recusar("classico", RuntimeError("quebrado"))
        self.assertFalse(a.pendente)
        self.assertEqual((a.como_dict()["failed"], a.aplicado), ("classico", "vivo"))
        a.pedir("vivo")
        a.pedir("classico")  # escolher de novo tenta de novo
        self.assertTrue(a.pendente)

    def test_valores_fora_da_escolha(self):
        self.assertEqual((Animacoes("3d", "terminal").desejado, Animacoes("3d", "terminal").aplicado), ("vivo", "vivo"))
        with self.assertRaises(ValueError):
            Animacoes().pedir("3d")

    def test_o_que_a_ipc_mostra(self):
        d = Animacoes().como_dict()
        self.assertEqual(d["choices"], ["vivo", "classico"])
        self.assertEqual([u["id"] for u in d["unavailable"]], ["3d", "terminal"])
        self.assertTrue(all(u["reason"] for u in d["unavailable"]))


# ---------- o mascote da telinha ----------
class MascoteDaTelinha(unittest.TestCase):
    def test_a_biblioteca_de_cada_pacote(self):
        classico = oa.Biblioteca.carregar(pacote="classico")
        for p in mascote.PACOTES:
            b, dela = mascote.biblioteca(p), oa.Biblioteca.carregar(pacote=p)
            self.assertEqual(set(b.estados()), set(mascote.ESTADOS))
            for e in mascote.ESTADOS:
                fonte = classico if e in mascote.DO_CLASSICO else dela  # alerta e reset: o clássico, em qualquer pacote
                for t in (0.0, 0.7, 2.3, 6.1):
                    self.assertEqual(b.parametros(e, t), fonte.parametros(e, t), f"{p} {e} {t}")
        self.assertIs(mascote.biblioteca("vivo"), mascote.BIBLIOTECA)
        for fora in mascote.EM_AVALIACAO + ("nada",):
            with self.assertRaises(ValueError):
                mascote.biblioteca(fora)

    def test_momento_seguro(self):
        m = mascote.Mascote()
        m.ir_para("ocioso", 0.0)
        self.assertTrue(m.momento_seguro(10.0))
        m.ir_para("trabalhando", 10.0)
        self.assertFalse(m.momento_seguro(60.0))
        m.ir_para("esperando", 60.0)
        self.assertFalse(m.momento_seguro(120.0))
        m.ir_para("reset", 120.0)
        self.assertFalse(m.momento_seguro(122.0))
        m.ir_para("concluido", 130.0)
        self.assertFalse(m.momento_seguro(130.0 + mascote.FIM_DA_CONCLUSAO - .05))  # ainda comemorando
        self.assertTrue(m.momento_seguro(130.0 + mascote.FIM_DA_CONCLUSAO))
        m.ir_para("ocioso", 200.0)
        self.assertFalse(m.momento_seguro(200.2))  # no meio da mistura de estados
        self.assertTrue(m.momento_seguro(200.0 + mascote.DURACAO_MISTURA))
        m.ir_para("alerta", 300.0)
        self.assertTrue(m.momento_seguro(301.0))

    def test_a_troca_e_uma_mistura_sem_salto(self):
        for estado in ("ocioso", "concluido"):
            with self.subTest(estado):
                m = mascote.Mascote(cor_fixa=(200, 100, 80))
                m.ir_para(estado, 0.0)
                agora = 7.3
                antes, _ = m.parametros_atuais(agora)
                m.usar_pacote("classico", agora)
                self.assertFalse(m.momento_seguro(agora + 0.1))  # trocando
                logo, u = m.parametros_atuais(agora)
                self.assertEqual(logo, antes)  # no instante da troca, a pose é a de antes: nada salta
                self.assertEqual(u, 1.0)  # a troca de pacote não apaga os extras do estado
                depois, u = m.parametros_atuais(agora + mascote.DURACAO_MISTURA)
                self.assertEqual(depois, mascote.biblioteca("classico").parametros(estado, agora + mascote.DURACAO_MISTURA))
                self.assertEqual(u, 1.0)
                self.assertEqual(m.pacote, "classico")
                # o estado não recomeçou: o concluído não comemora de novo
                self.assertEqual(m.inicio, 0.0)

    def test_os_extras_se_cruzam_sem_sumir(self):
        """Na telinha, o quadro da troca começa igual ao do pacote de antes, termina igual ao do novo, e no meio o que
        aparece nunca é menos que o de um pacote só (o ✓, o zzZ ou o "?" não piscam)."""
        import numpy as np

        def puro(pacote, estado, t):
            m = mascote.Mascote(cor_fixa=(200, 100, 80))
            m.usar_pacote(pacote, 0.0, ja=True)
            m.ir_para(estado, 0.0)
            return visto(m.camada(t))

        def visto(img):  # o que aparece: a cor já multiplicada pelo alfa (a borda quase transparente não conta)
            return np.asarray(img.convert("RGBa"), dtype=int)

        def visiveis(a):
            return int((a[..., 3] > 40).sum())

        for estado, de, para in (("concluido", "vivo", "classico"), ("ocioso", "classico", "vivo"),
                                 ("concluido", "classico", "vivo")):
            with self.subTest(f"{estado} {de}->{para}"):
                agora = 7.3
                m = mascote.Mascote(cor_fixa=(200, 100, 80))
                m.usar_pacote(de, 0.0, ja=True)
                m.ir_para(estado, 0.0)
                m.camada(agora - 0.2)
                m.usar_pacote(para, agora)
                self.assertLessEqual(np.abs(visto(m.camada(agora)) - puro(de, estado, agora)).max(), 1)
                minimo = min(visiveis(puro(de, estado, agora)), visiveis(puro(para, estado, agora + .5)))
                for dt in (0.05, 0.15, 0.25, 0.35, 0.45):
                    self.assertGreaterEqual(visiveis(visto(m.camada(agora + dt))), minimo * 0.97, dt)
                fim = agora + mascote.DURACAO_MISTURA
                self.assertLessEqual(np.abs(visto(m.camada(fim)) - puro(para, estado, fim)).max(), 1)
                self.assertIsNone(m._troca_pacote)

    def test_a_mistura_anda_aos_poucos(self):
        m = mascote.Mascote(cor_fixa=(200, 100, 80))
        m.ir_para("ocioso", 0.0)
        m.usar_pacote("classico", 10.0)
        anterior = None
        for i in range(0, 11):  # a 20 quadros por segundo durante a troca
            p, _ = m.parametros_atuais(10.0 + i * 0.05)
            if anterior is not None:
                for k in ("dx", "dy", "rot"):
                    self.assertLess(abs(p[k] - anterior[k]), 2.5, f"{k} no quadro {i}")
                for k in ("sx", "sy"):
                    self.assertLess(abs(p[k] - anterior[k]), 0.03, f"{k} no quadro {i}")
            anterior = p

    def test_fora_da_troca_e_o_animador_da_biblioteca(self):
        m = mascote.Mascote(cor_fixa=(1, 2, 3))
        for e in mascote.ESTADOS:
            m.ir_para(e, 1.0)
            for t in (1.0, 1.2, 2.0, 5.5):
                self.assertEqual(m.parametros_atuais(t), oa.Animador.parametros_atuais(m, t))
                self.assertEqual(m.camada(t).tobytes(), oa.Animador.camada(m, t).tobytes())

    def test_ja_troca_sem_misturar(self):
        m = mascote.Mascote()
        m.usar_pacote("classico", 0.0, ja=True)
        self.assertIsNone(m._troca_pacote)
        self.assertTrue(m.momento_seguro(5.0))

    def test_os_12_avatares_nos_dois_pacotes(self):
        cat = avatares.catalogo()
        self.assertEqual(len(cat.avatares), 12)
        for p in mascote.PACOTES:
            for av in cat.avatares.values():
                m = mascote.Mascote(cor_fixa=(200, 100, 80))
                m.usar(av, None, 0.0, ja=True)
                m.usar_pacote(p, 0.0, ja=True)
                for e in mascote.ESTADOS:
                    m.ir_para(e, 0.0)
                    img = m.camada(2.4)
                    self.assertIsNotNone(img.getbbox(), f"{p} {av.id} {e}")
                    self.assertEqual(img.size, (mascote.COL_W * mascote.S, mascote.COL_H * mascote.S))

    def test_fim_da_conclusao_cobre_a_comemoracao_de_cada_pacote(self):
        self.assertEqual(mascote.FIM_DA_CONCLUSAO, FIM_DA_CONCLUSAO)
        for p in mascote.PACOTES:
            pasta = PASTA_OA / ("animacoes" if p == "classico" else f"animacoes_{p}")
            dados = json.loads((pasta / "concluido.json").read_text())
            fins = []
            for trilha in dados["trilhas"]:
                if trilha.get("ciclo"):
                    continue  # repete o tempo todo (um aceno): não é a comemoração
                fases = trilha["fases"]
                fins += [f["de"] + f.get("dur", 0) for f in fases[:-1]] + [fases[-1]["de"]]  # a última, sem fim, é o balanço
            for x in dados["extras"]:
                if "dur" in x:
                    fins.append(x.get("de", 0) + x["dur"])
                fins += [x.get("atraso", 0) + x.get("desenha", 0), x.get("cresce", 0)]
            with self.subTest(p):
                self.assertLessEqual(max(fins), mascote.FIM_DA_CONCLUSAO)


# ---------- o Ombro rodando: a Aplicacao e a IPC ----------
def quieto(app):
    """Sem atividade nos agentes e com o mascote num ponto seguro (o --demo inventa atividade)."""
    app.atividade = lambda: QUIETO
    app.mascote.momento_seguro = lambda agora: True


class NaAplicacao(Temporaria):
    def app(self, **cfg):
        a = Aplicacao(OmbroConfig(demo=True, **cfg))
        self.addCleanup(a.fechar)
        return a

    def test_comeca_no_escolhido_no_demo(self):
        a = self.app(pacote_animacao="classico")
        self.assertEqual((a.mascote.pacote, a.animacoes.aplicado, a.animacoes.pendente), ("classico", "classico", False))
        self.assertIs(a.mascote.biblioteca, mascote.biblioteca("classico"))

    def test_ocioso_troca_na_hora_sem_reiniciar(self):
        a = self.app()
        quieto(a)
        a.trocar_pacote_se_seguro(0.0)
        mascote_antes = a.mascote
        a.aplicar_cfg(replace(a.cfg, pacote_animacao="classico"))
        self.assertIs(a.mascote, mascote_antes)  # o mesmo mascote: nada foi reiniciado
        self.assertEqual((a.mascote.pacote, a.animacao_publicada["applied"], a.animacao_publicada["pending"]),
                         ("classico", "classico", False))
        self.assertTrue(a._redesenhar)  # a telinha recebe o quadro novo já

    def test_trabalhando_fica_pendente_e_entra_depois(self):
        a = self.app()
        quieto(a)
        a.trocar_pacote_se_seguro(0.0)
        a.atividade = lambda: atividade(("codex", [sessao("codex", "x", Activity.WORKING)]))
        a.aplicar_cfg(replace(a.cfg, pacote_animacao="classico"))
        self.assertEqual(a.mascote.pacote, "vivo")
        self.assertEqual((a.animacao_publicada["pending"], a.animacao_publicada["busy_providers"]), (True, ["codex"]))
        a.atividade = lambda: QUIETO
        self.assertFalse(a.trocar_pacote_se_seguro(100.0))  # acabou agora
        self.assertTrue(a.trocar_pacote_se_seguro(100.0 + FIM_DA_CONCLUSAO))
        self.assertEqual(a.mascote.pacote, "classico")

    def test_layout_sem_mascote_so_olha_os_agentes(self):
        a = self.app(layout="classico")
        a.atividade = lambda: QUIETO
        a.mascote.ir_para("trabalhando", 0.0)  # parado: o clássico não desenha o mascote
        a.trocar_pacote_se_seguro(0.0)
        a.aplicar_cfg(replace(a.cfg, pacote_animacao="classico"))
        self.assertEqual(a.mascote.pacote, "classico")

    def test_pacote_que_nao_carrega_mantem_o_em_uso(self):
        a = self.app()
        quieto(a)
        a.trocar_pacote_se_seguro(0.0)
        with mock.patch.object(mascote, "biblioteca", side_effect=OSError("arquivo sumiu")):
            a.aplicar_cfg(replace(a.cfg, pacote_animacao="classico"))
        self.assertEqual(a.mascote.pacote, "vivo")
        self.assertEqual((a.animacao_publicada["failed"], a.animacao_publicada["applied"]), ("classico", "vivo"))
        a.trocar_pacote_se_seguro(10.0)  # não fica tentando a cada quadro
        self.assertEqual(a.mascote.pacote, "vivo")

    def test_atividade_vem_de_todos_os_agentes(self):
        a = self.app(layout="minimal-v2")  # o layout de um agente só (o Claude): mesmo assim, todos contam
        store = a.monitor.store
        a.coletado_em = object()
        with mock.patch.object(store, "sessions", side_effect=lambda pid=None: {
                "codex": [sessao("codex", "x", Activity.WORKING)]}.get(pid, [])):
            self.assertEqual(a.atividade().agentes, (("codex", 1),))


class Reinicio(Temporaria):
    def setUp(self):
        super().setUp()
        amb = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta)})
        amb.start()
        self.addCleanup(amb.stop)

    def app(self, **cfg):
        # como o ombrod de verdade (sem --demo), mas sem coletar nem abrir nada
        with mock.patch("ombro.app.Monitor.montar") as montar, mock.patch("ombro.app.ColetorHardware"):
            montar.return_value = mock.Mock(registry=[], store=mock.Mock(sessions=lambda pid=None: []))
            a = Aplicacao(OmbroConfig(**cfg))
        return a

    def test_pendente_ao_reiniciar_comeca_no_que_estava_em_uso(self):
        gravar_aplicado(self.pasta / "animacao.json", "vivo")
        a = self.app(pacote_animacao="classico")  # escolhido antes de reiniciar, ainda não aplicado
        self.assertEqual((a.mascote.pacote, a.animacoes.desejado, a.animacoes.pendente), ("vivo", "classico", True))
        # a atividade retomada depois do reinício segura a troca
        a.coletado_em = object()
        a.atividade = lambda: atividade(("claude", [sessao("claude", "s", Activity.WORKING)]))
        a.mascote.momento_seguro = lambda agora: True
        self.assertFalse(a.trocar_pacote_se_seguro(0.0))
        self.assertEqual(a.animacao_publicada["waiting_for"], "activity")
        a.atividade = lambda: QUIETO
        a.trocar_pacote_se_seguro(1.0)
        self.assertTrue(a.trocar_pacote_se_seguro(1.0 + FIM_DA_CONCLUSAO))
        self.assertEqual(ler_aplicado(self.pasta / "animacao.json"), "classico")  # gravado para o próximo reinício

    def test_antes_da_primeira_leitura_nao_troca(self):
        gravar_aplicado(self.pasta / "animacao.json", "vivo")
        a = self.app(pacote_animacao="classico")
        a.mascote.momento_seguro = lambda agora: True
        self.assertFalse(a.trocar_pacote_se_seguro(100.0))  # coletado_em ainda None
        self.assertEqual(a.animacao_publicada["waiting_for"], "starting")

    def test_primeira_vez_comeca_no_vivo_de_antes(self):
        a = self.app(pacote_animacao="classico")  # sem animacao.json: o Ombro de antes desta opção usava o vivo
        self.assertEqual((a.mascote.pacote, a.animacoes.pendente), ("vivo", True))
        b = self.app()
        self.assertEqual((b.mascote.pacote, b.animacoes.pendente), ("vivo", False))


class PelaIPC(Rodando):
    def test_config_update_aplica_na_hora_e_grava(self):
        d, app = self.daemon()
        quieto(app)
        app.executar(lambda: app.trocar_pacote_se_seguro(0.0))
        n = app.quadros
        r = d.m_config_update({"changes": {"pacote_animacao": "classico"}})
        self.assertEqual(r["applied"], ["pacote_animacao"])
        self.assertEqual(r["values"], {"pacote_animacao": "classico"})
        self.assertEqual((r["animation"]["applied"], r["animation"]["pending"]), ("classico", False))
        self.assertEqual(self.no_arquivo()["pacote_animacao"], "classico")
        self.esperar(lambda: app.quadros > n)  # o laço seguiu, sem reiniciar
        g = d.m_config_get({})
        self.assertEqual((g["values"]["pacote_animacao"], g["animation"]["applied"]), ("classico", "classico"))
        self.assertEqual(d.m_providers({})["animation"]["applied"], "classico")
        self.assertEqual(d._display()["mascot"]["package"], "classico")

    def test_config_update_com_atividade_fica_pendente(self):
        d, app = self.daemon()
        quieto(app)
        app.atividade = lambda: atividade(("claude", [sessao("claude", "s", attention=Attention.WAITING_APPROVAL)]))
        r = d.m_config_update({"changes": {"pacote_animacao": "classico"}})
        self.assertEqual(r["values"], {"pacote_animacao": "classico"})  # a escolha vale e está gravada
        a = r["animation"]
        self.assertEqual((a["desired"], a["applied"], a["pending"], a["waiting_for"]), ("classico", "vivo", True, "activity"))
        self.assertEqual(app.mascote.pacote, "vivo")
        self.assertEqual(d.m_providers({})["animation"]["pending"], True)

    def test_valor_invalido_pela_ipc(self):
        from ombro.ipc import ErroIPC

        d, app = self.daemon()
        with self.assertRaises(ErroIPC):
            d.m_config_update({"changes": {"pacote_animacao": "3d"}})
        self.assertEqual(app.cfg.pacote_animacao, "vivo")

    def test_esquema_pela_ipc(self):
        d, _ = self.daemon()
        o = next(x for x in d.m_config_schema({})["options"] if x["key"] == "pacote_animacao")
        self.assertEqual((o["choices"], o["live"], o["group"]), (["vivo", "classico"], True, "appearance"))


if __name__ == "__main__":
    unittest.main()
