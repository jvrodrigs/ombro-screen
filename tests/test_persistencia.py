"""SQLite: formato, gravação, recuperação, retenção, migração e falhas. Só bancos temporários."""
import sqlite3
import stat
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from core import persistence
from core.events import AgentEvent, EventType
from core.models import Activity, AgentSession, ProviderSnapshot
from core.monitor import Monitor
from core.persistence import StateDB
from core.providers import ProviderAdapter, ProviderCapabilities

T0 = datetime(2026, 11, 14, 15, 0)


def t(seg=0, dias=0):
    return T0 + timedelta(seconds=seg, days=dias)


def ev(tipo, sid, seg=0, atividade="working", atencao="none", provider="claude", projeto=None, dias=0):
    s = None if tipo is EventType.SESSION_ENDED else AgentSession(provider, sid, atividade, atencao, project=projeto,
                                                                  started_at=t(0))
    return AgentEvent(tipo, provider, t(seg, dias), sid, s)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pasta = Path(tmp.name)
        self.caminho = self.pasta / "sub" / "state.db"

    def banco(self, relogio=lambda: T0):
        db = StateDB(self.caminho, relogio)
        self.addCleanup(db.fechar)
        return db

    def linhas(self, sql):
        with closing(sqlite3.connect(str(self.caminho))) as c:
            return c.execute(sql).fetchall()


