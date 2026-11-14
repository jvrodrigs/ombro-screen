"""StateStore: snapshots, eventos, isolamento por provider, atrasos, falhas e recuperação."""
import time
import unittest
from datetime import datetime, timedelta

from apresentacao import uso_claude
from coletores.demo import Demo
from core.events import AgentEvent, EventBus, EventType
from core.models import Activity, AgentSession, Attention, ProviderSnapshot, QuotaReport, QuotaWindow, UsageMetrics
from core.state import StateStore
from mascote import ESTADOS, Mascote
from providers.claude import snapshot_de_uso

T0 = datetime(2026, 11, 14, 15, 0)


def t(seg):
    return T0 + timedelta(seconds=seg)


def sessao(sid, atividade="working", atencao="none", provider="claude"):
    return AgentSession(provider, sid, atividade, atencao)


def snap(seg, *sessoes, provider="claude", **kw):
    return ProviderSnapshot(provider, t(seg), sessions=sessoes, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.eventos = []
        self.bus.subscribe(self.eventos.append)
        self.store = StateStore(self.bus)

    def tipos(self):
        return [(e.type.value, e.session_id) for e in self.eventos]


class Snapshots(Base):
    def test_comecar_mudar_e_acabar(self):
        self.store.apply_snapshot(snap(0, sessao("a"), sessao("b", "idle")))
        self.store.apply_snapshot(snap(5, sessao("a", "idle", "waiting_user"), sessao("b", "idle")))
        self.store.apply_snapshot(snap(10, sessao("b", "idle")))
        self.assertEqual(self.tipos(), [
            ("session_started", "a"), ("session_started", "b"), ("provider_changed", None),
            ("session_changed", "a"),
            ("session_ended", "a"),
        ])
        self.assertEqual([s.session_id for s in self.store.sessions("claude")], ["b"])

    def test_snapshot_igual_nao_gera_evento_mas_atualiza_o_horario(self):
        for seg in range(0, 30, 5):
            self.store.apply_snapshot(snap(seg, sessao("a")))
        self.assertEqual(len(self.eventos), 2)  # started + provider_changed, uma vez
        self.assertEqual(self.store.snapshot("claude").collected_at, t(25))

    def test_snapshot_atrasado_nao_apaga_dado_novo(self):
        self.store.apply_snapshot(snap(10, sessao("a", "idle")))
        self.assertEqual(self.store.apply_snapshot(snap(5, sessao("a"))), [])
        self.assertIs(self.store.sessions("claude")[0].activity, Activity.IDLE)

    def test_providers_isolados(self):
        self.store.apply_snapshot(snap(0, sessao("1")))
        self.store.apply_snapshot(snap(0, sessao("1", "idle", provider="codex"), provider="codex"))
        self.store.apply_snapshot(snap(5))  # o claude não tem mais sessões
        self.assertEqual(self.store.sessions("claude"), [])
        self.assertEqual([s.key for s in self.store.sessions("codex")], [("codex", "1")])
        self.assertEqual(len(self.store.sessions()), 1)
        self.assertEqual(self.store.sessions("cursor"), [])

    def test_mudanca_que_nao_e_de_status_nao_avisa(self):
        self.store.apply_snapshot(snap(0, sessao("a")))
        n = len(self.eventos)
        mudou = AgentSession("claude", "a", "working", "none", status_since=t(3))
        self.store.apply_snapshot(snap(5, mudou))
        self.assertEqual(len(self.eventos), n)
        self.assertEqual(self.store.sessions("claude")[0].status_since, t(3))  # mas o estado guarda


class Falhas(Base):
    def test_falha_passageira_repete_o_ultimo_valor(self):
        uso = UsageMetrics("today", input_tokens=10)
        self.store.apply_snapshot(snap(0, sessao("a"), usage=uso))
        self.store.apply_snapshot(snap(5, status="partial", failed={"sessions"}, usage=uso))
        st = self.store.provider("claude")
        self.assertEqual([s.session_id for s in self.store.sessions("claude")], ["a"])  # não "acabou"
        self.assertEqual(st.partes_velhas, frozenset({"sessions"}))
        self.assertNotIn("session_ended", [e for e, _ in self.tipos()])

    def test_falha_longa_deixa_as_sessoes_desconhecidas_sem_encerrar(self):
        self.store.apply_snapshot(snap(0, sessao("a")))
        for seg in (5, 20, 31, 40):
            self.store.apply_snapshot(snap(seg, status="partial", failed={"sessions"}))
        s = self.store.sessions("claude")[0]
        self.assertEqual((s.activity, s.attention), (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertNotIn("session_ended", [e for e, _ in self.tipos()])
        self.store.apply_snapshot(snap(45, sessao("a")))  # voltou a ler
        self.assertIs(self.store.sessions("claude")[0].activity, Activity.WORKING)

    def test_erro_total_mantem_tudo_por_um_tempo(self):
        q = QuotaReport("statusline", t(0), windows=[QuotaWindow("5h", "sessão 5h", 40)])
        self.store.apply_snapshot(snap(0, sessao("a"), quotas=q))
        self.store.apply_snapshot(ProviderSnapshot("claude", t(5), status="error", errors=["x"]))
        efetivo = self.store.snapshot("claude")
        self.assertEqual(efetivo.quotas, q)
        self.assertEqual(len(efetivo.sessions), 1)
        self.assertEqual(self.tipos()[-1], ("provider_changed", None))
        self.store.apply_snapshot(ProviderSnapshot("claude", t(60), status="error"))
        self.assertIsNone(self.store.snapshot("claude").quotas)  # velho demais: some, como antes

    def test_erro_antes_de_qualquer_coleta(self):
        self.store.apply_snapshot(ProviderSnapshot("claude", t(0), status="error"))
        self.assertEqual(self.store.sessions("claude"), [])

    def test_consumidor_quebrado_nao_corrompe_o_estado(self):
        def quebrado(e):
            raise RuntimeError("bug")

        self.bus.subscribe(quebrado)
        with self.assertLogs("ombro.eventos", "ERROR"):
            self.store.apply_snapshot(snap(0, sessao("a"), sessao("b")))
        self.assertEqual(len(self.store.sessions("claude")), 2)
        self.assertEqual(len(self.eventos), 3)


class Eventos(Base):
    def test_evento_aplica_e_publica(self):
        e = AgentEvent(EventType.SESSION_STARTED, "codex", t(1), "x", sessao("x", provider="codex"))
        self.assertTrue(self.store.apply_event(e))
        self.assertEqual(self.store.sessions("codex")[0].session_id, "x")
        self.assertEqual(self.eventos, [e])

    def test_repetido_atrasado_e_de_sessao_encerrada(self):
        inicio = AgentEvent(EventType.SESSION_STARTED, "codex", t(1), "x", sessao("x", provider="codex"))
        parada = AgentEvent(EventType.SESSION_CHANGED, "codex", t(5), "x", sessao("x", "idle", provider="codex"))
        atrasado = AgentEvent(EventType.SESSION_CHANGED, "codex", t(3), "x", sessao("x", "working", "waiting_user", provider="codex"))
        fim = AgentEvent(EventType.SESSION_ENDED, "codex", t(8), "x")
        tarde = AgentEvent(EventType.SESSION_CHANGED, "codex", t(7), "x", sessao("x", provider="codex"))
        self.assertTrue(self.store.apply_event(inicio))
        self.assertFalse(self.store.apply_event(inicio))  # repetido
        self.assertTrue(self.store.apply_event(parada))
        self.assertFalse(self.store.apply_event(atrasado))  # mais velho que o último
        self.assertIs(self.store.sessions("codex")[0].activity, Activity.IDLE)
        self.assertTrue(self.store.apply_event(fim))
        self.assertFalse(self.store.apply_event(fim))
        self.assertFalse(self.store.apply_event(tarde))  # depois do fim, com horário de antes
        self.assertEqual(self.store.sessions("codex"), [])
        self.assertTrue(self.store.apply_event(AgentEvent(EventType.SESSION_STARTED, "codex", t(20), "x",
                                                          sessao("x", provider="codex"))))  # começou de novo

    def test_evento_de_provider_nao_mexe_em_sessoes(self):
        self.assertFalse(self.store.apply_event(AgentEvent(EventType.PROVIDER_CHANGED, "codex", t(1), detail="ok")))


class Recuperacao(Base):
    def test_sessoes_do_banco_entram_desconhecidas_e_a_coleta_concilia(self):
        n = self.store.restore([sessao("a"), sessao("b", "idle", "waiting_user"), sessao("a")])
        self.assertEqual(n, 2)
        self.assertTrue(all(s.activity is Activity.UNKNOWN for s in self.store.sessions("claude")))
        self.assertTrue(self.store.provider("claude").recuperado)
        self.store.apply_snapshot(snap(0, sessao("a")))  # "a" continua, "b" acabou enquanto o Ombro estava fechado
        self.assertEqual(self.tipos(), [("session_changed", "a"), ("session_ended", "b"), ("provider_changed", None)])
        self.assertFalse(self.store.provider("claude").recuperado)
        self.assertEqual(len(self.store.sessions("claude")), 1)  # sem duplicar


class MascoteIgual(unittest.TestCase):
    """O cloud decide igual com os dados vindos do StateStore e do coletor direto, quadro a quadro."""

    def test_ciclo_de_demonstracao(self):
        demo = Demo()
        store = StateStore(EventBus())
        direto, novo = Mascote(duracao_concluido=7), Mascote(duracao_concluido=7)
        vistos = set()
        relogio = 0.0
        for volta in range(2):
            for i in range(len(Demo._CENARIOS)):
                demo.t0 = time.time() - i * 7 - 1
                u = demo.claude(7)
                store.apply_snapshot(snapshot_de_uso(u, T0 + timedelta(seconds=relogio)))
                v = uso_claude(store.snapshot("claude"))
                for _ in range(35):
                    a = direto.decidir(u, relogio)
                    b = novo.decidir(v, relogio)
                    self.assertEqual(a, b, f"t={relogio:.1f}")
                    direto.ir_para(a, relogio)
                    novo.ir_para(b, relogio)
                    vistos.add(a)
                    relogio += .2
        self.assertEqual(vistos, set(ESTADOS))


if __name__ == "__main__":
    unittest.main()
