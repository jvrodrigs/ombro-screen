"""core/models.py: validação, campos opcionais, chaves compostas e snapshots."""
import math
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from pathlib import Path

from core.models import (
    Activity, AgentSession, Attention, ProviderSnapshot, QuotaReport, QuotaWindow, SnapshotStatus, UsageMetrics,
)

AGORA = datetime(2026, 11, 14, 15, 0)
RAIZ = Path(__file__).resolve().parent.parent


class Sessao(unittest.TestCase):
    def test_padrao_e_desconhecido_nao_ocioso(self):
        s = AgentSession("claude", "abc")
        self.assertEqual((s.activity, s.attention), (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertIsNone(s.project)

    def test_chave_composta_separa_providers(self):
        a, b = AgentSession("claude", "1"), AgentSession("codex", "1")
        self.assertNotEqual(a.key, b.key)
        self.assertEqual(len({a.key, b.key}), 2)

    def test_aceita_texto_do_enum(self):
        s = AgentSession("claude", "x", activity="working", attention="waiting_user")
        self.assertIs(s.activity, Activity.WORKING)
        self.assertIs(s.attention, Attention.WAITING_USER)

    def test_valores_invalidos(self):
        for args, kw in ((("", "x"), {}), (("claude", "  "), {}), (("claude", 3), {}), (("claude", "x"), {"activity": "busy"}),
                         (("claude", "x"), {"project": ""}), (("claude", "x"), {"started_at": "ontem"}),
                         (("claude", "x"), {"updated_at": 1700000000})):
            with self.assertRaises((ValueError, TypeError), msg=(args, kw)):
                AgentSession(*args, **kw)

    def test_imutavel(self):
        with self.assertRaises(FrozenInstanceError):
            AgentSession("claude", "x").activity = Activity.IDLE


class Uso(unittest.TestCase):
    def test_ausente_nao_e_zero(self):
        m = UsageMetrics("today")
        self.assertIsNone(m.total_tokens)
        self.assertIsNone(m.cost_usd)
        self.assertEqual(UsageMetrics("today", input_tokens=0).total_tokens, 0)
        self.assertEqual(UsageMetrics("today", input_tokens=10, output_tokens=5).total_tokens, 15)
        self.assertEqual(UsageMetrics("today", output_tokens=5).total_tokens, 5)

    def test_extremos_e_invalidos(self):
        self.assertEqual(UsageMetrics("today", input_tokens=10**15).input_tokens, 10**15)
        for kw in ({"input_tokens": -1}, {"output_tokens": 1.5}, {"messages": True}, {"context_percent": 101},
                   {"context_percent": math.nan}, {"cost_usd": -0.01}, {"hourly_tokens": [1] * 23},
                   {"hourly_tokens": [None] * 23 + [-5]}):
            with self.assertRaises(ValueError, msg=kw):
                UsageMetrics("today", **kw)
        with self.assertRaises(ValueError):
            UsageMetrics("")

    def test_horas_viram_tupla(self):
        m = UsageMetrics("today", hourly_tokens=[0] * 10 + [None] * 14)
        self.assertIsInstance(m.hourly_tokens, tuple)
        hash(m)  # dá para comparar e deduplicar


class Quotas(unittest.TestCase):
    def test_janela_generica(self):
        w = QuotaWindow("mensal", "mês", 12, AGORA + timedelta(days=20))
        self.assertEqual(w.used_percent, 12.0)
        self.assertIsNone(QuotaWindow("x", "x").used_percent)  # sem percentual: não inventa

    def test_percentual_fora_da_faixa(self):
        for v in (-0.1, 100.01, math.nan, "50", True):
            with self.assertRaises(ValueError):
                QuotaWindow("5h", "sessão 5h", v)

    def test_relatorio(self):
        r = QuotaReport("statusline", AGORA, windows=[QuotaWindow("5h", "sessão 5h", 40)])
        self.assertEqual(r.window("5h").used_percent, 40.0)
        self.assertIsNone(r.window("7d"))
        self.assertEqual(QuotaReport("/usage", AGORA).windows, ())  # fonte sem janelas ≠ sem fonte
        with self.assertRaises(ValueError):
            QuotaReport("x", AGORA, windows=[QuotaWindow("5h", "a"), QuotaWindow("5h", "b")])
        with self.assertRaises(TypeError):
            QuotaReport("x", None)


class Snapshot(unittest.TestCase):
    def test_sem_sessoes_nao_e_falha(self):
        s = ProviderSnapshot("claude", AGORA)
        self.assertIs(s.status, SnapshotStatus.OK)
        self.assertEqual((s.sessions, s.failed), ((), frozenset()))

    def test_parcial_diz_o_que_falhou(self):
        s = ProviderSnapshot("claude", AGORA, status="partial", failed={"sessions"}, errors=["sessões: x"])
        self.assertEqual(s.failed, frozenset({"sessions"}))
        with self.assertRaises(ValueError):
            ProviderSnapshot("claude", AGORA, status="partial")
        with self.assertRaises(ValueError):
            ProviderSnapshot("claude", AGORA, failed={"sessions"})  # OK com falha
        with self.assertRaises(ValueError):
            ProviderSnapshot("claude", AGORA, status="partial", failed={"disco"})

    def test_sessoes_do_proprio_provider_e_sem_repetir(self):
        with self.assertRaises(ValueError):
            ProviderSnapshot("claude", AGORA, sessions=[AgentSession("codex", "1")])
        with self.assertRaises(ValueError):
            ProviderSnapshot("claude", AGORA, sessions=[AgentSession("claude", "1"), AgentSession("claude", "1")])

    def test_mesmo_conteudo_ignora_so_o_horario(self):
        a = ProviderSnapshot("claude", AGORA, sessions=[AgentSession("claude", "1", "working", "none")])
        self.assertTrue(replace(a, collected_at=AGORA + timedelta(seconds=5)).mesmo_conteudo(a))
        self.assertFalse(replace(a, sessions=()).mesmo_conteudo(a))
        self.assertFalse(a.mesmo_conteudo(None))


class Independencia(unittest.TestCase):
    def test_core_nao_carrega_tela_usb_nem_pillow(self):
        codigo = ("import sys; import core.models, core.providers, core.events, core.state, core.persistence, core.monitor; "
                  "print(sorted(m for m in ('PIL', 'usb', 'serial', 'numpy', 'psutil') if m in sys.modules))")
        r = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
