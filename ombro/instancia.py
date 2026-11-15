"""Um Ombro ao vivo por usuário: o lock de ~/.ombro-screen/run/ombro.lock.

"Ao vivo" é quem lê os eventos dos hooks, grava o state.db e fala com a tela USB: o ombrod sempre, e o
tela.py fora do --demo e do --png (e no --demo com --usb). Dois desses ao mesmo tempo dividiriam os
eventos, gravariam o mesmo banco e disputariam a tela.

O lock é um `flock` exclusivo: o sistema solta sozinho quando o processo termina, mesmo num kill -9,
então um arquivo que sobrou de uma instância que caiu nunca trava a próxima (não é um arquivo de PID
que se confere só pela existência). Dentro do arquivo vai só um JSON informativo (PID, tipo e início),
para as mensagens de erro e o doctor; ele não decide nada.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Optional

from .caminhos import arquivo_lock, garantir_pasta_privada


class OutraInstancia(RuntimeError):
    def __init__(self, dono: Optional[dict]):
        self.dono = dono or {}
        pid, tipo = self.dono.get("pid"), self.dono.get("kind", "Ombro")
        super().__init__(f"já existe um Ombro rodando ({tipo}{f', PID {pid}' if pid else ''})")


def _abrir(caminho: Path) -> int:
    garantir_pasta_privada(caminho.parent)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    return os.open(str(caminho), flags, 0o600)


def _ler_dono(fd: int) -> Optional[dict]:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        dados = json.loads(os.read(fd, 4096).decode("utf-8") or "null")
        return dados if isinstance(dados, dict) else None
    except (OSError, ValueError):
        return None


class Trava:
    """`with Trava("ombrod"):` ou adquirir()/soltar(). Levanta OutraInstancia se já há um Ombro ao vivo."""

    def __init__(self, tipo: str, caminho: Optional[Path] = None):
        self.tipo = tipo
        self.caminho = Path(caminho) if caminho is not None else arquivo_lock()
        self._fd: Optional[int] = None

    @property
    def presa(self) -> bool:
        return self._fd is not None

    def adquirir(self) -> "Trava":
        if self._fd is not None:
            return self
        fd = _abrir(self.caminho)
        try:
            for tentativa in range(4):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    # quem_esta_rodando() segura um lock compartilhado por microssegundos: tenta de novo
                    # antes de concluir que há outro Ombro
                    if tentativa == 3:
                        raise
                    time.sleep(0.05)
        except BlockingIOError:
            dono = _ler_dono(fd)
            os.close(fd)
            raise OutraInstancia(dono) from None
        except BaseException:
            os.close(fd)
            raise
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps({"pid": os.getpid(), "kind": self.tipo, "started_at": int(time.time())}).encode())
        self._fd = fd
        return self

    def soltar(self) -> None:
        """Solta o lock. O arquivo fica (apagá-lo abriria uma janela para duas instâncias com arquivos diferentes)."""
        if self._fd is None:
            return
        try:
            os.ftruncate(self._fd, 0)
        except OSError:
            pass
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "Trava":
        return self.adquirir()

    def __exit__(self, *_exc) -> None:
        self.soltar()


def quem_esta_rodando(caminho: Optional[Path] = None) -> Optional[dict]:
    """O dono do lock, se há um Ombro ao vivo; None se ninguém segura o lock (mesmo que o arquivo exista).

    Não cria a pasta run/ se ela não existe (o doctor e o status não devem criar nada)."""
    caminho = Path(caminho) if caminho is not None else arquivo_lock()
    try:
        fd = os.open(str(caminho), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return _ler_dono(fd) or {}
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)
