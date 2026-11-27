"""O agente de cada layout de um agente só (Ombro/automático, clássico e Minimal v2): preferências independentes,
o agente "automatic", as condições mostradas sem zeros inventados e a troca de fonte sem "concluído" nem reset falso.
Dados postos direto no StateStore; nenhum agente de verdade."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import avatares
import ombro.app as modulo
from apresentacao import escolher_automatico, quadro_agente, uso_do_agente, visao_agente
from coletores.hardware import Hardware
from config import AGENTES_DE_LAYOUT, PROVIDERS, ConfigInvalida, OmbroConfig, ler_arquivo, resolver
from core.models import (Activity, AgentSession, Attention, ProviderSnapshot, QuotaReport, QuotaWindow,
                         SnapshotStatus, UsageMetrics)
from ombro import preferencias
from ombro.daemon import Daemon

A, T = Activity, Attention
AGORA = datetime.now().replace(microsecond=0)
HW = Hardware(cpu_pct=30.0, ram_usada=8 * 1024 ** 3, ram_total=16 * 1024 ** 3)
SIMPLES = ("auto", "ombro", "classico", "minimal-v2")
CHAVE = {"auto": "agente_auto", "ombro": "agente_auto", "classico": "agente_classico", "minimal-v2": "agente_minimal"}


def sessao(pid, sid="s1", atividade=A.IDLE, atencao=T.NONE, minutos=3, projeto=None, **extra):
    quando = AGORA - timedelta(minutes=minutos)
    return AgentSession(pid, sid, atividade, atencao, project=projeto or f"proj-{pid}", status_since=quando,
                        updated_at=quando, **extra)


def snap(pid, *sessoes, **extra):
    return ProviderSnapshot(pid, AGORA, sessions=tuple(sessoes), **extra)


def do_claude(*sessoes, cinco_horas=95.0):
    return snap("claude", *sessoes,
                usage=UsageMetrics("today", 5000, 700, 0, 3),
                quotas=QuotaReport("statusline", AGORA, False, (
                    QuotaWindow("5h", "5h", cinco_horas, AGORA + timedelta(hours=2)),
                    QuotaWindow("7d", "7d", 40.0, AGORA + timedelta(days=3)))))


class Base(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="om", dir="/tmp" if os.path.isdir("/tmp") else None))
        self.addCleanup(shutil.rmtree, self.pasta, True)
        amb = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta), "HOME": str(self.pasta)})
        amb.start()
        self.addCleanup(amb.stop)
        self.arquivo = self.pasta / "config.json"
        self.dados = {}
        self.t = 100.0

    def app(self, **cfg):
        app = modulo.Aplicacao(OmbroConfig(demo=True, arquivo=self.arquivo, **cfg))
        self.addCleanup(app.fechar)

        def coletar():
            for s in self.dados.values():
                app.monitor.store.apply_snapshot(s)

        app.monitor.atualizar_todos = coletar
        return app

    def por(self, *snaps):
        for s in snaps:
            self.dados[s.provider_id] = s

    def volta(self, app, passo=5.0):
        """Uma volta do laço: coleta (se for hora), avatar e quadro."""
        self.t += passo
        app._uso = app.ler_claude()
        app.atualizar_avatar(self.t, ja=app._trocou_fonte)
        app._trocou_fonte = False
        return app.quadro(HW, app._uso, self.t)

    def desenhar(self, app, passo=0.2):
        """Um quadro sem coletar de novo (como entre duas coletas, ou logo depois de uma preferência)."""
        self.t += passo
        if app._uso is None:
            app._uso = app.ler_claude()
        return app.quadro(HW, app._uso, self.t)


class Configuracao(Base):
    def test_sem_as_chaves_vale_o_claude_e_o_arquivo_antigo_abre(self):
        self.arquivo.write_text(json.dumps({"versao": 1, "layout": "minimal-v2", "brilho": 40}))
        cfg, avisos = resolver({}, self.arquivo)
        self.assertEqual((cfg.agente_auto, cfg.agente_classico, cfg.agente_minimal, avisos),
                         ("claude", "claude", "claude", []))
        self.assertEqual(cfg.agente_do_layout, "claude")

    def test_os_valores_aceitos_e_o_invalido(self):
        self.assertEqual(AGENTES_DE_LAYOUT, ("automatic", "claude", "codex", "cursor", "opencode"))
        for valor in AGENTES_DE_LAYOUT:
            self.arquivo.write_text(json.dumps({"agente_classico": valor}))
            self.assertEqual(ler_arquivo(self.arquivo)[0], {"agente_classico": valor})
        for ruim in ("auto", "Claude", "", None, 1):
            self.arquivo.write_text(json.dumps({"agente_auto": ruim}))
            with self.assertRaises(ConfigInvalida, msg=repr(ruim)):
                ler_arquivo(self.arquivo)

    def test_auto_e_ombro_dividem_a_chave_e_o_multiagente_nao_tem(self):
        base = OmbroConfig(agente_auto="codex", agente_classico="cursor", agente_minimal="opencode")
        esperado = {"auto": "codex", "ombro": "codex", "classico": "cursor", "minimal-v2": "opencode", "multiagente": None}
        from dataclasses import replace

        self.assertEqual({lay: replace(base, layout=lay).agente_do_layout for lay in esperado}, esperado)

    def test_o_esquema_oferece_as_tres_ao_vivo(self):
        opcoes = {o["key"]: o for o in preferencias.esquema()["options"]}
        for chave in ("agente_auto", "agente_classico", "agente_minimal"):
            self.assertEqual((opcoes[chave]["type"], opcoes[chave]["live"], opcoes[chave]["choices"]),
                             ("enum", True, list(AGENTES_DE_LAYOUT)), chave)
        with self.assertRaises(ConfigInvalida):
            preferencias.validar_mudancas({"agente_minimal": "gemini"})


class PelaIPC(Base):
    def daemon(self, **cfg):
        app = self.app(**cfg)
        d = Daemon(app.cfg)
        d.app, d.estado = app, "running"
        d._no_laco = lambda fn: fn()  # o teste é a própria thread do laço
        return d, app

    def test_cada_layout_guarda_o_seu_vale_na_hora_e_sobrevive_ao_reinicio(self):
        self.por(do_claude(), snap("codex", sessao("codex")), snap("cursor", sessao("cursor")),
                 snap("opencode", sessao("opencode")))
        self.arquivo.write_text(json.dumps({"versao": 1, "brilho": 33}))
        d, app = self.daemon()
        for chave, valor in (("agente_auto", "codex"), ("agente_classico", "cursor"), ("agente_minimal", "opencode")):
            r = d.m_config_update({"changes": {chave: valor}, "persist": True})
            self.assertEqual((r["applied"], r["restart_required"], r["values"]), ([chave], [], {chave: valor}))
        gravado = json.loads(self.arquivo.read_text())
        self.assertEqual({k: gravado[k] for k in ("brilho", "agente_auto", "agente_classico", "agente_minimal")},
                         {"brilho": 33, "agente_auto": "codex", "agente_classico": "cursor", "agente_minimal": "opencode"})
        for layout, esperado in (("auto", "codex"), ("classico", "cursor"), ("minimal-v2", "opencode"), ("ombro", "codex")):
            d.m_layout({"layout": layout, "persist": True})
            self.desenhar(app)
            self.assertEqual(d._display()["agent"], {"selected": esperado, "effective": esperado, "condition": "ocioso"})
            self.assertEqual(app.sujeito()[0], esperado)
        valores = d.m_config_get({})["values"]
        self.assertEqual((valores["agente_auto"], valores["agente_classico"], valores["agente_minimal"]),
                         ("codex", "cursor", "opencode"))
        reiniciado = resolver({}, self.arquivo)[0]
        self.assertEqual((reiniciado.agente_auto, reiniciado.agente_classico, reiniciado.agente_minimal),
                         ("codex", "cursor", "opencode"))

    def test_valor_invalido_nao_muda_nada(self):
        from ombro.ipc import ErroIPC

        d, app = self.daemon()
        with self.assertRaises(ErroIPC):
            d.m_config_update({"changes": {"agente_auto": "gemini"}, "persist": True})
        self.assertEqual(app.cfg.agente_auto, "claude")
        self.assertFalse(self.arquivo.exists())

    def test_o_agente_publicado_e_o_do_ultimo_quadro_e_o_multiagente_nao_publica(self):
        self.por(do_claude(), snap("codex", sessao("codex")))
        d, app = self.daemon()
        self.volta(app)
        self.assertEqual(d._display()["agent"]["effective"], "claude")
        d.m_config_update({"changes": {"agente_auto": "codex"}, "persist": False})
        self.assertEqual(d._display()["agent"]["effective"], "claude")  # ainda é o quadro de antes
        self.desenhar(app)
        self.assertEqual(d._display()["agent"]["effective"], "codex")
        d.m_layout({"layout": "multiagente"})
        self.desenhar(app)
        self.assertIsNone(d._display()["agent"])


class CadaAgenteEmCadaLayout(Base):
    def preparar(self):
        self.por(do_claude(sessao("claude", atividade=A.WORKING)),
                 snap("codex", sessao("codex", "a", A.WORKING), sessao("codex", "b", A.IDLE, T.WAITING_APPROVAL, 9)),
                 snap("cursor", sessao("cursor", atividade=A.WORKING)),
                 snap("opencode", sessao("opencode", atividade=A.IDLE, atencao=T.ERROR)))

    def test_dados_avatar_cor_e_tamanho_de_cada_um(self):
        self.preparar()
        cat = avatares.catalogo()
        for layout in SIMPLES:
            for paisagem in ((False, True) if layout == "classico" else (False,)):
                imagens = {}
                for pid in PROVIDERS:
                    with self.subTest(layout=layout, pid=pid, paisagem=paisagem):
                        app = self.app(layout=layout, paisagem=paisagem, **{CHAVE[layout]: pid})
                        img = self.volta(app)
                        esperado = (320, 480) if layout == "classico" and not paisagem else (480, 320)
                        self.assertEqual(img.size, esperado)
                        imagens[pid] = img.tobytes()
                        self.assertEqual(app.agente_efetivo, pid)
                        self.assertEqual(app.sujeito()[0], pid)
                        av = app.avatar_de(pid)
                        self.assertEqual(app.mascote.forma.avatar, av.id)
                        self.assertEqual(app.mascote.cor, app.cor_de(pid, av))
                        self.assertEqual(app.cor_de(pid, av), avatares.cor_hex(cat.cor_agente[pid]))
                        extra = app._extra_do_agente()
                        if pid == "claude":
                            self.assertEqual(extra, {})  # o caminho de sempre
                            continue
                        v = extra["visao"]
                        self.assertEqual((v.titulo, v.tokens, v.saida), (pid, "—", "—"))
                        self.assertEqual({linha[0] for linha in v.linhas}, {f"proj-{pid}"})
                        self.assertEqual(app._uso.sessoes, 2 if pid == "codex" else 1)
                        self.assertEqual((app._uso.cinco_horas, app._uso.semana, app._uso.tokens_saida), (None, None, 0))
                self.assertEqual(len(set(imagens.values())), len(PROVIDERS), (layout, paisagem))

    def test_avatar_escolhido_e_cor_por_id_do_agente_efetivo(self):
        self.preparar()
        app = self.app(agente_auto="codex", avatares=(("codex", "pixel"),), cores_avatares=(("pixel", "#12AB7F"),))
        self.volta(app)
        self.assertEqual((app.mascote.forma.avatar, app.mascote.cor), ("pixel", (0x12, 0xAB, 0x7F)))

    def test_trocar_de_agente_redesenha_mesmo_com_numeros_iguais(self):
        self.por(snap("codex", sessao("codex", projeto="igual")), snap("opencode", sessao("opencode", projeto="igual")))
        for layout in SIMPLES:
            app = self.app(layout=layout, **{CHAVE[layout]: "codex"})
            self.volta(app)
            antes = app._extra_do_agente()["visao"]
            app.aplicar_cfg(preferencias.aplicar_em(app.cfg, {CHAVE[layout]: "opencode"}))
            self.desenhar(app)
            depois = app._extra_do_agente()["visao"]
            self.assertEqual((antes.titulo, depois.titulo), ("codex", "opencode"))
            self.assertNotEqual(antes, depois)  # a chave do cache do desenho leva o agente


class Automatico(Base):
    def escolha(self, **por_agente):
        return escolher_automatico((pid, por_agente[pid]) for pid in PROVIDERS if pid in por_agente)

    def test_atencao_antes_de_trabalho_antes_de_recencia(self):
        erro = [sessao("opencode", atencao=T.ERROR, minutos=60)]
        aprovacao = [sessao("cursor", atencao=T.WAITING_APPROVAL, minutos=1)]
        espera = [sessao("codex", atencao=T.WAITING_USER, minutos=1)]
        trabalho = [sessao("claude", atividade=A.WORKING, minutos=0)]
        parada = [sessao("claude", minutos=0)]
        self.assertEqual(self.escolha(claude=trabalho, codex=espera, cursor=aprovacao, opencode=erro), "opencode")
        self.assertEqual(self.escolha(claude=trabalho, codex=espera, cursor=aprovacao), "cursor")
        self.assertEqual(self.escolha(claude=trabalho, codex=espera), "codex")
        self.assertEqual(self.escolha(claude=trabalho, codex=[sessao("codex", minutos=0)]), "claude")
        velho_trabalho = [sessao("codex", atividade=A.WORKING, minutos=50)]
        self.assertEqual(self.escolha(claude=parada, codex=velho_trabalho), "codex")

    def test_recencia_status_e_empate(self):
        self.assertEqual(self.escolha(claude=[sessao("claude", minutos=9)], cursor=[sessao("cursor", minutos=2)]), "cursor")
        self.assertEqual(self.escolha(claude=[sessao("claude", atividade=A.UNKNOWN, atencao=T.UNKNOWN, minutos=0)],
                                      cursor=[sessao("cursor", minutos=30)]), "cursor")  # parada antes de sem notícias
        iguais = {p: [sessao(p, atividade=A.WORKING)] for p in PROVIDERS}
        for _ in range(3):
            self.assertEqual(self.escolha(**iguais), "claude")
        del iguais["claude"]
        self.assertEqual(self.escolha(**iguais), "codex")
        sem_hora = [AgentSession("claude", "x", A.IDLE, T.NONE)]
        self.assertEqual(self.escolha(claude=sem_hora, opencode=[sessao("opencode", minutos=500)]), "opencode")

    def test_ignora_desconectadas_e_sem_candidato_nao_vira_claude(self):
        fora = [sessao("opencode", atencao=T.ERROR, disconnected=True)]
        self.assertEqual(self.escolha(opencode=fora, cursor=[sessao("cursor")]), "cursor")
        self.assertIsNone(self.escolha(opencode=fora, claude=[]))
        self.assertIsNone(self.escolha())

    def test_na_aplicacao_ignora_desligados_e_nao_guarda_dado_antigo(self):
        self.por(do_claude(), snap("codex", sessao("codex", atividade=A.WORKING)),
                 snap("opencode", sessao("opencode", atencao=T.ERROR)))
        app = self.app(layout="minimal-v2", agente_minimal="automatic", providers=("claude", "codex", "cursor"))
        self.volta(app)
        self.assertEqual(app.agente_efetivo, "codex")  # o OpenCode está desligado
        self.assertEqual(app._extra_do_agente()["visao"].modo, "automático")
        self.por(snap("codex"))
        self.volta(app)
        self.assertIsNone(app.agente_efetivo)  # o Claude está ligado, mas sem sessão: não é o plano B
        v = app._extra_do_agente()["visao"]
        self.assertEqual((v.titulo, v.conta, v.tokens, v.linhas, v.pontos), ("agentes", "—", "—", (), ()))
        self.assertEqual(app._info_agente, {"selected": "automatic", "effective": None, "condition": "sem_agente"})
        self.assertEqual(app.sujeito(), (None, None))
        self.assertEqual(app.mascote.forma.avatar, app.avatar_de(None).id)
        self.por(do_claude(sessao("claude")))
        self.volta(app)
        self.assertEqual((app.agente_efetivo, app._extra_do_agente()), ("claude", {}))  # escolhido: o de sempre


class Condicoes(Base):
    def quadro(self, pid, s, ligado=True, informa=True):
        sessoes = s.sessions if s is not None else ()
        return quadro_agente(visao_agente(pid, pid.title(), ligado, s, sessoes, informa), AGORA)

    def test_cada_condicao_tem_o_seu_texto_e_nenhum_zero_inventado(self):
        casos = {
            "desligado": self.quadro("codex", snap("codex", sessao("codex")), ligado=False),
            "nao_instalado": self.quadro("codex", snap("codex", integration="not_installed")),
            "nao_configurado": self.quadro("cursor", snap("cursor", integration="not_configured")),
            "indisponivel": self.quadro("codex", None),
            "desconectado": self.quadro("opencode", snap("opencode", sessao("opencode", disconnected=True),
                                                         condition="offline")),
            "sem_plugin": self.quadro("opencode", snap("opencode", condition="never")),
        }
        for estado, v in casos.items():
            with self.subTest(estado):
                self.assertEqual(v.estado, estado)
                self.assertEqual((v.conta, v.tokens, v.saida), ("—", "—", "—"))
                self.assertTrue(v.rotulo and v.mensagem)
                self.assertNotIn("ocioso", v.rotulo)
                self.assertNotEqual(v.rodape, "nenhuma sessão esperando")
        vazio = self.quadro("codex", snap("codex"))
        self.assertEqual((vazio.estado, vazio.conta, vazio.mensagem), ("sem_sessoes", "0", "nenhuma sessão aberta"))

    def test_desconectado_e_sem_noticias_nunca_aparecem_como_ocioso(self):
        fora = self.quadro("opencode", snap("opencode", sessao("opencode", disconnected=True), condition="offline"))
        self.assertEqual([linha[1] for linha in fora.linhas], ["último: ocioso"])
        self.assertEqual((fora.rotulo, fora.pontos), ("desconectado", ()))
        sem = self.quadro("codex", snap("codex", sessao("codex", atividade=A.UNKNOWN, atencao=T.UNKNOWN)))
        self.assertEqual((sem.estado, sem.rotulo, sem.pontos), ("desconhecido", "sem notícias", ("unknown",)))
        self.assertEqual(sem.linhas[0][1], "sem notícias")
        misto = self.quadro("codex", snap("codex", sessao("codex", "a"),
                                          sessao("codex", "b", A.UNKNOWN, T.UNKNOWN)))
        self.assertEqual(misto.rodape, "há sessão sem notícias")

    def test_rodape_espera_erro_e_o_cursor_que_nao_informa(self):
        espera = self.quadro("codex", snap("codex", sessao("codex", atencao=T.WAITING_USER, minutos=12)))
        self.assertEqual((espera.rodape, espera.tom_rodape), ("proj-codex espera há 12 min", "alerta"))
        erro = self.quadro("opencode", snap("opencode", sessao("opencode", atencao=T.ERROR)))
        self.assertEqual((erro.estado, erro.rodape, erro.pontos), ("erro", "proj-opencode parou com erro", ("error",)))
        cursor = self.quadro("cursor", snap("cursor", sessao("cursor")), informa=False)
        self.assertIn("não informa esperas", cursor.rodape)
        app = self.app(agente_auto="cursor")  # na aplicação, vem das capacidades do provider
        self.por(snap("cursor", sessao("cursor")))
        self.volta(app)
        self.assertIn("não informa esperas", app._extra_do_agente()["visao"].rodape)

    def test_mascote_nao_conta_desconectadas_nem_erro_como_atividade(self):
        v = visao_agente("opencode", "OpenCode", True,
                         None, (sessao("opencode", "a", A.WORKING, disconnected=True), sessao("opencode", "b", atencao=T.ERROR)))
        u = uso_do_agente(v)
        self.assertEqual((u.sessoes, u.trabalhando, u.esperando), (1, 0, 0))

    def test_claude_desligado_diz_isso_em_vez_de_dados_vazios(self):
        app = self.app(providers=("codex",))
        self.volta(app)
        v = app._extra_do_agente()["visao"]
        self.assertEqual((v.titulo, v.rotulo, v.conta), ("claude", "desligado", "—"))


class TrocaDeFonte(Base):
    def rodar(self, app, segundos=8.0):
        """Alguns quadros depois de uma coleta: o bastante para passar os 3 s de calma."""
        estados = [app.mascote.estado]
        self.volta(app, 0.2)
        for _ in range(int(segundos / 0.5)):
            self.desenhar(app, 0.5)
            estados.append(app.mascote.estado)
        return estados

    def test_trocar_de_um_que_trabalha_para_um_parado_nao_conclui(self):
        self.por(snap("codex", sessao("codex", atividade=A.WORKING)), snap("opencode", sessao("opencode")))
        for troca in ("preferencia", "layout"):
            app = self.app(agente_auto="codex", agente_minimal="opencode")
            self.rodar(app)
            self.assertEqual(app.mascote.estado, "trabalhando")
            mudanca = {"agente_auto": "opencode"} if troca == "preferencia" else {"layout": "minimal-v2"}
            app.aplicar_cfg(preferencias.aplicar_em(app.cfg, mudanca))
            app._uso = None
            estados = self.rodar(app)
            self.assertNotIn("concluido", estados[1:], troca)
            self.assertEqual(app.mascote.estado, "ocioso", troca)

    def test_trocar_o_claude_nao_gera_reset_falso_na_volta(self):
        self.por(do_claude(), snap("codex", sessao("codex")))
        app = self.app()
        self.rodar(app, 1)
        app.aplicar_cfg(preferencias.aplicar_em(app.cfg, {"agente_auto": "codex"}))
        self.assertEqual(app.mascote._reseta_ant, {})
        self.assertNotIn("alerta", self.rodar(app)[-3:])  # o limite do Claude não vale para o Codex
        depois = do_claude()
        janelas = tuple(QuotaWindow(w.window_id, w.label, 10.0, w.resets_at + timedelta(hours=5))
                        for w in depois.quotas.windows)
        self.por(snap("claude", usage=depois.usage, quotas=QuotaReport("statusline", AGORA, False, janelas)))
        app.aplicar_cfg(preferencias.aplicar_em(app.cfg, {"agente_auto": "claude"}))
        app._uso = None
        self.assertNotIn("reset", self.rodar(app))

    def test_o_mesmo_agente_em_outro_layout_mantem_a_memoria(self):
        self.por(do_claude(sessao("claude", atividade=A.WORKING)))
        app = self.app()
        self.volta(app)
        app.aplicar_cfg(preferencias.aplicar_em(app.cfg, {"layout": "minimal-v2"}))
        self.assertTrue(app.mascote._havia_trabalho)

    def test_um_fim_confirmado_conclui_e_um_fim_sem_confirmacao_nao(self):
        trabalhando = snap("codex", sessao("codex", atividade=A.WORKING))
        casos = {
            "confirmado": (snap("codex", sessao("codex")), True),
            "sem notícias": (snap("codex", sessao("codex", atividade=A.UNKNOWN, atencao=T.UNKNOWN)), False),
            "sumiu": (snap("codex"), False),
            "desconectada": (snap("codex", sessao("codex", disconnected=True)), False),
            "coleta falhou": (snap("codex", status=SnapshotStatus.ERROR, errors=("x",)), False),
        }
        for nome, (depois, conclui) in casos.items():
            with self.subTest(nome):
                self.dados.clear()
                self.por(trabalhando)
                app = self.app(agente_auto="codex")
                self.rodar(app)
                self.por(depois)
                self.assertEqual("concluido" in self.rodar(app), conclui)

    def test_o_automatico_que_troca_de_agente_nao_conclui(self):
        self.por(snap("codex", sessao("codex", atividade=A.WORKING)), snap("cursor", sessao("cursor", minutos=30)))
        app = self.app(agente_auto="automatic")
        self.rodar(app)
        self.assertEqual(app.agente_efetivo, "codex")
        self.por(snap("codex"))
        estados = self.rodar(app)
        self.assertEqual(app.agente_efetivo, "cursor")
        self.assertNotIn("concluido", estados)
        self.assertEqual(app.mascote.forma.avatar, app.avatar_de("cursor").id)


class Multiagente(Base):
    def test_as_chaves_dos_layouts_nao_mudam_o_multiagente(self):
        self.por(do_claude(sessao("claude", atividade=A.WORKING)), snap("codex", sessao("codex", atencao=T.WAITING_USER)))
        quadros = []
        with mock.patch.object(modulo, "datetime", wraps=datetime) as relogio:
            relogio.now.return_value = AGORA
            relogio.min, relogio.max = datetime.min, datetime.max
            for escolhas in ({}, {"agente_auto": "opencode", "agente_classico": "automatic", "agente_minimal": "cursor"}):
                self.t = 100.0
                app = self.app(layout="multiagente", **escolhas)
                app.semente = 7
                app.mascote.inicio = 0.0
                quadros.append(self.volta(app).tobytes())
                self.assertEqual((app._extra_do_agente(), app.agente_publicado), ({}, None))
        self.assertEqual(quadros[0], quadros[1])



class ClaudeSemConfirmacao(Base):
    """O Claude ligado: nada que não esteja confirmado aparece como ocioso, e os confirmados seguem como eram."""

    def legado(self, app, layout, quando):
        """O quadro que a projeção de sempre (uso_claude, sem ressalva) daria agora."""
        from apresentacao import uso_claude
        from layout_minimal_v2 import desenhar_minimal
        from layout_ombro import desenhar_ombro
        from render import desenhar

        u = uso_claude(app.monitor.store.snapshot("claude"))
        if layout == "classico":
            return desenhar(HW, u, AGORA, paisagem_=app.cfg.paisagem_efetiva)
        if layout == "minimal-v2":
            return desenhar_minimal(HW, u, app.mascote, AGORA, quando, agente="claude", usar_cache=False)
        return desenhar_ombro(HW, u, app.mascote, AGORA, quando, usar_cache=False)

    def quadros(self, dado, layout, paisagem=False):
        """(quadro do app, quadro da projeção de sempre, app) com o relógio parado."""
        self.dados.clear()
        if dado is not None:
            self.por(dado)
        with mock.patch.object(modulo, "datetime", wraps=datetime) as relogio, \
                mock.patch("render.datetime", wraps=datetime) as outro:
            relogio.now.return_value = outro.now.return_value = AGORA
            relogio.min, relogio.max = datetime.min, datetime.max
            app = self.app(layout=layout, paisagem=paisagem)
            import layout_minimal_v2
            import layout_ombro

            layout_ombro._ultimo_fixo = layout_minimal_v2._ultimo_fixo = None
            layout_minimal_v2.reiniciar_niveis()
            self.t = 100.0
            img = self.volta(app, 0.0)
            layout_minimal_v2.reiniciar_niveis()
            return img.tobytes(), self.legado(app, layout, 100.0).tobytes(), app

    CONFIRMADOS = {
        "trabalhando": do_claude(sessao("claude", atividade=A.WORKING)),
        "esperando": do_claude(sessao("claude", atencao=T.WAITING_USER)),
        "parada": do_claude(sessao("claude")),
        "erro": do_claude(sessao("claude", atencao=T.ERROR), sessao("claude", "s2", A.WORKING)),
        "ocioso sem alerta": do_claude(sessao("claude"), cinco_horas=40.0),
        "trabalhando sem alerta": do_claude(sessao("claude", atividade=A.WORKING), cinco_horas=40.0),
    }
    TELAS = (("auto", False), ("ombro", False), ("minimal-v2", False), ("classico", False), ("classico", True))

    def test_estados_confirmados_tem_os_mesmos_pixels(self):
        for nome, dado in self.CONFIRMADOS.items():
            for layout, paisagem in self.TELAS:
                with self.subTest(nome=nome, layout=layout, paisagem=paisagem):
                    novo, antigo, app = self.quadros(dado, layout, paisagem)
                    self.assertEqual(novo, antigo)
                    self.assertEqual(app._extra_do_agente(), {})

    def test_sem_confirmacao_muda_o_quadro_e_diz_a_condicao(self):
        sem_noticias = sessao("claude", atividade=A.UNKNOWN, atencao=T.UNKNOWN)
        casos = {
            "sem noticias": (do_claude(sem_noticias, cinco_horas=40.0), "desconhecido"),
            "parada e sem noticias": (do_claude(sessao("claude", "s2"), sem_noticias, cinco_horas=40.0), "desconhecido"),
            "sem sessoes": (do_claude(cinco_horas=40.0), "sem_sessoes"),
            "nao instalado": (snap("claude", integration="not_installed"), "nao_instalado"),
            "coleta falhou sem dados": (snap("claude", status=SnapshotStatus.ERROR, errors=("x",)), "indisponivel"),
            "sem noticias com o limite no fim": (do_claude(sem_noticias), "alerta"),
            "coleta falhou com dados": (snap("claude", status=SnapshotStatus.ERROR, errors=("x",),
                                             usage=UsageMetrics("today", 10, 5)), "indisponivel"),
            "desconectada": (do_claude(sessao("claude", disconnected=True), cinco_horas=40.0), "sem_sessoes"),
        }
        for nome, (dado, condicao) in casos.items():
            for layout, paisagem in self.TELAS:
                with self.subTest(nome=nome, layout=layout, paisagem=paisagem):
                    novo, antigo, app = self.quadros(dado, layout, paisagem)
                    self.assertNotEqual(novo, antigo)
                    self.assertEqual(app._info_agente["condition"], condicao)

    def test_os_textos_e_os_dados_que_continuam_valendo(self):
        sem_noticias = sessao("claude", atividade=A.UNKNOWN, atencao=T.UNKNOWN)
        _, _, app = self.quadros(do_claude(sem_noticias), "auto")
        u = app._uso
        self.assertEqual((u.condicao, u.sem_noticias, u.status_sessoes, u.sessoes), ("desconhecido", 1, ["unknown"], 1))
        self.assertEqual((u.cinco_horas.pct, u.tokens_saida), (95.0, 700))  # são dele: continuam na tela
        self.assertEqual(app._info_agente["condition"], "alerta")  # o limite é um fato; a ressalva fica embaixo
        import layout_minimal_v2 as v2
        import layout_ombro

        f = layout_ombro.fixo(u, "ocioso", AGORA)
        self.assertEqual((f.rotulo, f.nota), ("1 sem notícias", "há sessão sem notícias"))
        self.assertEqual(v2.fixo(u, "ocioso", AGORA, "claude").estado, "desconhecido")
        self.assertEqual(v2.fixo(u, "alerta", AGORA, "claude").estado, "alerta")  # o limite é um fato
        _, _, app = self.quadros(do_claude(), "minimal-v2")
        f = v2.fixo(app._uso, "ocioso", AGORA, "claude")
        self.assertEqual((f.estado, f.nota), ("sem_sessoes", "Nenhuma sessão aberta"))
        _, _, app = self.quadros(snap("claude", integration="not_installed"), "auto")
        v = app._extra_do_agente()["visao"]
        self.assertEqual((v.titulo, v.rotulo, v.tokens, v.conta), ("claude", "não instalado", "—", "—"))

    def test_nada_de_zero_inventado_nem_de_ativa_sem_confirmacao(self):
        import layout_minimal_v2 as v2
        import layout_ombro
        import render

        falha = snap("claude", status=SnapshotStatus.ERROR, errors=("x",),
                     usage=UsageMetrics("today", 10, 5))
        _, _, app = self.quadros(falha, "minimal-v2")
        for desenho in (layout_ombro.fixo(app._uso, "ocioso", AGORA), v2.fixo(app._uso, "ocioso", AGORA, "claude")):
            self.assertEqual(desenho.sessoes, "—")  # a coleta falhou: não há contagem
        self.assertEqual(v2.sufixo_das_sessoes(v2.fixo(app._uso, "ocioso", AGORA, "claude")), "")
        # limites sem tokens, sem confirmação: os limites ficam, os tokens são "—", nunca 0
        so_limites = do_claude(sessao("claude", atividade=A.UNKNOWN, atencao=T.UNKNOWN), cinco_horas=40.0)
        so_limites = ProviderSnapshot("claude", AGORA, sessions=so_limites.sessions, quotas=so_limites.quotas)
        _, _, app = self.quadros(so_limites, "auto")
        self.assertEqual(app._extra_do_agente(), {})
        for desenho in (layout_ombro.fixo(app._uso, "ocioso", AGORA), v2.fixo(app._uso, "ocioso", AGORA, "claude")):
            self.assertEqual((desenho.tokens_total, desenho.tokens_saida), (None, None))
            self.assertEqual(desenho.janelas[0][0], 40.0)
        self.assertEqual((render.tokens(None), render.total_de_tokens(app._uso), render.tokens_de_saida(app._uso)),
                         ("—", None, None))
        self.assertEqual(v2.sufixo_das_sessoes(v2.fixo(app._uso, "ocioso", AGORA, "claude")), "")
        # confirmado: como sempre
        _, _, app = self.quadros(do_claude(sessao("claude"), cinco_horas=40.0), "minimal-v2")
        f = v2.fixo(app._uso, "ocioso", AGORA, "claude")
        self.assertEqual((f.sessoes, v2.sufixo_das_sessoes(f), f.tokens_saida), (1, " ativa", 700))

    def test_o_mesmo_vale_para_os_outros_agentes(self):
        misto = snap("codex", sessao("codex", "a"), sessao("codex", "b", A.UNKNOWN, T.UNKNOWN))
        v = visao_agente("codex", "Codex", True, misto, misto.sessions)
        self.assertEqual((v.estado, quadro_agente(v, AGORA).rotulo), ("desconhecido", "sem notícias"))


class FimSemConfirmacaoEmQualquerAgente(TrocaDeFonte):
    """Nos layouts de um agente, nenhum agente (o Claude inclusive) comemora um fim que ninguém confirmou."""

    def casos(self, pid):
        return {
            "sem notícias": snap(pid, sessao(pid, atividade=A.UNKNOWN, atencao=T.UNKNOWN)),
            "sumiu": snap(pid),
            "desconectada": snap(pid, sessao(pid, disconnected=True)),
            "coleta falhou": snap(pid, status=SnapshotStatus.ERROR, errors=("x",)),
        }

    def test_cada_agente_em_cada_layout(self):
        for pid in PROVIDERS:
            for layout in ("auto", "minimal-v2"):
                for nome, depois in {**self.casos(pid), "confirmado": snap(pid, sessao(pid))}.items():
                    with self.subTest(pid=pid, layout=layout, caso=nome):
                        self.dados.clear()
                        self.por(snap(pid, sessao(pid, atividade=A.WORKING)))
                        app = self.app(layout=layout, **{CHAVE[layout]: pid})
                        self.rodar(app)
                        self.assertEqual(app.mascote.estado, "trabalhando")
                        self.por(depois)
                        self.assertEqual("concluido" in self.rodar(app), nome == "confirmado")

    def test_depois_de_trocar_de_qualquer_agente_para_qualquer_outro(self):
        for antes in PROVIDERS:
            for depois in PROVIDERS:
                if antes == depois:
                    continue
                with self.subTest(antes=antes, depois=depois):
                    self.dados.clear()
                    self.por(snap(antes, sessao(antes, atividade=A.WORKING)),
                             snap(depois, sessao(depois, atividade=A.WORKING)))
                    app = self.app(agente_auto=antes)
                    self.rodar(app)
                    app.aplicar_cfg(preferencias.aplicar_em(app.cfg, {"agente_auto": depois}))
                    app._uso = None
                    self.assertNotIn("concluido", self.rodar(app))
                    self.por(snap(depois, sessao(depois, atividade=A.UNKNOWN, atencao=T.UNKNOWN)))
                    self.assertNotIn("concluido", self.rodar(app))

    def test_o_multiagente_para_o_layout_do_claude_nao_conclui(self):
        self.por(snap("codex", sessao("codex", atividade=A.WORKING)), snap("claude", sessao("claude")))
        app = self.app(layout="multiagente")
        self.rodar(app)
        app.aplicar_cfg(preferencias.aplicar_em(app.cfg, {"layout": "auto"}))
        app._uso = None
        self.assertNotIn("concluido", self.rodar(app))


for _nome in [n for n in vars(TrocaDeFonte) if n.startswith("test_")]:
    setattr(FimSemConfirmacaoEmQualquerAgente, _nome, None)  # os da classe-mãe já rodam nela


if __name__ == "__main__":
    unittest.main()
