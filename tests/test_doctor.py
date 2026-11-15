"""`ombro doctor` (ombro/doctor.py): verificações, JSON estável e relatório sem dados pessoais.

O que depende da máquina (processos, launchctl, USB, versões dos agentes) é trocado por respostas fixas.
"""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ombro import doctor
from ombro.doctor import FAIL, PASS, SKIP, WARN, Ambiente
from ombro.ipc import DaemonIndisponivel

LEGADO_DESLIGADO = {"installed": False, "ours": False, "loaded": None,
                    "legacy": {"installed": False, "loaded": None}, "backups": []}


def sem_daemon(_metodo, _params=None):
    raise DaemonIndisponivel("o ombrod não está rodando")


def daemon_falso(display=None, providers=()):
    respostas = {
        "status": {"daemon": "running", "health": "healthy", "pid": 123, "protocol": 1, "version": "0.4.0", "uptime_s": 9},
        "providers.list": {"providers": list(providers)},
        "display.status": display or {"kind": "usb", "connection": "connected", "revision": "A", "transport": "usb"},
    }
    return lambda metodo, params=None: respostas[metodo]


class Base(unittest.TestCase):
    def setUp(self):
        self.casa = Path(tempfile.mkdtemp(prefix="omdoc"))
        self.addCleanup(shutil.rmtree, self.casa, ignore_errors=True)
        self.pasta = self.casa / ".ombro-screen"
        pilha = ExitStack()
        self.addCleanup(pilha.close)
        pilha.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.casa), "OMBRO_SCREEN_DIR": str(self.pasta)}))
        for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            os.environ.pop(var, None)
        pilha.enter_context(mock.patch("ombro.processos.processos_do_ombro", return_value=[]))
        pilha.enter_context(mock.patch("ombro.launchagent.Servico.estado", return_value=LEGADO_DESLIGADO))
        pilha.enter_context(mock.patch("usb_direto.problema", return_value="falta o libusb"))
        pilha.enter_context(mock.patch("serial.tools.list_ports.comports", return_value=[]))
        self.which = pilha.enter_context(mock.patch("shutil.which", return_value=None))

    def amb(self, **kw):
        base = dict(plataforma="darwin", ipc=sem_daemon, executar=lambda args: None, tem_modulo=lambda nome: True)
        base.update(kw)
        return Ambiente(**base)

    def checks(self, **kw):
        r = doctor.diagnosticar(self.amb(**kw))
        return r, {c["id"]: c for c in r["checks"]}


class Esquema(Base):
    def test_json_estavel(self):
        r, por_id = self.checks()
        self.assertEqual((r["schema"], r["schema_version"]), ("ombro.doctor", 1))
        self.assertEqual(set(r["summary"]), {"status", "pass", "warn", "fail", "skip"})
        self.assertEqual(len(por_id), len(r["checks"]))  # ids únicos
        for c in r["checks"]:
            self.assertEqual(set(c), {"id", "category", "status", "title", "message", "action", "details"})
            self.assertIn(c["status"], (PASS, WARN, FAIL, SKIP))
            self.assertIn(c["category"], ("environment", "service", "providers", "storage", "display", "configuration"))
        json.dumps(r)
        for essencial in ("env.os", "env.python", "env.deps", "config.file", "storage.sqlite", "service.daemon",
                          "service.instances", "provider.claude", "provider.codex", "provider.cursor", "display.connection"):
            self.assertIn(essencial, por_id)

    def test_cli_json_e_codigo_de_saida(self):
        out = io.StringIO()
        with redirect_stdout(out):
            codigo = doctor.comando(SimpleNamespace(json=True, report=False, output=None))
        r = json.loads(out.getvalue())
        self.assertEqual(r["schema"], "ombro.doctor")
        self.assertEqual(codigo, 1 if r["summary"]["status"] == FAIL else 0)


