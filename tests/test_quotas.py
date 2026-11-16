"""Os limites da assinatura do Claude (5 h e semana): de onde vêm, o que vale, e o que o Ombro diz sobre a idade deles.

O que estes testes fixam:
  - Só há número novo quando o Claude Code no terminal conversa (statusline) ou roda /usage. O app Claude Desktop,
    incluindo a aba Code, não escreve em nenhuma fonte: o Ombro então mostra o último valor, com a idade à vista.
  - O Ombro não consulta nada pela rede e não toca em credenciais: não há autenticação, rate limit nem timeout de
    rede para falhar. As falhas que existem são de arquivo (sumiu, ilegível, quebrado, pela metade).
  - Uma janela que reiniciou fica sem número até a próxima observação (nunca vira 0% sozinha).

Tudo com arquivos de mentira numa pasta temporária: nenhuma credencial e nenhum dado real.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import statusline_tap
from apresentacao import uso_claude
from coletores import claude
from coletores.claude import DESATUALIZADO_MS, ColetorClaude
from ombro.daemon import quotas_do_snapshot
from ombro.doctor import PASS, WARN, _coletas
from providers.claude import snapshot_de_uso
from render import aviso_uso

CONTA_A, CONTA_B = "conta-aaaa", "conta-bbbb"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.config, self.tela, self.home = base / "claude-teste", base / "ombro", base / "home"
        for p in (self.config, self.tela, self.home / ".habblaud" / "usage"):
            p.mkdir(parents=True)
        for p in (mock.patch.object(claude, "PASTA_TELA", self.tela), mock.patch.dict(os.environ, {"HOME": str(self.home)})):
            p.start()
            self.addCleanup(p.stop)
        self.agora = datetime.now()
        self.c = ColetorClaude(str(self.config))

    def ms(self, **atras):
        return int((self.agora - timedelta(**atras)).timestamp() * 1000)

    def daqui(self, **delta):
        return (self.agora + timedelta(**delta)).timestamp()

    def statusline(self, pct5, pct7, reset5=None, reset7=None, **atras):
        (self.tela / "uso.json").write_text(json.dumps({
            "fetchedAt": self.ms(**atras),
            "five_hour": {"utilization": pct5, "resets_at": reset5 if reset5 is not None else self.daqui(hours=3)},
            "seven_day": {"utilization": pct7, "resets_at": reset7 if reset7 is not None else self.daqui(days=2)}}))

    def claude_json(self, pct5=None, pct7=None, conta=CONTA_A, do_cache=CONTA_A, **atras):
        dados = {"oauthAccount": {"accountUuid": conta}} if conta else {}
        if pct5 is not None:
            dados["cachedUsageUtilization"] = {
                "fetchedAtMs": self.ms(**atras), "accountUuid": do_cache,
                "utilization": {"five_hour": {"utilization": pct5, "resets_at": self.daqui(hours=3)},
                                "seven_day": {"utilization": pct7, "resets_at": self.daqui(days=2)}}}
        (self.config / ".claude.json").write_text(json.dumps(dados))

    def quotas(self):
        """O que a IPC entrega (providers.list[].quotas), a partir de uma leitura de agora."""
        return quotas_do_snapshot(snapshot_de_uso(self.c.ler(), datetime.now()))


class SemTerminal(Base):
    """O caso do usuário: só o app Claude, sem nenhuma sessão no terminal."""

    def test_sem_nenhuma_fonte_os_dados_sao_indisponiveis(self):
        u = self.c.ler()
        self.assertFalse(u.configurado)
        self.assertIsNone(self.quotas())  # o app mostra "Dados indisponíveis", sem número inventado
        self.assertEqual(aviso_uso(u, datetime.now()), "sem dados de uso: veja o README")

    def test_o_que_o_cli_deixou_antes_aparece_com_a_idade_de_quando_foi_observado(self):
        self.statusline(32, 54, minutes=56)  # a última conversa no terminal foi há 56 min
        q = self.quotas()
        self.assertEqual([w["used_percent"] for w in q["windows"]], [32, 54])
        self.assertEqual((q["source"], q["state"], q["stale"], q["scope"]), ("statusline", "stale", True, "account"))
        self.assertAlmostEqual(q["age_seconds"], 56 * 60, delta=5)
        self.assertFalse(q["auto_update"])  # não se atualiza sozinho: depende do Claude Code no terminal

    def test_ler_de_novo_o_mesmo_arquivo_nao_rejuvenesce_o_dado(self):
        """Uso só pelo app Claude: o arquivo não muda. Cada leitura do Ombro continua dizendo a idade de verdade."""
        self.statusline(32, 54, minutes=20)
        observado = self.c.ler().coletado_em
        for _ in range(5):  # o laço do ombrod lê a cada 5 s
            u = self.c.ler()
            self.assertEqual(u.coletado_em, observado)
        self.assertTrue(u.desatualizado)
        self.assertEqual(aviso_uso(u, datetime.now()), "números de 20 min atrás")

    def test_reiniciar_o_ombrod_nao_muda_a_idade(self):
        self.statusline(32, 54, minutes=40)
        antes = self.c.ler().coletado_em
        outro = ColetorClaude(str(self.config))  # o ombrod reiniciou (ou o app foi fechado e aberto: a fonte é o ombrod)
        self.assertEqual(outro.ler().coletado_em, antes)
        self.assertTrue(outro.ler().desatualizado)

    def test_um_numero_novo_entra_na_leitura_seguinte_sem_reiniciar_nada(self):
        self.statusline(32, 54, minutes=56)
        self.assertEqual(self.c.ler().cinco_horas.pct, 32)
        self.statusline(43, 56)  # o Claude Code no terminal recebeu uma resposta agora
        q = self.quotas()
        self.assertEqual(([w["used_percent"] for w in q["windows"]], q["state"]), ([43, 56], "fresh"))
        self.assertLess(q["age_seconds"], 5)

    def test_limiar_de_atualizado_para_ultimo_valor_conhecido(self):
        minutos = DESATUALIZADO_MS // 60_000
        self.statusline(10, 20, minutes=minutos - 1)
        self.assertEqual(self.quotas()["state"], "fresh")
        self.statusline(10, 20, minutes=minutos + 1)
        self.assertEqual(self.quotas()["state"], "stale")


class QualFonteVale(Base):
    def test_a_observacao_mais_recente_vence_qualquer_que_seja_a_fonte(self):
        self.statusline(32, 54, minutes=50)
        self.claude_json(43, 56, minutes=2)  # um /usage mais novo
        u = self.c.ler()
        self.assertEqual((u.fonte, u.cinco_horas.pct, u.semana.pct), ("/usage", 43, 56))
        self.statusline(45, 57)  # e depois o statusline voltou a ser o mais novo
        u = self.c.ler()
        self.assertEqual((u.fonte, u.cinco_horas.pct), ("statusline", 45))

    def test_um_statusline_velho_nao_passa_por_cima_de_um_dado_mais_novo(self):
        self.claude_json(43, 56, minutes=1)
        self.statusline(32, 54, minutes=90)
        u = self.c.ler()
        self.assertEqual((u.fonte, u.cinco_horas.pct, u.desatualizado), ("/usage", 43, False))

    def test_as_fontes_nao_se_misturam(self):
        """As duas janelas vêm da mesma observação: a semana de uma fonte nunca completa as 5 h de outra."""
        (self.tela / "uso.json").write_text(json.dumps({"fetchedAt": self.ms(), "five_hour": {"utilization": 43}}))
        self.claude_json(10, 99, minutes=30)
        u = self.c.ler()
        self.assertEqual((u.fonte, u.cinco_horas.pct), ("statusline", 43))
        self.assertIsNone(u.semana)  # resposta parcial: a semana fica sem número, não com o 99 da outra fonte

    def test_no_empate_vale_a_fonte_mais_confiavel(self):
        self.statusline(11, 12)
        self.claude_json(21, 22)
        self.assertEqual(self.c.ler().fonte, "statusline")

    def test_horario_no_futuro_nao_vence_para_sempre(self):
        (self.tela / "uso.json").write_text(json.dumps({"fetchedAt": self.ms(days=-3), "five_hour": {"utilization": 5}}))
        u = self.c.ler()
        self.assertLessEqual(u.coletado_em, datetime.now())  # relógio errado: no máximo "agora"

    def test_escopo_de_cada_fonte(self):
        self.statusline(1, 2)
        self.assertEqual(self.c.ler().escopo, "account")
        (self.tela / "uso.json").unlink()
        (self.home / ".habblaud" / "usage" / f"{self.config.name}.json").write_text(
            json.dumps({"fetchedAt": self.ms(), "five_hour": {"utilization": 9}}))
        self.assertEqual(self.c.ler().escopo, "unknown")  # a fonte não diz o que cobre


class TrocaDeConta(Base):
    def test_cache_do_usage_de_outra_conta_nao_entra(self):
        self.claude_json(80, 90, conta=CONTA_B, do_cache=CONTA_A)  # o cache é da conta de antes
        self.assertFalse(self.c.ler().configurado)
        self.claude_json(15, 25, conta=CONTA_B, do_cache=CONTA_B)
        self.assertEqual(self.c.ler().cinco_horas.pct, 15)

    def test_o_que_foi_visto_antes_da_troca_e_descartado(self):
        self.claude_json(conta=CONTA_A)
        self.statusline(70, 80, minutes=3)
        self.assertEqual(self.c.ler().cinco_horas.pct, 70)
        self.claude_json(conta=CONTA_B)  # entrou com outra conta
        u = self.c.ler()
        self.assertFalse(u.configurado)  # o 70% era da conta A: não é mostrado como se fosse da B
        self.agora = datetime.now() + timedelta(seconds=1)
        self.statusline(5, 6)  # a conta nova conversou, depois da troca
        with mock.patch.object(claude, "datetime", wraps=datetime) as relogio:
            relogio.now.return_value = self.agora + timedelta(seconds=1)
            relogio.fromtimestamp = datetime.fromtimestamp
            u = self.c.ler()
        self.assertEqual((u.cinco_horas.pct, u.fonte), (5, "statusline"))

    def test_a_conta_nunca_vai_para_o_que_a_ipc_entrega(self):
        self.claude_json(43, 56)
        self.assertNotIn(CONTA_A, json.dumps(self.quotas()))


class Reset(Base):
    def test_janela_que_reiniciou_fica_sem_numero_e_nunca_vira_zero(self):
        self.statusline(99, 40, reset5=self.daqui(minutes=-10), minutes=30)  # as 5 h reiniciaram há 10 min
        u = self.c.ler()
        self.assertIsNone(u.cinco_horas)
        self.assertEqual(list(u.expiradas), ["cinco_horas"])
        q = quotas_do_snapshot(snapshot_de_uso(u, datetime.now()))
        cinco, semana = q["windows"]
        self.assertEqual((cinco["used_percent"], cinco["expired"]), (None, True))  # nem 99, nem 0
        self.assertEqual((semana["used_percent"], semana["expired"]), (40, False))

    def test_todas_as_janelas_reiniciaram_estado_aguardando_atualizacao(self):
        self.statusline(99, 98, reset5=self.daqui(minutes=-10), reset7=self.daqui(minutes=-5), minutes=1)
        q = self.quotas()
        self.assertEqual(q["state"], "awaiting_update")
        self.assertEqual(aviso_uso(self.c.ler(), datetime.now()), "limite reiniciou: sem número novo")

    def test_o_reset_expirado_chega_ao_layout_como_veio_do_coletor(self):
        self.statusline(99, 40, reset5=self.daqui(minutes=-10))
        u = self.c.ler()
        v = uso_claude(snapshot_de_uso(u, datetime.now()))  # o caminho StateStore -> tela
        self.assertEqual((v.cinco_horas, list(v.expiradas), v.semana.pct), (None, ["cinco_horas"], 40))

    def test_numero_novo_depois_do_reset_volta_ao_normal(self):
        self.statusline(99, 40, reset5=self.daqui(minutes=-10))
        self.assertEqual(self.quotas()["windows"][0]["expired"], True)
        self.statusline(3, 41)
        q = self.quotas()
        self.assertEqual((q["windows"][0]["used_percent"], q["windows"][0]["expired"], q["state"]), (3, False, "fresh"))


class FalhasDeArquivo(Base):
    """Não há consulta pela rede: o que pode falhar é o arquivo."""

    def test_arquivo_quebrado_ou_pela_metade_nao_derruba_e_a_outra_fonte_vale(self):
        self.claude_json(43, 56, minutes=5)
        for conteudo in ("", '{"fetchedAt": 12, "five_hour": {"utiliz', "[1,2]", "null", '{"fetchedAt": "ontem"}'):
            (self.tela / "uso.json").write_text(conteudo)
            u = self.c.ler()
            self.assertEqual((u.fonte, u.cinco_horas.pct, u.erros), ("/usage", 43, []), conteudo)

    def test_arquivo_ilegivel(self):
        self.statusline(43, 56)
        (self.tela / "uso.json").chmod(0o000)
        self.addCleanup(lambda: (self.tela / "uso.json").chmod(0o600))
        u = self.c.ler()
        self.assertFalse(u.configurado)
        self.assertEqual(u.erros, [])

    def test_valores_fora_do_esperado(self):
        (self.tela / "uso.json").write_text(json.dumps({
            "fetchedAt": self.ms(), "five_hour": {"utilization": "muito"}, "seven_day": {"utilization": 250}}))
        u = self.c.ler()
        self.assertIsNone(u.cinco_horas)  # sem número, sem inventar
        self.assertEqual(u.semana.pct, 100.0)

    def test_recupera_quando_o_arquivo_volta_a_ser_valido(self):
        (self.tela / "uso.json").write_text("{quebrado")
        self.assertFalse(self.c.ler().configurado)
        self.statusline(43, 56)
        self.assertEqual(self.c.ler().cinco_horas.pct, 43)

    def test_falha_nos_limites_nao_mexe_nos_tokens_nem_nas_sessoes(self):
        pasta = self.config / "projects" / "proj"
        pasta.mkdir(parents=True)
        (pasta / "s.jsonl").write_text(json.dumps({
            "type": "assistant", "timestamp": datetime.now().astimezone().isoformat(),
            "message": {"id": "m1", "usage": {"input_tokens": 100, "output_tokens": 20}}}) + "\n")
        (self.tela / "uso.json").write_text("{quebrado")
        u = self.c.ler()
        self.assertFalse(u.configurado)
        self.assertEqual((u.tokens_entrada, u.tokens_saida, u.mensagens_hoje), (100, 20, 1))
        self.statusline(43, 56)
        v = self.c.ler()  # e os limites chegarem não muda a contagem de tokens
        self.assertEqual((v.tokens_entrada, v.tokens_saida, v.cinco_horas.pct), (100, 20, 43))


class Statusline(unittest.TestCase):
    """O statusline roda também sem resposta nova (refreshInterval, troca de modo): isso não é uma observação nova."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name) / "ombro"
        p = mock.patch.object(statusline_tap, "PASTA", self.pasta)
        p.start()
        self.addCleanup(p.stop)
        self.relogio = 1_800_000_000.0
        r = mock.patch.object(statusline_tap.time, "time", lambda: self.relogio)
        r.start()
        self.addCleanup(r.stop)

    def entrada(self, pct5=42, api_ms=5000, sessao="sessao-1"):
        return json.dumps({"session_id": sessao, "cost": {"total_api_duration_ms": api_ms},
                           "rate_limits": {"five_hour": {"used_percentage": pct5, "resets_at": 1_900_000_000},
                                           "seven_day": {"used_percentage": 60, "resets_at": 1_900_500_000}}})

    def salvo(self):
        return json.loads((self.pasta / "uso.json").read_text())

    def test_redesenho_sem_resposta_nova_mantem_o_horario_da_observacao(self):
        statusline_tap.capturar(self.entrada())
        observado = self.salvo()["fetchedAt"]
        self.relogio += 40 * 60  # 40 min depois o statusline roda de novo (refreshInterval), com os mesmos dados
        statusline_tap.capturar(self.entrada())
        s = self.salvo()
        self.assertEqual(s["fetchedAt"], observado)  # o dado continua tendo a idade de verdade
        self.assertEqual(s["seenAt"], int(self.relogio * 1000))  # e fica registrado que foi lido de novo

    def test_resposta_nova_da_api_e_uma_observacao_nova_mesmo_com_o_mesmo_percentual(self):
        statusline_tap.capturar(self.entrada(api_ms=5000))
        self.relogio += 600
        statusline_tap.capturar(self.entrada(api_ms=9000))  # o Claude respondeu: os limites são de agora
        self.assertEqual(self.salvo()["fetchedAt"], int(self.relogio * 1000))

    def test_percentual_diferente_ou_outra_sessao_e_observacao_nova(self):
        statusline_tap.capturar(self.entrada())
        self.relogio += 600
        statusline_tap.capturar(self.entrada(pct5=50))
        self.assertEqual(self.salvo()["fetchedAt"], int(self.relogio * 1000))
        self.relogio += 600
        statusline_tap.capturar(self.entrada(pct5=50, sessao="sessao-2"))
        self.assertEqual(self.salvo()["fetchedAt"], int(self.relogio * 1000))

    def test_sem_como_saber_vale_o_comportamento_de_sempre(self):
        dados = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 42}}})  # versão antiga: sem sessão nem custo
        statusline_tap.capturar(dados)
        self.relogio += 600
        statusline_tap.capturar(dados)
        self.assertEqual(self.salvo()["fetchedAt"], int(self.relogio * 1000))

    def test_o_id_da_sessao_nao_e_gravado(self):
        statusline_tap.capturar(self.entrada(sessao="sessao-secreta-123"))
        self.assertNotIn("sessao-secreta-123", (self.pasta / "uso.json").read_text())


