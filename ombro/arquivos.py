"""Gravar um arquivo inteiro de uma vez, sem nunca deixar metade dele no disco.

Escreve num arquivo temporário escondido na mesma pasta, força o disco (fsync) e troca pelo nome final
com os.replace, que é atômico: quem lê vê o arquivo antigo inteiro ou o novo inteiro. Se o destino é um
link simbólico (configurações gerenciadas por um repositório de dotfiles, por exemplo), grava no arquivo
para onde o link aponta, e o link continua lá.

`assinatura` e `conferir_inalterado` servem para notar que outro programa mexeu no arquivo entre a
leitura e a gravação (o Claude Code salvando uma preferência, por exemplo): nesse caso não se grava nada.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple


class ArquivoMudou(RuntimeError):
    """O arquivo mudou depois de lido: gravar agora apagaria a mudança de outro programa."""


Assinatura = Optional[Tuple[int, int, str]]  # (tamanho, mtime_ns, sha256) ou None (não existia)


def destino_real(caminho: Path) -> Path:
    """O arquivo de verdade por trás de um link (o próprio caminho, se não é link)."""
    return Path(os.path.realpath(caminho)) if os.path.islink(caminho) else Path(caminho)


def assinatura(caminho: Path) -> Assinatura:
    try:
        st = os.stat(caminho)
        with open(caminho, "rb") as f:
            dados = f.read()
    except FileNotFoundError:
        return None
    return st.st_size, st.st_mtime_ns, hashlib.sha256(dados).hexdigest()


def conferir_inalterado(caminho: Path, antes: Assinatura) -> None:
    if assinatura(caminho) != antes:
        raise ArquivoMudou(f"{Path(caminho).name} mudou enquanto o Ombro trabalhava; nada foi gravado, tente de novo")


def gravar_atomico(caminho: Path, dados: bytes, modo: Optional[int] = None, antes: Assinatura = ...) -> Path:
    """Grava `dados` em `caminho` (ou no alvo do link). `modo`: permissão de um arquivo novo; um arquivo
    que já existe mantém a sua. Com `antes`, confere que ninguém mexeu no arquivo desde a leitura.
    Devolve o arquivo gravado."""
    alvo = destino_real(Path(caminho))
    pasta = alvo.parent
    if not pasta.is_dir():
        raise FileNotFoundError(f"a pasta de {alvo.name} não existe")
    try:
        modo_final = os.stat(alvo).st_mode & 0o7777
    except FileNotFoundError:
        modo_final = 0o600 if modo is None else modo
    tmp = pasta / f".{alvo.name}.ombro-{os.getpid()}-{os.urandom(4).hex()}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            visto = 0
            while visto < len(dados):
                visto += os.write(fd, dados[visto:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, modo_final)
        if antes is not ...:
            conferir_inalterado(alvo, antes)
        os.replace(tmp, alvo)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:  # a troca de nome também precisa chegar ao disco
        dfd = os.open(str(pasta), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return alvo