class Ambientes(Base):
    def test_ambiente_valido(self):
        _, c = self.checks()
        self.assertEqual(c["env.deps"]["status"], PASS)
        self.assertEqual(c["config.file"]["status"], PASS)
        self.assertEqual(c["storage.dir"]["status"], SKIP)  # a pasta ainda não existe: o doctor não cria
        self.assertFalse(self.pasta.exists())

    def test_ambiente_invalido(self):
        _, c = self.checks(versao_python=(3, 8, 10), tem_modulo=lambda nome: nome not in ("PIL", "usb"), plataforma="linux")
        self.assertEqual(c["env.python"]["status"], WARN)
        self.assertEqual(c["env.deps"]["status"], FAIL)
        self.assertIn("Pillow", c["env.deps"]["message"])
        self.assertEqual(c["env.os"]["status"], WARN)
        self.assertEqual(c["service.launchagent"]["status"], SKIP)

    def test_config_invalido(self):
        self.pasta.mkdir(mode=0o700)
        (self.pasta / "config.json").write_text('{"brilho": 900}')
        r, c = self.checks()
        self.assertEqual(c["config.file"]["status"], FAIL)
        self.assertEqual(r["summary"]["status"], FAIL)
        (self.pasta / "config.json").write_text('{"chave_estranha": 1}')
        _, c = self.checks()
        self.assertEqual(c["config.file"]["status"], WARN)

    def test_permissoes_inadequadas(self):
        self.pasta.mkdir()
        os.chmod(self.pasta, 0o755)
        (self.pasta / "config.json").write_text("{}")
        os.chmod(self.pasta / "config.json", 0o644)
        _, c = self.checks()
        self.assertEqual(c["storage.dir"]["status"], WARN)
        self.assertIn("chmod 700", c["storage.dir"]["action"])
        self.assertEqual(c["storage.files"]["status"], WARN)
        self.assertEqual(oct(os.stat(self.pasta).st_mode & 0o777), "0o755")  # avisa, não muda


class Servico(Base):
    def test_servico_parado(self):
        _, c = self.checks()
        self.assertEqual(c["service.daemon"]["status"], WARN)
        self.assertIn("não está rodando", c["service.daemon"]["message"])

    def test_servico_rodando(self):
        _, c = self.checks(ipc=daemon_falso())
        self.assertEqual(c["service.daemon"]["status"], PASS)
        self.assertEqual(c["display.connection"]["status"], PASS)

    def test_instancia_duplicada(self):
        with mock.patch("ombro.doctor.quem_esta_rodando", return_value={"pid": 1, "kind": "ombrod"}), \
                mock.patch("ombro.processos.processos_do_ombro", return_value=[{"pid": 2, "kind": "tela.py", "live": True}]):
            _, c = self.checks(ipc=daemon_falso())
        self.assertEqual(c["service.instances"]["status"], FAIL)

    def test_launchagent_legado(self):
        legado = dict(LEGADO_DESLIGADO, legacy={"installed": True, "loaded": {"pid": 9}})
        with mock.patch("ombro.launchagent.Servico.estado", return_value=legado):
            _, c = self.checks()
        self.assertEqual(c["service.launchagent"]["status"], WARN)
        self.assertIn("migrate", c["service.launchagent"]["action"])


class Armazenamento(Base):
    def test_banco_indisponivel_ou_estragado(self):
        self.pasta.mkdir(mode=0o700)
        (self.pasta / "state.db").write_bytes(b"isto nao e sqlite" * 100)
        _, c = self.checks()
        self.assertEqual(c["storage.sqlite"]["status"], FAIL)
        self.assertEqual((self.pasta / "state.db").read_bytes(), b"isto nao e sqlite" * 100)  # não apaga nem conserta

    def test_banco_de_versao_mais_nova(self):
        self.pasta.mkdir(mode=0o700)
        conn = sqlite3.connect(self.pasta / "state.db")
        conn.execute("PRAGMA user_version = 99")
        conn.close()
        _, c = self.checks()
        self.assertEqual(c["storage.sqlite"]["status"], FAIL)
        self.assertIn("mais nova", c["storage.sqlite"]["message"])

    def test_banco_saudavel(self):
        from core.persistence import StateDB

        db = StateDB(self.pasta / "state.db")
        self.assertTrue(db.abrir())
        db.fechar()
        _, c = self.checks()
        self.assertEqual(c["storage.sqlite"]["status"], PASS)

    def test_eventos_esperando_sem_daemon(self):
        (self.pasta / "eventos").mkdir(parents=True)
        (self.pasta / "eventos" / "1.json").write_text("{}")
        _, c = self.checks()
        self.assertEqual(c["storage.events"]["status"], WARN)


