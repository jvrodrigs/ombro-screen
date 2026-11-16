"""O ombrod: lock de instância, o laço compartilhado (ombro/app.py), saúde, preferências e o processo inteiro.

Nada aqui precisa de tela USB, de agentes instalados nem de rede: os processos rodam com --demo e numa
pasta temporária (OMBRO_SCREEN_DIR), com o simulador numa porta livre.
"""
import json
from datetime import datetime, timedelta
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from config import OmbroConfig
from core.models import Activity, AgentSession, Attention
from ombro import ipc, preferencias, processos
from ombro.app import Aplicacao, Indisponivel, ao_vivo
from ombro.arquivos import ArquivoMudou, assinatura, gravar_atomico
from coletores.hardware import Hardware
from core.models import ProviderSnapshot, QuotaReport, QuotaWindow, UsageMetrics
from ombro.daemon import (
    Daemon, SaidaIndisponivel, quotas_do_snapshot, sistema_do_hardware, status_da_sessao, uso_do_snapshot,
)
from ombro.instancia import OutraInstancia, Trava, quem_esta_rodando

RAIZ = Path(__file__).resolve().parent.parent


def pasta_curta():
    return tempfile.mkdtemp(prefix="om", dir="/tmp" if os.path.isdir("/tmp") else None)


def porta_livre():
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Temporaria(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(pasta_curta())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.pasta, ignore_errors=True))
        p = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta)})
        p.start()
        self.addCleanup(p.stop)


class Lock(Temporaria):
    def test_uma_instancia_por_vez(self):
        a = Trava("ombrod").adquirir()
        self.addCleanup(a.soltar)
        with self.assertRaises(OutraInstancia) as e:
            Trava("tela.py").adquirir()
        self.assertEqual(e.exception.dono["pid"], os.getpid())
        self.assertIn("ombrod", str(e.exception))
        self.assertEqual(quem_esta_rodando()["kind"], "ombrod")
        a.soltar()
        self.assertIsNone(quem_esta_rodando())
        with Trava("tela.py"):
            pass

    def test_arquivo_que_sobrou_nao_trava(self):
        run = self.pasta / "run"
        run.mkdir(mode=0o700)
        (run / "ombro.lock").write_text('{"pid": 999999, "kind": "ombrod"}')  # de uma instância que morreu
        self.assertIsNone(quem_esta_rodando())
        with Trava("ombrod"):
            self.assertEqual(quem_esta_rodando()["pid"], os.getpid())

    def test_processo_morto_com_kill_9_solta_o_lock(self):
        codigo = ("import sys, time; sys.path.insert(0, %r); from ombro.instancia import Trava; "
                  "Trava('ombrod').adquirir(); print('ok', flush=True); time.sleep(30)") % str(RAIZ)
        p = subprocess.Popen([sys.executable, "-c", codigo], stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.kill)
        self.assertEqual(p.stdout.readline().strip(), "ok")
        with self.assertRaises(OutraInstancia) as e:
            Trava("ombrod").adquirir()
        self.assertEqual(e.exception.dono["pid"], p.pid)
        p.send_signal(signal.SIGKILL)
        p.wait(5)
        with Trava("ombrod"):  # o sistema soltou: nada de PID velho travando
            pass

    def test_link_no_lugar_do_lock_nao_e_seguido(self):
        run = self.pasta / "run"
        run.mkdir(mode=0o700)
        alvo = self.pasta / "alvo"
        alvo.write_text("importante")
        (run / "ombro.lock").symlink_to(alvo)
        with self.assertRaises(OSError):
            Trava("ombrod").adquirir()
        self.assertEqual(alvo.read_text(), "importante")

    def test_quem_precisa_do_lock(self):
        self.assertTrue(ao_vivo(OmbroConfig()))
        self.assertTrue(ao_vivo(OmbroConfig(usb=True)))
        self.assertFalse(ao_vivo(OmbroConfig(demo=True)))
        self.assertTrue(ao_vivo(OmbroConfig(demo=True, usb=True)))
        self.assertFalse(ao_vivo(OmbroConfig(png=Path("q.png"))))


