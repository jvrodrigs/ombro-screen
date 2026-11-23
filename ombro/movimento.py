"""A preferência "reduzir movimento" do macOS, lida pelo próprio ombrod (a telinha não passa pelo WebView do app).

O app tem a media query `prefers-reduced-motion` do WebView; o ombrod roda sozinho (LaunchAgent, com o app fechado) e
lê a mesma preferência do sistema: `NSWorkspace.accessibilityDisplayShouldReduceMotion`, chamada pelo runtime do
Objective-C com `ctypes` (biblioteca padrão: sem dependência, sem binário, sem processo filho). A leitura é guardada
por alguns segundos: ninguém consulta o sistema a cada quadro.

Quando não dá para ler (outro sistema, o framework não abriu, qualquer erro), o resultado é "reduzido": quem anima a
cor do mascote (o ciclo "rgb") fica na cor-base parada. Parado é o lado seguro: nunca anima contra uma preferência
que não deu para conferir.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Optional

TTL_S = 5.0  # quanto tempo vale uma leitura (o ombrod consulta no máximo isto a cada TTL_S, não a cada quadro)


def ler_do_sistema() -> Optional[bool]:
    """True/False: a preferência "Reduzir movimento" do macOS. None: não deu para ler."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util

        caminho = ctypes.util.find_library("objc")
        if not caminho:
            return None
        objc = ctypes.CDLL(caminho)
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype, objc.objc_getClass.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        objc.sel_registerName.restype, objc.sel_registerName.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
        enviar = objc.objc_msgSend
        enviar.argtypes, enviar.restype = [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p
        classe = objc.objc_getClass(b"NSWorkspace")
        if not classe:
            return None
        ws = enviar(classe, objc.sel_registerName(b"sharedWorkspace"))
        if not ws:
            return None
        enviar.restype = ctypes.c_bool
        return bool(enviar(ws, objc.sel_registerName(b"accessibilityDisplayShouldReduceMotion")))
    except Exception:  # noqa: BLE001 - ler uma preferência nunca derruba o laço; sem leitura, fica parado
        return None


class MovimentoReduzido:
    """A leitura guardada. `leitor` e `relogio` vêm de fora nos testes."""

    def __init__(self, leitor: Callable[[], Optional[bool]] = ler_do_sistema,
                 relogio: Callable[[], float] = time.monotonic, ttl: float = TTL_S):
        self._leitor, self._relogio, self._ttl = leitor, relogio, ttl
        self._valor: Optional[bool] = None
        self._em: Optional[float] = None

    def reduzido(self) -> bool:
        """Verdadeiro se é para ficar parado: a preferência está ligada, ou não deu para ler."""
        agora = self._relogio()
        if self._em is None or agora - self._em >= self._ttl:
            self._em = agora
            try:
                self._valor = self._leitor()
            except Exception:  # noqa: BLE001
                self._valor = None
        return self._valor is not False
