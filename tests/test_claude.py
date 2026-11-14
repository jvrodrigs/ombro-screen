"""Coletor do Claude Code: leitura de datas, janelas do plano, tokens e sessões."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
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


class ColetorEmPastaTemporaria(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
