"""Onde o Ombro Hub guarda o que é dele, e como conferir que essas pastas são mesmo privadas.

  ~/.ombro-screen/              (ou $OMBRO_SCREEN_DIR) config.json, state.db, eventos/, uso.json
  ~/.ombro-screen/run/          ombro.lock e ombro.sock, só enquanto um Ombro roda
  ~/.ombro-screen/backups/      cópias das configurações dos agentes antes de cada mudança

As pastas são criadas com 0700. Uma pasta que é link simbólico, de outro usuário ou que não é pasta
é recusada (não se segue nem se corrige): é o tipo de troca de caminho que um socket ou um lock não
podem aceitar. Uma pasta nossa com permissão aberta demais é fechada para 0700.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from config import pasta_ombro

# sockaddr_un.sun_path: 104 bytes no macOS, 108 no Linux (com o \0 do fim)
MAX_SOCKET = 103 if sys.platform == "darwin" else 107


class CaminhoInseguro(OSError):
    """Uma pasta ou arquivo do Ombro que não dá para usar com segurança (dono, tipo ou link)."""


def pasta_run() -> Path:
    return pasta_ombro() / "run"


def arquivo_socket() -> Path:
    return pasta_run() / "ombro.sock"


def arquivo_lock() -> Path:
    return pasta_run() / "ombro.lock"


def pasta_backups() -> Path:
    return pasta_ombro() / "backups"


def garantir_pasta_privada(pasta: Path) -> Path:
    """Cria `pasta` (e as de cima que faltarem) com 0700 e confere que ela é nossa. Devolve a pasta."""
    pasta = Path(pasta)
    # a pasta de cima (~/.ombro-screen) nasce 0700 se não existe; se já existe, fica como o usuário deixou
    # (OMBRO_SCREEN_DIR pode apontar para uma pasta que não é só do Ombro)
    pasta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    pasta.mkdir(exist_ok=True, mode=0o700)
    conferir_pasta_privada(pasta, corrigir=True)
    return pasta


def conferir_pasta_privada(pasta: Path, corrigir: bool = False) -> None:
    """Levanta CaminhoInseguro se `pasta` é link, não é pasta ou é de outro usuário.
    Com `corrigir`, fecha para 0700 uma pasta nossa aberta para o grupo ou os outros."""
    try:
        st = os.lstat(pasta)
    except FileNotFoundError:
        raise CaminhoInseguro(f"a pasta {pasta.name} não existe") from None
    if stat.S_ISLNK(st.st_mode):
        raise CaminhoInseguro(f"{pasta.name} é um link simbólico; o Ombro só usa pastas de verdade aqui")
    if not stat.S_ISDIR(st.st_mode):
        raise CaminhoInseguro(f"{pasta.name} não é uma pasta")
    if st.st_uid != os.getuid():
        raise CaminhoInseguro(f"a pasta {pasta.name} é de outro usuário")
    if st.st_mode & 0o077:
        if not corrigir:
            raise CaminhoInseguro(f"a pasta {pasta.name} está aberta para outros usuários ({oct(st.st_mode & 0o777)})")
        os.chmod(pasta, 0o700)


def permissao_aberta(caminho: Path) -> bool:
    """O arquivo ou pasta pode ser lido por outros usuários (grupo ou todos)?"""
    try:
        return bool(os.stat(caminho).st_mode & 0o077)
    except OSError:
        return False


def socket_cabe(caminho: Path) -> bool:
    return len(os.fsencode(str(caminho))) <= MAX_SOCKET
