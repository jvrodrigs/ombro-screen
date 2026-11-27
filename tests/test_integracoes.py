"""O gerenciador de integrações (ombro/integracoes.py). Tudo num HOME temporário: nunca nos arquivos reais."""
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ombro import cli, integracoes
from ombro.integracoes import ALVOS, ErroIntegracao, comando_do_hook, e_do_ombro

PY = "/usr/bin/python3"
OUTROS_CLAUDE = {
    "model": "opus",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "afplay /System/Library/Sounds/Glass.aiff"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-guard.sh"}]}],
    },
}


class Base(unittest.TestCase):
    def setUp(self):
        self.casa = Path(tempfile.mkdtemp(prefix="omhome"))
        self.addCleanup(shutil.rmtree, self.casa, ignore_errors=True)
        env = {"HOME": str(self.casa), "OMBRO_SCREEN_DIR": str(self.casa / ".ombro-screen")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            os.environ.pop(var, None)
        for pasta in (".claude", ".codex", ".cursor"):
            (self.casa / pasta).mkdir()

    def arq(self, provider):
        return ALVOS[provider].arquivo

    def gravar(self, provider, dados):
        self.arq(provider).write_text(json.dumps(dados, indent=2) + "\n")

    def dados(self, provider):
        return json.loads(self.arq(provider).read_text())

    def comandos(self, provider):
        d = self.dados(provider)["hooks"]
        if provider == "cursor":
            return {ev: [h["command"] for h in g] for ev, g in d.items()}
        return {ev: [h["command"] for grupo in g for h in grupo["hooks"]] for ev, g in d.items()}


class Reconhecimento(unittest.TestCase):
    def test_entrada_do_ombro(self):
        for prov in ALVOS:
            self.assertTrue(e_do_ombro(comando_do_hook(prov, PY), prov))
            self.assertTrue(e_do_ombro(comando_do_hook(prov, "/caminho com espaço/python3.14"), prov))
        self.assertTrue(e_do_ombro("/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py claude || true", "claude"))
        self.assertTrue(e_do_ombro("/usr/bin/python3 /x/ombro_hook.py cursor 2>/dev/null || echo '{}'", "cursor"))
        self.assertFalse(e_do_ombro("my-guard.sh", "claude"))
        self.assertFalse(e_do_ombro(None, "claude"))
        # citam o hook, mas de um jeito que não é o do Ombro: ambíguo, nunca mexer
        self.assertIsNone(e_do_ombro("cd /x && python3 ombro_hook.py claude", "claude"))
        self.assertIsNone(e_do_ombro("python3 /x/ombro_hook.py claude; rm -rf /", "claude"))
        self.assertIsNone(e_do_ombro("python3 /x/ombro_hook.py codex || true", "claude"))  # de outro provider
        self.assertIsNone(e_do_ombro("python3 '/x/ombro_hook.py claude", "claude"))  # aspas quebradas

    def test_caminho_com_espaco_e_aspas(self):
        cmd = comando_do_hook("claude", "/Users/Fulano de Tal/py env/bin/python")
        t = shlex.split(cmd)
        self.assertEqual(t[0], "/Users/Fulano de Tal/py env/bin/python")
        self.assertEqual(Path(t[1]).name, "ombro_hook.py")
        self.assertEqual(t[2:], ["claude", "||", "true"])
        self.assertEqual(shlex.split(comando_do_hook("cursor", PY))[-1], "{}")


class Instalar(Base):
    def test_claude_preserva_o_resto_e_e_idempotente(self):
        self.gravar("claude", OUTROS_CLAUDE)
        r = integracoes.instalar("claude", python=PY)
        self.assertTrue(r.mudou)
        d = self.dados("claude")
        self.assertEqual(d["model"], "opus")
        self.assertEqual(d["permissions"], OUTROS_CLAUDE["permissions"])
        self.assertEqual(d["hooks"]["PreToolUse"], OUTROS_CLAUDE["hooks"]["PreToolUse"])
        cmds = self.comandos("claude")
        self.assertEqual(cmds["Stop"][0], "afplay /System/Library/Sounds/Glass.aiff")
        for ev in integracoes._eventos("claude"):
            self.assertEqual(sum(1 for c in cmds[ev] if e_do_ombro(c, "claude")), 1, ev)
        handler = d["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(handler, {"type": "command", "command": comando_do_hook("claude", PY), "async": True})
        texto = self.arq("claude").read_text()
        r2 = integracoes.instalar("claude", python=PY)
        self.assertFalse(r2.mudou)
        self.assertEqual(self.arq("claude").read_text(), texto)
        self.assertEqual(len(integracoes.backups("claude")), 1)  # a segunda vez não grava nem faz backup
        self.assertEqual(integracoes.analisar(ALVOS["claude"], PY).estado, "configured")

    def test_reinstalar_troca_so_as_entradas_do_ombro_no_lugar(self):
        velho = "/usr/bin/python3 /pasta/antiga/ombro_hook.py claude || true"
        self.gravar("claude", {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "antes.sh"}]},
            {"hooks": [{"type": "command", "command": velho, "async": True}]},
            {"hooks": [{"type": "command", "command": "depois.sh"}]},
        ]}})
        self.assertEqual(integracoes.analisar(ALVOS["claude"], PY).estado, "partial")
        integracoes.instalar("claude", python=PY)
        self.assertEqual(self.comandos("claude")["Stop"], ["antes.sh", comando_do_hook("claude", PY), "depois.sh"])
        # o handler trocado ganha os campos certos (async), sem sobrar nada do antigo
        self.assertEqual(self.dados("claude")["hooks"]["Stop"][1]["hooks"][0]["async"], True)

    def test_outro_python_que_existe_conta_como_configurado_e_e_mantido(self):
        integracoes.instalar("claude", python=PY)
        self.assertEqual(integracoes.analisar(ALVOS["claude"]).estado, "configured")
        texto = self.arq("claude").read_text()
        self.assertFalse(integracoes.instalar("claude").mudou)  # sem --python: mantém o /usr/bin/python3 de lá
        self.assertEqual(self.arq("claude").read_text(), texto)
        self.assertEqual(integracoes.analisar(ALVOS["claude"], python=sys.executable).estado, "partial")

    def test_python_que_sumiu_e_desatualizado(self):
        sumiu = "/nao/existe/python3"
        integracoes.instalar("claude", python=sumiu)
        self.assertEqual(integracoes.analisar(ALVOS["claude"]).estado, "partial")
        integracoes.instalar("claude")  # sem --python: o python que sumiu não é reaproveitado
        self.assertEqual(integracoes.analisar(ALVOS["claude"]).estado, "configured")
        self.assertNotIn(sumiu, self.arq("claude").read_text())

    def test_grupo_misto_perde_so_o_handler_do_ombro(self):
        velho = "/usr/bin/python3 /antiga/ombro_hook.py claude || true"
        self.gravar("claude", {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "meu.sh"},
                                                              {"type": "command", "command": velho}]}]}})
        integracoes.remover("claude")
        self.assertEqual(self.dados("claude"), {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "meu.sh"}]}]}})

    def test_dry_run_nao_grava(self):
        self.gravar("claude", OUTROS_CLAUDE)
        antes = self.arq("claude").read_bytes()
        r = integracoes.instalar("claude", dry_run=True, python=PY)
        self.assertTrue(r.mudou)
        self.assertIn("+", r.diff)
        self.assertIn("ombro_hook.py", r.diff)
        self.assertEqual(self.arq("claude").read_bytes(), antes)
        self.assertEqual(integracoes.backups("claude"), [])

    def test_arquivo_inexistente_e_criado_privado(self):
        integracoes.instalar("cursor", python=PY)
        d = self.dados("cursor")
        self.assertEqual(d["version"], 1)
        self.assertEqual(set(d["hooks"]), set(integracoes._eventos("cursor")))
        self.assertEqual(d["hooks"]["stop"], [{"command": comando_do_hook("cursor", PY)}])
        self.assertEqual(self.arq("cursor").stat().st_mode & 0o777, 0o600)

    def test_codex(self):
        integracoes.instalar("codex", python=PY)
        h = self.dados("codex")["hooks"]
        self.assertEqual(h["SessionEnd"][0]["hooks"][0], {"type": "command", "command": comando_do_hook("codex", PY), "timeout": 3})
        self.assertTrue(h["Stop"][0]["hooks"][0]["async"])
        self.assertIn("/hooks", integracoes.instalar("codex", python="/outro/python3").nota)

    def test_pasta_do_agente_inexistente(self):
        shutil.rmtree(self.casa / ".codex")
        with self.assertRaises(ErroIntegracao):
            integracoes.instalar("codex", python=PY)
        self.assertFalse((self.casa / ".codex").exists())  # não cria pasta de agente

    def test_variaveis_de_pasta_dos_agentes(self):
        outra = self.casa / "claude-alt"
        outra.mkdir()
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(outra)}):
            integracoes.instalar("claude", python=PY)
            self.assertTrue((outra / "settings.json").exists())
        self.assertFalse((self.casa / ".claude" / "settings.json").exists())


