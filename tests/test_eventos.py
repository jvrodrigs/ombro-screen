"""EventBus: ordem, vários inscritos, cancelamento, falhas isoladas e eventos repetidos."""
import unittest
from datetime import datetime, timedelta

from core.events import AgentEvent, EventBus, EventType, registrar_no_log
from core.models import AgentSession

AGORA = datetime(2026, 11, 14, 15, 0)


def ev(n=0, tipo=EventType.SESSION_CHANGED, sid="s1", atividade="working", provider="claude"):
    sessao = AgentSession(provider, sid, atividade, "none") if tipo is not EventType.SESSION_ENDED else None
    return AgentEvent(tipo, provider, AGORA + timedelta(seconds=n), sid, sessao)


class Evento(unittest.TestCase):
    def test_id_sai_do_conteudo(self):
        self.assertEqual(ev(1).event_id, ev(1).event_id)
        self.assertNotEqual(ev(1).event_id, ev(2).event_id)
        self.assertNotEqual(ev(1).event_id, ev(1, atividade="idle").event_id)
        self.assertNotEqual(ev(1).event_id, ev(1, provider="codex").event_id)

    def test_validacao(self):
        with self.assertRaises(ValueError):
            AgentEvent(EventType.SESSION_CHANGED, "claude", AGORA, "s1")  # sem a sessão
        with self.assertRaises(ValueError):
            AgentEvent(EventType.SESSION_ENDED, "claude", AGORA)  # sem session_id
        with self.assertRaises(ValueError):
            AgentEvent(EventType.SESSION_STARTED, "claude", AGORA, "s2", AgentSession("claude", "s1"))
        with self.assertRaises(TypeError):
            AgentEvent(EventType.PROVIDER_CHANGED, "claude", "agora")
        AgentEvent(EventType.PROVIDER_CHANGED, "claude", AGORA, detail="error")


class Barramento(unittest.TestCase):
    def test_entrega_em_ordem_a_todos(self):
        bus, a, b = EventBus(), [], []
        bus.subscribe(a.append)
        bus.subscribe(b.append)
        eventos = [ev(i) for i in range(5)]
        for e in eventos:
            self.assertTrue(bus.publish(e))
        self.assertEqual(a, eventos)
        self.assertEqual(b, eventos)

    def test_filtro_por_tipo(self):
        bus, fins = EventBus(), []
        bus.subscribe(fins.append, tipos=[EventType.SESSION_ENDED])
        bus.publish(ev(1))
        bus.publish(ev(2, EventType.SESSION_ENDED))
        self.assertEqual([e.type for e in fins], [EventType.SESSION_ENDED])

    def test_cancelar_inscricao(self):
        bus, a = EventBus(), []
        cancelar = bus.subscribe(a.append)
        bus.publish(ev(1))
        self.assertTrue(cancelar())
        self.assertFalse(bus.unsubscribe(a.append))
        bus.publish(ev(2))
        self.assertEqual(len(a), 1)

    def test_repetido_nao_entrega_de_novo(self):
        bus, a = EventBus(), []
        bus.subscribe(a.append)
        self.assertTrue(bus.publish(ev(1)))
        self.assertFalse(bus.publish(ev(1)))
        self.assertEqual(len(a), 1)

    def test_memoria_de_repetidos_e_limitada(self):
        bus = EventBus(memoria=3)
        for i in range(5):
            bus.publish(ev(i))
        self.assertTrue(bus.publish(ev(0)))  # esquecido: entra de novo
        self.assertFalse(bus.publish(ev(4)))

    def test_inscrito_que_falha_nao_atrapalha(self):
        bus, depois = EventBus(), []

        def quebrado(e):
            raise RuntimeError("bug")

        bus.subscribe(quebrado)
        bus.subscribe(depois.append)
        with self.assertLogs("ombro.eventos", "ERROR") as logs:
            for i in range(3):
                bus.publish(ev(i))
        self.assertEqual(len(depois), 3)
        self.assertEqual(len(logs.records), 1)  # a mesma falha entra uma vez

    def test_publicar_durante_a_entrega_mantem_a_ordem(self):
        bus, ordem = EventBus(), []

        def reage(e):
            ordem.append(("a", e.session_id))
            if e.session_id == "s1":
                bus.publish(ev(9, sid="s2"))

        bus.subscribe(reage)
        bus.subscribe(lambda e: ordem.append(("b", e.session_id)))
        bus.publish(ev(1, sid="s1"))
        self.assertEqual(ordem, [("a", "s1"), ("b", "s1"), ("a", "s2"), ("b", "s2")])

    def test_log_de_depuracao_sem_projeto(self):
        e = AgentEvent(EventType.SESSION_STARTED, "claude", AGORA, "abc", AgentSession("claude", "abc", project="segredo"))
        with self.assertLogs("ombro.eventos", "DEBUG") as logs:
            registrar_no_log(e)
            registrar_no_log(AgentEvent(EventType.PROVIDER_CHANGED, "claude", AGORA, detail="error"))
        texto = "\n".join(logs.output)
        self.assertIn("session_started claude/abc", texto)
        self.assertNotIn("segredo", texto)


if __name__ == "__main__":
    unittest.main()