class Processos(unittest.TestCase):
    def test_papel_e_modo(self):
        self.assertEqual(processos._papel(["/x/python", "/a b/ombro-screen/tela.py", "--usb"]), "tela.py")
        self.assertEqual(processos._papel(["python3", "ombrod.py"]), "ombrod")
        self.assertEqual(processos._papel(["python3", "-m", "ombro.daemon"]), "ombrod")
        self.assertIsNone(processos._papel(["python3", "outro.py", "tela.py"]))
        self.assertEqual(processos._papel(["python3", "-u", "tela.py"]), "tela.py")
        self.assertTrue(processos.tela_ao_vivo(["python", "tela.py", "--usb"]))
        self.assertTrue(processos.tela_ao_vivo(["python", "tela.py"]))
        self.assertFalse(processos.tela_ao_vivo(["python", "tela.py", "--demo"]))
        self.assertTrue(processos.tela_ao_vivo(["python", "tela.py", "--demo", "--usb"]))
        self.assertFalse(processos.tela_ao_vivo(["python", "tela.py", "--png", "q.png"]))

    def test_nao_lista_a_si_mesmo(self):
        self.assertNotIn(os.getpid(), [p["pid"] for p in processos.processos_do_ombro()])


class SaidaFalsa:
    def __init__(self):
        self.imagens, self.brilho, self.fechada = [], None, False

    def pronta(self):
        return True

    def mostrar(self, img):
        self.imagens.append(img)

    def definir_brilho(self, v):
        self.brilho = v
        return True

    def estado(self):
        return {"kind": "usb", "connection": "connected", "brightness": self.brilho}

    def fechar(self):
        self.fechada = True


class App(Temporaria):
    def rodando(self, **cfg):
        app = Aplicacao(OmbroConfig(demo=True, intervalo=0.05, **cfg), saida=SaidaFalsa())
        parar = threading.Event()
        t = threading.Thread(target=app.laco, args=(parar.is_set,), daemon=True)
        t.start()
        for _ in range(100):
            if app.quadros:
                break
            time.sleep(0.02)

        def fim():
            parar.set()
            app.acordar()
            t.join(5)
            app.fechar()

        self.addCleanup(fim)
        return app, fim

    def test_pedidos_rodam_no_laco(self):
        app, _ = self.rodando()
        dono = []
        self.assertEqual(app.executar(lambda: dono.append(threading.current_thread()) or 42), 42)
        self.assertIsNot(dono[0], threading.current_thread())
        with self.assertRaises(ZeroDivisionError):  # o erro volta para quem pediu; o laço segue
            app.executar(lambda: 1 / 0)
        q = app.quadros
        time.sleep(0.2)
        self.assertGreater(app.quadros, q)

    def test_fora_do_laco_e_indisponivel(self):
        app = Aplicacao(OmbroConfig(demo=True), saida=SaidaFalsa())
        self.addCleanup(app.fechar)
        with self.assertRaises(Indisponivel):
            app.executar(lambda: 1)

    def test_mudancas_ao_vivo(self):
        app, _ = self.rodando()
        self.assertTrue(app.executar(lambda: app.definir_brilho(45)))
        self.assertEqual((app.saida.brilho, app.cfg.brilho), (45, 45))
        app.executar(lambda: app.definir_layout("multiagente"))
        time.sleep(0.2)
        self.assertIsNotNone(app.resumo)  # coletou de novo para o multiagente
        app.executar(lambda: app.definir_layout("classico"))  # o clássico também troca com o Ombro rodando
        self.assertEqual(app.cfg.layout, "classico")
        app.executar(lambda: app.definir_layout("multiagente"))
        app.executar(lambda: app.definir_cor("#3D7A4F"))
        self.assertEqual(app.mascote.cor_fixa, (0x3D, 0x7A, 0x4F))
        app.executar(lambda: app.definir_vista(vista="attention"))
        self.assertEqual(app.seletor.vista, "attention")

    def test_fechar_libera_quem_espera_e_a_saida(self):
        app, fim = self.rodando()
        fim()
        self.assertTrue(app.saida.fechada)
        with self.assertRaises(Indisponivel):
            app.executar(lambda: 1)