class Falhas(Base):
    def test_json_invalido_nao_e_tocado(self):
        self.arq("claude").write_text('{"hooks": {,}')
        with self.assertRaises(ErroIntegracao) as e:
            integracoes.instalar("claude", python=PY)
        self.assertIn("não é um JSON válido", str(e.exception))
        self.assertEqual(self.arq("claude").read_text(), '{"hooks": {,}')
        self.assertEqual(integracoes.analisar(ALVOS["claude"], PY).estado, "invalid")

    def test_formato_desconhecido(self):
        for dados in ({"hooks": []}, {"hooks": {"Stop": {"x": 1}}}, {"hooks": {"Stop": [{"hooks": "x"}]}}, [1, 2]):
            self.arq("claude").write_text(json.dumps(dados))
            with self.assertRaises(ErroIntegracao, msg=dados):
                integracoes.instalar("claude", python=PY)
            self.assertEqual(json.loads(self.arq("claude").read_text()), dados)
        self.gravar("cursor", {"version": 2, "hooks": {}})
        with self.assertRaises(ErroIntegracao):
            integracoes.instalar("cursor", python=PY)

    def test_conflito_com_entrada_ambigua(self):
        dados = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "cd ~ && python3 x/ombro_hook.py claude"}]}]}}
        self.gravar("claude", dados)
        with self.assertRaises(ErroIntegracao) as e:
            integracoes.instalar("claude", python=PY)
        self.assertIn("não reconhece", str(e.exception))
        self.assertEqual(self.dados("claude"), dados)
        self.assertEqual(integracoes.analisar(ALVOS["claude"], PY).estado, "conflict")
        r = integracoes.remover("claude")  # remover também não mexe nela, e avisa
        self.assertEqual(self.dados("claude"), dados)
        self.assertIn("à mão", r.nota)

    @unittest.skipIf(os.geteuid() == 0, "root ignora permissões")
    def test_somente_leitura(self):
        self.gravar("claude", OUTROS_CLAUDE)
        os.chmod(self.arq("claude"), 0o400)
        self.addCleanup(os.chmod, self.arq("claude"), 0o600)
        with self.assertRaises(ErroIntegracao) as e:
            integracoes.instalar("claude", python=PY)
        self.assertIn("permissão", str(e.exception))
        self.assertEqual(self.dados("claude"), OUTROS_CLAUDE)

    def test_escrita_interrompida_deixa_o_original(self):
        self.gravar("claude", OUTROS_CLAUDE)
        with mock.patch("ombro.arquivos.os.replace", side_effect=OSError("disco cheio")):
            with self.assertRaises(ErroIntegracao):
                integracoes.instalar("claude", python=PY)
        self.assertEqual(self.dados("claude"), OUTROS_CLAUDE)
        self.assertEqual([p.name for p in self.arq("claude").parent.iterdir()], ["settings.json"])

    def test_sem_backup_nao_grava(self):
        self.gravar("claude", OUTROS_CLAUDE)
        with mock.patch("ombro.integracoes.fazer_backup", side_effect=OSError("sem espaço")):
            with self.assertRaises(ErroIntegracao):
                integracoes.instalar("claude", python=PY)
        self.assertEqual(self.dados("claude"), OUTROS_CLAUDE)

    def test_arquivo_mudou_durante_a_operacao(self):
        self.gravar("claude", OUTROS_CLAUDE)
        ler = integracoes.ler

        def ler_e_alguem_mexe(alvo):
            leitura = ler(alvo)
            self.arq("claude").write_text(json.dumps({"model": "sonnet"}))  # o Claude Code salvou algo nesse meio tempo
            return leitura

        with mock.patch("ombro.integracoes.ler", side_effect=ler_e_alguem_mexe):
            with self.assertRaises(ErroIntegracao) as e:
                integracoes.instalar("claude", python=PY)
        self.assertIn("mudou", str(e.exception))
        self.assertEqual(self.dados("claude"), {"model": "sonnet"})

    def test_conferencia_falha_restaura_o_original(self):
        self.gravar("claude", OUTROS_CLAUDE)
        original = self.arq("claude").read_text()
        with mock.patch("ombro.integracoes.analisar", wraps=integracoes.analisar) as a:
            a.side_effect = lambda alvo, python=None, leitura=None: integracoes.Analise(
                "claude", "", True, True, ["Stop"], ambiguos=[] if leitura is None or leitura.texto == original else [],
                erro=None if leitura is None or leitura.texto == original else "simulado")
            with self.assertRaises(ErroIntegracao):
                integracoes.instalar("claude", python=PY)
        self.assertEqual(self.arq("claude").read_text(), original)

    def test_link_simbolico_continua_link(self):
        real = self.casa / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text(json.dumps(OUTROS_CLAUDE))
        self.arq("claude").symlink_to(real)
        integracoes.instalar("claude", python=PY)
        self.assertTrue(self.arq("claude").is_symlink())
        self.assertIn("SessionStart", json.loads(real.read_text())["hooks"])
        r = integracoes.restaurar("claude")
        self.assertTrue(r.mudou)
        self.assertEqual(json.loads(real.read_text()), OUTROS_CLAUDE)


