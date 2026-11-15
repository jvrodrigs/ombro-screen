"""Outros processos do Ombro deste usuário: para não disputar a tela com uma versão que não usa o lock.

O LaunchAgent antigo (`com.ombro-screen`) roda `tela.py --usb` com o código da pasta. Um `tela.py` de
antes da Fase 4 não segura o lock de instância, então o ombrod precisa vê-lo pelo nome do processo.
Só lê a linha de comando dos processos do próprio usuário (psutil); não envia sinal a ninguém.
"""
from __future__ import annotations

import os
from typing import List, Optional

try:
    import psutil
except ImportError:  # o doctor avisa; aqui, sem psutil, não dá para procurar
    psutil = None


def _papel(cmdline: List[str]) -> Optional[str]:
    """"tela.py", "ombrod" ou None, olhando os argumentos do processo."""
    if len(cmdline) >= 3 and "-m" in cmdline[1:-1] and cmdline[cmdline.index("-m", 1) + 1] == "ombro.daemon":
        return "ombrod"
    # o script é o primeiro argumento depois do interpretador que não é uma opção (python3 -u tela.py)
    script = next((a for a in cmdline[1:] if not a.startswith("-")), None)
    nome = os.path.basename(script) if script else ""
    return {"ombrod.py": "ombrod", "tela.py": "tela.py"}.get(nome)


def tela_ao_vivo(cmdline: List[str]) -> bool:
    """Um tela.py que lê eventos ou fala com a tela (não é --png, nem --demo sem --usb)?"""
    if "--png" in cmdline:
        return False
    return "--demo" not in cmdline or "--usb" in cmdline


def processos_do_ombro(ignorar: Optional[set] = None) -> List[dict]:
    """[{pid, kind, live}] dos processos do Ombro deste usuário (fora os de `ignorar` e este)."""
    if psutil is None:
        return []
    ignorar = set(ignorar or ()) | {os.getpid()}
    eu = os.getuid()
    achados = []
    for p in psutil.process_iter(["pid", "uids", "cmdline"]):
        try:
            info = p.info
            if info["pid"] in ignorar or not info.get("cmdline") or (info.get("uids") and info["uids"].real != eu):
                continue
            papel = _papel(info["cmdline"])
            if papel is None:
                continue
            achados.append({"pid": info["pid"], "kind": papel,
                            "live": papel == "ombrod" or tela_ao_vivo(info["cmdline"])})
        except (psutil.Error, OSError, KeyError, TypeError):
            continue
    return achados


def conflitos(dono_do_lock: Optional[int] = None) -> List[dict]:
    """tela.py ao vivo que não são o dono do lock: um tela.py antigo (sem lock) disputando a tela.
    (Um ombrod sem o lock está saindo: ele não chega a abrir nada.)"""
    return [p for p in processos_do_ombro({dono_do_lock} if dono_do_lock else None) if p["live"] and p["kind"] == "tela.py"]
