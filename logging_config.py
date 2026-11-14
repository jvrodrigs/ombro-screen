"""Logs do Ombro: um formato só, no stderr (no LaunchAgent, o arquivo de log).

  2026-11-14 12:30:00 INFO [ombro.usb] tela USB conectada (USB direto)

Nível INFO por padrão; DEBUG com `--debug`. Cada parte usa um logger filho de "ombro":
ombro.main, ombro.config, ombro.claude, ombro.hardware, ombro.render, ombro.usb, ombro.simulador.

Regras que valem para todos:
  - nada a cada quadro: problemas que se repetem entram uma vez (SoQuandoMuda) e a volta ao
    normal entra uma vez;
  - nunca registrar conteúdo de prompts, respostas, arquivos do Claude Code, credenciais ou
    variáveis de ambiente; nomes de projeto e caminhos com a sua pasta pessoal só no DEBUG,
    e com a pasta pessoal trocada por ~ (veja `curto`).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
FORMATO_DATA = "%Y-%m-%d %H:%M:%S"
RAIZ = "ombro"


def logger(parte: str) -> logging.Logger:
    return logging.getLogger(f"{RAIZ}.{parte}")


def configurar(debug: bool = False, fluxo=None) -> logging.Logger:
    """Liga os logs do Ombro (só os dele: bibliotecas como o pyusb ficam de fora). Pode ser chamado de novo."""
    raiz = logging.getLogger(RAIZ)
    for h in list(raiz.handlers):
        if getattr(h, "_ombro", False):
            raiz.removeHandler(h)
    h = logging.StreamHandler(fluxo if fluxo is not None else sys.stderr)
    h._ombro = True  # type: ignore[attr-defined]
    h.setFormatter(logging.Formatter(FORMATO, FORMATO_DATA))
    raiz.addHandler(h)
    raiz.setLevel(logging.DEBUG if debug else logging.INFO)
    raiz.propagate = False
    return raiz


def curto(caminho) -> str:
    """Caminho com a pasta pessoal trocada por ~, para não espalhar o nome do usuário pelos logs."""
    p = str(caminho)
    casa = str(Path.home())
    return "~" + p[len(casa):] if p == casa or p.startswith(casa + "/") else p


def sem_casa(texto) -> str:
    """Troca a pasta pessoal por ~ em qualquer ponto do texto (mensagens de erro trazem caminhos)."""
    return str(texto).replace(str(Path.home()), "~")


class SoQuandoMuda:
    """Registra um problema na primeira vez e quando ele muda; não a cada repetição.

        avisos = SoQuandoMuda(log)
        avisos.registrar("macmon", logging.WARNING, "macmon falhou: %s", erro)   # entra
        avisos.registrar("macmon", logging.WARNING, "macmon falhou: %s", erro)   # igual: não entra
        avisos.resolver("macmon", "macmon voltou")                                # entra uma vez
    """

    def __init__(self, log: logging.Logger):
        self.log = log
        self._ativos: dict[str, str] = {}

    def registrar(self, chave: str, nivel: int, msg: str, *args, exc_info=None) -> bool:
        texto = msg % args if args else msg
        if self._ativos.get(chave) == texto:
            return False
        self._ativos[chave] = texto
        self.log.log(nivel, texto, exc_info=exc_info)
        return True

    def resolver(self, chave: str, msg: str | None = None, *args, nivel: int = logging.INFO) -> bool:
        """O problema passou: registra `msg` (se houver) só se ele estava ativo."""
        if self._ativos.pop(chave, None) is None:
            return False
        if msg:
            self.log.log(nivel, msg, *args)
        return True

    def ativo(self, chave: str) -> bool:
        return chave in self._ativos