class Saude(Temporaria):
    def daemon(self, display, providers, banco_ativo=True, banco=True):
        d = Daemon(OmbroConfig(demo=True))
        app = mock.Mock()
        app.saida.estado.return_value = display
        app.cfg = d.cfg
        app.ultimo_quadro_em, app.quadros = None, 0
        app.monitor.banco = mock.Mock(ativo=banco_ativo) if banco else None
        d.app, d.estado = app, "running"
        d.ipc = mock.Mock()
        d.publicado = {"providers": providers, "sessions": [], "collected_at": None}
        return d

    def test_tela_desconectada_e_agente_nao_instalado_nao_sao_falhas(self):
        d = self.daemon({"kind": "usb", "connection": "disconnected"}, [
            {"id": "claude", "enabled": True, "integration": "ok", "collection": "ok", "state": "ocioso"},
            {"id": "codex", "enabled": True, "integration": "not_installed", "collection": "ok"},
            {"id": "cursor", "enabled": False},
        ])
        s = d.saude()
        self.assertEqual(s["status"], "healthy")
        self.assertEqual(s["components"]["display"], {"status": "unavailable", "state": "disconnected"})
        self.assertEqual(s["components"]["provider.codex"]["state"], "not_installed")
        self.assertEqual(s["components"]["provider.cursor"]["state"], "disabled")

    def test_falhas_parciais_degradam(self):
        for display, prov, banco in (
            ({"connection": "error"}, "ok", True),
            ({"connection": "connected"}, "error", True),
            ({"connection": "connected"}, "partial", True),
            ({"connection": "connected"}, "ok", False),
        ):
            d = self.daemon(display, [{"id": "claude", "enabled": True, "integration": "ok", "collection": prov}],
                            banco_ativo=banco)
            self.assertEqual(d.saude()["status"], "degraded", (display, prov, banco))
            d._reavaliar()
            self.assertEqual(d.estado, "degraded")

    def test_preferencia_sem_laco_e_recusada_ou_so_gravada(self):
        from ombro.ipc import ErroIPC

        d = Daemon(OmbroConfig(demo=True, arquivo=self.pasta / "config.json"))  # ainda sem app (esperando um conflito)
        with self.assertRaises(ErroIPC) as e:
            d.m_brilho({"value": 20})
        self.assertEqual(e.exception.codigo, "SERVICE_UNAVAILABLE")
        self.assertFalse((self.pasta / "config.json").exists())
        r = d.m_brilho({"value": 20, "persist": True})
        self.assertEqual((r["applied"], r["restart_required"]), ([], ["brilho"]))
        self.assertEqual(json.loads((self.pasta / "config.json").read_text())["brilho"], 20)

    def test_mudanca_so_ao_reiniciar(self):
        from ombro.ipc import ErroIPC

        d = Daemon(OmbroConfig(demo=True, arquivo=self.pasta / "config.json"))
        with self.assertRaises(ErroIPC) as e:
            d.m_config_update({"changes": {"providers.codex": False}, "persist": False})
        self.assertEqual(e.exception.codigo, "VALIDATION_ERROR")
        r = d.m_config_update({"changes": {"providers.codex": False, "usb": True}})
        self.assertEqual(sorted(r["restart_required"]), ["providers.codex", "usb"])
        dados = json.loads((self.pasta / "config.json").read_text())
        self.assertEqual((dados["usb"], dados["providers"]["codex"]["enabled"]), (True, False))
        self.assertEqual(d.m_config_get({})["providers_enabled"], {"claude": True, "codex": True, "cursor": True})
        self.assertEqual(d.m_config_schema({})["version"], 1)

    def test_estado_da_sessao(self):
        s = AgentSession("codex", "s1", Activity.WORKING, Attention.NONE)
        self.assertEqual(status_da_sessao(s), "working")
        self.assertEqual(status_da_sessao(replace(s, attention=Attention.WAITING_APPROVAL)), "approval")
        self.assertEqual(status_da_sessao(replace(s, attention=Attention.ERROR)), "error")
        self.assertEqual(status_da_sessao(replace(s, activity=Activity.IDLE, attention=Attention.WAITING_USER)), "waiting")
        self.assertEqual(status_da_sessao(replace(s, activity=Activity.UNKNOWN)), "unknown")

    def test_simulador_sem_porta(self):
        s = SaidaIndisponivel("porta 5680 indisponível")
        self.assertTrue(s.pronta())
        self.assertEqual(s.estado()["connection"], "error")


