"""Logs: formato, --debug, sem repetição a cada leitura e sem dados sensíveis."""
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import logging_config
from logging_config import SoQuandoMuda, configurar, curto, sem_casa

RAIZ = Path(__file__).resolve().parent.parent


class CapturaDeLog(unittest.TestCase):
    """Liga os logs do Ombro num buffer, no nível DEBUG, e desfaz no fim."""

    def setUp(self):
        self.buffer = io.StringIO()
        raiz = logging.getLogger("ombro")
        antes = (list(raiz.handlers), raiz.level, raiz.propagate)
        configurar(debug=True, fluxo=self.buffer)

        def desfazer():
            raiz.handlers[:] = antes[0]
            raiz.setLevel(antes[1])
            raiz.propagate = antes[2]
        self.addCleanup(desfazer)

    def linhas(self, nivel=None):
        todas = self.buffer.getvalue().splitlines()
        return [linha for linha in todas if nivel is None or f" {nivel} [" in linha]


class Formato(CapturaDeLog):
    def test_formato_unico(self):
        logging_config.logger("usb").info("tela USB conectada (%s)", "USB direto")
        self.assertRegex(self.buffer.getvalue(),
                         r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO \[ombro\.usb\] tela USB conectada \(USB direto\)\n$")

    def test_sem_debug_esconde_o_detalhe(self):
        configurar(debug=False, fluxo=self.buffer)
        log = logging_config.logger("claude")
        log.debug("detalhe")
        log.info("importante")
        self.assertEqual([re.sub(r"^\S+ \S+ ", "", linha) for linha in self.linhas()], ["INFO [ombro.claude] importante"])

    def test_configurar_de_novo_nao_duplica(self):
        configurar(debug=True, fluxo=self.buffer)
        configurar(debug=True, fluxo=self.buffer)
        logging_config.logger("main").info("uma vez")
        self.assertEqual(len(self.linhas()), 1)

    def test_bibliotecas_ficam_de_fora(self):
        logging.getLogger("usb.core").debug("barulho do pyusb")
        self.assertEqual(self.linhas(), [])


class SoQuandoMudaTeste(CapturaDeLog):
    def test_repeticao_nao_entra_e_a_volta_entra_uma_vez(self):
        avisos = SoQuandoMuda(logging_config.logger("hardware"))
        for _ in range(5):
            avisos.registrar("macmon", logging.WARNING, "macmon falhou (%s)", "x")
        avisos.registrar("macmon", logging.WARNING, "macmon falhou (%s)", "y")  # mudou: entra
        self.assertTrue(avisos.ativo("macmon"))
        avisos.resolver("macmon", "macmon voltou")
        avisos.resolver("macmon", "macmon voltou")
        self.assertEqual(len(self.linhas("WARNING")), 2)
        self.assertEqual(len(self.linhas("INFO")), 1)

    def test_resolver_sem_problema_nao_registra(self):
        self.assertFalse(SoQuandoMuda(logging_config.logger("x")).resolver("k", "tudo certo"))
        self.assertEqual(self.linhas(), [])

    def test_caminhos_sem_a_pasta_pessoal(self):
        casa = str(Path.home())
        self.assertEqual(curto(Path.home() / ".claude"), "~/.claude")
        self.assertEqual(curto("/dev/cu.usbmodem1"), "/dev/cu.usbmodem1")
        self.assertEqual(curto(casa + "-outra/x"), casa + "-outra/x")  # só a pasta pessoal mesmo
        self.assertEqual(sem_casa(f"Permission denied: '{casa}/.claude/x.json'"), "Permission denied: '~/.claude/x.json'")


class ColetoresNoLog(CapturaDeLog):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.config = self.base / "claude"
        (self.config / "sessions").mkdir(parents=True)
        (self.base / "tela").mkdir()
        from coletores import claude

        self.claude = claude
        p = mock.patch.object(claude, "PASTA_TELA", self.base / "tela")
        p.start()
        self.addCleanup(p.stop)

    def test_privacidade_sem_projeto_prompt_nem_pasta_pessoal(self):
        segredo_prompt = "minha senha do banco é 1234"
        quando = int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)
        (self.config / "sessions" / "s.json").write_text(json.dumps({
            "pid": os.getpid(), "status": "waiting", "statusUpdatedAt": quando, "cwd": "/Users/fulano/projeto-secreto"}))
        proj = self.config / "projects" / "projeto-secreto"
        proj.mkdir(parents=True)
        (proj / "t.jsonl").write_text(json.dumps({"type": "user", "message": {"content": segredo_prompt}}) + "\n" +
                                      json.dumps({"type": "assistant", "timestamp": datetime.now().astimezone().isoformat(),
                                                  "message": {"id": "a", "content": segredo_prompt, "usage": {"output_tokens": 3}}}) + "\n")
        (self.base / "tela" / "uso.json").write_text("{quebrado")
        c = self.claude.ColetorClaude(str(self.config))
        with mock.patch.object(Path, "home", return_value=self.base):
            for _ in range(3):
                u = c.ler()
        self.assertEqual(u.espera_projeto, "projeto-secreto")  # a tela mostra, o log não
        texto = self.buffer.getvalue()
        self.assertNotIn("projeto-secreto", texto)
        self.assertNotIn(segredo_prompt, texto)
        self.assertNotIn("fulano", texto)

    def test_erro_de_leitura_entra_uma_vez_e_a_volta_tambem(self):
        c = self.claude.ColetorClaude(str(self.config))
        with mock.patch.object(self.claude.ColetorClaude, "_sessoes", side_effect=PermissionError("sem acesso")):
            for _ in range(4):
                c.ler()
        c.ler()
        avisos = self.linhas("WARNING")
        self.assertEqual(len(avisos), 1)
        self.assertIn("não consegui ler sessões do Claude Code (PermissionError: sem acesso)", avisos[0])
        self.assertTrue(any("detalhes da falha em sessões" in linha for linha in self.linhas("DEBUG")))  # traceback no debug
        self.assertTrue(any("voltou ao normal" in linha for linha in self.linhas("INFO")))

    def test_fonte_e_numeros_velhos_so_quando_mudam(self):
        uso = self.base / "tela" / "uso.json"
        velho = int((datetime.now() - timedelta(minutes=50)).timestamp() * 1000)
        uso.write_text(json.dumps({"fetchedAt": velho, "five_hour": {"utilization": 10}}))
        c = self.claude.ColetorClaude(str(self.config))
        for _ in range(3):
            c.ler()
        uso.write_text(json.dumps({"fetchedAt": int(datetime.now().timestamp() * 1000), "five_hour": {"utilization": 10}}))
        c.ler()
        c.ler()
        self.assertEqual([linha.split("] ")[1] for linha in self.linhas("INFO")],
                         ["uso do plano (5 h e semana): fonte statusline", "o uso do plano voltou a ser atual (statusline)"])
        self.assertEqual(len(self.linhas("WARNING")), 1)

    def test_amostra_estranha_do_macmon_entra_uma_vez(self):
        from coletores import hardware

        with mock.patch.object(hardware.LeitorMacmon, "disponivel", staticmethod(lambda: False)):
            c = hardware.ColetorHardware()
        c.macmon = SimpleNamespace(ultimo={"cpu_scaled_ratio": None})
        for _ in range(10):
            c.ler()
        c.macmon.ultimo = {"cpu_scaled_ratio": .5}
        c.ler()
        self.assertEqual(len(self.linhas("WARNING")), 1)
        self.assertIn("voltaram ao normal", self.linhas("INFO")[-1])


