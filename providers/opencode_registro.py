"""O registro das origens do OpenCode e o que se lembra de cada sessão entre reinícios do Ombro.

  opencode/registry.json  as origens que VOCÊ registrou, a que cada uma ficou ligada e as revogadas
  opencode/state.json     cursores, pedidos já respondidos, remoções e eventos já vistos (contra reprises)
  opencode/registry.lock  uma mudança por vez entre a CLI (registrar, revogar) e o ombrod (coletar)

Uma origem só existe aqui depois de um registro explícito (`ombro integrations install opencode`). Estados:

  pending    registrada, nunca conectou
  active     ligada a uma instância local: a âncora (o arquivo do banco que o processo tem aberto) e a marca dele
  ambiguous  duas instâncias se apresentaram antes de haver vínculo: não serve mais
  revoked    desinstalada: nunca mais é aceita, mesmo com o plugin ainda ligado

Os três arquivos são do Ombro, `0600`, trocados de uma vez. Um arquivo que existe e não se entende não é
sobrescrito: nada é aplicado até alguém resolver.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

ORIGEM = re.compile(r"[a-z0-9][a-z0-9-]{7,63}")
ANCORA = re.compile(r"[0-9a-f]{16,64}")
ESTADOS = ("pending", "active", "ambiguous", "revoked")
VERSAO_REGISTRO = 2
VERSAO_ESTADO = 1


class Ilegivel(Exception):
    """O arquivo existe e não dá para ler ou entender: falha fechada."""


@contextmanager
def trava(pasta: Path):
    """Exclusiva entre processos. A pasta precisa existir."""
    fd = os.open(str(pasta / "registry.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # fechar solta a trava


def _ler(arq: Path) -> Optional[dict]:
    try:
        d = json.loads(arq.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise Ilegivel(arq.name) from e
    if not isinstance(d, dict):
        raise Ilegivel(arq.name)
    return d


def gravar(arq: Path, dados: dict) -> None:
    """Troca o arquivo de uma vez (temporário, fsync, rename). Levanta OSError."""
    arq.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = arq.parent / f".{arq.name}.{os.getpid()}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dados, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, arq)


def registro_vazio() -> dict:
    return {"v": VERSAO_REGISTRO, "current": None, "origins": {}}


def ler_registro(pasta: Path) -> dict:
    """{v, current, origins: {origem: {state, anchor?, mark?}}}. Sem o arquivo: vazio. Levanta Ilegivel."""
    d = _ler(pasta / "registry.json")
    if d is None:
        return registro_vazio()
    origens = d.get("origins")
    if d.get("v") != VERSAO_REGISTRO or not isinstance(origens, dict):
        raise Ilegivel("registry.json")
    for origem, o in origens.items():
        if not ORIGEM.fullmatch(origem) or not isinstance(o, dict) or o.get("state") not in ESTADOS:
            raise Ilegivel("registry.json")
        if o["state"] == "active" and not (isinstance(o.get("anchor"), str) and ANCORA.fullmatch(o["anchor"])
                                           and isinstance(o.get("mark"), str) and o["mark"]):
            raise Ilegivel("registry.json")
    return d


def gravar_registro(pasta: Path, registro: dict) -> None:
    gravar(pasta / "registry.json", registro)


def ler_estado(pasta: Path) -> dict:
    d = _ler(pasta / "state.json")
    if d is None:
        return {"v": VERSAO_ESTADO, "sessions": {}, "seen": []}
    if d.get("v") != VERSAO_ESTADO or not isinstance(d.get("sessions"), dict) or not isinstance(d.get("seen"), list):
        raise Ilegivel("state.json")
    return d
