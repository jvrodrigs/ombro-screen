"""O LaunchAgent do ombrod (macOS): iniciar com a sessão do usuário e reiniciar se cair.

  python3 -m ombro service install [--usb] [--dry-run]   grava ~/Library/LaunchAgents/com.ombro-screen.daemon.plist e liga
  python3 -m ombro service start | stop | restart | status
  python3 -m ombro service uninstall                      desliga e apaga só o plist do Ombro
  python3 -m ombro service migrate [--dry-run]            troca o LaunchAgent antigo (com.ombro-screen, tela.py) por este
  python3 -m ombro service rollback                       desfaz a migração: volta o antigo

Regras:
  - só o usuário atual (domínio gui/<uid>), sem sudo, sem LaunchDaemon, nunca como root;
  - só mexe nos dois rótulos do Ombro; um plist com o nosso nome que não aponta para o ombrod.py é recusado;
  - o LaunchAgent antigo nunca é desligado sem `migrate`. Com ele ligado, `install` recusa (os dois
    disputariam a tela). A migração guarda o plist antigo em ~/.ombro-screen/backups/launchagents/ e
    `rollback` o devolve;
  - caminhos absolutos no plist (o launchd não conhece ~ nem a pasta do terminal); espaços funcionam,
    porque cada argumento é um item da lista ProgramArguments.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from config import pasta_ombro
from logging_config import curto, logger

from .arquivos import gravar_atomico
from .caminhos import garantir_pasta_privada, pasta_backups

log = logger("service")

ROTULO = "com.ombro-screen.daemon"
LEGADO = "com.ombro-screen"
RAIZ = Path(__file__).resolve().parent.parent
PATH_PADRAO = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"  # macmon, libusb, codex, cursor-agent
ARGS_DO_LEGADO = {"--usb", "--no-usb", "--paisagem", "--no-paisagem", "--girar", "--no-girar", "--sem-macmon",
                  "--no-sem-macmon", "--debug", "--no-debug"}  # sem valor; com valor: abaixo
ARGS_COM_VALOR = {"--revisao", "--porta", "--transporte", "--brilho", "--lib", "--claude-dir", "--layout", "--vista",
                  "--foco", "--intervalo", "--cor-mascote", "--porta-sim", "--config"}


class ErroServico(Exception):
    pass


def pasta_agents() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_novo() -> Path:
    return pasta_agents() / f"{ROTULO}.plist"


def plist_legado() -> Path:
    return pasta_agents() / f"{LEGADO}.plist"


def arquivo_de_log() -> Path:
    return Path.home() / "Library" / "Logs" / "ombro-screen" / "ombrod.log"


# ---------- o plist ----------
def gerar(python: Optional[str] = None, extras: Sequence[str] = (), raiz: Path = RAIZ) -> dict:
    # sem .pyc gravado ao lado do código: no Ombro.app ele fica dentro do bundle assinado
    env = {"PATH": PATH_PADRAO, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if os.environ.get("OMBRO_SCREEN_DIR"):
        env["OMBRO_SCREEN_DIR"] = str(pasta_ombro())  # já expandido: o launchd não conhece ~
    log_ = str(arquivo_de_log())
    return {
        "Label": ROTULO,
        "ProgramArguments": [str(Path(python or sys.executable)), str(raiz / "ombrod.py"), *extras],
        "WorkingDirectory": str(raiz),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # reinicia se cair; um `stop` (SIGTERM, saída 0) não volta sozinho
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",  # a tela anima: sem o freio de processos em segundo plano
        "StandardOutPath": log_,
        "StandardErrorPath": log_,
    }


def e_nosso(dados: dict) -> bool:
    args = dados.get("ProgramArguments") or []
    return dados.get("Label") == ROTULO and len(args) >= 2 and Path(args[1]).name == "ombrod.py"


def ler_plist(caminho: Path) -> Optional[dict]:
    try:
        with open(caminho, "rb") as f:
            dados = plistlib.load(f)
    except FileNotFoundError:
        return None
    except (OSError, plistlib.InvalidFileException, ValueError) as e:
        raise ErroServico(f"{curto(caminho)} não é um plist válido ({type(e).__name__}); não mexo nele") from None
    if not isinstance(dados, dict):
        raise ErroServico(f"{curto(caminho)} não tem um dicionário no topo; não mexo nele")
    return dados


def validar(dados: dict) -> None:
    """Confere o que o launchd precisa: rótulo, argumentos absolutos que existem, pasta de trabalho."""
    args = dados.get("ProgramArguments")
    if dados.get("Label") != ROTULO or not isinstance(args, list) or len(args) < 2:
        raise ErroServico("plist sem Label ou ProgramArguments")
    for a in args[:2]:
        if not os.path.isabs(a):
            raise ErroServico(f"caminho relativo no plist: {a}")
        if not os.path.exists(a):
            raise ErroServico(f"não existe: {curto(a)}")
    if not os.path.isdir(dados.get("WorkingDirectory", "")):
        raise ErroServico("WorkingDirectory não existe")
    plistlib.loads(plistlib.dumps(dados))  # serializável e relido igual


def args_do_legado(dados: dict) -> List[str]:
    """As opções do tela.py do LaunchAgent antigo que o ombrod também aceita (o resto fica de fora)."""
    args = list(dados.get("ProgramArguments") or [])
    try:
        i = next(i for i, a in enumerate(args) if Path(a).name == "tela.py")
    except StopIteration:
        raise ErroServico("o LaunchAgent antigo não roda o tela.py; não sei migrá-lo") from None
    resto, extras, j = args[i + 1:], [], 0
    while j < len(resto):
        a = resto[j]
        nome = a.split("=", 1)[0]
        if nome in ARGS_DO_LEGADO:
            extras.append(a)
        elif nome in ARGS_COM_VALOR:
            if "=" in a:
                extras.append(a)
            elif j + 1 < len(resto):
                extras += [a, resto[j + 1]]
                j += 1
        j += 1
    return extras


# ---------- o launchctl ----------
Executor = Callable[[List[str]], subprocess.CompletedProcess]


def _executar(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=15)


class Launchctl:
    def __init__(self, executar: Executor = _executar):
        self._executar = executar
        self.dominio = f"gui/{os.getuid()}"

    def _rodar(self, *args: str) -> subprocess.CompletedProcess:
        return self._executar(["launchctl", *args])

    def info(self, rotulo: str) -> Optional[dict]:
        """None se o serviço não está carregado; senão {state, pid, last_exit_code} (o que o launchctl disser)."""
        r = self._rodar("print", f"{self.dominio}/{rotulo}")
        if r.returncode != 0:
            return None
        info = {"state": None, "pid": None, "last_exit_code": None}
        for linha in r.stdout.splitlines():
            linha = linha.strip()
            for chave, nome in (("state = ", "state"), ("pid = ", "pid"), ("last exit code = ", "last_exit_code")):
                if linha.startswith(chave) and info[nome] is None:
                    valor = linha[len(chave):].strip()
                    info[nome] = int(valor) if nome == "pid" and valor.isdigit() else valor
        return info

    def ligar(self, plist: Path) -> None:
        r = self._rodar("bootstrap", self.dominio, str(plist))
        if r.returncode != 0:
            raise ErroServico(f"launchctl bootstrap falhou ({(r.stderr or r.stdout).strip()[:200]})")

    def desligar(self, rotulo: str) -> None:
        r = self._rodar("bootout", f"{self.dominio}/{rotulo}")
        if r.returncode != 0 and self.info(rotulo) is not None:
            raise ErroServico(f"launchctl bootout falhou ({(r.stderr or r.stdout).strip()[:200]})")

    def reiniciar(self, rotulo: str, matar: bool = True) -> None:
        r = self._rodar("kickstart", *(["-k"] if matar else []), f"{self.dominio}/{rotulo}")
        if r.returncode != 0:
            raise ErroServico(f"launchctl kickstart falhou ({(r.stderr or r.stdout).strip()[:200]})")


# ---------- operações ----------
class Servico:
    def __init__(self, launchctl: Optional[Launchctl] = None, dry_run: bool = False, saida=print):
        self.lc = launchctl or Launchctl()
        self.dry_run = dry_run
        self.saida = saida

    def _passo(self, texto: str) -> None:
        self.saida(("  [simulação] " if self.dry_run else "  ") + texto)

    def estado(self) -> dict:
        novo, legado = plist_novo(), plist_legado()
        dados_novo = ler_plist(novo) if novo.exists() else None
        dados_legado = None
        try:
            dados_legado = ler_plist(legado) if legado.exists() else None
        except ErroServico:
            dados_legado = {"invalid": True}
        return {
            "label": ROTULO, "plist": curto(novo), "installed": dados_novo is not None,
            "ours": bool(dados_novo and e_nosso(dados_novo)),
            "loaded": self.lc.info(ROTULO), "log": curto(arquivo_de_log()),
            "program": [curto(a) for a in (dados_novo or {}).get("ProgramArguments", [])],
            "legacy": {"label": LEGADO, "plist": curto(legado), "installed": dados_legado is not None,
                       "loaded": self.lc.info(LEGADO)},
            "backups": [curto(p) for p in self.backups_legado()],
        }

    def _gravar_plist(self, dados: dict) -> None:
        validar(dados)
        destino = plist_novo()
        atual = ler_plist(destino) if destino.exists() else None
        if atual is not None and not e_nosso(atual):
            raise ErroServico(f"{curto(destino)} existe e não aponta para o ombrod.py; não sobrescrevo")
        if self.dry_run:
            self._passo(f"gravar {curto(destino)}:")
            self.saida(plistlib.dumps(dados).decode("utf-8"))
            return
        pasta_agents().mkdir(parents=True, exist_ok=True)
        arquivo_de_log().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        gravar_atomico(destino, plistlib.dumps(dados), modo=0o644)
        self._passo(f"gravado {curto(destino)}")

    def _ligar_novo(self) -> None:
        if self.lc.info(ROTULO) is not None:
            self._passo(f"recarregar {ROTULO} (bootout + bootstrap)")
            if not self.dry_run:
                self.lc.desligar(ROTULO)
        else:
            self._passo(f"ligar {ROTULO} (bootstrap)")
        if not self.dry_run:
            self.lc.ligar(plist_novo())

    def instalar(self, python=None, extras: Sequence[str] = ()) -> None:
        if plist_legado().exists() or self.lc.info(LEGADO) is not None:
            raise ErroServico(f"o LaunchAgent antigo ({LEGADO}) está instalado: os dois disputariam a tela. "
                              "Use `python3 -m ombro service migrate` (guarda o antigo e dá para desfazer)")
        self._gravar_plist(gerar(python, extras))
        self._ligar_novo()

    def desinstalar(self) -> None:
        destino = plist_novo()
        atual = ler_plist(destino) if destino.exists() else None
        if atual is not None and not e_nosso(atual):
            raise ErroServico(f"{curto(destino)} não aponta para o ombrod.py; não apago")
        if self.lc.info(ROTULO) is not None:
            self._passo(f"desligar {ROTULO} (bootout)")
            if not self.dry_run:
                self.lc.desligar(ROTULO)
        if atual is not None:
            self._passo(f"apagar {curto(destino)}")
            if not self.dry_run:
                destino.unlink()
        if atual is None and self.lc.info(ROTULO) is None:
            self._passo("nada instalado")

    def iniciar(self) -> None:
        if not plist_novo().exists():
            raise ErroServico("o serviço não está instalado (python3 -m ombro service install)")
        if self.lc.info(ROTULO) is None:
            self._passo(f"ligar {ROTULO}")
            if not self.dry_run:
                self.lc.ligar(plist_novo())
        else:
            self._passo(f"iniciar {ROTULO} (kickstart)")
            if not self.dry_run:
                self.lc.reiniciar(ROTULO, matar=False)

    def parar(self) -> None:
        if self.lc.info(ROTULO) is None:
            self._passo("o serviço já está desligado")
            return
        self._passo(f"desligar {ROTULO} (bootout; volta no próximo login ou com `service start`)")
        if not self.dry_run:
            self.lc.desligar(ROTULO)

    def reiniciar(self) -> None:
        if self.lc.info(ROTULO) is None:
            return self.iniciar()
        self._passo(f"reiniciar {ROTULO} (kickstart -k)")
        if not self.dry_run:
            self.lc.reiniciar(ROTULO)

    # ---------- migração do LaunchAgent antigo ----------
    @staticmethod
    def pasta_backup_legado() -> Path:
        return pasta_backups() / "launchagents"

    def backups_legado(self) -> List[Path]:
        p = self.pasta_backup_legado()
        return sorted(p.glob(f"*-{LEGADO}.plist")) if p.is_dir() else []

    def migrar(self, python=None, usb: Optional[bool] = None) -> None:
        legado = plist_legado()
        dados = ler_plist(legado)
        if dados is None:
            raise ErroServico(f"não há LaunchAgent antigo em {curto(legado)}; use `service install`")
        if dados.get("Label") != LEGADO:
            raise ErroServico(f"{curto(legado)} tem outro Label ({dados.get('Label')!r}); não mexo nele")
        extras = args_do_legado(dados)
        if usb is not None:
            extras = [a for a in extras if a not in ("--usb", "--no-usb")] + ["--usb" if usb else "--no-usb"]
        novo = gerar(python, extras)
        validar(novo)
        self._passo(f"opções levadas do antigo: {' '.join(extras) or '(nenhuma)'}")
        carimbo = time.strftime("%Y%m%d-%H%M%S")
        guarda = self.pasta_backup_legado() / f"{carimbo}-{LEGADO}.plist"
        self._passo(f"guardar {curto(legado)} em {curto(guarda)}")
        if not self.dry_run:
            garantir_pasta_privada(pasta_backups())
            garantir_pasta_privada(guarda.parent)
            shutil.copy2(legado, guarda)
            os.chmod(guarda, 0o600)
        if self.lc.info(LEGADO) is not None:
            self._passo(f"desligar o antigo ({LEGADO}, bootout: o tela.py dele recebe SIGTERM e fecha a tela)")
            if not self.dry_run:
                self.lc.desligar(LEGADO)
        self._passo(f"tirar {curto(legado)} de LaunchAgents (para não voltar no próximo login)")
        if not self.dry_run:
            legado.unlink()
        try:
            self._gravar_plist(novo)
            self._ligar_novo()
        except ErroServico:
            if not self.dry_run:
                self.saida("  a ligação do novo falhou: voltando o antigo")
                self._restaurar_legado(guarda)
            raise

    def _restaurar_legado(self, guarda: Path) -> None:
        if plist_novo().exists():
            if self.lc.info(ROTULO) is not None:
                self.lc.desligar(ROTULO)
            plist_novo().unlink()
        shutil.copy2(guarda, plist_legado())
        self.lc.ligar(plist_legado())

    def desfazer_migracao(self) -> None:
        lista = self.backups_legado()
        if not lista:
            raise ErroServico("não há backup do LaunchAgent antigo (nenhuma migração feita por aqui)")
        guarda = lista[-1]
        if plist_legado().exists():
            raise ErroServico(f"{curto(plist_legado())} já existe; nada a desfazer")
        self._passo(f"desligar e apagar {ROTULO}")
        self._passo(f"devolver {curto(guarda)} para {curto(plist_legado())} e ligar {LEGADO}")
        if not self.dry_run:
            self._restaurar_legado(guarda)


# ---------- a CLI ----------
def comando(args) -> int:
    if sys.platform != "darwin":
        print("o serviço (LaunchAgent) só existe no macOS; rode python3 ombrod.py como preferir", file=sys.stderr)
        return 2
    if os.geteuid() == 0:
        print("não rode como root: o LaunchAgent é do seu usuário (sem sudo)", file=sys.stderr)
        return 2
    linhas: List[str] = []
    s = Servico(dry_run=args.dry_run, saida=(linhas.append if args.json else print))
    extras = [] if args.usb is None else (["--usb"] if args.usb else ["--no-usb"])
    try:
        if args.acao == "status":
            st = s.estado()
            if args.json:
                print(json.dumps(st, ensure_ascii=False, indent=2))
            else:
                carregado = st["loaded"]
                print(f"Serviço {ROTULO}")
                print(f"  plist:     {st['plist']} ({'instalado' if st['installed'] else 'não instalado'})")
                print(f"  launchd:   {'carregado, ' + str(carregado.get('state')) if carregado else 'não carregado'}"
                      + (f", PID {carregado['pid']}" if carregado and carregado.get("pid") else ""))
                if carregado and carregado.get("last_exit_code") not in (None, "0", "(never exited)"):
                    print(f"  última saída: {carregado['last_exit_code']}")
                print(f"  log:       {st['log']}")
                lg = st["legacy"]
                if lg["installed"] or lg["loaded"]:
                    print(f"  antigo:    {LEGADO} {'carregado' if lg['loaded'] else 'instalado, não carregado'}"
                          " (migre com: python3 -m ombro service migrate)")
            return 0
        acoes = {
            "install": lambda: s.instalar(args.python, extras),
            "uninstall": s.desinstalar,
            "start": s.iniciar,
            "stop": s.parar,
            "restart": s.reiniciar,
            "migrate": lambda: s.migrar(args.python, args.usb),
            "rollback": s.desfazer_migracao,
        }
        if not args.json:
            print(f"service {args.acao}{' (simulação: nada muda)' if args.dry_run else ''}:", flush=True)
        acoes[args.acao]()
    except ErroServico as e:
        if args.json:
            print(json.dumps({"ok": False, "action": args.acao, "error": str(e), "steps": linhas}, ensure_ascii=False))
        else:
            print(f"erro: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "action": args.acao, "dry_run": args.dry_run, "steps": linhas}, ensure_ascii=False, indent=2))
    return 0
