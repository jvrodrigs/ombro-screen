"""Codex, Cursor e os hooks do Claude: estados a partir de eventos, ids, atrasos, repetição e tempo."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest import mock

from coletores.claude import SessaoClaude, UsoClaude, resumo_sessoes
from core.caixa import EventoLocal
from core.models import Activity as A
from core.models import AgentSession
from core.models import Attention as T
from core.rastreador import TTL_SESSAO, TTL_TRABALHANDO
from providers import hooks
from providers.claude import ClaudeProvider
from providers.codex import CodexProvider
from providers.cursor import CursorProvider

T0 = datetime(2026, 11, 14, 15, 0)


def t(seg):
    return T0 + timedelta(seconds=seg)


def ev(provider, evento, seg, sessao="s1", projeto="proj", detalhe=None):
    return EventoLocal(provider, sessao, evento, t(seg), projeto, detalhe)


class Relogio:
    def __init__(self):
        self.agora = T0

    def __call__(self):
        return self.agora


def provider(cls):
    r = Relogio()
    p = cls(relogio=r, monotonic=lambda: 0.0)
    return p, r


def estado(p, r, seg, sid="s1"):
    r.agora = t(seg)
    s = {x.session_id: x for x in p.collect().sessions}.get(sid)
    return (s.activity, s.attention) if s else None


class Codex(unittest.TestCase):
    def test_um_turno_completo(self):
        p, r = provider(CodexProvider)
        p.receber([ev("codex", "SessionStart", 0, detalhe="startup")])
        self.assertEqual(estado(p, r, 0), (A.IDLE, T.NONE))
        p.receber([ev("codex", "UserPromptSubmit", 1)])
        self.assertEqual(estado(p, r, 1), (A.WORKING, T.NONE))
        p.receber([ev("codex", "PermissionRequest", 2)])
        self.assertEqual(estado(p, r, 2), (A.WORKING, T.WAITING_APPROVAL))
        p.receber([ev("codex", "PostToolUse", 9)])  # aprovado: voltou a trabalhar
        self.assertEqual(estado(p, r, 9), (A.WORKING, T.NONE))
        p.receber([ev("codex", "Stop", 12)])  # fim do turno, não da sessão
        self.assertEqual(estado(p, r, 12), (A.IDLE, T.NONE))
        p.receber([ev("codex", "SessionEnd", 20)])
        self.assertIsNone(estado(p, r, 20))

    def test_ferramenta_sozinha_nao_e_aprovacao(self):
        p, r = provider(CodexProvider)
        p.receber([ev("codex", "UserPromptSubmit", 0), ev("codex", "PostToolUse", 1)])
        self.assertEqual(estado(p, r, 1), (A.WORKING, T.NONE))

    def test_erro_fica_ate_o_proximo_prompt(self):
        p, r = provider(CodexProvider)
        p.receber([ev("codex", "UserPromptSubmit", 0), ev("codex", "PostModelRequest", 1, detalhe="failed"),
                   ev("codex", "Stop", 2)])
        self.assertEqual(estado(p, r, 2), (A.IDLE, T.ERROR))
        p.receber([ev("codex", "PostModelRequest", 3, detalhe="completed")])  # um pedido que deu certo não apaga o erro
        self.assertEqual(estado(p, r, 3), (A.IDLE, T.ERROR))
        p.receber([ev("codex", "UserPromptSubmit", 4)])
        self.assertEqual(estado(p, r, 4), (A.WORKING, T.NONE))

    def test_interrupcao(self):
        p, r = provider(CodexProvider)
        p.receber([ev("codex", "UserPromptSubmit", 0), ev("codex", "Interrupt", 1)])
        self.assertEqual(estado(p, r, 1), (A.IDLE, T.NONE))

    def test_sessoes_simultaneas_e_projetos(self):
        p, r = provider(CodexProvider)
        p.receber([ev("codex", "UserPromptSubmit", 0, "a", "api"), ev("codex", "PermissionRequest", 1, "b", "web"),
                   ev("codex", "Stop", 2, "a", "api")])
        r.agora = t(3)
        s = {x.session_id: x for x in p.collect().sessions}
        self.assertEqual((s["a"].activity, s["b"].attention), (A.IDLE, T.WAITING_APPROVAL))
        self.assertEqual((s["a"].project, s["b"].project), ("api", "web"))
        self.assertEqual(len(s), 2)

    def test_repetido_atrasado_e_depois_do_fim(self):
        p, r = provider(CodexProvider)
        p.receber([ev("codex", "UserPromptSubmit", 0), ev("codex", "Stop", 5)])
        p.receber([ev("codex", "Stop", 5)])  # repetido
        p.receber([ev("codex", "PermissionRequest", 3)])  # chegou atrasado: não volta no tempo
        self.assertEqual(estado(p, r, 6), (A.IDLE, T.NONE))
        p.receber([ev("codex", "SessionEnd", 10), ev("codex", "PostToolUse", 9)])  # atrasado, de antes do fim
        self.assertIsNone(estado(p, r, 11))
        p.receber([ev("codex", "SessionStart", 30, detalhe="resume")])  # retomada depois: volta
        self.assertEqual(estado(p, r, 30), (A.IDLE, T.NONE))

    def test_eventos_perdidos_nao_deixam_trabalhando_para_sempre(self):
        p, r = provider(CodexProvider)
        p.receber([ev("codex", "UserPromptSubmit", 0)])
        meio = TTL_TRABALHANDO.total_seconds()
        self.assertEqual(estado(p, r, meio - 1), (A.WORKING, T.NONE))
        self.assertEqual(estado(p, r, meio + 1), (A.UNKNOWN, T.NONE))  # sem notícia: desconhecido, não ocioso
        self.assertIsNone(estado(p, r, TTL_SESSAO.total_seconds() + 1))  # abandonada: sai

    def test_recupera_do_banco_como_desconhecida(self):
        p, r = provider(CodexProvider)
        p.restaurar([AgentSession("codex", "velha", A.WORKING, T.WAITING_APPROVAL, updated_at=t(0)),
                     AgentSession("claude", "outra", A.WORKING, T.NONE, updated_at=t(0))])
        r.agora = t(5)
        [s] = p.collect().sessions
        self.assertEqual((s.session_id, s.activity, s.attention), ("velha", A.UNKNOWN, T.UNKNOWN))
        p.receber([ev("codex", "Stop", 6, "velha")])
        self.assertEqual(estado(p, r, 6, "velha"), (A.IDLE, T.NONE))

    def test_eventos_de_outro_provider_sao_ignorados(self):
        p, r = provider(CodexProvider)
        self.assertEqual(p.receber([ev("cursor", "stop", 0)]), 0)
        self.assertEqual(p.collect().sessions, ())


class Integracao(unittest.TestCase):
    def test_nao_instalado_nao_configurado_e_ok(self):
        p, _ = provider(CodexProvider)
        with mock.patch.object(hooks.shutil, "which", return_value=None), mock.patch.object(hooks.Path, "exists", return_value=False), \
                mock.patch.object(CodexProvider, "configurado", return_value=False):
            self.assertEqual(p.collect().integration, "not_installed")
        p, _ = provider(CodexProvider)
        with mock.patch.object(hooks.shutil, "which", return_value="/usr/local/bin/codex"), \
                mock.patch.object(CodexProvider, "configurado", return_value=False):
            self.assertEqual(p.collect().integration, "not_configured")
        p, _ = provider(CodexProvider)
        with mock.patch.object(hooks.shutil, "which", return_value=None), mock.patch.object(hooks.Path, "exists", return_value=False), \
                mock.patch.object(CodexProvider, "configurado", return_value=False):
            p.receber([ev("codex", "SessionStart", 0)])
            self.assertEqual(p.collect().integration, "ok")  # chegou evento: a integração existe (por projeto, talvez)

    def test_configurado_procura_o_hook_nos_arquivos(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / "hooks.json"
            p, _ = provider(CursorProvider)
            p.configs = (str(arq),)
            self.assertFalse(p.configurado())  # arquivo não existe
            arq.write_text('{"version": 1, "hooks": {"stop": [{"command": "python3 /x/ombro_hook.py cursor"}]}}')
            self.assertTrue(p.configurado())

    def test_deteccao_e_guardada_por_um_minuto(self):
        p, _ = provider(CursorProvider)
        relogio = [0.0]
        p._monotonic = lambda: relogio[0]
        with mock.patch.object(CursorProvider, "instalado", return_value=True) as inst, \
                mock.patch.object(CursorProvider, "configurado", return_value=False):
            for i in range(10):
                p.collect()
            relogio[0] = 61.0
            p.collect()
        self.assertEqual(inst.call_count, 2)


class Cursor(unittest.TestCase):
    def test_estados(self):
        p, r = provider(CursorProvider)
        p.receber([ev("cursor", "sessionStart", 0)])
        self.assertEqual(estado(p, r, 0), (A.IDLE, T.UNKNOWN))  # o Cursor não avisa quando espera aprovação
        p.receber([ev("cursor", "beforeSubmitPrompt", 1)])
        self.assertEqual(estado(p, r, 1), (A.WORKING, T.UNKNOWN))
        p.receber([ev("cursor", "postToolUse", 2), ev("cursor", "postToolUseFailure", 3)])
        self.assertEqual(estado(p, r, 3), (A.WORKING, T.UNKNOWN))
        p.receber([ev("cursor", "stop", 4, detalhe="completed")])
        self.assertEqual(estado(p, r, 4), (A.IDLE, T.UNKNOWN))
        p.receber([ev("cursor", "beforeSubmitPrompt", 5), ev("cursor", "stop", 6, detalhe="error")])
        self.assertEqual(estado(p, r, 6), (A.IDLE, T.ERROR))
        p.receber([ev("cursor", "sessionEnd", 7, detalhe="window_close")])
        self.assertIsNone(estado(p, r, 7))

    def test_projetos_diferentes_e_sem_metricas(self):
        p, r = provider(CursorProvider)
        p.receber([ev("cursor", "beforeSubmitPrompt", 0, "c1", "site"), ev("cursor", "beforeSubmitPrompt", 1, "c2", None)])
        r.agora = t(2)
        snap = p.collect()
        self.assertEqual({s.session_id: s.project for s in snap.sessions}, {"c1": "site", "c2": None})
        self.assertIsNone(snap.usage)
        self.assertIsNone(snap.quotas)  # nada inventado
        self.assertFalse(CursorProvider.capabilities.token_usage or CursorProvider.capabilities.quota_windows)


class FonteFixa:
    def __init__(self, sessoes):
        self.sessoes = sessoes

    def ler(self):
        return UsoClaude(lista_sessoes=self.sessoes, **resumo_sessoes(self.sessoes))


class ClaudeComHooks(unittest.TestCase):
    def provider(self, status="waiting", desde=t(10)):
        s = SessaoClaude("sess-1", status, status, desde, "proj")
        return ClaudeProvider(FonteFixa([s]), relogio=lambda: t(100))

    def sessao(self, p):
        return p.collect().sessions[0]

    def test_sem_hooks_nada_muda(self):
        p = self.provider()
        self.assertEqual(self.sessao(p).attention, T.WAITING_USER)

    def test_permission_request_recente_vira_aprovacao(self):
        p = self.provider()
        p.receber([ev("claude", "PermissionRequest", 11, "sess-1")])
        self.assertEqual(self.sessao(p).attention, T.WAITING_APPROVAL)

    def test_permission_request_antigo_nao_vale(self):
        p = self.provider(desde=t(60))  # o status mudou depois do pedido de permissão: agora é outra espera
        p.receber([ev("claude", "PermissionRequest", 11, "sess-1")])
        self.assertEqual(self.sessao(p).attention, T.WAITING_USER)

    def test_aprovacao_nao_inventa_espera(self):
        p = self.provider(status="busy")
        p.receber([ev("claude", "PermissionRequest", 11, "sess-1")])
        self.assertEqual((self.sessao(p).activity, self.sessao(p).attention), (A.WORKING, T.NONE))

    def test_stop_failure_e_erro_ate_um_stop(self):
        p = self.provider(status="idle")
        p.receber([ev("claude", "StopFailure", 11, "sess-1")])
        self.assertEqual(self.sessao(p).attention, T.ERROR)
        p.receber([ev("claude", "Stop", 30, "sess-1")])
        self.assertEqual(self.sessao(p).attention, T.NONE)

    def test_sessao_com_id_e_pid(self):
        s = SessaoClaude("sess-1#4242", "waiting", "waiting", t(10), "proj")
        p = ClaudeProvider(FonteFixa([s]), relogio=lambda: t(100))
        p.receber([ev("claude", "PermissionRequest", 12, "sess-1")])
        self.assertEqual(self.sessao(p).attention, T.WAITING_APPROVAL)

    def test_tela_de_sempre_ve_igual(self):
        from apresentacao import uso_claude

        p = self.provider()
        antes = uso_claude(p.collect())
        p.receber([ev("claude", "PermissionRequest", 11, "sess-1")])
        depois = uso_claude(p.collect())
        self.assertEqual((antes.esperando, antes.status_sessoes), (depois.esperando, depois.status_sessoes))

    def test_fim_da_sessao_limpa(self):
        p = self.provider()
        p.receber([ev("claude", "PermissionRequest", 11, "sess-1"), ev("claude", "SessionEnd", 12, "sess-1")])
        self.assertEqual(self.sessao(p).attention, T.WAITING_USER)
        self.assertEqual(replace(self.sessao(p)).project, "proj")


if __name__ == "__main__":
    unittest.main()
