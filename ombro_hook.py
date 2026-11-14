#!/usr/bin/env python3
"""Hook do Ombro para Claude Code, Codex e Cursor: anota que algo aconteceu numa sessão, e só.

  python3 /CAMINHO/ombro-screen/ombro_hook.py claude      (ou codex, ou cursor)

O agente roda este script nos eventos configurados e passa um JSON pelo stdin. O script grava um
arquivo pequeno em ~/.ombro-screen/eventos/ (ou $OMBRO_SCREEN_DIR/eventos) com: o provider, o id
da sessão, o nome do evento, o horário, o nome curto da pasta do projeto e, em alguns eventos, um
status de uma lista fechada. Mais nada: nem prompt, nem resposta, nem comando, nem conteúdo de
arquivo, nem caminho completo, nem e-mail, nem mensagem de erro.

Regras:
  - não usa rede, não lê credenciais, não roda outros programas e não mexe no projeto;
  - nunca bloqueia nem decide nada: sai sempre com código 0, em milissegundos;
  - Claude Code e Codex: não imprime nada (lá, o stdout de alguns eventos entraria na conversa);
    Cursor: imprime "{}", um JSON válido e vazio, que não muda nada (lá, alguns hooks exigem JSON);
  - Ombro desligado: os eventos esperam na pasta, até MAX_PENDENTES; dali em diante, são descartados.

Só biblioteca padrão, para rodar com qualquer python3.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

VERSAO = 1
MAX_ENTRADA = 8 * 1024 * 1024  # bytes de stdin; acima disso o evento é descartado
MAX_PENDENTES = 5000  # arquivos esperando o Ombro; acima disso o evento é descartado

# Eventos guardados por provider, e de qual campo sai o "detalhe" (None = nenhum).
EVENTOS = {
    "claude": {
        "SessionStart": "source", "SessionEnd": "reason", "PermissionRequest": None, "Stop": None, "StopFailure": None,
    },
    "codex": {
        "SessionStart": "source", "SessionEnd": None, "UserPromptSubmit": None, "PermissionRequest": None,
        "PostToolUse": None, "Stop": None, "Interrupt": None, "PostModelRequest": "status",
    },
    "cursor": {
        "sessionStart": None, "sessionEnd": "reason", "beforeSubmitPrompt": None, "postToolUse": None,
        "postToolUseFailure": None, "stop": "status",
    },
}
DETALHES = {  # os únicos valores de detalhe aceitos; qualquer outro vira null
    "startup", "resume", "clear", "compact", "fork",  # SessionStart.source
    "logout", "prompt_input_exit", "other",  # SessionEnd.reason (Claude Code, Codex)
    "completed", "aborted", "error", "window_close", "user_close",  # Cursor: sessionEnd.reason, stop.status
    "failed", "cancelled",  # Codex: PostModelRequest.status
}
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def pasta() -> Path:
    return Path(os.environ.get("OMBRO_SCREEN_DIR", "~/.ombro-screen")).expanduser() / "eventos"


def _id_sessao(v) -> str:
    v = str(v)
    return v if _ID.fullmatch(v) else "h-" + hashlib.sha256(v.encode()).hexdigest()[:20]


def _projeto(dados: dict):
    caminho = dados.get("cwd")
    raizes = dados.get("workspace_roots")
    if not caminho and isinstance(raizes, list) and raizes:
        caminho = raizes[0]
    if not isinstance(caminho, str) or not caminho.strip():
        return None
    nome = os.path.basename(caminho.rstrip("/\\")) or None
    return nome[:80] if nome else None


def evento(provider: str, dados: dict, agora_ms: int):
    """O registro a guardar, ou None se o evento não interessa ao Ombro."""
    if not isinstance(dados, dict):
        return None
    nome = dados.get("hook_event_name")
    eventos = EVENTOS.get(provider, {})
    if nome not in eventos:
        return None
    sessao = dados.get("session_id") or dados.get("conversation_id")
    if not sessao:
        return None
    campo = eventos[nome]
    detalhe = dados.get(campo) if campo else None
    return {
        "v": VERSAO,
        "provider": provider,
        "session": _id_sessao(sessao),
        "event": nome,
        "ts": agora_ms,
        "project": _projeto(dados),
        "detail": detalhe if detalhe in DETALHES else None,
    }


def gravar(registro: dict, destino: Path) -> bool:
    destino.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.scandir(destino) as it:
        if sum(1 for e in it if not e.name.startswith(".")) >= MAX_PENDENTES:
            return False  # o Ombro está desligado há muito tempo: não enche o disco
    nome = f"{time.time_ns():020d}-{os.getpid()}-{os.urandom(3).hex()}.json"
    tmp = destino / f".{nome}.tmp"  # escondido até ficar inteiro: o Ombro só lê o nome final
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, json.dumps(registro, separators=(",", ":")).encode())
    finally:
        os.close(fd)
    os.replace(tmp, destino / nome)
    return True


def _ler_stdin() -> bytes:
    entrada = sys.stdin.buffer.read(MAX_ENTRADA + 1)
    if len(entrada) > MAX_ENTRADA:
        while sys.stdin.buffer.read(1 << 20):  # esvazia o resto sem guardar, para o agente não ver o pipe quebrar
            pass
        return b""
    return entrada


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    provider = argv[0] if argv else ""
    try:
        entrada = _ler_stdin()
        if provider in EVENTOS and entrada:
            registro = evento(provider, json.loads(entrada), int(time.time() * 1000))
            if registro:
                gravar(registro, pasta())
    except BaseException:  # noqa: BLE001 - um monitor nunca pode atrapalhar o agente
        pass
    finally:
        if provider == "cursor":
            sys.stdout.write("{}")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
