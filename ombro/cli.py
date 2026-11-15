"""`python3 -m ombro`: a CLI de gerenciamento do Ombro.

  python3 -m ombro status | providers | sessions | display | health | config     (pelo ombrod, via IPC)
  python3 -m ombro display brightness 40 [--persist]      python3 -m ombro display layout multiagente
  python3 -m ombro display preview quadro.png             o último quadro da tela, sem desenhar de novo
  python3 -m ombro config set brilho 40                   uma preferência (allowlist em ombro/preferencias.py)
  python3 -m ombro integrations list | check | install | uninstall | rollback [claude|codex|cursor] [--dry-run]
  python3 -m ombro service install | start | stop | restart | status | uninstall | migrate | rollback
  python3 -m ombro doctor [--json] [--report] [--output arquivo]

Também roda de qualquer pasta: python3 /caminho/ombro-screen/ombro status.

Tudo o que é estado do Ombro vem do ombrod pela IPC: a CLI nunca abre o banco, a tela ou os providers
para "ver por conta própria". Sem o ombrod, avisa e sai com código 3 (não inicia nada sozinha). Os
comandos de integrações e serviço mexem em arquivos fora do Ombro e por isso só rodam aqui, quando você
pede, nunca pela IPC. `--json` dá a saída estável para scripts e para o futuro Ombro.app.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from . import VERSAO
from .ipc import PROTOCOLO, ClienteIPC, DaemonIndisponivel, ErroIPC

SEM_DAEMON = 3  # código de saída quando o ombrod não está rodando
NAO_RODANDO = "O ombrod não está rodando."
DICA = "Inicie com: python3 ombrod.py   (ou instale o serviço: python3 -m ombro service install)"
NOMES = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor"}


class Falha(Exception):
    def __init__(self, mensagem: str, codigo: int = 1):
        super().__init__(mensagem)
        self.codigo = codigo


# ---------- formatação ----------
def ha_quanto(iso: Optional[str], agora: Optional[datetime] = None) -> str:
    if not iso:
        return "-"
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return "-"
    s = max(0, int(((agora or datetime.now()) - t).total_seconds()))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def duracao(segundos: Optional[float]) -> str:
    if segundos is None:
        return "-"
    s = int(segundos)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d}d " if d else "") + (f"{h}h " if h or d else "") + f"{m}m {s}s"


def tabela(linhas: List[List[str]], cabecalho: List[str]) -> str:
    todas = [cabecalho] + linhas
    larguras = [max(len(str(l[i])) for l in todas) for i in range(len(cabecalho))]
    return "\n".join("  ".join(str(c).ljust(larguras[i]) for i, c in enumerate(l)).rstrip() for l in todas)


def pares(titulo: str, itens) -> str:
    largura = max(len(k) for k, _ in itens) + 2
    return "\n".join([titulo, ""] + [f"{(k + ':').ljust(largura)}{v}" for k, v in itens])


def _sim(v) -> str:
    return "-" if v is None else ("sim" if v else "não")


# ---------- IPC ----------
def _chamar(args, metodo: str, params: Optional[dict] = None):
    try:
        with ClienteIPC(timeout=args.timeout) as c:
            return c.chamar(metodo, params)
    except DaemonIndisponivel as e:
        raise Falha(f"{NAO_RODANDO}\n{DICA}" if "não está rodando" in str(e) else f"{NAO_RODANDO} ({e})", SEM_DAEMON) from None
    except ErroIPC as e:
        raise Falha(f"o ombrod recusou: {e.mensagem} [{e.codigo}]") from None


def _saida(args, dados, texto) -> None:
    if args.json:
        print(json.dumps(dados, ensure_ascii=False, indent=2))
    else:
        print(texto() if callable(texto) else texto)


# ---------- comandos do ombrod ----------
def cmd_status(args) -> int:
    st = _chamar(args, "status")

    def texto():
        prov = st["providers"]
        d = st["display"]
        return pares("Ombro Hub", [
            ("Daemon", st["daemon"] + (" (dados de demonstração)" if st.get("demo") else "")),
            ("Saúde", st["health"]),
            ("Tempo rodando", duracao(st.get("uptime_s"))),
            ("Display", f"{d.get('connection')} ({d.get('kind')})"),
            ("Layout", d.get("layout")),
            ("Providers", f"{prov['configured']} configurados, {prov['installed']} instalados, {prov['operational']} operacionais"),
            ("Sessões", st["sessions"]),
            ("Aguardando", st["waiting"]),
            ("Com erro", st.get("errors", 0)),
            ("Armazenamento", st["storage"]),
            ("Última coleta", ha_quanto(st.get("collected_at")) + (" atrás" if st.get("collected_at") else "")),
            ("IPC", f"v{st['protocol']} (ombrod {st['version']}, PID {st['pid']})"),
        ] + ([("Conflito", "outro Ombro sem lock: PID " + ", ".join(map(str, st["conflicts"])))] if st.get("conflicts") else []))

    _saida(args, st, texto)
    return 0


def cmd_providers(args) -> int:
    r = _chamar(args, "providers.list")
    saude = _chamar(args, "health")["components"]

    def texto():
        linhas = []
        for p in r["providers"]:
            comp = saude.get(f"provider.{p['id']}", {})
            if not p.get("enabled"):
                linhas.append([NOMES.get(p["id"], p["id"]), "não", "-", "-", "-", "desligado", "-"])
                continue
            integ = {"ok": "sim", "not_configured": "não", "not_installed": "-"}.get(p.get("integration"), "-")
            dados = "sim" if p.get("collection") in ("ok", "partial") and p.get("installed") else "não"
            ultima = p.get("last_event_at") or p.get("last_collected_at")
            linhas.append([NOMES.get(p["id"], p["id"]), "sim", _sim(p.get("installed")), integ, dados,
                           f"{comp.get('status', '-')} ({comp.get('state', '-')})", ha_quanto(ultima)])
        if not linhas:
            return "Nenhum provider coletado ainda (a primeira coleta leva até 5 s)."
        return tabela(linhas, ["PROVIDER", "LIGADO", "INSTALADO", "INTEGRAÇÃO", "DADOS", "ESTADO", "ATUALIZADO"])

    _saida(args, {**r, "health": {k: v for k, v in saude.items() if k.startswith("provider.")}}, texto)
    return 0


def cmd_sessions(args) -> int:
    params = {"provider": args.provider} if args.provider else {}
    r = _chamar(args, "sessions.list", params)
    if not args.full:  # por padrão, nada de id inteiro (só o começo, para distinguir)
        for s in r["sessions"]:
            s["session_id"] = s["session_id"][:8]

    def texto():
        if not r["sessions"]:
            return "Nenhuma sessão conhecida agora."
        linhas = [[NOMES.get(s["provider"], s["provider"]), s.get("project") or "-", s["status"],
                   ha_quanto(s.get("status_since") or s.get("updated_at"))] + ([s["session_id"]] if args.full else [])
                  for s in r["sessions"]]
        return tabela(linhas, ["PROVIDER", "PROJETO", "STATUS", "DESDE"] + (["SESSÃO"] if args.full else []))

    _saida(args, r, texto)
    return 0


def cmd_display(args) -> int:
    acao = args.acao
    if acao == "brightness":
        r = _chamar(args, "display.set_brightness", {"value": args.valor, "persist": args.persist})
        quando = "agora" if r["applied"] else "no próximo início"
        _saida(args, r, f"brilho: {args.valor} % ({quando}" + (", gravado no config.json)" if args.persist else ", até reiniciar)"))
        return 0
    if acao == "layout":
        r = _chamar(args, "display.set_layout", {"layout": args.valor, "persist": args.persist})
        if r["restart_required"]:
            _saida(args, r, f"layout {args.valor}: {'gravado; ' if args.persist else ''}vale depois de reiniciar o ombrod "
                            "(o clássico muda a orientação da tela)")
        else:
            _saida(args, r, f"layout: {args.valor}" + (" (gravado no config.json)" if args.persist else " (até reiniciar)"))
        return 0
    if acao == "preview":
        r = _chamar(args, "display.preview", {"format": "png"})
        if not r.get("available"):
            raise Falha("ainda não há quadro para mostrar")
        destino = Path(args.valor)
        destino.write_bytes(base64.b64decode(r["data_base64"]))
        info = {k: v for k, v in r.items() if k != "data_base64"}
        info["file"] = str(destino)
        _saida(args, info, f"quadro de {r['captured_at']} salvo em {destino} ({r['width']}x{r['height']})")
        return 0
    d = _chamar(args, "display.status")

    def texto():
        itens = [("Tipo", d.get("kind")), ("Conexão", d.get("connection"))]
        if d.get("kind") == "usb":
            itens += [("Revisão", d.get("revision")), ("Transporte", d.get("transport")), ("Porta", d.get("port")),
                      ("Brilho", f"{d['brightness']} %" if d.get("brightness") is not None else "-"),
                      ("Orientação", d.get("orientation")), ("Conectada desde", d.get("connected_since") or "-")]
        elif d.get("kind") == "simulator":
            itens += [("Endereço", d.get("url") or "-"), ("Orientação", d.get("orientation") or "-")]
        itens += [("Layout", d.get("layout")), ("Quadros", d.get("frames")),
                  ("Último quadro", ha_quanto(d.get("last_frame_at")) + (" atrás" if d.get("last_frame_at") else ""))]
        if d.get("last_error"):
            itens.append(("Último erro", d["last_error"]))
        return pares("Tela", itens)

    _saida(args, d, texto)
    return 0


def cmd_health(args) -> int:
    h = _chamar(args, "health")

    def texto():
        linhas = [[nome, c["status"], c.get("state", "")] for nome, c in h["components"].items()]
        return f"Saúde: {h['status']}\n\n" + tabela(linhas, ["COMPONENTE", "SAÚDE", "ESTADO"])

    _saida(args, h, texto)
    return 0 if h["status"] != "unhealthy" else 1


def _valor_cli(texto: str):
    """`40` -> 40, `true` -> True, `null` -> None; o resto fica texto (o ombrod valida)."""
    try:
        return json.loads(texto)
    except ValueError:
        return texto


def cmd_config(args) -> int:
    if args.acao == "set":
        r = _chamar(args, "config.update", {"changes": {args.chave: _valor_cli(args.valor)}, "persist": not args.no_persist})
        partes = []
        if r["applied"]:
            partes.append("aplicada agora")
        if r["restart_required"]:
            partes.append("vale depois de reiniciar")
        if r["persisted"]:
            partes.append(f"gravada em {r['file']}")
        _saida(args, r, f"{args.chave}: " + ", ".join(partes))
        return 0
    c = _chamar(args, "config.get")

    def texto():
        linhas = [[k, json.dumps(v, ensure_ascii=False), "sim" if k in c["editable"] else ""] for k, v in c["values"].items()]
        arq = c["file"] + ("" if c["file_exists"] else " (não existe; os padrões valem)")
        return f"Arquivo: {arq}\n\n" + tabela(linhas, ["CHAVE", "VALOR", "EDITÁVEL"])

    _saida(args, c, texto)
    return 0


# ---------- comandos locais (mexem em arquivos fora do Ombro: só aqui, só quando pedido) ----------
def cmd_integrations(args) -> int:
    from . import integracoes

    return integracoes.comando(args)


def cmd_service(args) -> int:
    from . import launchagent

    return launchagent.comando(args)


def cmd_doctor(args) -> int:
    from . import doctor

    return doctor.comando(args)


# ---------- argumentos ----------
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ombro", description="Gerencia o Ombro Hub (o ombrod) e as integrações com os agentes.")
    p.add_argument("--version", action="version", version=f"ombro {VERSAO} (protocolo IPC {PROTOCOLO})")
    p.add_argument("--timeout", type=float, default=3.0, help="segundos esperando o ombrod (padrão 3)")
    sub = p.add_subparsers(dest="comando", metavar="COMANDO")
    sub.required = True

    def novo(nome, fn, ajuda, json_=True):
        s = sub.add_parser(nome, help=ajuda, description=ajuda)
        if json_:
            s.add_argument("--json", action="store_true", help="saída em JSON (estável, para scripts)")
        s.set_defaults(fn=fn)
        return s

    novo("status", cmd_status, "estado do ombrod: tela, providers, sessões, armazenamento")
    novo("providers", cmd_providers, "os agentes monitorados e o estado de cada um")
    s = novo("sessions", cmd_sessions, "as sessões conhecidas agora")
    s.add_argument("--provider", choices=tuple(NOMES), help="só deste agente")
    s.add_argument("--full", action="store_true", help="mostrar o id inteiro das sessões")
    s = novo("display", cmd_display, "a tela: estado, brilho, layout e o último quadro")
    s.add_argument("acao", nargs="?", choices=("status", "brightness", "layout", "preview"), default="status")
    s.add_argument("valor", nargs="?", help="brightness: 0-100; layout: o nome; preview: o arquivo PNG")
    s.add_argument("--persist", action="store_true", help="gravar no config.json (sem isso, vale até reiniciar)")
    novo("health", cmd_health, "a saúde de cada componente")
    s = novo("config", cmd_config, "a configuração em uso, e mudar preferências permitidas")
    s.add_argument("acao", nargs="?", choices=("get", "set"), default="get")
    s.add_argument("chave", nargs="?")
    s.add_argument("valor", nargs="?")
    s.add_argument("--no-persist", action="store_true", help="só agora, sem gravar no config.json")

    s = novo("integrations", cmd_integrations, "instalar, conferir e remover os hooks do Ombro nos agentes")
    s.add_argument("acao", choices=("list", "check", "install", "uninstall", "rollback"))
    s.add_argument("provider", nargs="?", choices=tuple(NOMES))
    s.add_argument("--dry-run", action="store_true", help="mostrar o que mudaria, sem gravar nada")
    s.add_argument("--python", help="o python que os hooks vão usar (padrão: este)")

    s = novo("service", cmd_service, "o LaunchAgent do ombrod (macOS)")
    s.add_argument("acao", choices=("install", "uninstall", "start", "stop", "restart", "status", "migrate", "rollback"))
    s.add_argument("--dry-run", action="store_true", help="mostrar o plist e os comandos, sem mudar nada")
    s.add_argument("--usb", action=argparse.BooleanOptionalAction, default=None,
                   help="passar --usb (ou --no-usb) ao ombrod; sem isso, vale o config.json")
    s.add_argument("--python", help="o python que o serviço vai usar (padrão: este)")

    s = novo("doctor", cmd_doctor, "diagnóstico da instalação (não muda nada)")
    s.add_argument("--report", action="store_true", help="relatório de suporte, sem dados pessoais")
    s.add_argument("--output", type=Path, help="gravar o relatório neste arquivo (em vez de mostrar)")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.comando == "display":
        if args.acao in ("brightness", "layout", "preview") and args.valor is None:
            parser().error(f"display {args.acao} precisa de um valor")
        if args.acao == "brightness":
            try:
                args.valor = int(args.valor)
            except ValueError:
                parser().error("display brightness precisa de um número de 0 a 100")
    if args.comando == "config" and args.acao == "set" and (args.chave is None or args.valor is None):
        parser().error("config set precisa de uma chave e um valor, como: config set brilho 40")
    try:
        return args.fn(args)
    except Falha as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e).splitlines()[0], "exit_code": e.codigo}, ensure_ascii=False))
        else:
            print(str(e), file=sys.stderr)
        return e.codigo
    except KeyboardInterrupt:
        return 130