class Preferencias(Temporaria):
    def test_grava_so_o_permitido_e_preserva_o_resto(self):
        arq = self.pasta / "config.json"
        arq.write_text('{"versao": 1, "usb": true, "minha": "x", "multi_agent": {"view": "overview"}}')
        mud = preferencias.validar_mudancas({"brilho": 40, "vista": "focus", "foco": "codex", "cor_mascote": "3d7a4f"})
        preferencias.gravar(arq, mud)
        dados = json.loads(arq.read_text())
        self.assertEqual(dados["usb"], True)
        self.assertEqual(dados["minha"], "x")
        self.assertEqual(dados["brilho"], 40)
        self.assertEqual(dados["cor_mascote"], "#3D7A4F")
        self.assertEqual(dados["multi_agent"], {"view": "focus", "focus_provider": "codex"})
        self.assertEqual(oct(arq.stat().st_mode & 0o777), oct(arq.stat().st_mode & 0o777))

    def test_recusa(self):
        from config import ConfigInvalida

        for mud in ({}, {"lib": "/tmp"}, {"claude_dir": "/tmp"}, {"nao_abrir": True}, {"demo": True}, {"png": "q.png"},
                    {"brilho": 150}, {"layout": "outro"}, {"usb": "sim"}, {"providers.codex": "false"},
                    {"providers.outro": False}, {"porta": "/etc/passwd"}, {"porta": "/dev/cu.x; rm"}, {"intervalo": 0.0},
                    {"porta_sim": 80}, {"brilho": None}, {"intervalo_rotacao": 2}):
            with self.assertRaises(ConfigInvalida, msg=mud):
                preferencias.validar_mudancas(mud)
        ok = preferencias.validar_mudancas({"usb": True, "porta": "/dev/cu.usbmodem1101", "intervalo": None, "debug": True,
                                            "providers.codex": False, "revisao": "b", "cor_mascote": None})
        self.assertEqual(ok["revisao"], "B")

    def test_nao_desliga_todos_os_agentes(self):
        from config import ConfigInvalida

        with self.assertRaises(ConfigInvalida):
            preferencias.validar_mudancas({"providers.claude": False}, ligados=("claude",))
        preferencias.validar_mudancas({"providers.claude": False, "providers.codex": True}, ligados=("claude",))
        preferencias.validar_mudancas({"providers.cursor": False}, ligados=("claude", "cursor"))

    def test_agentes_vao_para_o_bloco_providers(self):
        arq = self.pasta / "config.json"
        arq.write_text('{"versao": 1, "providers": {"claude": {"enabled": true}}}')
        preferencias.gravar(arq, {"providers.codex": False, "providers.cursor": False})
        self.assertEqual(json.loads(arq.read_text())["providers"],
                         {"claude": {"enabled": True}, "codex": {"enabled": False}, "cursor": {"enabled": False}})
        from config import ler_arquivo

        self.assertEqual(ler_arquivo(arq)[0]["providers"], ("claude",))  # o tela.py e o ombrod leem igual

    def test_esquema_completo_e_seguro(self):
        from config import PERSISTENTES

        sc = preferencias.esquema()
        chaves = {o["key"] for o in sc["options"]}
        self.assertEqual(sc["version"], 1)
        self.assertTrue({"lib", "claude_dir", "nao_abrir"}.isdisjoint(chaves))  # caminhos e coisas do terminal: nunca
        cobertas = {k.split(".")[0] for k in chaves}
        self.assertEqual(PERSISTENTES - cobertas, {"lib", "claude_dir", "nao_abrir"})  # o resto, tudo
        for o in sc["options"]:
            self.assertIn(o["type"], ("boolean", "integer", "number", "string", "enum", "color"))
            self.assertIn("live", o)
            if o["type"] == "enum":
                self.assertTrue(o["choices"])

    def test_config_invalido_nao_e_sobrescrito(self):
        from config import ConfigInvalida

        arq = self.pasta / "config.json"
        arq.write_text("{quebrado")
        with self.assertRaises(ConfigInvalida):
            preferencias.gravar(arq, {"brilho": 40})
        self.assertEqual(arq.read_text(), "{quebrado")

    def test_novo_arquivo_nasce_privado(self):
        arq = self.pasta / "config.json"
        preferencias.gravar(arq, {"brilho": 40})
        self.assertEqual(arq.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(arq.read_text()), {"versao": 1, "brilho": 40})


