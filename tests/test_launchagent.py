"""O LaunchAgent do ombrod (ombro/launchagent.py), com um launchctl falso: nada é instalado de verdade."""
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ombro import launchagent as la
from ombro.launchagent import ErroServico, Launchctl, Servico


class LaunchctlFalso:
    """Guarda os rótulos carregados e os comandos recebidos, como o launchctl responderia."""

    def __init__(self, carregados=(), falhar=()):
        self.carregados = set(carregados)
        self.falhar = set(falhar)
        self.comandos = []

    def __call__(self, args):
        self.comandos.append(args[1:])
        acao = args[1]
        ok = subprocess.CompletedProcess(args, 0, "", "")
        if acao in self.falhar:
            return subprocess.CompletedProcess(args, 5, "", "Input/output error")
        if acao == "print":
            rotulo = args[2].rsplit("/", 1)[1]
            if rotulo not in self.carregados:
                return subprocess.CompletedProcess(args, 113, "", "Could not find service")
            return subprocess.CompletedProcess(args, 0, f"{rotulo} = {{\n\tstate = running\n\tpid = 4321\n\tlast exit code = 0\n}}", "")
        if acao == "bootstrap":
            self.carregados.add(plistlib.loads(Path(args[3]).read_bytes())["Label"])
        elif acao == "bootout":
            self.carregados.discard(args[2].rsplit("/", 1)[1])
        return ok


class Base(unittest.TestCase):
    def setUp(self):
        self.casa = Path(tempfile.mkdtemp(prefix="om la"))  # com espaço no caminho, de propósito
        self.addCleanup(shutil.rmtree, self.casa, ignore_errors=True)
        p = mock.patch.dict(os.environ, {"HOME": str(self.casa), "OMBRO_SCREEN_DIR": str(self.casa / ".ombro-screen")})
        p.start()
        self.addCleanup(p.stop)
        self.raiz = self.casa / "meus projetos" / "ombro-screen"
        self.raiz.mkdir(parents=True)
        (self.raiz / "ombrod.py").write_text("")
        r = mock.patch.object(la, "RAIZ", self.raiz)
        r.start()
        self.addCleanup(r.stop)
        self.lc = LaunchctlFalso()
        self.saida = []

    def servico(self, dry_run=False):
        return Servico(Launchctl(self.lc), dry_run=dry_run, saida=self.saida.append)

    def legado(self, carregado=True):
        la.pasta_agents().mkdir(parents=True, exist_ok=True)
        dados = {"Label": la.LEGADO, "ProgramArguments": [sys.executable, str(self.raiz / "tela.py"), "--usb", "--brilho", "40", "--png", "x"],
                 "RunAtLoad": True, "KeepAlive": True}
        la.plist_legado().write_bytes(plistlib.dumps(dados))
        if carregado:
            self.lc.carregados.add(la.LEGADO)
        return dados