class NaTelinha(Base):
    """O quadro da tela USB (e da prévia) muda com o número novo e sinaliza o dado velho."""

    def quadro(self, layout):
        from coletores.hardware import Hardware
        from layout_minimal_v2 import desenhar_minimal
        from layout_ombro import desenhar_ombro
        from mascote import Mascote

        m = Mascote(cor_fixa=(200, 100, 80))
        m.inicio = 0.0
        fixo = datetime(2026, 11, 16, 17, 0, 0)
        u = self.c.ler()
        h = Hardware(disponivel=False)
        img = desenhar_minimal(h, u, m, fixo, 0.4, agente="claude") if layout == "minimal-v2" else desenhar_ombro(h, u, m, fixo, 0.4)
        return img.tobytes()

    def test_numero_novo_muda_o_quadro(self):
        for layout in ("ombro", "minimal-v2"):
            self.statusline(32, 54)
            antes = self.quadro(layout)
            self.statusline(43, 56)
            self.assertNotEqual(self.quadro(layout), antes, layout)

    def test_dado_velho_e_janela_reiniciada_aparecem_no_quadro(self):
        for layout in ("ombro", "minimal-v2"):
            self.statusline(32, 54)
            atual = self.quadro(layout)
            self.statusline(32, 54, minutes=40)  # os mesmos números, velhos: o aviso entra no quadro
            velho = self.quadro(layout)
            self.assertNotEqual(velho, atual, layout)
            self.statusline(32, 54, reset5=self.daqui(minutes=-5))  # as 5 h reiniciaram: a barra fica sem número
            self.assertNotIn(self.quadro(layout), (atual, velho), layout)


