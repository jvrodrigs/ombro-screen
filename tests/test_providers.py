"""Contrato ProviderAdapter, registro de providers e o ClaudeProvider sobre o coletor de sempre."""
import dataclasses
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from apresentacao import status_tela, uso_claude
from coletores import claude
from coletores.claude import ColetorClaude
from coletores.demo import Demo
from core.models import Activity, AgentSession, Attention, ProviderSnapshot, SnapshotStatus
from core.providers import ProviderAdapter, ProviderCapabilities, ProviderRegistry
from providers.claude import ClaudeProvider, FonteDemo, snapshot_de_uso

AGORA = datetime(2026, 11, 14, 15, 0)


class Fixo(ProviderAdapter):
    provider_id = "teste"
    display_name = "Teste"
    capabilities = ProviderCapabilities(sessions=True)

    def __init__(self, snap=None, erro=None):
        self.snap, self.erro, self.fechado = snap, erro, False

    def collect(self):
        if self.erro:
            raise self.erro
        return self.snap or ProviderSnapshot(self.provider_id, AGORA)

    def close(self):
        self.fechado = True


class Contrato(unittest.TestCase):
    def test_collect_e_obrigatorio(self):
        class SemCollect(ProviderAdapter):
            provider_id = "x"

        with self.assertRaises(TypeError):
            SemCollect()

    def test_provider_simples_nao_implementa_o_que_nao_tem(self):
        self.assertEqual(Fixo().capabilities, ProviderCapabilities(sessions=True))
        self.assertFalse(Fixo().capabilities.costs)
        Fixo().close()  # close() é opcional

    def test_capacidades_do_claude(self):
        c = ClaudeProvider.capabilities
        self.assertTrue(c.sessions and c.attention and c.token_usage and c.quota_windows)
        self.assertTrue(c.events)  # com os hooks opcionais
        self.assertFalse(c.context_usage or c.costs)


class Registro(unittest.TestCase):
    def test_registrar_e_buscar(self):
        r = ProviderRegistry()
        p = Fixo()
        r.register(p)
        self.assertIs(r.get("teste"), p)
        self.assertIn("teste", r)
        self.assertEqual(r.ids(), ["teste"])

    def test_duplicado_invalido_e_inexistente(self):
        r = ProviderRegistry()
        r.register(Fixo())
        with self.assertRaisesRegex(ValueError, "já registrado"):
            r.register(Fixo())
        for pid in ("", "Claude", "1x", "com espaço", None):
            p = Fixo()
            p.provider_id = pid
            with self.assertRaises(ValueError, msg=pid):
                r.register(p)
        with self.assertRaisesRegex(KeyError, "desconhecido: 'codex'.*registrados: teste"):
            r.get("codex")
        with self.assertRaises(TypeError):
            r.register(object())

    def test_falha_na_coleta_vira_snapshot_de_erro(self):
        r = ProviderRegistry(relogio=lambda: AGORA)
        r.register(Fixo(erro=RuntimeError("disco sumiu")))
        with self.assertLogs("ombro.providers", "ERROR") as logs:
            s1 = r.collect("teste")
            s2 = r.collect("teste")
        self.assertIs(s1.status, SnapshotStatus.ERROR)
        self.assertEqual((s1.collected_at, s1.errors), (AGORA, ("RuntimeError: disco sumiu",)))
        self.assertEqual(s1, s2)
        self.assertEqual(len(logs.records), 1)  # uma vez, não a cada coleta

    def test_snapshot_de_outro_provider_e_erro(self):
        r = ProviderRegistry(relogio=lambda: AGORA)
        r.register(Fixo(snap=ProviderSnapshot("outro", AGORA)))
        with self.assertLogs("ombro.providers", "ERROR"):
            self.assertIs(r.collect("teste").status, SnapshotStatus.ERROR)

    def test_fechar_todos_mesmo_com_erro(self):
        r = ProviderRegistry()
        ruim, bom = Fixo(), Fixo()
        bom.provider_id = "bom"
        ruim.close = mock.Mock(side_effect=RuntimeError)
        r.register(ruim)
        r.register(bom)
        r.close()
        self.assertTrue(bom.fechado)
        self.assertEqual([s.provider_id for s in r.collect_all()], ["teste", "bom"])


