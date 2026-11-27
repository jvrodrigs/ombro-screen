"""`python3 -m ombro doctor`: o diagnóstico da instalação. Só lê; nunca muda nada.

  python3 -m ombro doctor                 resultado no terminal
  python3 -m ombro doctor --json          o mesmo, em JSON (schema "ombro.doctor", versão 1; ver docs/troubleshooting.md)
  python3 -m ombro doctor --report        relatório de suporte, sem dados pessoais (no terminal)
  python3 -m ombro doctor --report --output relatorio.txt     o mesmo, num arquivo (0600), só se pedido

Cada verificação dá PASS, WARN, FAIL ou SKIP, com uma mensagem e, quando há o que fazer, a ação.
Sai com 1 se alguma deu FAIL.

O que nunca faz: rodar prompts ou abrir sessões nos agentes (só `--version`, com tempo limite), abrir a
tela USB (só procura o dispositivo na lista do USB, então "detectada" não quer dizer que as imagens
chegam), gravar no banco (abre só para leitura), instalar hooks ou mexer no serviço.

O relatório não leva: prompts, conteúdo de projetos, nomes de projeto, ids de sessão, credenciais, tokens,
o conteúdo do config.json ou dos arquivos dos agentes, payloads dos hooks, o nome do usuário ou da máquina.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from config import ConfigInvalida, arquivo_padrao, pasta_ombro, resolver
from logging_config import curto, logger

from . import VERSAO
from .caminhos import arquivo_socket, permissao_aberta, socket_cabe
from .instancia import quem_esta_rodando
from .ipc import PROTOCOLO, DaemonIndisponivel, ErroIPC, chamar

log = logger("doctor")

ESQUEMA = "ombro.doctor"
VERSAO_ESQUEMA = 1
PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
ORDEM = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3}
PYTHONS = ((3, 9), (3, 14))


@dataclass
class Verificacao:
    id: str  # estável, para o app: "env.python", "provider.codex"...
    categoria: str  # environment, service, providers, storage, display, configuration
    status: str
    titulo: str
    mensagem: str
    acao: Optional[str] = None
    detalhes: Dict = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {"id": self.id, "category": self.categoria, "status": self.status, "title": self.titulo,
                "message": self.mensagem, "action": self.acao, "details": self.detalhes}


# ---------- sanitização (relatório e JSON) ----------
_SEGREDOS = [
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b[A-Za-z0-9+/=_-]{40,}"),  # qualquer coisa longa demais para ser um nome
    re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+"),
]


def sanitizar(texto: str) -> str:
    """Pasta pessoal vira ~, o usuário e a máquina somem, e o que parece segredo vira <removido>."""
    s = str(texto)
    casa = str(Path.home())
    if casa and casa != "/":
        s = s.replace(casa, "~")
    try:
        usuario = os.environ.get("USER") or os.getlogin()
    except OSError:
        usuario = ""
    if usuario and len(usuario) >= 3:
        s = re.sub(rf"(?<![A-Za-z0-9]){re.escape(usuario)}(?![A-Za-z0-9])", "<usuario>", s)
    host = socket.gethostname().split(".")[0]
    if host and len(host) >= 3:
        s = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(host)}(?![A-Za-z0-9])", "<maquina>", s)
    for padrao in _SEGREDOS:
        s = padrao.sub("<removido>", s)
    return s


def _limpo(obj):
    if isinstance(obj, str):
        return sanitizar(obj)
    if isinstance(obj, dict):
        return {k: _limpo(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_limpo(v) for v in obj]
    return obj


# ---------- o que o doctor consulta (injetável nos testes) ----------
@dataclass
class Ambiente:
    plataforma: str = sys.platform
    versao_python: tuple = tuple(sys.version_info[:3])
    ipc: Callable = chamar  # (metodo, params) -> resultado; levanta DaemonIndisponivel
    executar: Callable = None  # (args) -> stdout|None, com tempo limite
    tem_modulo: Callable = None  # nome -> bool

    def __post_init__(self):
        if self.executar is None:
            self.executar = _executar
        if self.tem_modulo is None:
            self.tem_modulo = _tem_modulo


def _executar(args: List[str]) -> Optional[str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _tem_modulo(nome: str) -> bool:
    try:
        importlib.import_module(nome)
        return True
    except Exception:  # noqa: BLE001 - um módulo quebrado conta como ausente
        return False


# ---------- verificações ----------
def ambiente(amb: Ambiente) -> List[Verificacao]:
    v = []
    mac = amb.plataforma == "darwin"
    v.append(Verificacao("env.os", "environment", PASS if mac else WARN, "Sistema operacional",
                         "macOS" if mac else f"{amb.plataforma}: o simulador funciona; a tela USB e o serviço são do macOS",
                         detalhes={"platform": amb.plataforma}))
    if mac:
        ver = platform.mac_ver()[0]
        v.append(Verificacao("env.macos", "environment", PASS if ver else WARN, "Versão do macOS",
                             ver or "não consegui ler a versão", detalhes={"version": ver}))
    else:
        v.append(Verificacao("env.macos", "environment", SKIP, "Versão do macOS", "não é macOS"))
    v.append(Verificacao("env.arch", "environment", PASS, "Arquitetura", platform.machine() or "desconhecida",
                         detalhes={"machine": platform.machine()}))
    py = amb.versao_python
    ok = PYTHONS[0] <= tuple(py[:2]) <= PYTHONS[1]
    v.append(Verificacao("env.python", "environment", PASS if ok else WARN, "Python",
                         f"{'.'.join(map(str, py))}" + ("" if ok else " (o Ombro é testado do 3.9 ao 3.14)"),
                         None if ok else "use um Python de 3.9 a 3.14", {"version": ".".join(map(str, py))}))
    faltam = [n for n, m in (("Pillow", "PIL"), ("psutil", "psutil")) if not amb.tem_modulo(m)]
    v.append(Verificacao("env.deps", "environment", FAIL if faltam else PASS, "Dependências obrigatórias",
                         f"faltam: {', '.join(faltam)}" if faltam else "Pillow e psutil instalados",
                         "python3 -m pip install -r requirements.txt" if faltam else None, {"missing": faltam}))
    opcionais = [("pyserial", "serial", "porta serial da tela"), ("numpy", "numpy", "biblioteca da tela")]
    if mac:
        opcionais.append(("pyusb", "usb", "envio direto pelo USB (revisão A)"))
    faltam = [f"{n} ({para})" for n, m, para in opcionais if not amb.tem_modulo(m)]
    if mac and not shutil.which("macmon"):
        faltam.append("macmon (GPU e temperatura; brew install macmon)")
    v.append(Verificacao("env.optional", "environment", WARN if faltam else PASS, "Dependências opcionais",
                         f"faltam: {'; '.join(faltam)}" if faltam else "todas instaladas",
                         "python3 -m pip install -r requirements.txt" if faltam else None, {"missing": faltam}))
    return v


def pastas() -> List[Verificacao]:
    v = []
    base = pasta_ombro()
    if not base.exists():
        v.append(Verificacao("storage.dir", "storage", SKIP, "Pasta do Ombro", f"{curto(base)} ainda não existe (o Ombro cria ao rodar)"))
    else:
        st = os.lstat(base)
        problemas = []
        if stat.S_ISLNK(st.st_mode):
            problemas.append("é um link simbólico")
        if st.st_uid != os.getuid():
            problemas.append("é de outro usuário")
        aberta = bool(st.st_mode & 0o077)
        status = FAIL if problemas else (WARN if aberta else PASS)
        msg = "; ".join(problemas) if problemas else (f"permissão {oct(st.st_mode & 0o777)}: outros usuários podem ler"
                                                      if aberta else "privada (0700)")
        v.append(Verificacao("storage.dir", "storage", status, "Pasta do Ombro", f"{curto(base)}: {msg}",
                             f"chmod 700 {curto(base)}" if aberta and not problemas else None,
                             {"path": curto(base), "mode": oct(st.st_mode & 0o777)}))
        abertos = [p.name for p in (base / "config.json", base / "state.db", base / "uso.json") if p.exists() and permissao_aberta(p)]
        if abertos:
            v.append(Verificacao("storage.files", "storage", WARN, "Permissão dos arquivos",
                                 f"legíveis por outros usuários: {', '.join(abertos)}",
                                 "chmod 600 " + " ".join(curto(base / a) for a in abertos)))
    sock = arquivo_socket()
    v.append(Verificacao("service.socket_path", "service", PASS if socket_cabe(sock) else FAIL, "Caminho do socket",
                         "cabe no limite do sistema" if socket_cabe(sock) else "longo demais para um socket Unix",
                         None if socket_cabe(sock) else "use um OMBRO_SCREEN_DIR mais curto"))
    return v


def configuracao() -> List[Verificacao]:
    try:
        cfg, avisos = resolver({})
    except ConfigInvalida as e:
        return [Verificacao("config.file", "configuration", FAIL, "Arquivo de configuração", str(e),
                            "corrija o arquivo (veja a seção 7 do README)")]
    arq = cfg.arquivo or arquivo_padrao()
    if cfg.arquivo is None:
        return [Verificacao("config.file", "configuration", PASS, "Arquivo de configuração",
                            f"{curto(arq)} não existe: valem os padrões")]
    if avisos:
        return [Verificacao("config.file", "configuration", WARN, "Arquivo de configuração",
                            f"{curto(arq)} é válido, com avisos: " + "; ".join(avisos), "remova as chaves desconhecidas",
                            {"warnings": avisos})]
    return [Verificacao("config.file", "configuration", PASS, "Arquivo de configuração", f"{curto(arq)} é válido",
                        detalhes={"layout": cfg.layout, "usb": cfg.usb, "providers": list(cfg.providers)})]


def armazenamento(daemon: Optional[dict]) -> List[Verificacao]:
    from core.persistence import VERSAO as VERSAO_BANCO

    v = []
    db = pasta_ombro() / "state.db"
    if not db.exists():
        v.append(Verificacao("storage.sqlite", "storage", SKIP, "Banco de estado (SQLite)",
                             f"{curto(db)} ainda não existe (criado na primeira execução sem --demo)"))
    else:
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
            try:
                versao = conn.execute("PRAGMA user_version").fetchone()[0]
                integro = conn.execute("PRAGMA quick_check").fetchone()[0]
                conhecido = 0 < versao <= VERSAO_BANCO  # num banco mais novo, as tabelas podem ser outras
                sessoes = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] if conhecido else 0
                eventos = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] if conhecido else 0
            finally:
                conn.close()
        except sqlite3.Error as e:
            v.append(Verificacao("storage.sqlite", "storage", FAIL, "Banco de estado (SQLite)", f"não consegui ler ({e})",
                                 f"mova {curto(db)} para outro lugar para o Ombro começar um banco novo"))
        else:
            detalhes = {"schema": versao, "supported": VERSAO_BANCO, "sessions": sessoes, "events": eventos,
                        "retention_days": 7}
            if versao > VERSAO_BANCO:
                st, msg, acao = FAIL, f"versão {versao}, mais nova que a deste Ombro ({VERSAO_BANCO}); ele segue só em memória", \
                    "atualize o Ombro"
            elif integro != "ok":
                st, msg, acao = FAIL, f"verificação de integridade falhou ({integro})", f"mova {curto(db)} para outro lugar"
            else:
                st, msg, acao = PASS, f"íntegro, versão {versao}; {sessoes} sessões e {eventos} eventos guardados (retenção de 7 dias)", None
            if st == PASS and permissao_aberta(db):
                st, acao = WARN, f"chmod 600 {curto(db)}"
                msg += "; legível por outros usuários"
            v.append(Verificacao("storage.sqlite", "storage", st, "Banco de estado (SQLite)", msg, acao, detalhes))
    pasta = pasta_ombro() / "eventos"
    try:
        pendentes = sum(1 for e in os.scandir(pasta) if e.name.endswith(".json") and not e.name.startswith("."))
    except OSError:
        pendentes = 0
    if daemon is None and pendentes:
        v.append(Verificacao("storage.events", "storage", WARN, "Eventos dos hooks",
                             f"{pendentes} evento(s) esperando um Ombro rodando (ficam até 24 h; no máximo 5000)",
                             "inicie o ombrod", {"pending": pendentes}))
    else:
        v.append(Verificacao("storage.events", "storage", WARN if pendentes > 4000 else PASS, "Eventos dos hooks",
                             f"{pendentes} pendente(s)", None, {"pending": pendentes}))
    return v


def servico(amb: Ambiente) -> tuple:
    """(verificações, status do daemon ou None)."""
    from . import processos

    v = []
    status = None
    try:
        status = amb.ipc("status", None)
    except DaemonIndisponivel:
        dono = quem_esta_rodando()
        if dono:
            v.append(Verificacao("service.daemon", "service", WARN if dono.get("kind") == "tela.py" else FAIL, "ombrod",
                                 f"o Ombro ao vivo é um {dono.get('kind', '?')} (PID {dono.get('pid', '?')}), não o ombrod"
                                 if dono.get("kind") == "tela.py" else "o ombrod segura o lock mas não responde pela IPC",
                                 "pare o tela.py e rode o ombrod" if dono.get("kind") == "tela.py"
                                 else "veja o log (~/Library/Logs/ombro-screen/ombrod.log) e reinicie: python3 -m ombro service restart",
                                 {"lock_owner": dono}))
        else:
            v.append(Verificacao("service.daemon", "service", WARN, "ombrod", "O ombrod não está rodando.",
                                 "python3 ombrod.py, ou instale o serviço: python3 -m ombro service install"))
    except ErroIPC as e:
        v.append(Verificacao("service.daemon", "service", FAIL, "ombrod", f"respondeu com erro: {e.mensagem}",
                             "atualize a CLI e o ombrod para a mesma versão"))
    else:
        st = PASS if status.get("daemon") == "running" else WARN
        v.append(Verificacao("service.daemon", "service", st, "ombrod",
                             f"{status.get('daemon')}, saúde {status.get('health')}, PID {status.get('pid')}, "
                             f"protocolo v{status.get('protocol')}",
                             None if st == PASS else "python3 -m ombro health mostra o componente com problema",
                             {"state": status.get("daemon"), "health": status.get("health"), "protocol": status.get("protocol"),
                              "version": status.get("version"), "uptime_s": status.get("uptime_s")}))
        if status.get("protocol") != PROTOCOLO:
            v.append(Verificacao("service.protocol", "service", FAIL, "Versão do protocolo",
                                 f"o ombrod fala v{status.get('protocol')}, a CLI v{PROTOCOLO}", "reinicie o ombrod"))
    dono = quem_esta_rodando()
    vivos = processos.processos_do_ombro()
    sem_lock = [p for p in vivos if p["live"] and p["kind"] == "tela.py" and (not dono or p["pid"] != dono.get("pid"))]
    if sem_lock and dono:
        v.append(Verificacao("service.instances", "service", FAIL, "Instâncias",
                             f"{len(sem_lock) + 1} Ombros ao vivo: um com o lock e {len(sem_lock)} sem (tela.py antigo?)",
                             "pare o tela.py antigo (ou migre o LaunchAgent: python3 -m ombro service migrate)",
                             {"pids": [p["pid"] for p in sem_lock]}))
    elif sem_lock:
        v.append(Verificacao("service.instances", "service", WARN, "Instâncias",
                             f"um tela.py sem lock (versão antiga?) está rodando: PID {', '.join(str(p['pid']) for p in sem_lock)}",
                             "se for o LaunchAgent antigo: python3 -m ombro service migrate", {"pids": [p["pid"] for p in sem_lock]}))
    else:
        v.append(Verificacao("service.instances", "service", PASS, "Instâncias", "no máximo um Ombro ao vivo"))
    if amb.plataforma == "darwin":
        from .launchagent import Servico

        try:
            e = Servico().estado()
        except Exception as erro:  # noqa: BLE001
            v.append(Verificacao("service.launchagent", "service", WARN, "LaunchAgent", f"não consegui ler ({erro})"))
        else:
            if e["legacy"]["loaded"] or e["legacy"]["installed"]:
                v.append(Verificacao("service.launchagent", "service", WARN, "LaunchAgent",
                                     "o LaunchAgent antigo (com.ombro-screen, tela.py) está "
                                     + ("ligado" if e["legacy"]["loaded"] else "instalado"),
                                     "python3 -m ombro service migrate --dry-run (para ver), depois sem --dry-run", {"legacy": True}))
            elif e["installed"]:
                ok = e["ours"] and e["loaded"]
                v.append(Verificacao("service.launchagent", "service", PASS if ok else WARN, "LaunchAgent",
                                     "instalado e carregado" if ok else ("instalado, não carregado" if e["ours"] else "plist desconhecido"),
                                     None if ok else "python3 -m ombro service start", {"loaded": bool(e["loaded"])}))
            else:
                v.append(Verificacao("service.launchagent", "service", SKIP, "LaunchAgent", "não instalado (opcional)",
                                     "python3 -m ombro service install"))
    else:
        v.append(Verificacao("service.launchagent", "service", SKIP, "LaunchAgent", "só no macOS"))
    return v, status


def agentes(amb: Ambiente, daemon: Optional[dict]) -> List[Verificacao]:
    from .integracoes import ALVOS, analisar

    vivos = {}
    if daemon is not None:
        try:
            vivos = {p["id"]: p for p in amb.ipc("providers.list", None)["providers"]}
        except (DaemonIndisponivel, ErroIPC):
            vivos = {}
    v = []
    for pid, alvo in ALVOS.items():
        a = analisar(alvo)
        exe = next((shutil.which(e) for e in alvo.executaveis if shutil.which(e)), None)
        versao = None
        if exe:
            saida = amb.executar([exe, "--version"])
            versao = sanitizar(saida.splitlines()[0])[:60] if saida else None
        p = vivos.get(pid, {})
        detalhes = {"installed": a.instalado, "version": versao, "integration": a.estado,
                    "events": f"{len(a.presentes)}/{len(a.eventos)}", "enabled": p.get("enabled"),
                    "capabilities": p.get("capabilities"), "last_event_at": p.get("last_event_at"),
                    "last_collected_at": p.get("last_collected_at"), "collection": p.get("collection")}
        nome = alvo.nome
        if not a.instalado and a.estado == "not_configured":
            v.append(Verificacao(f"provider.{pid}", "providers", SKIP, nome, "não instalado", detalhes=detalhes))
            continue
        base = f"{versao or 'versão desconhecida'}; "
        if a.estado == "invalid":
            st, msg, acao = FAIL, base + (a.erro or "configuração inválida"), f"corrija {a.arquivo}"
        elif a.estado == "conflict":
            st, msg, acao = FAIL, base + "há entradas ambíguas com o ombro_hook.py", f"python3 -m ombro integrations check {pid}"
        elif a.estado == "partial":
            st, msg, acao = WARN, base + "integração incompleta ou desatualizada", f"python3 -m ombro integrations install {pid}"
        elif a.estado == "not_configured":
            if pid == "claude":
                st, msg, acao = PASS, base + "funciona pelos arquivos; hooks (aprovação e erro) não instalados", \
                    "opcional: python3 -m ombro integrations install claude"
            else:
                st, msg, acao = WARN, base + "integração não configurada (sem os hooks, o Ombro não vê as sessões)", \
                    f"python3 -m ombro integrations install {pid}"
        else:
            st, msg, acao = PASS, base + "integração configurada", None
        if p.get("collection") == "error":
            st, msg = FAIL if st == PASS else st, msg + "; a coleta está falhando no ombrod"
            acao = acao or "python3 -m ombro providers; rode o ombrod com --debug"
        elif p.get("last_event_at"):
            msg += f"; último evento {p['last_event_at']}"
        v.append(Verificacao(f"provider.{pid}", "providers", st, nome, msg, acao, detalhes))
        if p.get("enabled"):
            v.extend(_coletas(pid, nome, p))
    p = vivos.get("opencode", {})
    v.append(_opencode(p))
    if p.get("enabled"):
        v.extend(_coletas("opencode", "OpenCode", p))
    return v


def _opencode(p: dict) -> Verificacao:
    """O OpenCode é conferido pelo que o plugin conta: o doctor não roda o binário nem lê a configuração dele."""
    from .integracao_opencode import analisar

    a = analisar()
    versao, condicao, desconhecidas = p.get("version"), p.get("condition"), p.get("unknown_sessions") or 0
    detalhes = {"installed": a["instalado"], "version": versao, "integration": a["state"], "condition": condicao,
                "enabled": p.get("enabled"), "capabilities": p.get("capabilities"), "unknown_sessions": desconhecidas,
                "last_event_at": p.get("last_event_at"), "collection": p.get("collection")}
    base = f"{versao or 'versão desconhecida'}; "
    instalar = "python3 -m ombro integrations install opencode"
    perdidas = f"; {desconhecidas} sessão(ões) com estado desconhecido" if desconhecidas else ""
    if p.get("enabled") is False:
        st, msg, acao = SKIP, "desligado nas preferências", None
    elif a["state"] == "invalid":
        st, msg, acao = FAIL, str(a["erro"]), None
    elif condicao in ("incompatible", "conflict", "unverified"):
        st, msg = FAIL, base + str(p.get("condition_detail"))
        acao = {"incompatible": "atualize o OpenCode à mão", "conflict": instalar + " --new-origin",
                "unverified": "confira a versão e onde o OpenCode guarda o banco"}[condicao]
    elif condicao == "unregistered":
        st, msg, acao = WARN, base + str(p.get("condition_detail")), instalar
    elif a["state"] == "not_configured":
        if not a["instalado"]:
            st, msg, acao = SKIP, "não instalado", None
        else:
            st, msg, acao = WARN, "integração não configurada (sem o plugin, o Ombro não vê as sessões)", instalar
    elif not p:
        st, msg, acao = WARN, "configurado; sem o ombrod não dá para saber se o plugin conectou", "python3 ombrod.py"
    elif p.get("collection") == "error":
        st, msg, acao = FAIL, base + "a coleta está falhando no ombrod" + perdidas, "veja o log do ombrod"
    elif condicao == "never" or a["state"] == "partial":
        st, msg, acao = (WARN, "configurado, mas o plugin nunca conectou",
                         "python3 -m ombro integrations check opencode")
    elif condicao == "offline":
        st, msg, acao = WARN, "desconectado (o OpenCode está fechado ou sem o plugin)" + perdidas, None
    elif condicao == "partial":
        st, msg, acao = WARN, base + str(p.get("condition_detail")) + perdidas, None
    elif p.get("disconnected_sessions"):
        st, msg, acao = (WARN, base + f"conectado; {p['disconnected_sessions']} sessão(ões) de uma origem desconectada "
                         "(último estado conhecido)", None)
    elif desconhecidas:
        st, msg, acao = WARN, base + "conectado, recuperando" + perdidas + " até o próximo evento", None
    else:
        st, msg, acao = PASS, base + "saudável: o plugin está conectado", None
    return Verificacao("provider.opencode", "providers", st, "OpenCode", msg, acao, detalhes)


def _idade(segundos: Optional[int]) -> str:
    if segundos is None:
        return "?"
    return f"{segundos // 60} min" if segundos < 7200 else f"{segundos // 3600} h"


def _coletas(pid: str, nome: str, p: dict) -> List[Verificacao]:
    """Três coisas diferentes, cada uma com o seu resultado: as sessões, os tokens locais e os limites da assinatura.
    Ter sessões e tokens em dia não diz nada sobre os limites: eles vêm de outra fonte."""
    caps, velhas = set(p.get("capabilities") or []), set(p.get("stale_parts") or [])
    v = []
    if "sessions" in caps:
        ok = "sessions" not in velhas and p.get("collection") != "error"
        v.append(Verificacao(f"sessions.{pid}", "providers", PASS if ok else WARN, f"{nome}: sessões",
                             f"{p.get('sessions', 0)} sessão(ões) agora" if ok else "a última leitura falhou; mostrando a anterior",
                             detalhes={"sessions": p.get("sessions"), "last_collected_at": p.get("last_collected_at")}))
    if "token_usage" in caps:
        uso = p.get("usage") or {}
        ok = "usage" not in velhas and bool(uso)
        v.append(Verificacao(f"tokens.{pid}", "providers", PASS if ok else WARN, f"{nome}: tokens locais",
                             (f"{uso.get('total_tokens') or 0} tokens hoje, do histórico desta máquina (não são os limites da assinatura)"
                              if ok else "sem leitura dos tokens de hoje"),
                             detalhes={"scope": uso.get("scope"), "total_tokens": uso.get("total_tokens")}))
    if "quota_windows" in caps:
        q = p.get("quotas")
        if not q:
            v.append(Verificacao(f"quotas.{pid}", "providers", WARN, f"{nome}: limites da assinatura",
                                 "dados indisponíveis: nenhuma fonte de limites configurada",
                                 "ligue o statusline (README, seção 4) ou rode /usage no Claude Code",
                                 {"source": None, "state": "unavailable", "auto_update": False}))
            return v
        estado, idade = q.get("state"), _idade(q.get("age_seconds"))
        cobre = "a assinatura inteira" if q.get("scope") == "account" else "escopo não informado pela fonte"
        como = ("atualização automática" if q.get("auto_update") else
                "só há número novo quando o Claude Code no terminal conversa ou roda /usage; o app Claude não atualiza")
        detalhes = {"source": q.get("source"), "state": estado, "observed_at": q.get("observed_at"),
                    "last_read_at": p.get("last_collected_at"), "age_seconds": q.get("age_seconds"),
                    "scope": q.get("scope"), "auto_update": bool(q.get("auto_update")),
                    "next_attempt": None,  # não há consulta agendada: o Ombro não consulta nada pela rede
                    "windows": [{"id": w.get("id"), "expired": bool(w.get("expired"))} for w in q.get("windows", [])]}
        if estado == "fresh":
            st, msg, acao = PASS, f"fonte {q.get('source')}, observado há {idade} ({cobre}); {como}", None
        elif estado == "awaiting_update":
            st, msg = WARN, f"a janela reiniciou depois da última observação (há {idade}) e ainda não há número novo; {como}"
            acao = "envie uma mensagem ou rode /usage no Claude Code no terminal"
        else:
            st, msg = WARN, f"último valor conhecido, de há {idade} (fonte {q.get('source')}, {cobre}); {como}"
            acao = "envie uma mensagem ou rode /usage no Claude Code no terminal"
        v.append(Verificacao(f"quotas.{pid}", "providers", st, f"{nome}: limites da assinatura", msg, acao, detalhes))
    return v


def tela(amb: Ambiente, daemon: Optional[dict]) -> List[Verificacao]:
    v = []
    if amb.plataforma == "darwin":
        try:
            import usb_direto

            falta = usb_direto.problema()
        except Exception as e:  # noqa: BLE001
            falta = str(e)
        v.append(Verificacao("display.driver", "display", WARN if falta else PASS, "Envio direto pelo USB",
                             f"indisponível: {falta}" if falta else "pyusb e libusb prontos (revisão A)",
                             "brew install libusb; python3 -m pip install pyusb" if falta else None))
    else:
        v.append(Verificacao("display.driver", "display", SKIP, "Envio direto pelo USB", "só no macOS"))
    if daemon is not None:
        try:
            d = amb.ipc("display.status", None)
        except (DaemonIndisponivel, ErroIPC):
            d = None
        if d is not None:
            conexao, tipo = d.get("connection"), d.get("kind")
            st = {"connected": PASS, "disconnected": WARN}.get(conexao, FAIL)
            if tipo == "simulator" and conexao == "connected":
                msg = "o ombrod está usando o simulador (sem --usb)"
            elif tipo == "usb":
                msg = f"{conexao}, revisão {d.get('revision')}, {d.get('transport')}" + (
                    f"; último erro: {d['last_error']}" if d.get("last_error") else "")
            else:
                msg = f"{tipo}: {conexao}" + (f" ({d['last_error']})" if d.get("last_error") else "")
            acao = None if st == PASS else ("confira o cabo; o ombrod tenta reconectar a cada 3 s" if conexao == "disconnected"
                                            else "python3 -m ombro display; veja o log do ombrod")
            v.append(Verificacao("display.connection", "display", st, "Tela", msg, acao,
                                 {k: d.get(k) for k in ("kind", "connection", "revision", "transport", "brightness",
                                                        "orientation", "layout", "last_frame_at", "last_error")}))
            return v
    # sem ombrod: procura o dispositivo sem abri-lo (outro processo pode estar com ele)
    achou = None
    if amb.plataforma == "darwin":
        try:
            import usb_direto

            if usb_direto.problema() is None:
                achou = usb_direto.LcdRevAUsb.auto_detect_com_port()
        except Exception:  # noqa: BLE001
            achou = None
    if achou is None:
        try:
            from serial.tools import list_ports

            achou = any("usbmodem" in p.device or "USB35INCH" in (p.serial_number or "") for p in list_ports.comports())
        except Exception:  # noqa: BLE001
            achou = None
    if achou:
        v.append(Verificacao("display.connection", "display", PASS, "Tela", "detectada no USB (o envio de imagens não foi testado)"))
    elif achou is False:
        v.append(Verificacao("display.connection", "display", WARN, "Tela", "nenhuma tela encontrada no USB",
                             "confira o cabo e o hub (ls /dev/cu.*)"))
    else:
        v.append(Verificacao("display.connection", "display", SKIP, "Tela", "não deu para procurar (falta pyusb/pyserial)"))
    return v


def diagnosticar(amb: Optional[Ambiente] = None) -> dict:
    amb = amb or Ambiente()
    checks = ambiente(amb) + configuracao() + pastas()
    serv, daemon = servico(amb)
    checks += serv + armazenamento(daemon) + agentes(amb, daemon) + tela(amb, daemon)
    resumo = {s: sum(1 for c in checks if c.status == s) for s in (PASS, WARN, FAIL, SKIP)}
    geral = FAIL if resumo[FAIL] else (WARN if resumo[WARN] else PASS)
    return {"schema": ESQUEMA, "schema_version": VERSAO_ESQUEMA, "ombro_version": VERSAO, "ipc_protocol": PROTOCOLO,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": {"status": geral, **{k.lower(): v for k, v in resumo.items()}},
            "checks": [_limpo(c.como_dict()) for c in checks]}


# ---------- saída ----------
def texto(r: dict) -> str:
    linhas = ["Ombro Doctor", ""]
    for c in r["checks"]:
        linhas.append(f"[{c['status']}] {c['title']}: {c['message']}")
        if c["action"] and c["status"] in (WARN, FAIL):
            linhas.append(f"       -> {c['action']}")
    s = r["summary"]
    linhas += ["", f"{s['pass']} PASS, {s['warn']} WARN, {s['fail']} FAIL, {s['skip']} SKIP"]
    return "\n".join(linhas)


def relatorio(r: dict) -> str:
    """Para colar numa issue: o resultado e o ambiente, já sem dados pessoais."""
    cabeca = [
        "# Relatório do Ombro (gerado por `python3 -m ombro doctor --report`)",
        f"Gerado em: {r['generated_at']}",
        f"Ombro {r['ombro_version']}, protocolo IPC v{r['ipc_protocol']}, esquema {r['schema']} v{r['schema_version']}",
        f"Python {platform.python_version()} ({platform.python_implementation()}), {sys.platform} {platform.machine()}"
        + (f", macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else ""),
        "",
        "Sem: prompts, projetos, ids de sessão, conteúdo de configurações, credenciais, usuário ou máquina.",
        "",
    ]
    corpo = texto(r).splitlines()[2:]
    detalhes = ["", "## Detalhes", json.dumps({c["id"]: c["details"] for c in r["checks"] if c["details"]},
                                              ensure_ascii=False, indent=2, default=str)]
    return sanitizar("\n".join(cabeca + corpo + detalhes)) + "\n"


def comando(args) -> int:
    r = diagnosticar()
    saida = json.dumps(r, ensure_ascii=False, indent=2) if args.json else (relatorio(r) if args.report else texto(r))
    if args.output:
        destino = Path(args.output)
        fd = os.open(str(destino), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(saida if saida.endswith("\n") else saida + "\n")
        print(f"gravado em {curto(destino)}")
    else:
        print(saida)
    return 1 if r["summary"]["status"] == FAIL else 0
