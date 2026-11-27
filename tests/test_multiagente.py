"""Consolidação de vários agentes: resumo, Monitor com eventos e banco, configuração e o layout."""
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import layout_multiagente as lm
import ombro_hook
from coletores.hardware import Hardware
from config import ConfigInvalida, OmbroConfig, resolver
from core.models import Activity as A
from core.models import AgentSession, ProviderSnapshot, QuotaReport, QuotaWindow
from core.models import Attention as T
from core.monitor import Monitor
from core.resumo import resumir, resumir_provider
from core.state import StateStore
from layout_minimal_v2 import CPU_Y, HW_BARRA_X, Tema
from mascote import Mascote
from providers.codex import CodexProvider
from providers.cursor import CursorProvider

AGORA = datetime(2026, 11, 14, 15, 0)
GB = 1024**3
NOMES = [("claude", "Claude Code"), ("codex", "Codex"), ("cursor", "Cursor")]


def s(pid, sid, a=A.WORKING, t=T.NONE, proj="proj", minutos=1):
    return AgentSession(pid, sid, a, t, project=proj, status_since=AGORA - timedelta(minutes=minutos), updated_at=AGORA)


def store_com(*snaps):
    st = StateStore()
    for snap in snaps:
        st.apply_snapshot(snap)
    return st


def snap(pid, *sessoes, **kw):
    return ProviderSnapshot(pid, AGORA, sessions=sessoes, **kw)


class Resumo(unittest.TestCase):
    def test_tres_agentes(self):
        st = store_com(snap("claude", s("claude", "a"), s("claude", "b", A.IDLE, T.NONE)),
                       snap("codex", s("codex", "c", A.WORKING, T.WAITING_APPROVAL)),
                       snap("cursor", s("cursor", "d", A.WORKING, T.UNKNOWN)))
        r = resumir(st, NOMES)
        self.assertEqual((r.total_sessoes, r.trabalhando, r.aguardando, r.providers_ativos), (4, 3, 1, 3))
        self.assertEqual([p.estado for p in r.providers], ["trabalhando", "aprovacao", "trabalhando"])
        self.assertEqual((r.mais_urgente.provider_id, r.mais_urgente.motivo), ("codex", "aprovacao"))

    def test_prioridade_de_atencao(self):
        st = store_com(snap("claude", s("claude", "a", A.IDLE, T.WAITING_USER, minutos=30)),
                       snap("codex", s("codex", "b", A.WORKING, T.WAITING_APPROVAL, minutos=1)),
                       snap("cursor", s("cursor", "c", A.IDLE, T.ERROR, minutos=1)))
        self.assertEqual(resumir(st, NOMES).mais_urgente.motivo, "erro")  # erro > aprovação > resposta
        st = store_com(snap("claude", s("claude", "a", A.IDLE, T.WAITING_USER, minutos=30)),
                       snap("codex", s("codex", "b", A.IDLE, T.WAITING_USER, minutos=2)))
        self.assertEqual(resumir(st, NOMES).mais_urgente.provider_id, "claude")  # empate: quem espera há mais tempo

    def test_mesmo_id_em_providers_diferentes(self):
        st = store_com(snap("claude", s("claude", "1")), snap("codex", s("codex", "1", A.IDLE)))
        r = resumir(st, NOMES)
        self.assertEqual(r.total_sessoes, 2)
        self.assertEqual((r.provider("claude").trabalhando, r.provider("codex").trabalhando), (1, 0))

    def test_falha_de_um_nao_afeta_os_outros(self):
        st = store_com(snap("claude", s("claude", "a")), ProviderSnapshot("codex", AGORA, status="error", errors=["x"]))
        r = resumir(st, NOMES)
        self.assertEqual((r.provider("claude").estado, r.provider("codex").estado), ("trabalhando", "indisponivel"))

    def test_estados_sem_sessoes(self):
        casos = {"nao_instalado": snap("codex", integration="not_installed"),
                 "nao_configurado": snap("codex", integration="not_configured"),
                 "sem_sessoes": snap("codex"), "indisponivel": None}
        for esperado, sn in casos.items():
            self.assertEqual(resumir_provider("codex", "Codex", sn, ()).estado, esperado)
        self.assertEqual(resumir_provider("codex", "Codex", snap("codex"), [s("codex", "x", A.UNKNOWN, T.UNKNOWN)]).estado,
                         "desconhecido")

    def test_alerta_de_limite_so_com_quota_verificada(self):
        q = QuotaReport("statusline", AGORA, windows=[QuotaWindow("5h", "sessão 5h", 93)])
        r = resumir(store_com(snap("claude", s("claude", "a", A.IDLE), quotas=q)), NOMES)
        self.assertEqual((r.provider("claude").estado, r.mais_urgente.motivo), ("alerta", "alerta"))
        r = resumir(store_com(snap("codex", s("codex", "a", A.IDLE))), NOMES)
        self.assertIsNone(r.mais_urgente)
        self.assertIsNone(r.provider("codex").quota_max)

    def test_nenhum_agente(self):
        r = resumir(StateStore(), NOMES)
        self.assertEqual((r.total_sessoes, r.mais_urgente), (0, None))
        self.assertEqual({p.estado for p in r.providers}, {"indisponivel"})