class Plist(Base):
    def test_gerado_com_caminhos_absolutos_e_espacos(self):
        d = la.gerar(sys.executable, ["--usb"], raiz=self.raiz)
        self.assertEqual(d["Label"], "com.ombro-screen.daemon")
        self.assertEqual(d["ProgramArguments"], [sys.executable, str(self.raiz / "ombrod.py"), "--usb"])
        self.assertIn(" ", d["ProgramArguments"][1])
        self.assertEqual(d["WorkingDirectory"], str(self.raiz))
        self.assertTrue(all(os.path.isabs(a) for a in d["ProgramArguments"][:2]))
        self.assertNotIn("~", plistlib.dumps(d).decode())
        self.assertEqual(d["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(d["EnvironmentVariables"]["OMBRO_SCREEN_DIR"], str(self.casa / ".ombro-screen"))
        la.validar(d)
        self.assertTrue(la.e_nosso(d))

    def test_validacao(self):
        d = la.gerar(sys.executable, raiz=self.raiz)
        for estragado in ({**d, "Label": "x"}, {**d, "ProgramArguments": ["python", "ombrod.py"]},
                          {**d, "ProgramArguments": [sys.executable, "/nao/existe/ombrod.py"]}, {**d, "WorkingDirectory": "/nao/existe"}):
            with self.assertRaises(ErroServico):
                la.validar(estragado)

    @unittest.skipUnless(shutil.which("plutil"), "plutil só no macOS")
    def test_plutil_aceita(self):
        arq = self.casa / "t.plist"
        arq.write_bytes(plistlib.dumps(la.gerar(sys.executable, raiz=self.raiz)))
        self.assertEqual(subprocess.run(["plutil", "-lint", str(arq)], capture_output=True).returncode, 0)

    def test_opcoes_do_legado(self):
        dados = {"ProgramArguments": ["/py", "/x/tela.py", "--usb", "--brilho", "40", "--png", "q.png", "--layout=multiagente", "--demo"]}
        self.assertEqual(la.args_do_legado(dados), ["--usb", "--brilho", "40", "--layout=multiagente"])
        with self.assertRaises(ErroServico):
            la.args_do_legado({"ProgramArguments": ["/bin/sh", "x.sh"]})


class Operacoes(Base):
    def test_instalar_parar_iniciar_desinstalar(self):
        s = self.servico()
        s.instalar(sys.executable, ["--usb"])
        self.assertTrue(la.plist_novo().exists())
        self.assertIn(la.ROTULO, self.lc.carregados)
        self.assertTrue(la.arquivo_de_log().parent.is_dir())
        self.assertEqual(s.estado()["loaded"]["pid"], 4321)
        s.instalar(sys.executable, ["--usb"])  # de novo: recarrega, não duplica
        self.assertEqual(sum(1 for c in self.lc.comandos if c[0] == "bootstrap"), 2)
        s.parar()
        self.assertNotIn(la.ROTULO, self.lc.carregados)
        self.assertTrue(la.plist_novo().exists())  # parar não apaga
        s.iniciar()
        self.assertIn(la.ROTULO, self.lc.carregados)
        s.reiniciar()
        self.assertIn(["kickstart", "-k", f"gui/{os.getuid()}/{la.ROTULO}"], self.lc.comandos)
        s.desinstalar()
        self.assertFalse(la.plist_novo().exists())
        self.assertNotIn(la.ROTULO, self.lc.carregados)

    def test_dry_run_nao_muda_nada(self):
        s = self.servico(dry_run=True)
        s.instalar(sys.executable)
        self.assertFalse(la.plist_novo().exists())
        self.assertEqual([c for c in self.lc.comandos if c[0] != "print"], [])
        self.assertTrue(any("ombrod.py" in linha for linha in self.saida))

    def test_nao_instala_com_o_legado(self):
        self.legado()
        with self.assertRaises(ErroServico) as e:
            self.servico().instalar(sys.executable)
        self.assertIn("migrate", str(e.exception))
        self.assertIn(la.LEGADO, self.lc.carregados)  # o antigo segue intocado
        self.assertFalse(la.plist_novo().exists())

    def test_nao_mexe_em_plist_desconhecido(self):
        la.pasta_agents().mkdir(parents=True)
        estranho = {"Label": la.ROTULO, "ProgramArguments": ["/bin/sh", "/outro.sh"]}
        la.plist_novo().write_bytes(plistlib.dumps(estranho))
        with self.assertRaises(ErroServico):
            self.servico().instalar(sys.executable)
        with self.assertRaises(ErroServico):
            self.servico().desinstalar()
        self.assertEqual(plistlib.loads(la.plist_novo().read_bytes()), estranho)
        # outros LaunchAgents nunca são tocados, nem numa instalação e desinstalação completas
        la.plist_novo().unlink()
        outro = la.pasta_agents() / "com.outro.app.plist"
        outro.write_bytes(plistlib.dumps({"Label": "com.outro.app"}))
        s = self.servico()
        s.instalar(sys.executable)
        s.desinstalar()
        self.assertEqual(plistlib.loads(outro.read_bytes()), {"Label": "com.outro.app"})
        self.assertTrue(all("com.outro.app" not in " ".join(c) for c in self.lc.comandos))

    def test_plist_invalido(self):
        la.pasta_agents().mkdir(parents=True)
        la.plist_novo().write_text("não é plist")
        with self.assertRaises(ErroServico):
            self.servico().instalar(sys.executable)
        self.assertEqual(la.plist_novo().read_text(), "não é plist")


class Migracao(Base):
    def test_migrar_e_desfazer(self):
        antigo = self.legado()
        s = self.servico()
        s.migrar(sys.executable)
        self.assertNotIn(la.LEGADO, self.lc.carregados)
        self.assertFalse(la.plist_legado().exists())
        self.assertIn(la.ROTULO, self.lc.carregados)
        novo = plistlib.loads(la.plist_novo().read_bytes())
        self.assertEqual(novo["ProgramArguments"][2:], ["--usb", "--brilho", "40"])
        guardado = s.backups_legado()
        self.assertEqual(len(guardado), 1)
        self.assertEqual(guardado[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(plistlib.loads(guardado[0].read_bytes()), antigo)
        s.desfazer_migracao()
        self.assertEqual(plistlib.loads(la.plist_legado().read_bytes()), antigo)
        self.assertIn(la.LEGADO, self.lc.carregados)
        self.assertNotIn(la.ROTULO, self.lc.carregados)
        self.assertFalse(la.plist_novo().exists())

    def test_migracao_simulada(self):
        self.legado()
        self.servico(dry_run=True).migrar(sys.executable)
        self.assertTrue(la.plist_legado().exists())
        self.assertIn(la.LEGADO, self.lc.carregados)
        self.assertFalse(la.plist_novo().exists())
        self.assertFalse((self.casa / ".ombro-screen" / "backups").exists())

    def test_falha_ao_ligar_o_novo_volta_o_antigo(self):
        antigo = self.legado()
        self.lc.falhar = {"bootstrap"}
        s = self.servico()
        with mock.patch.object(Launchctl, "ligar", side_effect=[ErroServico("falhou"), None]):
            with self.assertRaises(ErroServico):
                s.migrar(sys.executable)
        self.assertEqual(plistlib.loads(la.plist_legado().read_bytes()), antigo)
        self.assertFalse(la.plist_novo().exists())

    def test_sem_legado(self):
        with self.assertRaises(ErroServico):
            self.servico().migrar(sys.executable)
        with self.assertRaises(ErroServico):
            self.servico().desfazer_migracao()


if __name__ == "__main__":
    unittest.main()
