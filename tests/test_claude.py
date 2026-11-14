"""Coletor do Claude Code: leitura de datas, janelas do plano, tokens e sessões."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from coletores import claude
from coletores.claude import ColetorClaude, _epoch_ms, _janela, _num


class Conversoes(unittest.TestCase):
    def test_num(self):
        self.assertEqual(_num(3), 3.0)
        self.assertEqual(_num(" 4.5 "), 4.5)
        self.assertIsNone(_num(True))  # bool não é número aqui
        self.assertIsNone(_num("abc"))
        self.assertIsNone(_num(None))

    def test_epoch_ms_respeita_o_fuso_do_texto(self):
        self.assertEqual(_epoch_ms("2023-11-14T19:13:20-03:00"), 1_700_000_000_000)
        self.assertEqual(_epoch_ms("2023-11-14T22:13:20+00:00"), 1_700_000_000_000)
        self.assertEqual(_epoch_ms("1700000000"), 1_700_000_000_000)  # número em texto
        self.assertIsNone(_epoch_ms(None))
        self.assertIsNone(_epoch_ms(""))

    def test_epoch_ms_aceita_segundos_milissegundos_e_iso(self):
        self.assertEqual(_epoch_ms(1_700_000_000), 1_700_000_000_000)
        self.assertEqual(_epoch_ms(1_700_000_000_000), 1_700_000_000_000)
        self.assertEqual(_epoch_ms("2023-11-14T22:13:20Z"), 1_700_000_000_000)
        self.assertIsNone(_epoch_ms("ontem"))

    def test_janela_limita_e_descarta_a_que_ja_reiniciou(self):
        agora = 1_700_000_000_000
        j = _janela({"utilization": 140, "resets_at": (agora + 60_000) / 1000}, agora)
        self.assertEqual(j.pct, 100.0)
        self.assertIsNone(_janela({"utilization": 40, "resets_at": (agora - 1) / 1000}, agora))
        self.assertEqual(_janela({"used_percentage": 12}, agora).pct, 12.0)
        self.assertIsNone(_janela({"utilization": "?"}, agora))


class PastaTemporaria(unittest.TestCase):
    """Uma pasta do Claude Code e uma pasta da tela, vazias, só para o teste."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.config = base / "claude-teste"
        self.config.mkdir()
        self.tela = base / "ombro"
        self.tela.mkdir()
        p = mock.patch.object(claude, "PASTA_TELA", self.tela)
        p.start()
        self.addCleanup(p.stop)
        self.c = ColetorClaude(str(self.config))

    def _transcript(self, linhas):
        pasta = self.config / "projects" / "proj"
        pasta.mkdir(parents=True, exist_ok=True)
        with (pasta / "s.jsonl").open("a", encoding="utf-8") as f:
            for linha in linhas:
                f.write(json.dumps(linha) + "\n")

    @staticmethod
    def _msg(mid, ent, sai, quando):
        return {"type": "assistant", "timestamp": quando.astimezone().isoformat(),
                "message": {"id": mid, "usage": {"input_tokens": ent, "output_tokens": sai,
                                                 "cache_read_input_tokens": 10}}}