class MonitorComEventos(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.eventos = self.dir / "eventos"

    def hook(self, provider, **dados):
        reg = ombro_hook.evento(provider, dados, int(time.time() * 1000))
        ombro_hook.gravar(reg, self.eventos)
        time.sleep(.002)

    def monitor(self, *providers):
        m = Monitor.montar(providers, banco=self.dir / "state.db", eventos=self.eventos)
        self.addCleanup(m.fechar)
        return m

    def test_eventos_vao_para_o_provider_certo_e_sao_apagados(self):
        m = self.monitor(CodexProvider(), CursorProvider())
        self.hook("codex", session_id="t1", hook_event_name="UserPromptSubmit", cwd="/x/api")
        self.hook("cursor", conversation_id="c1", hook_event_name="beforeSubmitPrompt", workspace_roots=["/x/site"])
        with self.assertLogs("ombro.estado", "INFO"):
            m.atualizar_todos()
        self.assertEqual([x.project for x in m.store.sessions("codex")], ["api"])
        self.assertEqual([x.project for x in m.store.sessions("cursor")], ["site"])
        self.assertEqual(list(self.eventos.glob("*.json")), [])

    def test_provider_desligado_descarta_e_defeituoso_nao_trava(self):
        class Quebrado(CodexProvider):
            def receber(self, eventos):
                raise RuntimeError("bug")

        m = self.monitor(Quebrado(), CursorProvider())
        self.hook("codex", session_id="t1", hook_event_name="Stop")
        self.hook("cursor", conversation_id="c1", hook_event_name="sessionStart")
        self.hook("claude", session_id="x", hook_event_name="Stop")  # claude desligado
        with self.assertLogs("ombro.estado", "ERROR"):
            m.atualizar_todos()
        self.assertEqual(len(m.store.sessions("cursor")), 1)  # o Cursor seguiu
        self.assertEqual(list(self.eventos.glob("*.json")), [])

    def test_ombro_desligado_e_depois_reiniciado(self):
        self.hook("codex", session_id="t1", hook_event_name="UserPromptSubmit")  # o Ombro ainda não estava rodando
        self.hook("codex", session_id="t1", hook_event_name="PermissionRequest")
        m = self.monitor(CodexProvider())
        m.atualizar_todos()
        [x] = m.store.sessions("codex")
        self.assertEqual(x.attention, T.WAITING_APPROVAL)
        m.fechar()
        m2 = self.monitor(CodexProvider())  # reiniciou: a sessão volta do banco, desconhecida
        m2.atualizar_todos()
        [y] = m2.store.sessions("codex")
        self.assertEqual((y.session_id, y.activity, y.attention), ("t1", A.UNKNOWN, T.UNKNOWN))
        self.hook("codex", session_id="t1", hook_event_name="Stop")
        m2.atualizar_todos()
        self.assertEqual(m2.store.sessions("codex")[0].activity, A.IDLE)
        self.assertEqual(len(m2.store.sessions("codex")), 1)  # sem duplicar

    def test_tres_providers_sem_os_agentes_instalados(self):
        from providers.claude import ClaudeProvider
        from providers.claude import FonteDemo
        from coletores.demo import Demo

        m = self.monitor(ClaudeProvider(FonteDemo(Demo(), 0)), CodexProvider(), CursorProvider())
        m.atualizar_todos()
        r = resumir(m.store, NOMES)
        self.assertEqual(r.provider("claude").sessoes, 3)
        self.assertEqual(r.provider("codex").sessoes + r.provider("cursor").sessoes, 0)


class Configuracao(unittest.TestCase):
    def arquivo(self, dados):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        arq = Path(tmp.name) / "config.json"
        arq.write_text(json.dumps(dados))
        return arq

    def test_padroes(self):
        cfg = OmbroConfig()
        self.assertEqual((cfg.providers, cfg.vista, cfg.foco, cfg.rotacao, cfg.intervalo_rotacao),
                         (("claude", "codex", "cursor", "opencode"), "overview", None, False, 15.0))

    def test_blocos_do_arquivo(self):
        arq = self.arquivo({"layout": "multiagente", "providers": {"codex": {"enabled": False}, "claude": {"enabled": True}},
                            "multi_agent": {"view": "focus", "focus_provider": "cursor", "auto_rotate": True, "rotate_interval": 20}})
        cfg, avisos = resolver({}, arq)
        self.assertEqual((cfg.layout, cfg.providers, cfg.vista, cfg.foco, cfg.rotacao, cfg.intervalo_rotacao),
                         ("multiagente", ("claude", "cursor", "opencode"), "focus", "cursor", True, 20.0))
        self.assertEqual(avisos, [])
        cfg, _ = resolver({"vista": "attention"}, arq)  # a linha de comando vence
        self.assertEqual(cfg.vista, "attention")

    def test_valores_invalidos(self):
        for dados, trecho in (({"providers": {"gemini": {"enabled": True}}}, "claude, codex, cursor"),
                              ({"providers": {"codex": {"enabled": "não"}}}, "true ou false"),
                              ({"providers": {"codex": True}}, "enabled"),
                              ({"multi_agent": {"rotate_interval": 1}}, "maior ou igual a 5"),
                              ({"multi_agent": {"view": "grade"}}, "overview, focus, attention"),
                              ({"multi_agent": {"cores": 1}}, "desconhecida")):
            with self.assertRaises(ConfigInvalida, msg=dados) as e:
                resolver({}, self.arquivo(dados))
            self.assertIn(trecho, str(e.exception))

    def test_arquivo_antigo_continua_valendo(self):
        cfg, avisos = resolver({}, self.arquivo({"versao": 1, "layout": "minimal-v2", "brilho": 40}))
        self.assertEqual((cfg.layout, cfg.brilho, cfg.providers),
                         ("minimal-v2", 40, ("claude", "codex", "cursor", "opencode")))
        self.assertEqual(avisos, [])
        self.assertTrue(OmbroConfig(layout="multiagente").animado)


def resumo_demo(urgente=True):
    q = QuotaReport("statusline", AGORA, windows=[QuotaWindow("5h", "sessão 5h", 42, AGORA + timedelta(hours=2)),
                                                  QuotaWindow("7d", "semana", 67, AGORA + timedelta(days=2))])
    from core.models import UsageMetrics

    uso = UsageMetrics("today", input_tokens=18_000_000, output_tokens=212_000, hourly_tokens=tuple([1000] * 16 + [None] * 8))
    st = store_com(snap("claude", s("claude", "a"), s("claude", "b", A.IDLE, T.WAITING_USER if urgente else T.NONE, "ombro-screen", 12),
                        quotas=q, usage=uso),
                   snap("codex", s("codex", "c", A.WORKING, T.WAITING_APPROVAL if urgente else T.NONE, "api-gateway", 3)),
                   snap("cursor", s("cursor", "d", A.WORKING, T.UNKNOWN, "um-projeto-com-um-nome-comprido-demais")))
    return resumir(st, NOMES)


def mascote():
    m = Mascote(cor_fixa=(223, 133, 89))
    m.estado = "trabalhando"
    return m


def hw(**kw):
    base = dict(cpu_pct=37.0, ram_usada=int(9.5 * GB), ram_total=16 * GB)
    base.update(kw)
    return Hardware(**base)


class Layout(unittest.TestCase):
    def quadro(self, resumo, vista="overview", foco=None, h=None):
        return lm.desenhar_multiagente(h or hw(), resumo, mascote(), AGORA, 1.1, vista, foco, usar_cache=False)

    def test_todas_as_visoes_em_480x320(self):
        r = resumo_demo()
        for vista, foco in (("overview", None), ("attention", None), ("focus", "claude"), ("focus", "codex"), ("focus", "cursor")):
            self.assertEqual(self.quadro(r, vista, foco).size, (480, 320), (vista, foco))
        self.assertEqual(self.quadro(resumir(StateStore(), NOMES)).size, (480, 320))  # ninguém ativo
        self.assertEqual(self.quadro(resumir(store_com(snap("claude", s("claude", "a"))), NOMES[:1])).size, (480, 320))

    def test_foco_mostra_so_o_que_existe(self):
        r = resumo_demo()
        codex = lm.plano(r, "focus", "codex", hw(), AGORA)
        claude = lm.plano(r, "focus", "claude", hw(), AGORA)
        self.assertFalse(any(isinstance(i, lm.Bloco) and i.tipo in ("quotas", "estatisticas", "grafico") for i in codex))
        self.assertTrue({"quotas", "estatisticas", "grafico"} <= {i.tipo for i in claude if isinstance(i, lm.Bloco)})
        textos = " ".join(i.s for i in codex if isinstance(i, lm.Texto))
        self.assertIn("o Codex não informa", textos)

    def test_attention_sem_urgencia_volta_a_visao_geral(self):
        r = resumo_demo(urgente=False)
        self.assertEqual(lm.plano(r, "attention", None, hw(), AGORA), lm.plano(r, "overview", None, hw(), AGORA))

    def test_estados_vazios_sao_diferentes(self):
        st = store_com(snap("claude"), snap("codex", integration="not_configured"),
                       ProviderSnapshot("cursor", AGORA, status="error"))
        textos = " ".join(i.s for i in lm.plano(resumir(st, NOMES), "overview", None, hw(), AGORA) if isinstance(i, lm.Texto))
        for t in ("sem sessões", "não configurado", "indisponível", "Nenhum agente ativo"):
            self.assertIn(t, textos)
        st = store_com(snap("codex", integration="not_installed"))
        textos = " ".join(i.s for i in lm.plano(resumir(st, NOMES[1:2]), "overview", None, hw(), AGORA) if isinstance(i, lm.Texto))
        self.assertNotIn("CODEX", textos)  # não instalado: não aparece como se fosse uma sessão

    def test_nome_longo_cortado(self):
        textos = [i.s for i in lm.plano(resumo_demo(), "overview", None, hw(), AGORA) if isinstance(i, lm.Texto)]
        self.assertTrue(all(len(t) <= 64 for t in textos))
        self.assertTrue(any(t.endswith("…") or "…" in t for t in textos))

    def test_cpu_e_ram_com_as_cores_do_minimal(self):
        r = resumo_demo()
        for cpu, cor in ((40.0, Tema.LARANJA), (80.0, Tema.AMARELO), (95.0, Tema.VERMELHO)):
            import layout_minimal_v2

            layout_minimal_v2.reiniciar_niveis()
            self.assertEqual(self.quadro(r, h=hw(cpu_pct=cpu)).getpixel((HW_BARRA_X + 3, CPU_Y)), cor)

    def test_reaproveitar_da_o_mesmo_quadro(self):
        lm._ultimo = None
        for i, vista in enumerate(("overview", "overview", "attention", "focus", "overview")):
            r, h = resumo_demo(i % 2 == 0), hw(cpu_pct=float(i * 20))
            import layout_minimal_v2

            layout_minimal_v2.reiniciar_niveis()
            com = lm.desenhar_multiagente(h, r, mascote(), AGORA, i * .2, vista, "claude")
            layout_minimal_v2.reiniciar_niveis()
            sem = lm.desenhar_multiagente(h, r, mascote(), AGORA, i * .2, vista, "claude", usar_cache=False)
            self.assertEqual(com.tobytes(), sem.tobytes(), vista)


class Seletor(unittest.TestCase):
    def test_fixo_foco_e_provider_que_nao_existe(self):
        r = resumo_demo(urgente=False)
        self.assertEqual(lm.SeletorDeVista().escolher(r, 0), ("overview", None))
        self.assertEqual(lm.SeletorDeVista("focus", "codex").escolher(r, 0), ("focus", "codex"))
        self.assertEqual(lm.SeletorDeVista("focus", None).escolher(r, 0), ("overview", None))
        r1 = resumir(store_com(snap("claude", s("claude", "a"))), NOMES[:1])
        self.assertEqual(lm.SeletorDeVista("focus", "cursor").escolher(r1, 0), ("overview", None))

    def test_rotacao_e_alerta_que_segura_a_tela(self):
        r = resumo_demo(urgente=False)
        sel = lm.SeletorDeVista(rotacao=True, intervalo=15)
        visoes = [sel.escolher(r, t) for t in (0, 15, 30, 45, 60)]
        self.assertEqual(visoes, [("overview", None), ("focus", "claude"), ("focus", "codex"), ("focus", "cursor"), ("overview", None)])
        urg = resumo_demo(urgente=True)
        self.assertEqual({sel.escolher(urg, t) for t in range(0, 120, 5)}, {("attention", None)})  # parado, sem piscar
        self.assertEqual(lm.SeletorDeVista(rotacao=True, intervalo=1).intervalo, 5.0)


if __name__ == "__main__":
    unittest.main()