class Atomico(Temporaria):
    def test_mantem_permissao_e_segue_o_link(self):
        real = self.pasta / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text("{}")
        os.chmod(real, 0o640)
        link = self.pasta / "settings.json"
        link.symlink_to(real)
        gravar_atomico(link, b'{"a": 1}')
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), '{"a": 1}')
        self.assertEqual(real.stat().st_mode & 0o777, 0o640)
        self.assertEqual([p.name for p in real.parent.iterdir()], ["settings.json"])  # nenhum temporário sobrou

    def test_arquivo_mudou_no_meio(self):
        arq = self.pasta / "a.json"
        arq.write_text("1")
        antes = assinatura(arq)
        arq.write_text("2")
        with self.assertRaises(ArquivoMudou):
            gravar_atomico(arq, b"3", antes=antes)
        self.assertEqual(arq.read_text(), "2")
        self.assertEqual(len(list(self.pasta.iterdir())), 1)

    def test_falha_na_escrita_nao_deixa_nada_pela_metade(self):
        arq = self.pasta / "a.json"
        arq.write_text("original")
        with mock.patch("ombro.arquivos.os.replace", side_effect=OSError("disco cheio")):
            with self.assertRaises(OSError):
                gravar_atomico(arq, b"novo")
        self.assertEqual(arq.read_text(), "original")
        self.assertEqual(len(list(self.pasta.iterdir())), 1)