class ClaudeEmPasta(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.config, self.tela = base / "claude", base / "tela"
        (self.config / "sessions").mkdir(parents=True)
        self.tela.mkdir()
        p = mock.patch.object(claude, "PASTA_TELA", self.tela)
        p.start()
        self.addCleanup(p.stop)
        self.provider = ClaudeProvider(ColetorClaude(str(self.config)), relogio=lambda: AGORA)

    def sessao(self, nome, **dados):
        dados.setdefault("pid", os.getpid())
        (self.config / "sessions" / f"{nome}.json").write_text(json.dumps(dados))

    def ms(self, minutos=0):
        return int((datetime.now() - timedelta(minutes=minutos)).timestamp() * 1000)

    def test_sem_arquivos(self):
        s = self.provider.collect()
        self.assertIs(s.status, SnapshotStatus.OK)
        self.assertEqual(s.sessions, ())
        self.assertIsNone(s.quotas)  # sem fonte: não inventa janelas
        self.assertEqual((s.usage.input_tokens, s.usage.output_tokens, s.usage.total_tokens), (0, 0, 0))
        self.assertEqual(s.collected_at, AGORA)

    def test_sessoes_simultaneas_e_status(self):
        self.sessao("1", sessionId="s-busy", status="busy", cwd="/x/a")
        self.sessao("2", sessionId="s-wait", status="waiting", statusUpdatedAt=self.ms(4), cwd="/x/b")
        self.sessao("3", sessionId="s-idle", status="idle")
        self.sessao("4", sessionId="s-novo", status="compacting")  # status que o Ombro não conhece
        s = {x.session_id: x for x in self.provider.collect().sessions}
        self.assertEqual((s["s-busy"].activity, s["s-busy"].attention), (Activity.WORKING, Attention.NONE))
        self.assertEqual((s["s-wait"].activity, s["s-wait"].attention), (Activity.IDLE, Attention.WAITING_USER))
        self.assertEqual((s["s-idle"].activity, s["s-idle"].attention), (Activity.IDLE, Attention.NONE))
        self.assertEqual((s["s-novo"].activity, s["s-novo"].attention), (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertEqual(status_tela(s["s-novo"]), "idle")  # a tela mostra como sempre mostrou
        self.assertEqual(s["s-wait"].project, "b")
        self.assertAlmostEqual((datetime.now() - s["s-wait"].status_since).total_seconds(), 240, delta=5)

    def test_ids_estaveis_e_sem_colisao(self):
        self.sessao("10", sessionId="mesma", status="busy")
        self.sessao("11", sessionId="mesma", status="idle", pid=1)  # a mesma conversa em outro processo
        self.sessao("12", status="idle", pid=2)  # sem sessionId
        with mock.patch.object(claude.os, "kill"):
            ids1 = sorted(x.session_id for x in self.provider.collect().sessions)
            ids2 = sorted(x.session_id for x in self.provider.collect().sessions)
        self.assertEqual(ids1, ids2)
        self.assertEqual(len(set(ids1)), 3)
        self.assertIn("pid-2", ids1)
        self.assertTrue(any(i.startswith("mesma#") for i in ids1))

    def test_quotas_e_expiracao(self):
        agora = datetime.now()
        (self.tela / "uso.json").write_text(json.dumps({
            "fetchedAt": self.ms(45),
            "five_hour": {"utilization": 99, "resets_at": (agora - timedelta(minutes=1)).timestamp()},  # já reiniciou
            "seven_day": {"utilization": 61.5, "resets_at": (agora + timedelta(days=3)).timestamp()}}))
        q = self.provider.collect().quotas
        self.assertEqual((q.source, q.stale), ("statusline", True))
        vencida = q.window("5h")  # a janela reiniciou: existe, sem número, marcada como aguardando atualização
        self.assertEqual((vencida.used_percent, vencida.expired), (None, True))
        self.assertEqual(q.scope, "account")
        self.assertEqual((q.window("7d").used_percent, q.window("7d").label), (61.5, "semana"))

    def test_tokens(self):
        pasta = self.config / "projects" / "p"
        pasta.mkdir(parents=True)
        agora = datetime.now().astimezone()
        (pasta / "t.jsonl").write_text(json.dumps({"type": "assistant", "timestamp": agora.isoformat(), "message": {
            "id": "m1", "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 90}}}) + "\n")
        m = self.provider.collect().usage
        self.assertEqual((m.input_tokens, m.output_tokens, m.cache_tokens, m.messages, m.total_tokens), (97, 3, 90, 1, 100))
        self.assertEqual(m.hourly_tokens[agora.hour], 100)
        self.assertEqual(m.scope, "today")

    def test_etapa_que_falha_vira_parcial(self):
        self.sessao("1", sessionId="a", status="busy")
        with mock.patch.object(ColetorClaude, "_sessoes", side_effect=PermissionError("sem acesso")):
            s = self.provider.collect()
        self.assertIs(s.status, SnapshotStatus.PARTIAL)
        self.assertEqual(s.failed, frozenset({"sessions"}))
        self.assertEqual(s.sessions, ())  # vazio porque falhou, e o snapshot diz isso
        self.assertIsNotNone(s.usage)


class MesmoResultadoDeAntes(unittest.TestCase):
    """O caminho novo (coletor -> provider -> apresentação) dá o mesmo UsoClaude que o coletor sozinho."""

    def igual(self, u):
        v = uso_claude(snapshot_de_uso(u, AGORA))
        for f in dataclasses.fields(u):
            if f.name not in ("lista_sessoes", "falhas"):  # só existem no lado do coletor
                self.assertEqual(getattr(v, f.name), getattr(u, f.name), f.name)

    def test_todos_os_cenarios_do_demo(self):
        d = Demo()
        for i in range(len(Demo._CENARIOS)):
            d.t0 = time.time() - i - .5
            self.igual(d.claude(1))
        self.igual(d.claude(0))

    def test_sem_dados_e_coleta_parcial(self):
        self.igual(claude.UsoClaude())
        u = Demo().claude(0)
        u.falhas, u.erros = ["tokens"], ["tokens: x"]
        u.tokens_entrada = u.tokens_saida = u.tokens_cache = u.mensagens_hoje = 0
        u.tokens_por_hora = [None] * 24
        self.igual(u)

    def test_varias_esperando_vale_a_mais_antiga(self):
        S = claude.SessaoClaude
        agora = datetime.now()
        lista = [S("a", "waiting", "waiting", agora - timedelta(minutes=2), "nova"),
                 S("b", "waiting", "waiting", agora - timedelta(minutes=9), None),
                 S("c", "waiting", "waiting", None, "sem-horario"),
                 S("d", "busy", "busy")]
        u = claude.UsoClaude(lista_sessoes=lista, **claude.resumo_sessoes(lista))
        self.assertEqual((u.espera_projeto, u.esperando), ("sessão", 3))
        self.igual(u)

    def test_demo_passa_pelo_provider(self):
        s = ClaudeProvider(FonteDemo(Demo(), 0), relogio=lambda: AGORA).collect()
        self.assertEqual(s.provider_id, "claude")
        self.assertEqual(len(s.sessions), 3)
        self.assertTrue(any(isinstance(x, AgentSession) and x.attention is Attention.WAITING_USER for x in s.sessions))


if __name__ == "__main__":
    unittest.main()