class Diagnostico(unittest.TestCase):
    """O doctor separa três coisas: sessões, tokens locais e limites da assinatura."""

    def provider(self, quotas, **mais):
        return {"id": "claude", "enabled": True, "capabilities": ["quota_windows", "sessions", "token_usage"],
                "sessions": 4, "collection": "ok", "stale_parts": [], "last_collected_at": "2026-11-16T17:00:00",
                "usage": {"scope": "today", "total_tokens": 1234}, "quotas": quotas, **mais}

    def limites(self, **campos):
        return {"source": "statusline", "state": "fresh", "observed_at": "2026-11-16T16:58:00", "age_seconds": 120,
                "scope": "account", "auto_update": False, "stale": False, "windows": [{"id": "5h", "expired": False}], **campos}

    def por_id(self, p):
        return {v.id: v for v in _coletas("claude", "Claude Code", p)}

    def test_tres_verificacoes_separadas(self):
        v = self.por_id(self.provider(self.limites()))
        self.assertEqual(sorted(v), ["quotas.claude", "sessions.claude", "tokens.claude"])
        self.assertEqual([v[k].status for k in sorted(v)], [PASS, PASS, PASS])
        self.assertIn("não são os limites da assinatura", v["tokens.claude"].mensagem)

    def test_limites_velhos_avisam_mesmo_com_sessoes_e_tokens_em_dia(self):
        v = self.por_id(self.provider(self.limites(state="stale", age_seconds=58 * 60)))
        q = v["quotas.claude"]
        self.assertEqual((q.status, v["sessions.claude"].status, v["tokens.claude"].status), (WARN, PASS, PASS))
        self.assertIn("último valor conhecido, de há 58 min", q.mensagem)
        self.assertIn("o app Claude não atualiza", q.mensagem)
        self.assertEqual(q.detalhes["auto_update"], False)
        self.assertIsNone(q.detalhes["next_attempt"])  # o Ombro não agenda consulta nenhuma pela rede
        self.assertEqual(q.detalhes["scope"], "account")
        self.assertEqual((q.detalhes["observed_at"], q.detalhes["last_read_at"]), ("2026-11-16T16:58:00", "2026-11-16T17:00:00"))

    def test_sem_fonte_e_aguardando_atualizacao(self):
        self.assertEqual(self.por_id(self.provider(None))["quotas.claude"].detalhes["state"], "unavailable")
        v = self.por_id(self.provider(self.limites(state="awaiting_update", windows=[{"id": "5h", "expired": True}])))
        self.assertEqual(v["quotas.claude"].status, WARN)
        self.assertIn("reiniciou", v["quotas.claude"].mensagem)


if __name__ == "__main__":
    unittest.main()
