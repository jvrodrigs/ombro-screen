"""Eventos de mudança de estado e o barramento que os entrega.

Os eventos nascem no StateStore, quando uma coleta muda alguma coisa (uma sessão começou, mudou
de status ou acabou; a coleta de um provider passou a falhar). Quem se interessa se inscreve no
EventBus: hoje, a persistência (core/persistence.py) e o log de depuração.

O barramento é local e síncrono: `publish` entrega a todos os inscritos antes de voltar, na ordem
em que os eventos chegaram. Sem threads e sem serviço externo.
"""
from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Deque, Iterable, List, Optional, Tuple

from logging_config import SoQuandoMuda, logger

from .models import AgentSession

log = logger("eventos")


class EventType(str, Enum):
    SESSION_STARTED = "session_started"
    SESSION_CHANGED = "session_changed"  # a atividade ou a atenção mudou
    SESSION_ENDED = "session_ended"
    PROVIDER_CHANGED = "provider_changed"  # a coleta passou a funcionar, a falhar em parte ou a falhar


SESSAO = (EventType.SESSION_STARTED, EventType.SESSION_CHANGED, EventType.SESSION_ENDED)


@dataclass(frozen=True)
class AgentEvent:
    """Uma mudança. `event_id` sai do conteúdo: o mesmo fato publicado duas vezes tem o mesmo id."""
    type: EventType
    provider_id: str
    occurred_at: datetime
    session_id: Optional[str] = None  # None nos eventos do provider
    session: Optional[AgentSession] = None  # como a sessão ficou (não vem em SESSION_ENDED)
    detail: Optional[str] = None  # curto e sem dado sensível, ex.: "partial"
    event_id: str = ""

    def __post_init__(self):
        object.__setattr__(self, "type", EventType(self.type))
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("provider_id é obrigatório")
        if not isinstance(self.occurred_at, datetime):
            raise TypeError("occurred_at deve ser um datetime")
        if self.type in SESSAO and not self.session_id:
            raise ValueError(f"{self.type.value} precisa de session_id")
        if self.type in (EventType.SESSION_STARTED, EventType.SESSION_CHANGED) and self.session is None:
            raise ValueError(f"{self.type.value} precisa da sessão")
        if self.session is not None and self.session.key != (self.provider_id, self.session_id):
            raise ValueError("a sessão do evento é de outra chave")
        if not self.event_id:
            object.__setattr__(self, "event_id", self._id())

    def _id(self) -> str:
        s = self.session
        partes = (self.type.value, self.provider_id, self.session_id or "", self.occurred_at.isoformat(),
                  s.activity.value if s else "", s.attention.value if s else "", self.detail or "")
        return hashlib.sha1("|".join(partes).encode()).hexdigest()[:20]

    @property
    def key(self) -> Optional[Tuple[str, str]]:
        return (self.provider_id, self.session_id) if self.session_id else None


Inscrito = Callable[[AgentEvent], None]


class EventBus:
    """Publica eventos para quem se inscreveu. Síncrono, em ordem, sem repetir o mesmo evento."""

    def __init__(self, memoria: int = 1024):
        self._inscritos: List[Tuple[Inscrito, Optional[frozenset]]] = []
        self._vistos: "OrderedDict[str, None]" = OrderedDict()  # ids recentes, para ignorar repetidos
        self._memoria = memoria
        self._fila: Deque[AgentEvent] = deque()
        self._entregando = False
        self._avisos = SoQuandoMuda(log)

    def subscribe(self, fn: Inscrito, tipos: Optional[Iterable[EventType]] = None) -> Callable[[], bool]:
        """Inscreve `fn` (todos os tipos, ou só `tipos`). Devolve uma função que cancela a inscrição."""
        self._inscritos.append((fn, frozenset(EventType(t) for t in tipos) if tipos is not None else None))
        return lambda: self.unsubscribe(fn)

    def unsubscribe(self, fn: Inscrito) -> bool:
        antes = len(self._inscritos)
        self._inscritos = [(f, t) for f, t in self._inscritos if f is not fn]
        return len(self._inscritos) < antes

    def publish(self, ev: AgentEvent) -> bool:
        """Entrega `ev`. Falso se ele já tinha sido publicado. Um inscrito que falha não impede os outros.

        Um inscrito que publica durante a entrega põe o evento novo na fila: ele é entregue depois
        do atual, a todos, mantendo a ordem.
        """
        if ev.event_id in self._vistos:
            return False
        self._vistos[ev.event_id] = None
        if len(self._vistos) > self._memoria:
            self._vistos.popitem(last=False)
        self._fila.append(ev)
        if self._entregando:
            return True
        self._entregando = True
        try:
            while self._fila:
                atual = self._fila.popleft()
                for fn, tipos in list(self._inscritos):
                    if tipos is None or atual.type in tipos:
                        self._entregar(fn, atual)
        finally:
            self._entregando = False
        return True

    def _entregar(self, fn: Inscrito, ev: AgentEvent) -> None:
        nome = getattr(fn, "__qualname__", repr(fn))
        try:
            fn(ev)
        except Exception as e:  # noqa: BLE001 - um consumidor com defeito não pode travar os outros nem o estado
            if self._avisos.registrar(nome, logging.ERROR, "o consumidor de eventos %s falhou (%s: %s)", nome, type(e).__name__, e):
                log.debug("detalhes da falha em %s", nome, exc_info=True)
        else:
            self._avisos.resolver(nome)


def registrar_no_log(ev: AgentEvent) -> None:
    """Consumidor de depuração: uma linha por mudança, sem nome de projeto."""
    if not log.isEnabledFor(logging.DEBUG):
        return
    if ev.session is not None:
        log.debug("%s %s/%s: atividade %s, atenção %s", ev.type.value, ev.provider_id, ev.session_id[:12],
                  ev.session.activity.value, ev.session.attention.value)
    else:
        log.debug("%s %s%s%s", ev.type.value, ev.provider_id, f"/{ev.session_id[:12]}" if ev.session_id else "",
                  f": {ev.detail}" if ev.detail else "")