class TelaUSBNoLog(CapturaDeLog):
    def test_porta_ocupada_avisa_uma_vez(self):
        import saida
        import usb_direto

        class Ocupada:
            @classmethod
            def auto_detect_com_port(cls):
                return True

            def __init__(self, **kw):
                raise OSError(16, "Resource busy")

        relogio = [0.0]
        with mock.patch.object(usb_direto, "LcdRevAUsb", Ocupada), mock.patch.object(saida.time, "monotonic", lambda: relogio[0]):
            t = saida.TelaUSB("A", "AUTO", 30, Path("/x"), paisagem=True, transporte="usb")
            for _ in range(20):
                relogio[0] += saida.RETENTAR_S
                t.mostrar(None)
        self.assertEqual(len(self.linhas("WARNING")), 1)
        self.assertIn("Resource busy", self.linhas("WARNING")[0])


class LinhaDeComando(unittest.TestCase):
    def rodar(self, *args):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = dict(os.environ, OMBRO_SCREEN_DIR=tmp.name, CLAUDE_CONFIG_DIR=str(Path(tmp.name) / "claude"))
        return subprocess.run([sys.executable, str(RAIZ / "tela.py"), "--demo", "--png", str(Path(tmp.name) / "q.png"), *args],
                              env=env, capture_output=True, text=True, timeout=60)

    def test_debug_mostra_a_configuracao_resolvida(self):
        r = self.rodar("--debug")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DEBUG [ombro.config] configuração resolvida", r.stderr)
        self.assertIn("INFO [ombro.main] quadro salvo em", r.stderr)

    def test_sem_debug_so_o_essencial(self):
        r = self.rodar()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("DEBUG", r.stderr)
        self.assertLessEqual(len(r.stderr.splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