class Agentes(Base):
    def test_provider_ausente(self):
        with mock.patch("ombro.integracoes.Alvo.instalado", return_value=False):  # nem o .app desta máquina conta
            _, c = self.checks()
        for pid in ("codex", "cursor"):
            self.assertEqual(c[f"provider.{pid}"]["status"], SKIP)

    def test_provider_instalado_sem_integracao(self):
        (self.casa / ".cursor").mkdir()
        self.which.side_effect = lambda nome: "/usr/local/bin/cursor-agent" if nome == "cursor-agent" else None
        _, c = self.checks(executar=lambda args: "2026.09.23-abc\n")
        self.assertEqual(c["provider.cursor"]["status"], WARN)
        self.assertIn("integrations install cursor", c["provider.cursor"]["action"])
        self.assertEqual(c["provider.cursor"]["details"]["version"], "2026.09.23-abc")

    def test_coleta_com_erro_no_daemon(self):
        (self.casa / ".claude").mkdir()
        prov = [{"id": "claude", "enabled": True, "collection": "error"}]
        _, c = self.checks(ipc=daemon_falso(providers=prov))
        self.assertEqual(c["provider.claude"]["status"], FAIL)

    def test_display_ausente(self):
        _, c = self.checks(ipc=daemon_falso(display={"kind": "usb", "connection": "disconnected"}))
        self.assertEqual(c["display.connection"]["status"], WARN)
        _, c = self.checks(ipc=daemon_falso(display={"kind": "usb", "connection": "error", "last_error": "busy"}))
        self.assertEqual(c["display.connection"]["status"], FAIL)


class Sanitizacao(Base):
    def test_sanitizar(self):
        casa = str(Path.home())
        with mock.patch.dict(os.environ, {"USER": "fulanodetal"}), mock.patch("socket.gethostname", return_value="MacDoFulano.local"):
            s = doctor.sanitizar(f"{casa}/projeto-secreto/a.py fulanodetal@MacDoFulano "
                                 "sk-ant-api03-abcdefghijklmnopqrstuv ghp_abcdefghijklmnopqrstuvwxyz012345 token=abc123")
        self.assertNotIn(casa, s)
        self.assertNotIn("fulanodetal", s)
        self.assertNotIn("MacDoFulano", s)
        self.assertNotIn("sk-ant", s)
        self.assertNotIn("ghp_", s)
        self.assertNotIn("abc123", s)
        self.assertIn("~/projeto-secreto", s)

    def test_relatorio_sem_dados_privados(self):
        # config com algo pessoal e eventos com nome de projeto: nada disso pode ir para o relatório
        self.pasta.mkdir(mode=0o700)
        (self.pasta / "config.json").write_text('{"claude_dir": "' + str(self.casa) + '/claude-privado", "cor_mascote": "#3D7A4F"}')
        (self.pasta / "eventos").mkdir()
        (self.pasta / "eventos" / "1.json").write_text('{"project": "projeto-super-secreto", "session": "sess-123"}')
        with mock.patch.dict(os.environ, {"USER": "fulanodetal"}):
            r = doctor.diagnosticar(self.amb(ipc=daemon_falso()))
            texto = doctor.relatorio(r)
            js = json.dumps(r)
        for proibido in (str(self.casa), "fulanodetal", "projeto-super-secreto", "sess-123", "claude-privado", "#3D7A4F"):
            self.assertNotIn(proibido, texto)
            self.assertNotIn(proibido, js)

    def test_relatorio_so_em_arquivo_quando_pedido(self):
        destino = self.casa / "relatorio.txt"
        with redirect_stdout(io.StringIO()):
            doctor.comando(SimpleNamespace(json=False, report=True, output=destino))
        self.assertEqual(destino.stat().st_mode & 0o777, 0o600)
        self.assertIn("Relatório do Ombro", destino.read_text())


if __name__ == "__main__":
    unittest.main()
