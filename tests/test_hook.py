"""ombro_hook.py e a caixa de eventos: privacidade, nunca atrapalhar o agente, escrita simultânea."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import ombro_hook
from core.caixa import MAX_POR_LEITURA, CaixaDeEventos

RAIZ = Path(__file__).resolve().parent.parent
SEGREDOS = ("rm -rf", "senha-123", "fulano@exemplo.com", "/Users/fulano", "conteudo do arquivo", "sk-abc")


def entrada_claude(evento="PermissionRequest", **extra):
    d = {"session_id": "abc-123", "hook_event_name": evento, "cwd": "/Users/fulano/codigo/meu-projeto",
         "transcript_path": "/Users/fulano/.claude/x.jsonl", "prompt": "senha-123",
         "tool_input": {"command": "rm -rf /", "content": "conteudo do arquivo"}, "permission_mode": "default"}
    d.update(extra)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        p = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.dir)})
        p.start()
        self.addCleanup(p.stop)
        self.eventos = self.dir / "eventos"

    def rodar(self, provider, entrada, texto=None):
        dados = texto if texto is not None else json.dumps(entrada)
        return subprocess.run([sys.executable, str(RAIZ / "ombro_hook.py"), provider], input=dados.encode() if isinstance(dados, str)
                              else dados, capture_output=True, timeout=30, env=dict(os.environ))

    def arquivos(self):
        return sorted(self.eventos.glob("*.json")) if self.eventos.exists() else []


class Hook(Base):
    def test_grava_so_os_campos_permitidos(self):
        r = self.rodar("claude", entrada_claude())
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
        [arq] = self.arquivos()
        dados = json.loads(arq.read_text())
        self.assertEqual(set(dados), {"v", "provider", "session", "event", "ts", "project", "detail"})
        self.assertEqual((dados["provider"], dados["session"], dados["event"], dados["project"]),
                         ("claude", "abc-123", "PermissionRequest", "meu-projeto"))
        texto = arq.read_text()
        for segredo in SEGREDOS:
            self.assertNotIn(segredo, texto)
        self.assertEqual(stat.S_IMODE(arq.stat().st_mode), 0o600)

    def test_cursor_responde_json_vazio_e_os_outros_nada(self):
        cursor = {"conversation_id": "c1", "hook_event_name": "stop", "status": "error", "workspace_roots": ["/x/site"],
                  "user_email": "fulano@exemplo.com"}
        self.assertEqual(self.rodar("cursor", cursor).stdout, b"{}")
        self.assertEqual(self.rodar("codex", {"session_id": "t", "hook_event_name": "Stop"}).stdout, b"")
        self.assertEqual(self.rodar("cursor", None, texto="lixo").stdout, b"{}")  # mesmo com erro, JSON válido
        eventos = [json.loads(a.read_text()) for a in self.arquivos()]
        self.assertIn({"provider": "cursor", "detail": "error", "project": "site"},
                      [{k: e[k] for k in ("provider", "detail", "project")} for e in eventos])
        self.assertNotIn("fulano", "".join(a.read_text() for a in self.arquivos()))

    def test_eventos_e_detalhes_fora_da_lista_sao_ignorados(self):
        self.rodar("claude", entrada_claude("UserPromptSubmit"))  # o Claude não precisa deste (e ele traz o prompt)
        self.rodar("claude", entrada_claude("PreToolUse"))
        self.rodar("desconhecido", entrada_claude())
        self.assertEqual(self.arquivos(), [])
        self.rodar("claude", entrada_claude("SessionEnd", reason="algo-novo"))
        self.assertIsNone(json.loads(self.arquivos()[0].read_text())["detail"])

    def test_entrada_invalida_ou_grande_sai_com_zero(self):
        for texto in ("", "nao é json", "[1,2]", '{"hook_event_name": "Stop"}', '{"session_id": "x"}'):
            r = self.rodar("claude", None, texto=texto)
            self.assertEqual((r.returncode, r.stdout), (0, b""), texto)
        grande = json.dumps(entrada_claude(tool_input={"content": "x" * (ombro_hook.MAX_ENTRADA + 10)}))
        r = self.rodar("claude", None, texto=grande)  # o agente escreve tudo sem o pipe quebrar
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.arquivos(), [])

    def test_id_de_sessao_estranho_vira_hash_estavel(self):
        a = ombro_hook.evento("codex", {"session_id": "id com espaço/../x", "hook_event_name": "Stop"}, 1)
        b = ombro_hook.evento("codex", {"session_id": "id com espaço/../x", "hook_event_name": "Stop"}, 2)
        self.assertEqual(a["session"], b["session"])
        self.assertTrue(a["session"].startswith("h-"))
        self.assertEqual(ombro_hook.evento("codex", {"session_id": "thr_123", "hook_event_name": "Stop"}, 1)["session"], "thr_123")

    def test_limite_de_pendentes_com_o_ombro_desligado(self):
        with mock.patch.object(ombro_hook, "MAX_PENDENTES", 3):
            for i in range(6):
                ombro_hook.gravar({"v": 1, "i": i}, self.eventos)
        self.assertEqual(len(self.arquivos()), 3)  # não enche o disco; o agente segue normal

    def test_escrita_simultanea_sem_perder_nem_corromper(self):
        def um(i):
            return self.rodar("codex", {"session_id": f"s{i % 5}", "hook_event_name": "PostToolUse", "cwd": f"/p/proj{i}"})

        with ThreadPoolExecutor(16) as ex:
            resultados = list(ex.map(um, range(48)))
        self.assertTrue(all(r.returncode == 0 for r in resultados))
        arquivos = self.arquivos()
        self.assertEqual(len(arquivos), 48)
        self.assertEqual(len({json.loads(a.read_text())["project"] for a in arquivos}), 48)  # todos inteiros
        self.assertEqual(list(self.eventos.glob(".*")), [])  # nenhum temporário largado

    def test_rapido(self):
        t0 = time.perf_counter()
        self.rodar("claude", entrada_claude())
        self.assertLess(time.perf_counter() - t0, 2.0)  # na prática, ~30 ms


class Caixa(Base):
    def gravar(self, **campos):
        reg = {"v": 1, "provider": "codex", "session": "s1", "event": "Stop", "ts": int(time.time() * 1000), "project": "p", "detail": None}
        reg.update(campos)
        ombro_hook.gravar(reg, self.eventos)
        time.sleep(.002)

    def test_le_em_ordem_e_so_apaga_ao_confirmar(self):
        for ev in ("SessionStart", "UserPromptSubmit", "Stop"):
            self.gravar(event=ev)
        caixa = CaixaDeEventos(self.eventos)
        eventos = caixa.ler()
        self.assertEqual([e.event for e in eventos], ["SessionStart", "UserPromptSubmit", "Stop"])
        self.assertEqual(len(self.arquivos()), 3)  # ainda não confirmou
        caixa.confirmar(eventos)
        self.assertEqual(self.arquivos(), [])

    def test_so_rele_quando_a_pasta_muda(self):
        self.gravar()
        caixa = CaixaDeEventos(self.eventos)
        self.assertEqual(len(caixa.ler()), 1)
        with mock.patch("core.caixa.os.scandir", side_effect=AssertionError("não devia listar")):
            self.assertEqual(caixa.ler(), [])  # nada mudou: só um stat

    def test_descarta_invalidos_grandes_e_velhos(self):
        self.eventos.mkdir(parents=True)
        (self.eventos / "001-lixo.json").write_text("{nao")
        (self.eventos / "002-outro.json").write_text(json.dumps({"v": 2, "provider": "x"}))
        (self.eventos / "003-grande.json").write_text("x" * 70_000)
        self.gravar(ts=int((time.time() - 2 * 86400) * 1000))  # dois dias atrás
        (self.eventos / ".004-escrevendo.json.tmp").write_text("{")  # ainda sendo gravado: invisível
        self.gravar(event="Stop")
        caixa = CaixaDeEventos(self.eventos)
        self.assertEqual([e.event for e in caixa.ler()], ["Stop"])
        self.assertEqual(caixa.descartados, 4)
        self.assertEqual(len(self.arquivos()), 1)
        self.assertTrue((self.eventos / ".004-escrevendo.json.tmp").exists())

    def test_sem_pasta_e_muitos_pendentes(self):
        self.assertEqual(CaixaDeEventos(self.dir / "nao-existe").ler(), [])
        with mock.patch("core.caixa.MAX_POR_LEITURA", 2):
            for i in range(3):
                self.gravar(session=f"s{i}")
            caixa = CaixaDeEventos(self.eventos)
            primeiro = caixa.ler()
            caixa.confirmar(primeiro)
            self.assertEqual(len(primeiro), 2)
            self.assertEqual(len(caixa.ler()), 1)  # o resto no ciclo seguinte
        self.assertGreater(MAX_POR_LEITURA, 100)

    def test_campos_validados(self):
        self.gravar(session="x" * 500)
        self.gravar(ts="ontem")
        self.gravar(project=123, detail=["x"])
        eventos = CaixaDeEventos(self.eventos).ler()
        self.assertEqual(len(eventos), 1)
        self.assertEqual((eventos[0].project, eventos[0].detail), (None, None))
        self.assertLess(abs((datetime.now() - eventos[0].quando) / timedelta(seconds=1)), 60)


if __name__ == "__main__":
    unittest.main()