class RemoverERollback(Base):
    def test_remover_so_o_do_ombro_e_idempotente(self):
        self.gravar("claude", OUTROS_CLAUDE)
        integracoes.instalar("claude", python=PY)
        integracoes.remover("claude")
        self.assertEqual(self.dados("claude"), OUTROS_CLAUDE)
        self.assertFalse(integracoes.remover("claude").mudou)
        self.assertFalse(integracoes.remover("codex").mudou)  # arquivo inexistente: nada a fazer

    def test_rollback_volta_e_pode_ser_desfeito(self):
        self.gravar("cursor", {"version": 1, "hooks": {"stop": [{"command": "meu.sh"}]}})
        integracoes.instalar("cursor", python=PY)
        instalado = self.dados("cursor")
        integracoes.restaurar("cursor")
        self.assertEqual(self.dados("cursor"), {"version": 1, "hooks": {"stop": [{"command": "meu.sh"}]}})
        integracoes.restaurar("cursor")  # o estado de antes do rollback virou backup
        self.assertEqual(self.dados("cursor"), instalado)

    def test_rollback_sem_backup(self):
        with self.assertRaises(ErroIntegracao):
            integracoes.restaurar("claude")

    def test_retencao_e_permissoes_dos_backups(self):
        self.gravar("claude", OUTROS_CLAUDE)
        for i in range(14):
            integracoes.instalar("claude", python=f"/py{i}/python3")
        lista = integracoes.backups("claude")
        self.assertEqual(len(lista), integracoes.MANTER_BACKUPS)
        for b in lista:
            self.assertEqual(b.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(b.with_suffix(".json").read_text())["file"], str(self.arq("claude")))
        pasta = lista[0].parent
        for p in (pasta, pasta.parent, pasta.parent.parent):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700, p)