class ProcessoInteiro(Temporaria):
    """O ombrod de verdade, num subprocesso com --demo."""

    @staticmethod
    def _matar(p):
        if p.poll() is None:
            p.kill()
            p.wait(5)
        if p.stdout:
            p.stdout.close()

    def iniciar(self, *args, esperar=True):
        self.env = dict(os.environ, OMBRO_SCREEN_DIR=str(self.pasta), PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.Popen([sys.executable, str(RAIZ / "ombrod.py"), "--demo", "--porta-sim", str(porta_livre()), *args],
                             env=self.env, cwd=self.pasta, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self._matar, p)
        if esperar:
            sock = self.pasta / "run" / "ombro.sock"
            for _ in range(150):
                try:
                    if ipc.chamar("ping", caminho=sock)["daemon"] in ("running", "degraded"):
                        break
                except ipc.DaemonIndisponivel:
                    pass
                time.sleep(0.1)
            else:
                p.kill()
                self.fail("o ombrod não subiu: " + p.communicate()[0])
        return p

    def test_sobe_responde_e_fecha_com_sigterm(self):
        p = self.iniciar()
        sock = self.pasta / "run" / "ombro.sock"
        st = ipc.chamar("status", caminho=sock)
        self.assertEqual(st["protocol"], 1)
        self.assertEqual(st["pid"], p.pid)
        self.assertTrue(st["demo"])
        for _ in range(50):  # a primeira coleta publica os providers
            if ipc.chamar("providers.list", caminho=sock)["providers"]:
                break
            time.sleep(0.1)
        provs = {x["id"]: x for x in ipc.chamar("providers.list", caminho=sock)["providers"]}
        self.assertEqual(list(provs), ["claude", "codex", "cursor"])
        self.assertEqual([w["id"] for w in provs["claude"]["quotas"]["windows"]], ["5h", "7d"])  # o demo do Claude tem limites
        self.assertIsNotNone(provs["claude"]["usage"]["total_tokens"])
        self.assertIsNone(provs["codex"]["quotas"])  # o Codex não informa limites: nada inventado
        self.assertIsNone(provs["codex"]["usage"])
        for _ in range(50):  # e o primeiro quadro, logo depois
            if ipc.chamar("display.preview", caminho=sock)["available"]:
                break
            time.sleep(0.1)
        self.assertEqual(ipc.chamar("display.preview", caminho=sock)["width"], 480)
        r = ipc.chamar("config.update", {"changes": {"brilho": 35}}, caminho=sock)
        self.assertEqual(r["applied"], ["brilho"])
        self.assertEqual(json.loads((self.pasta / "config.json").read_text())["brilho"], 35)
        # segundo ombrod: recusa e sai, sem mexer no primeiro
        r2 = subprocess.run([sys.executable, str(RAIZ / "ombrod.py"), "--demo", "--porta-sim", str(porta_livre())],
                            env=self.env, cwd=self.pasta, capture_output=True, text=True, timeout=30)
        self.assertEqual(r2.returncode, 75)
        self.assertIn("já existe um Ombro rodando", r2.stderr)
        self.assertEqual(ipc.chamar("ping", caminho=sock)["pong"], True)
        p.send_signal(signal.SIGTERM)
        saida, _ = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0, saida)
        self.assertIn("stopping -> stopped", saida)
        self.assertFalse(sock.exists())
        self.assertIsNone(quem_esta_rodando(self.pasta / "run" / "ombro.lock"))

    def test_tela_ao_vivo_nao_roda_junto_com_o_ombrod(self):
        p = self.iniciar()
        r = subprocess.run([sys.executable, str(RAIZ / "tela.py"), "--nao-abrir", "--sem-macmon", "--porta-sim", str(porta_livre())],
                           env=self.env, cwd=self.pasta, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 75)
        self.assertIn("já existe um Ombro rodando (ombrod", r.stderr)
        # o --png e o --demo do simulador continuam funcionando ao lado do ombrod
        r = subprocess.run([sys.executable, str(RAIZ / "tela.py"), "--demo", "--png", str(self.pasta / "q.png")],
                           env=self.env, cwd=self.pasta, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        p.send_signal(signal.SIGINT)
        p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0)

    def test_simulador_sem_porta_deixa_o_daemon_degradado_mas_vivo(self):
        with closing(socket.socket()) as ocupada:
            ocupada.bind(("127.0.0.1", 0))
            ocupada.listen(1)
            porta = ocupada.getsockname()[1]
            self.env = dict(os.environ, OMBRO_SCREEN_DIR=str(self.pasta))
            p = subprocess.Popen([sys.executable, str(RAIZ / "ombrod.py"), "--demo", "--porta-sim", str(porta)],
                                 env=self.env, cwd=self.pasta, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.addCleanup(self._matar, p)
            sock = self.pasta / "run" / "ombro.sock"
            estado = None
            for _ in range(150):
                try:
                    estado = ipc.chamar("status", caminho=sock)
                    if estado["daemon"] == "degraded":
                        break
                except ipc.DaemonIndisponivel:
                    pass
                time.sleep(0.1)
            self.assertEqual(estado["daemon"], "degraded")
            self.assertEqual(ipc.chamar("display.status", caminho=sock)["connection"], "error")
            self.assertEqual(ipc.chamar("health", caminho=sock)["components"]["display"]["status"], "degraded")
            p.terminate()
            p.communicate(timeout=10)
            self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()


class CamposDoApp(unittest.TestCase):
    """Campos que o Ombro.app usa (acrescentados na v1 do protocolo): só o que o provider entregou."""

    def test_quotas_e_uso(self):
        agora = datetime(2026, 11, 15, 10, 0)
        snap = ProviderSnapshot("claude", agora,
                                usage=UsageMetrics("today", input_tokens=900, output_tokens=100, messages=4),
                                quotas=QuotaReport("statusline", agora, stale=True, windows=(
                                    QuotaWindow("5h", "Sessão", 42.0, agora + timedelta(hours=2)),
                                    QuotaWindow("7d", "Semana", None, None))))
        q = quotas_do_snapshot(snap)
        self.assertEqual(q["source"], "statusline")
        self.assertTrue(q["stale"])
        self.assertEqual(q["windows"][0], {"id": "5h", "label": "Sessão", "used_percent": 42.0,
                                           "resets_at": "2026-11-15T12:00:00"})
        self.assertIsNone(q["windows"][1]["used_percent"])  # sem número, sem 0 inventado
        u = uso_do_snapshot(snap)
        self.assertEqual((u["total_tokens"], u["output_tokens"], u["messages"]), (1000, 100, 4))
        vazio = ProviderSnapshot("codex", agora)
        self.assertIsNone(quotas_do_snapshot(vazio))
        self.assertIsNone(uso_do_snapshot(vazio))
        self.assertIsNone(quotas_do_snapshot(None))

    def test_sistema(self):
        self.assertIsNone(sistema_do_hardware(None))
        self.assertIsNone(sistema_do_hardware(Hardware(disponivel=False)))
        s = sistema_do_hardware(Hardware(cpu_pct=12.345, ram_usada=8, ram_total=16, pressao_memoria="atencao"))
        self.assertEqual(s, {"cpu_percent": 12.3, "ram_used_bytes": 8, "ram_total_bytes": 16, "memory_pressure": "warning"})