class ColetorEmPastaTemporaria(PastaTemporaria):
    def test_sem_fontes_de_uso(self):
        u = self.c.ler()
        self.assertFalse(u.configurado)
        self.assertEqual(u.erros, [])

    def test_vale_a_fonte_mais_recente(self):
        agora = datetime.now()
        reset = (agora + timedelta(hours=2)).timestamp()
        (self.tela / "uso.json").write_text(json.dumps({
            "fetchedAt": int(agora.timestamp() * 1000),
            "five_hour": {"utilization": 42, "resets_at": reset},
        }))
        (self.config / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAtMs": int((agora - timedelta(hours=1)).timestamp() * 1000),
            "utilization": {"five_hour": {"utilization": 10, "resets_at": reset}},
        }}))
        u = self.c.ler()
        self.assertEqual(u.fonte, "statusline")
        self.assertEqual(u.cinco_horas.pct, 42)
        self.assertFalse(u.desatualizado)

    def test_numeros_antigos_ficam_desatualizados(self):
        antes = datetime.now() - timedelta(minutes=45)
        (self.tela / "uso.json").write_text(json.dumps({
            "fetchedAt": int(antes.timestamp() * 1000), "seven_day": {"utilization": 5}}))
        u = self.c.ler()
        self.assertTrue(u.desatualizado)
        self.assertEqual(u.semana.pct, 5)

    def test_tokens_de_hoje_sem_repetir_mensagem(self):
        agora = datetime.now()
        ontem = agora - timedelta(days=1)
        self._transcript([
            self._msg("a", 100, 5, agora),
            self._msg("a", 100, 20, agora),  # a mesma mensagem, com o uso final: fica o maior
            self._msg("b", 50, 7, agora),
            self._msg("velha", 999, 999, ontem),  # de outro dia: não conta
        ])
        u = self.c.ler()
        self.assertEqual(u.mensagens_hoje, 2)
        self.assertEqual(u.tokens_saida, 27)
        self.assertEqual(u.tokens_entrada, 170)  # 100 + 50 + 2x10 de cache
        self.assertEqual(u.tokens_por_hora[agora.hour], 197)

        # leitura incremental: só as linhas novas são lidas, sem contar de novo
        self._transcript([self._msg("c", 1, 1, agora)])
        u = self.c.ler()
        self.assertEqual(u.mensagens_hoje, 3)
        self.assertEqual(u.tokens_saida, 28)

    def test_sessoes_ignoram_processo_morto(self):
        pasta = self.config / "sessions"
        pasta.mkdir()
        desde = int((datetime.now() - timedelta(minutes=3)).timestamp() * 1000)
        (pasta / "vivo.json").write_text(json.dumps({
            "pid": os.getpid(), "status": "waiting", "statusUpdatedAt": desde, "cwd": "/x/meu-projeto"}))
        (pasta / "morto.json").write_text(json.dumps({"pid": 2**22 + 12345, "status": "busy"}))
        u = self.c.ler()
        self.assertEqual(u.sessoes, 1)
        self.assertEqual(u.esperando, 1)
        self.assertEqual(u.trabalhando, 0)
        self.assertEqual(u.espera_projeto, "meu-projeto")

    def test_arquivo_estranho_vira_aviso_e_nao_derruba(self):
        (self.tela / "uso.json").write_text("{nao é json")
        with mock.patch.object(ColetorClaude, "_sessoes", side_effect=RuntimeError("quebrou")):
            u = self.c.ler()
        self.assertEqual(u.erros, ["sessões: quebrou"])