class CLI(Base):
    def rodar(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            codigo = cli.main(list(args))
        return codigo, out.getvalue(), err.getvalue()

    def test_list_check_install_json(self):
        c, out, _ = self.rodar("integrations", "list", "--json")
        self.assertEqual(c, 0)
        self.assertEqual({i["provider"]: i["state"] for i in json.loads(out)["integrations"]},
                         {"claude": "not_configured", "codex": "not_configured", "cursor": "not_configured",
                          "opencode": "not_configured"})
        c, out, _ = self.rodar("integrations", "install", "claude", "--python", PY, "--dry-run")
        self.assertIn("nada foi gravado", out)
        self.assertFalse(self.arq("claude").exists())
        c, out, _ = self.rodar("integrations", "install", "claude", "--python", PY, "--json")
        r = json.loads(out)
        self.assertTrue(r["ok"] and r["changed"])
        self.assertEqual(r["check"]["state"], "configured")
        c, _, _ = self.rodar("integrations", "check", "claude", "--python", PY)
        self.assertEqual(c, 0)
        c, _, err = self.rodar("integrations", "install")
        self.assertEqual(c, 2)

    def test_status_nunca_instala(self):
        c, _, err = self.rodar("status")
        self.assertEqual(c, cli.SEM_DAEMON)
        self.assertIn("não está rodando", err)
        for p in ALVOS:
            self.assertFalse(self.arq(p).exists())


if __name__ == "__main__":
    unittest.main()
