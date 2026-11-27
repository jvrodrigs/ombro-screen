"""A caixa de entrada dos hooks: os arquivos que o ombro_hook.py grava em ~/.ombro-screen/eventos/.

  caixa = CaixaDeEventos(pasta_ombro() / "eventos")
  eventos = caixa.ler()          # os novos, em ordem de chegada, já validados
  ...aplicar...
  caixa.confirmar(eventos)        # só então apaga os arquivos

Cada arquivo é um evento pequeno, gravado inteiro de uma vez (o hook escreve num nome escondido e
renomeia), então nunca se lê um arquivo pela metade, mesmo com vários hooks gravando ao mesmo tempo.
A pasta só é relida quando muda (o horário de modificação dela), então ler a cada ciclo custa um stat.

Um arquivo inválido, grande demais ou velho demais (mais de MAX_IDADE) é apagado sem ser aplicado.
Se o Ombro cair entre ler e confirmar, os eventos são relidos na próxima vez; os providers ignoram
os repetidos.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from logging_config import SoQuandoMuda, curto, logger

log = logger("eventos")

MAX_ARQUIVO = 64 * 1024  # um evento tem uns 200 bytes; mais que isso não é do ombro_hook.py
MAX_IDADE = timedelta(hours=24)
MAX_POR_LEITURA = 2000  # o resto fica para o próximo ciclo


@dataclass(frozen=True)
class EventoLocal:
    """Um evento de hook, só com os campos permitidos."""
    provider: str
    session: str
    event: str  # o nome do evento como o agente chama (Stop, sessionEnd...)
    quando: datetime
    project: Optional[str] = None
    detail: Optional[str] = None
    arquivo: Optional[Path] = None  # de onde veio (para apagar depois de aplicar)
    # campos extras de quem precisa de mais que o básico (o plugin do OpenCode); cada provider valida o que usa
    extra: Optional[dict] = field(default=None, compare=False)

    @property
    def chave(self):
        return self.provider, self.session, self.event, self.quando, self.detail


def _ler(arq: Path) -> Optional[EventoLocal]:
    if arq.stat().st_size > MAX_ARQUIVO:
        return None
    d = json.loads(arq.read_text(encoding="utf-8"))
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    campos = (d.get("provider"), d.get("session"), d.get("event"))
    ts = d.get("ts")
    if not all(isinstance(c, str) and 0 < len(c) <= 128 for c in campos) or isinstance(ts, bool) or not isinstance(ts, int):
        return None
    projeto, detalhe, extra = d.get("project"), d.get("detail"), d.get("x")
    return EventoLocal(*campos, quando=datetime.fromtimestamp(ts / 1000),
                       project=projeto[:80] if isinstance(projeto, str) and projeto else None,
                       detail=detalhe[:32] if isinstance(detalhe, str) and detalhe else None, arquivo=arq,
                       extra=extra if isinstance(extra, dict) else None)


class CaixaDeEventos:
    def __init__(self, pasta: Path, relogio: Callable[[], datetime] = datetime.now):
        self.pasta = Path(pasta)
        self._relogio = relogio
        self._mtime: Optional[float] = None
        self._pendente = False  # sobrou arquivo da última leitura (mais de MAX_POR_LEITURA, ou sem confirmar)
        self._esperando: Dict[str, str] = {}  # arquivo sem confirmar -> provider
        self._avisos = SoQuandoMuda(log)
        self.lidos = self.descartados = 0  # contadores, para o log de depuração e o benchmark

    def ler(self) -> List[EventoLocal]:
        try:
            mtime = self.pasta.stat().st_mtime
        except FileNotFoundError:
            return []  # nenhum hook configurado ainda
        except OSError as e:
            self._avisos.registrar("pasta", logging.WARNING, "não consegui ler a pasta de eventos (%s)", e)
            return []
        if mtime == self._mtime and not self._pendente:
            return []
        self._mtime = mtime
        try:
            nomes = sorted(e.name for e in os.scandir(self.pasta) if e.name.endswith(".json") and not e.name.startswith("."))
        except OSError as e:
            self._avisos.registrar("pasta", logging.WARNING, "não consegui listar a pasta de eventos (%s)", e)
            return []
        self._avisos.resolver("pasta")
        # quem ficou sem confirmar tenta de novo, os mais antigos primeiro, sem ocupar a vez dos outros
        presentes = set(nomes)
        self._esperando = {n: p for n, p in self._esperando.items() if n in presentes}
        de_novo = [n for n in nomes if n in self._esperando][:MAX_POR_LEITURA]
        novos = [n for n in nomes if n not in self._esperando]
        segurar = set(self._esperando.values()) if len(self._esperando) > len(de_novo) else set()
        self._pendente = len(novos) > MAX_POR_LEITURA or bool(self._esperando)
        eventos, limite = [], self._relogio() - MAX_IDADE
        for nome in sorted(de_novo + novos[:MAX_POR_LEITURA]):  # os nomes começam pelo horário: ordem de chegada
            arq = self.pasta / nome
            try:
                ev = _ler(arq)
            except FileNotFoundError:
                continue  # outro Ombro já processou
            except (OSError, ValueError) as e:
                log.debug("evento ilegível descartado: %s (%s)", nome, type(e).__name__)
                ev = None
            if ev is None or ev.quando < limite:
                self._apagar(arq)
                self.descartados += 1
                continue
            if ev.provider in segurar and nome not in self._esperando:
                self._esperando[nome] = ev.provider  # há mais antigos dele na frente: este espera, para manter a ordem
                continue
            eventos.append(ev)
        self.lidos += len(eventos)
        if eventos:
            log.debug("%d evento(s) de hooks em %s", len(eventos), curto(self.pasta))
        return eventos

    def reler(self, eventos: Iterable[EventoLocal]) -> None:
        """Estes ficaram sem confirmar: voltam numa próxima leitura, mesmo sem arquivo novo."""
        for ev in eventos:
            if ev.arquivo is not None:
                self._esperando[ev.arquivo.name] = ev.provider
        self._pendente = True

    def confirmar(self, eventos: Iterable[EventoLocal]) -> None:
        """Os eventos foram aplicados: os arquivos podem sair."""
        for ev in eventos:
            if ev.arquivo is not None:
                self._esperando.pop(ev.arquivo.name, None)
                self._apagar(ev.arquivo)

    @staticmethod
    def _apagar(arq: Path) -> None:
        try:
            arq.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.debug("não consegui apagar %s", arq.name)