class DataFalsa(datetime):
    """datetime com `now()` controlado pelo teste (o coletor usa a hora local)."""
    agora = datetime(2026, 11, 13, 15, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.agora if tz is None else cls.agora.astimezone(tz)


class Fontes(PastaTemporaria):
    """Qual fonte do uso do plano vale, e o que acontece com arquivos vazios, quebrados ou incompletos."""

    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".habblaud" / "usage").mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        self.agora = datetime.now()
        self.reset = (self.agora + timedelta(hours=2)).timestamp()

    def ms(self, delta=timedelta()):
        return int((self.agora - delta).timestamp() * 1000)

    def tap(self, dados):
        (self.tela / "uso.json").write_text(dados if isinstance(dados, str) else json.dumps(dados))

    def habblaud(self, dados):
        (self.home / ".habblaud" / "usage" / f"{self.config.name}.json").write_text(json.dumps(dados))

    def usage(self, dados, quando):
        (self.config / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": {"fetchedAtMs": quando, "utilization": dados}}))

    def test_arquivo_vazio_ou_quebrado_e_ignorado_sem_erro(self):
        for conteudo in ("", "{nao é json", "[1, 2]", "null"):
            self.tap(conteudo)
            u = self.c.ler()
            self.assertFalse(u.configurado, conteudo)
            self.assertEqual(u.erros, [])

    def test_sem_horario_de_coleta_a_fonte_nao_vale(self):
        self.tap({"five_hour": {"utilization": 50}})
        self.assertFalse(self.c.ler().configurado)

    def test_fonte_sem_janelas_conta_como_configurada(self):
        self.tap({"fetchedAt": self.ms()})
        u = self.c.ler()
        self.assertTrue(u.configurado)
        self.assertEqual(u.fonte, "statusline")
        self.assertIsNone(u.cinco_horas)
        self.assertIsNone(u.semana)

    def test_valores_inesperados_viram_janela_vazia(self):
        self.tap({"fetchedAt": self.ms(), "five_hour": {"utilization": "muito"}, "seven_day": [50],
                  "outra_coisa": {"x": 1}})
        u = self.c.ler()
        self.assertIsNone(u.cinco_horas)
        self.assertIsNone(u.semana)
        self.assertEqual(u.erros, [])

    def test_horario_de_coleta_em_iso(self):
        self.tap({"fetchedAt": (self.agora - timedelta(minutes=2)).astimezone().isoformat(),
                  "five_hour": {"utilization": 7}})
        u = self.c.ler()
        self.assertEqual(u.cinco_horas.pct, 7)
        self.assertFalse(u.desatualizado)

    def test_habblaud_vale_quando_e_o_mais_recente(self):
        self.tap({"fetchedAt": self.ms(timedelta(minutes=10)), "five_hour": {"utilization": 1}})
        self.habblaud({"fetchedAt": self.ms(), "five_hour": {"utilization": 55, "resets_at": self.reset}})
        u = self.c.ler()
        self.assertEqual((u.fonte, u.cinco_horas.pct), ("habblaud", 55))

    def test_usage_do_claude_vale_quando_e_o_mais_recente(self):
        self.tap({"fetchedAt": self.ms(timedelta(minutes=10)), "five_hour": {"utilization": 1}})
        self.usage({"seven_day": {"utilization": 33}}, self.ms())
        u = self.c.ler()
        self.assertEqual((u.fonte, u.semana.pct), ("/usage", 33))

    def test_conta_padrao_le_o_claude_json_da_home(self):
        padrao = self.home / ".claude"
        padrao.mkdir()
        (self.home / ".claude.json").write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAt": self.ms(), "utilization": {"five_hour": {"utilization": 12}}}}))
        u = ColetorClaude(str(padrao)).ler()
        self.assertEqual((u.fonte, u.cinco_horas.pct), ("/usage", 12))

    def test_empate_fica_com_a_primeira_fonte(self):
        self.tap({"fetchedAt": self.ms(), "five_hour": {"utilization": 10}})
        self.habblaud({"fetchedAt": self.ms(), "five_hour": {"utilization": 20}})
        self.assertEqual(self.c.ler().fonte, "statusline")

    def test_janela_que_ja_reiniciou_some_mas_a_fonte_continua(self):
        passou = (self.agora - timedelta(minutes=1)).timestamp()
        self.tap({"fetchedAt": self.ms(), "five_hour": {"utilization": 99, "resets_at": passou},
                  "seven_day": {"utilization": 40, "resets_at": self.reset}})
        u = self.c.ler()
        self.assertTrue(u.configurado)
        self.assertIsNone(u.cinco_horas)
        self.assertEqual(u.semana.pct, 40)
        self.assertIsNotNone(u.semana.reseta_em)

    def test_desatualizado_a_partir_de_30_minutos(self):
        self.tap({"fetchedAt": self.ms(timedelta(minutes=29)), "five_hour": {"utilization": 1}})
        self.assertFalse(self.c.ler().desatualizado)
        self.tap({"fetchedAt": self.ms(timedelta(minutes=31)), "five_hour": {"utilization": 1}})
        self.assertTrue(self.c.ler().desatualizado)


class Tokens(PastaTemporaria):
    def arquivo(self):
        return self.config / "projects" / "proj" / "s.jsonl"

    def test_linha_incompleta_so_conta_quando_terminar(self):
        agora = datetime.now()
        self._transcript([self._msg("a", 1, 2, agora)])
        linha = json.dumps(self._msg("b", 1, 3, agora))
        with self.arquivo().open("a", encoding="utf-8") as f:
            f.write(linha[:20])  # o Claude Code ainda está escrevendo
        self.assertEqual(self.c.ler().mensagens_hoje, 1)
        with self.arquivo().open("a", encoding="utf-8") as f:
            f.write(linha[20:] + "\n")
        u = self.c.ler()
        self.assertEqual((u.mensagens_hoje, u.tokens_saida), (2, 5))

    def test_linhas_estranhas_sao_ignoradas(self):
        agora = datetime.now()
        self._transcript([
            {"type": "user", "message": {"usage": {"output_tokens": 999}}},  # só as respostas contam
            {"type": "assistant", "timestamp": "quando?", "message": {"id": "x", "usage": {"output_tokens": 999}}},
            {"type": "assistant", "timestamp": agora.astimezone().isoformat(), "message": {"id": "y", "usage": "999"}},
            self._msg("ok", 1, 4, agora),
        ])
        with self.arquivo().open("a", encoding="utf-8") as f:
            f.write('{"usage": quebrado\n')
        u = self.c.ler()
        self.assertEqual((u.mensagens_hoje, u.tokens_saida), (1, 4))
        self.assertEqual(u.erros, [])

    def test_sem_id_usa_o_request_id(self):
        agora = datetime.now()
        msg = self._msg(None, 1, 6, agora)
        del msg["message"]["id"]
        self._transcript([dict(msg, requestId="r1"), dict(msg, requestId="r1"), dict(msg, requestId="r2")])
        self.assertEqual(self.c.ler().mensagens_hoje, 2)

    def test_arquivo_reescrito_e_lido_de_novo(self):
        agora = datetime.now()
        self._transcript([self._msg(f"m{i}", 1, 1, agora) for i in range(5)])
        self.assertEqual(self.c.ler().mensagens_hoje, 5)
        self.arquivo().write_text(json.dumps(self._msg("novo", 1, 1, agora)) + "\n")  # menor que antes
        self.assertEqual(self.c.ler().mensagens_hoje, 6)

    def test_horario_em_utc_conta_no_dia_local(self):
        agora = datetime.now().astimezone()
        msg = self._msg("utc", 1, 8, agora)
        msg["timestamp"] = agora.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        self._transcript([msg])
        u = self.c.ler()
        self.assertEqual(u.tokens_saida, 8)
        self.assertEqual(u.tokens_por_hora[agora.hour], 19)

    def test_horas_que_nao_chegaram_ficam_vazias(self):
        with mock.patch.object(claude, "datetime", DataFalsa):
            u = self.c.ler()
        self.assertEqual(u.tokens_por_hora[:16], [0] * 16)
        self.assertEqual(u.tokens_por_hora[16:], [None] * 8)

    def test_virada_do_dia_zera_a_contagem(self):
        hoje = DataFalsa.agora
        self._transcript([self._msg("a", 1, 9, hoje)])
        mtime = hoje.timestamp()
        os.utime(self.arquivo(), (mtime, mtime))
        with mock.patch.object(claude, "datetime", DataFalsa):
            self.assertEqual(self.c.ler().tokens_saida, 9)
            with mock.patch.object(DataFalsa, "agora", hoje + timedelta(days=1)):
                u = self.c.ler()
        self.assertEqual((u.mensagens_hoje, u.tokens_saida), (0, 0))

    def test_arquivos_de_outros_dias_nem_sao_abertos(self):
        self._transcript([self._msg("a", 1, 1, datetime.now())])
        antigo = (datetime.now() - timedelta(days=2)).timestamp()
        os.utime(self.arquivo(), (antigo, antigo))
        with mock.patch.object(Path, "open", side_effect=AssertionError("não devia abrir")):
            self.assertEqual(self.c.ler().mensagens_hoje, 0)


class Sessoes(PastaTemporaria):
    def sessao(self, nome, **dados):
        pasta = self.config / "sessions"
        pasta.mkdir(exist_ok=True)
        (pasta / f"{nome}.json").write_text(json.dumps(dados))

    def ms(self, minutos):
        return int((datetime.now() - timedelta(minutes=minutos)).timestamp() * 1000)

    def test_trabalhando_esperando_e_ociosas(self):
        pid = os.getpid()
        self.sessao("a", pid=pid, status="busy")
        self.sessao("b", pid=pid, status="busy")
        self.sessao("c", pid=pid, status="waiting", statusUpdatedAt=self.ms(1), cwd="/x/um")
        self.sessao("d", pid=pid, status="idle")
        self.sessao("e", pid=pid, status="algo-novo")  # status desconhecido conta como ociosa
        u = self.c.ler()
        self.assertEqual((u.sessoes, u.trabalhando, u.esperando), (5, 2, 1))
        self.assertEqual(sorted(u.status_sessoes), ["busy", "busy", "idle", "idle", "waiting"])

    def test_mostra_quem_espera_ha_mais_tempo(self):
        pid = os.getpid()
        self.sessao("nova", pid=pid, status="waiting", statusUpdatedAt=self.ms(2), cwd="/x/recente")
        self.sessao("velha", pid=pid, status="waiting", statusUpdatedAt=self.ms(20), cwd="/x/antiga")
        u = self.c.ler()
        self.assertEqual(u.esperando, 2)
        self.assertEqual(u.espera_projeto, "antiga")
        self.assertAlmostEqual((datetime.now() - u.espera_desde).total_seconds(), 20 * 60, delta=5)

    def test_sessao_sem_dados_suficientes(self):
        pid = os.getpid()
        self.sessao("sem-pid", status="busy")
        self.sessao("pid-texto", pid=str(pid), status="busy")
        self.sessao("esperando-sem-horario", pid=pid, status="waiting")
        (self.config / "sessions" / "lixo.json").write_text("{")
        u = self.c.ler()
        self.assertEqual((u.sessoes, u.esperando), (1, 1))
        self.assertIsNone(u.espera_desde)
        self.assertEqual(u.espera_projeto, "")

    def test_processo_de_outro_usuario_conta(self):
        self.sessao("root", pid=1, status="busy")
        with mock.patch.object(claude.os, "kill", side_effect=PermissionError):
            u = self.c.ler()
        self.assertEqual((u.sessoes, u.trabalhando), (1, 1))

    def test_sem_pasta_de_sessoes(self):
        u = self.c.ler()
        self.assertEqual((u.sessoes, u.status_sessoes), (0, []))


class Pasta(unittest.TestCase):
    def test_claude_config_dir_e_a_conta(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/tmp/conta-a, /tmp/conta-b"}):
            c = ColetorClaude()
        self.assertEqual(c.config, Path("/tmp/conta-a"))
        self.assertEqual(c.conta, "conta-a")
        self.assertEqual(c.claude_json, Path("/tmp/conta-a/.claude.json"))
        self.assertEqual(ColetorClaude("/tmp/outra").config, Path("/tmp/outra"))  # --claude-dir vence a variável

if __name__ == "__main__":
    unittest.main()
