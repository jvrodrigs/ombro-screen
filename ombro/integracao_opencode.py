"""Ligar o plugin do Ombro no OpenCode. É você quem edita a configuração do OpenCode: o Ombro não a lê nem grava.

  python3 -m ombro integrations install opencode                 registra uma origem e mostra o trecho para colar
  python3 -m ombro integrations install opencode --new-origin    outra origem (para outra instalação ou uma cópia)
  python3 -m ombro integrations check opencode                   o trecho de novo, e se o plugin já conectou
  python3 -m ombro integrations uninstall opencode               revoga as origens (tire o trecho do OpenCode à mão)

O registro é do Ombro (providers/opencode_registro.py). Instalar de novo mantém a origem atual. Uma origem revogada
nunca volta a ser aceita, mesmo que o plugin continue ligado: registrar de novo cria outra.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Optional

from config import pasta_ombro
from logging_config import curto, logger
from providers import opencode_registro as registro
from providers.opencode import ferramenta_instalada

from .caminhos import garantir_pasta_privada

log = logger("integrations")

PLUGIN = Path(__file__).resolve().parent.parent / "integrations" / "opencode"
NOTA = ("Cole o trecho na configuração do OpenCode (versão 2.4.2 ou mais nova), junto dos plugins que você já tem, "
        "e reinicie o OpenCode. O Ombro não edita esse arquivo.")
NOTA_REMOCAO = ("A origem foi revogada: o Ombro não aceita mais nada dela. Tire também o trecho do Ombro da "
                "configuração do OpenCode e reinicie o OpenCode.")


def pasta() -> Path:
    return pasta_ombro() / "opencode"


def arquivo() -> Path:
    return pasta() / "registry.json"


def origem_salva() -> Optional[str]:
    """A origem atual (a do trecho), se houver uma registrada e não revogada."""
    try:
        reg = registro.ler_registro(pasta())
    except registro.Ilegivel:
        return None
    o = reg.get("current")
    return o if o in reg["origins"] and reg["origins"][o]["state"] != "revoked" else None


def trecho(origem: str) -> str:
    opcoes = {"origin": origem}
    if pasta_ombro() != Path("~/.ombro-screen").expanduser():
        opcoes["dir"] = str(pasta_ombro())  # o OpenCode não herda o OMBRO_SCREEN_DIR deste terminal
    return json.dumps({"plugins": [{"package": str(PLUGIN), "options": opcoes}]}, indent=2, ensure_ascii=False) + "\n"


def analisar() -> dict:
    """O mesmo formato das integrações por hooks. "configured": a origem atual já conectou; "partial": registrada e o
    plugin nunca conectou (ou a origem ficou ambígua)."""
    erro, estado, origem = None, "not_configured", None
    try:
        reg = registro.ler_registro(pasta())
        origem = origem_salva()
        if origem:
            estado = "configured" if reg["origins"][origem]["state"] == "active" else "partial"
    except registro.Ilegivel:
        erro, estado = f"{curto(arquivo())} não se lê; confira ou mova o arquivo", "invalid"
    presentes = ["plugin"] if estado == "configured" else []
    return {"provider": "opencode", "file": curto(arquivo()), "existe": arquivo().exists(),
            "instalado": ferramenta_instalada(), "eventos": ["plugin"], "presentes": presentes,
            "desatualizados": [], "ambiguos": [], "sobrando": [], "hook_existe": (PLUGIN / "server.js").is_file(),
            "erro": erro, "state": estado, "trecho": trecho(origem) if origem else None}


def _abrir():
    """O registro, para mudar (com a pasta criada). Erro de integração se não se lê."""
    from .integracoes import ErroIntegracao

    try:
        garantir_pasta_privada(pasta_ombro())
        garantir_pasta_privada(pasta())
    except OSError as e:
        raise ErroIntegracao(f"não consegui criar {curto(pasta())} ({type(e).__name__})") from None


def _mudar(fn):
    """Uma mudança no registro, sob a trava que o ombrod também usa: nunca no meio de uma coleta."""
    from .integracoes import ErroIntegracao

    _abrir()
    try:
        with registro.trava(pasta()):
            reg = registro.ler_registro(pasta())
            resultado = fn(reg)
            registro.gravar_registro(pasta(), reg)
    except registro.Ilegivel:
        raise ErroIntegracao(f"{curto(arquivo())} existe mas não se lê; confira ou mova o arquivo") from None
    except OSError as e:
        raise ErroIntegracao(f"não consegui gravar {curto(arquivo())} ({type(e).__name__})") from None
    return resultado


def instalar(dry_run: bool = False, nova: bool = False):
    from .integracoes import ErroIntegracao, Resultado, _TravaDeIntegracoes

    if not (PLUGIN / "server.js").is_file():
        raise ErroIntegracao(f"{curto(PLUGIN)} não existe; rode a partir da pasta do Ombro")
    with _TravaDeIntegracoes():
        try:
            registro.ler_registro(pasta())
        except registro.Ilegivel:
            raise ErroIntegracao(f"{curto(arquivo())} existe mas não se lê; confira ou mova o arquivo") from None
        origem = origem_salva()
        mudou = nova or origem is None
        r = Resultado("opencode", "install", curto(arquivo()), mudou, dry_run)
        if mudou and not dry_run:
            origem = "oc-" + secrets.token_hex(12)

            def registrar(reg: dict) -> None:
                reg["origins"][origem] = {"state": "pending", "registered_at": int(time.time())}
                reg["current"] = origem

            _mudar(registrar)
            log.info("integração opencode: origem registrada")
        r.diff = trecho("oc-gerada-ao-confirmar" if mudou and dry_run else origem)
    if not dry_run:
        r.analise, r.nota = analisar(), NOTA
    return r


def remover(dry_run: bool = False):
    from .integracoes import ErroIntegracao, Resultado, _TravaDeIntegracoes

    with _TravaDeIntegracoes():
        try:
            reg = registro.ler_registro(pasta())
        except registro.Ilegivel:
            raise ErroIntegracao(f"{curto(arquivo())} existe mas não se lê; confira ou mova o arquivo") from None
        ativas = [o for o, d in reg["origins"].items() if d["state"] != "revoked"]
        r = Resultado("opencode", "uninstall", curto(arquivo()), bool(ativas), dry_run)
        if ativas and not dry_run:
            def revogar(reg: dict) -> None:
                for o, d in reg["origins"].items():
                    if d["state"] != "revoked":
                        reg["origins"][o] = {"state": "revoked", "revoked_at": int(time.time())}
                reg["current"] = None

            _mudar(revogar)
            log.info("integração opencode: origens revogadas")
    r.nota = NOTA_REMOCAO if r.mudou else ""
    return r


def restaurar(dry_run: bool = False):
    from .integracoes import ErroIntegracao

    raise ErroIntegracao("o OpenCode não tem o que desfazer: o Ombro não edita a configuração dele, e uma origem "
                         "revogada não volta (use install para registrar outra)")