class Formato(Base):
    def test_cria_o_banco_na_versao_atual(self):
        db = self.banco()
        self.assertTrue(db.abrir())
        self.assertEqual(self.linhas("PRAGMA user_version"), [(persistence.VERSAO,)])
        tabelas = {n for (n,) in self.linhas("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"providers", "sessions", "events"} <= tabelas)
        self.assertEqual(stat.S_IMODE(self.caminho.stat().st_mode), 0o600)

    def test_reabrir_nao_migra_de_novo(self):
        db = self.banco()
        self.assertTrue(db.abrir())
        db.ao_evento(ev(EventType.SESSION_STARTED, "a"))
        db.fechar()
        with mock.patch.dict(persistence.MIGRACOES, {1: ["CREATE TABLE nao_deveria_rodar (x)"]}):
            self.assertTrue(self.banco().abrir())
        self.assertEqual(self.linhas("SELECT session_id FROM sessions"), [("a",)])  # os dados continuam

    def test_migracao_que_falha_nao_deixa_nada_pela_metade(self):
        ruim = {1: persistence.MIGRACOES[1][:1] + ["CREATE TABLE quebrada ("]}
        with mock.patch.object(persistence, "MIGRACOES", ruim), self.assertLogs("ombro.persistencia", "WARNING"):
            self.assertFalse(self.banco().abrir())
        self.assertEqual(self.linhas("PRAGMA user_version"), [(0,)])
        self.assertEqual(self.linhas("SELECT name FROM sqlite_master WHERE type = 'table'"), [])
        self.assertTrue(self.banco().abrir())  # com a migração certa, depois, funciona

    def test_banco_de_versao_mais_nova_fica_intocado(self):
        self.caminho.parent.mkdir(parents=True)
        with closing(sqlite3.connect(str(self.caminho))) as c:
            c.execute("PRAGMA user_version = 99")
        antes = self.caminho.read_bytes()
        with self.assertLogs("ombro.persistencia", "WARNING") as logs:
            self.assertFalse(self.banco().abrir())
        self.assertIn("versão mais nova", logs.output[0])
        self.assertEqual(self.caminho.read_bytes(), antes)


class Gravacao(Base):
    def setUp(self):
        super().setUp()
        self.db = self.banco()
        self.db.abrir()

    def test_sessao_comeca_muda_e_acaba(self):
        self.db.ao_evento(ev(EventType.SESSION_STARTED, "a"))
        self.db.ao_evento(ev(EventType.SESSION_CHANGED, "a", 5, "idle", "waiting_user"))
        self.assertEqual(self.linhas("SELECT activity, attention, ended_at FROM sessions"), [("idle", "waiting_user", None)])
        self.assertEqual([s.session_id for s in self.db.sessoes_abertas(t(10))], ["a"])
        self.db.ao_evento(ev(EventType.SESSION_ENDED, "a", 9))
        self.assertEqual(self.db.sessoes_abertas(t(10)), [])
        self.assertEqual(self.linhas("SELECT count(*) FROM events"), [(3,)])

    def test_mesmo_id_em_providers_diferentes(self):
        self.db.ao_evento(ev(EventType.SESSION_STARTED, "1"))
        self.db.ao_evento(ev(EventType.SESSION_STARTED, "1", provider="codex", atividade="idle"))
        self.db.ao_evento(ev(EventType.SESSION_ENDED, "1", 3))  # só a do claude
        abertas = self.db.sessoes_abertas(t(10))
        self.assertEqual([s.key for s in abertas], [("codex", "1")])
        self.assertIs(abertas[0].activity, Activity.IDLE)

    def test_evento_repetido_grava_uma_vez(self):
        e = ev(EventType.SESSION_STARTED, "a")
        self.db.ao_evento(e)
        self.db.ao_evento(e)
        self.assertEqual(self.linhas("SELECT count(*) FROM events"), [(1,)])
        self.assertEqual(self.linhas("SELECT count(*) FROM sessions"), [(1,)])

    def test_nao_guarda_nome_de_projeto(self):
        self.db.ao_evento(ev(EventType.SESSION_STARTED, "a", projeto="projeto-secreto"))
        self.db.fechar()
        self.assertNotIn(b"projeto-secreto", self.caminho.read_bytes())

    def test_status_do_provider(self):
        self.db.ao_evento(AgentEvent(EventType.PROVIDER_CHANGED, "claude", t(1), detail="partial"))
        self.db.ao_evento(AgentEvent(EventType.PROVIDER_CHANGED, "claude", t(2), detail="ok"))
        self.assertEqual(self.linhas("SELECT provider_id, status FROM providers"), [("claude", "ok")])

    def test_sessoes_sem_noticia_ha_muito_tempo_nao_voltam(self):
        self.db.ao_evento(ev(EventType.SESSION_STARTED, "velha"))
        self.db.ao_evento(ev(EventType.SESSION_STARTED, "nova", dias=6))
        self.assertEqual([s.session_id for s in self.db.sessoes_abertas(t(dias=8))], ["nova"])


class Retencao(Base):
    def test_apaga_o_que_passou_do_prazo(self):
        db = self.banco()
        db.abrir()
        db.ao_evento(ev(EventType.SESSION_STARTED, "acabou"))
        db.ao_evento(ev(EventType.SESSION_ENDED, "acabou", 1))
        db.ao_evento(ev(EventType.SESSION_STARTED, "esquecida"))
        db.ao_evento(ev(EventType.SESSION_STARTED, "viva", dias=7))
        self.assertTrue(db.limpar(t(dias=8)))
        self.assertEqual(self.linhas("SELECT session_id FROM sessions"), [("viva",)])
        self.assertEqual(self.linhas("SELECT session_id FROM events"), [("viva",)])

    def test_limite_de_eventos_e_uma_vez_por_hora(self):
        db = self.banco()
        db.abrir()
        with mock.patch.object(persistence, "MAX_EVENTOS", 3):
            for i in range(6):
                db.ao_evento(ev(EventType.SESSION_CHANGED, "a", i, "working" if i % 2 else "idle"))
            self.assertTrue(db.limpar(t(10)))
            self.assertEqual(self.linhas("SELECT count(*) FROM events"), [(3,)])
            self.assertFalse(db.limpar(t(20)))  # menos de uma hora depois: não roda
            self.assertTrue(db.limpar(t(20), forcar=True))


class Falhas(Base):
    def test_arquivo_corrompido_nao_e_apagado(self):
        self.caminho.parent.mkdir(parents=True)
        self.caminho.write_bytes(b"isto nao e um banco sqlite" * 100)
        antes = self.caminho.read_bytes()
        with self.assertLogs("ombro.persistencia", "WARNING") as logs:
            self.assertFalse(self.banco().abrir())
        self.assertIn("não foi apagado", logs.output[0])
        self.assertEqual(self.caminho.read_bytes(), antes)

    def test_caminho_indisponivel(self):
        self.caminho.mkdir(parents=True)  # uma pasta no lugar do arquivo
        db = self.banco()
        with self.assertLogs("ombro.persistencia", "WARNING"):
            self.assertFalse(db.abrir())
        db.ao_evento(ev(EventType.SESSION_STARTED, "a"))  # desligado: não faz nada nem levanta
        self.assertEqual(db.sessoes_abertas(), [])

    def test_banco_travado_pula_a_gravacao_e_avisa_uma_vez(self):
        db = self.banco()
        db.abrir()
        db._conn.execute("PRAGMA busy_timeout = 20")
        outro = sqlite3.connect(str(self.caminho), isolation_level=None)
        outro.execute("BEGIN EXCLUSIVE")
        with self.assertLogs("ombro.persistencia", "WARNING") as logs:
            for i in range(3):
                db.ao_evento(ev(EventType.SESSION_CHANGED, "a", i, "working" if i % 2 else "idle"))
        self.assertEqual(len(logs.records), 1)
        self.assertTrue(db.ativo)  # passageiro: continua ligado
        outro.execute("ROLLBACK")
        outro.close()
        with self.assertLogs("ombro.persistencia", "INFO"):
            db.ao_evento(ev(EventType.SESSION_STARTED, "b"))
        self.assertEqual(self.linhas("SELECT session_id FROM sessions"), [("b",)])


class FalsoClaude(ProviderAdapter):
    provider_id = "claude"
    capabilities = ProviderCapabilities(sessions=True)

    def __init__(self, sessoes, relogio):
        self.sessoes, self.relogio = sessoes, relogio

    def collect(self):
        return ProviderSnapshot("claude", self.relogio(), sessions=[AgentSession("claude", s, "working", "none")
                                                                    for s in self.sessoes])


class Reinicio(Base):
    def test_ciclo_completo(self):
        relogio = [t(0)]
        agora = lambda: relogio[0]  # noqa: E731
        p = FalsoClaude(["a", "b"], agora)
        m = Monitor.montar([p], banco=self.caminho, relogio=agora)
        for i in range(10):  # coletas iguais: grava só a primeira mudança
            relogio[0] = t(5 * i)
            m.atualizar("claude")
        self.assertEqual(self.linhas("SELECT count(*) FROM events"), [(3,)])  # 2 começos + status do provider
        m.fechar()

        relogio[0] = t(100)
        p2 = FalsoClaude(["a"], agora)  # "b" acabou enquanto o Ombro estava fechado
        m2 = Monitor.montar([p2], banco=self.caminho, relogio=agora)
        recuperadas = m2.store.sessions("claude")
        self.assertEqual(sorted(s.session_id for s in recuperadas), ["a", "b"])
        self.assertTrue(all(s.activity is Activity.UNKNOWN for s in recuperadas))  # não "trabalhando" só porque estava
        snap = m2.atualizar("claude")
        self.assertEqual([s.session_id for s in snap.sessions], ["a"])
        self.assertEqual(self.linhas("SELECT session_id FROM sessions WHERE ended_at IS NULL"), [("a",)])
        self.assertEqual(self.linhas("SELECT count(*) FROM sessions"), [(2,)])  # sem duplicar
        m2.fechar()

    def test_sem_banco_fica_so_em_memoria(self):
        m = Monitor.montar([FalsoClaude(["a"], lambda: T0)])
        self.assertEqual(len(m.atualizar("claude").sessions), 1)
        self.assertIsNone(m.banco)
        self.assertFalse(self.caminho.exists())


if __name__ == "__main__":
    unittest.main()
