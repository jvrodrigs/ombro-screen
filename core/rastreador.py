"""Sessões montadas a partir de eventos de hooks, para providers sem um arquivo de estado para reler.

O Claude Code grava o status de cada sessão em disco, e o Ombro relê a cada 5 s. O Codex e o Cursor
não: o que se sabe deles chega pelos hooks. Este rastreador guarda, por sessão, o último estado que
os eventos indicaram, e cada provider só diz como traduzir os seus eventos (`mapear`).

Regras:
  - um evento mais velho que o último aplicado à mesma sessão é ignorado (chegou atrasado);
  - o mesmo evento repetido (mesmo provider, sessão, nome, horário e detalhe) é ignorado;
  - evento de uma sessão que já acabou, com horário de antes do fim, é ignorado;
  - "trabalhando" sem nenhum evento há TTL_TRABALHANDO vira desconhecido (um evento de fim pode ter se
    perdido: terminal fechado, Mac dormindo). Não vira "ocioso": ninguém confirmou;
  - sessão sem nenhum evento há TTL_SESSAO sai da lista (abandonada);
  - sessões recuperadas do banco entram com estado desconhecido e seguem as mesmas regras.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, Optional, Tuple

from .caixa import EventoLocal
from .models import Activity, AgentSession, Attention

TTL_TRABALHANDO = timedelta(minutes=15)
TTL_SESSAO = timedelta(hours=4)
MEMORIA_VISTOS = 4096


@dataclass(frozen=True)
class Transicao:
    """O que um evento muda na sessão. None = deixa como está."""
    activity: Optional[Activity] = None
    attention: Optional[Attention] = None
    fim: bool = False  # a sessão acabou


Mapa = Callable[[EventoLocal, Optional[AgentSession]], Optional[Transicao]]


class RastreadorDeSessoes:
    def __init__(self, provider_id: str, mapear: Mapa, ttl_trabalhando: timedelta = TTL_TRABALHANDO,
                 ttl_sessao: timedelta = TTL_SESSAO):
        self.provider_id, self._mapear = provider_id, mapear
        self.ttl_trabalhando, self.ttl_sessao = ttl_trabalhando, ttl_sessao
        self._sessoes: "OrderedDict[str, AgentSession]" = OrderedDict()
        self._ultimo: Dict[str, datetime] = {}  # último evento aplicado por sessão
        self._fim: Dict[str, datetime] = {}  # sessões encerradas há pouco
        self._vistos: "OrderedDict[tuple, None]" = OrderedDict()
        self.ultimo_evento: Optional[datetime] = None  # o mais recente de todos (a integração está viva?)

    def aplicar(self, eventos: Iterable[EventoLocal]) -> int:
        n = 0
        for ev in sorted(eventos, key=lambda e: e.quando):
            if ev.provider != self.provider_id or ev.chave in self._vistos:
                continue
            self._vistos[ev.chave] = None
            if len(self._vistos) > MEMORIA_VISTOS:
                self._vistos.popitem(last=False)
            sid = ev.session
            if (sid in self._ultimo and ev.quando < self._ultimo[sid]) or (sid in self._fim and ev.quando <= self._fim[sid]):
                continue  # atrasado, ou de uma sessão que já acabou
            self.ultimo_evento = max(self.ultimo_evento or ev.quando, ev.quando)
            atual = self._sessoes.get(sid)
            t = self._mapear(ev, atual)
            if t is not None and t.fim:
                self._sessoes.pop(sid, None)
                self._ultimo.pop(sid, None)
                self._fim[sid] = ev.quando
                n += 1
                continue
            self._fim.pop(sid, None)
            self._ultimo[sid] = ev.quando
            if atual is None:
                atual = AgentSession(self.provider_id, sid, Activity.UNKNOWN, Attention.UNKNOWN, project=ev.project,
                                     started_at=ev.quando, status_since=ev.quando, updated_at=ev.quando)
            novo = replace(atual, updated_at=ev.quando, project=ev.project or atual.project)
            if t is not None:
                atividade = t.activity if t.activity is not None else atual.activity
                atencao = t.attention if t.attention is not None else atual.attention
                if (atividade, atencao) != (atual.activity, atual.attention):
                    novo = replace(novo, activity=atividade, attention=atencao, status_since=ev.quando)
            self._sessoes[sid] = novo
            n += 1
        return n

    def restaurar(self, sessoes: Iterable[AgentSession]) -> None:
        """Sessões que o banco lembrava: entram desconhecidas, até um evento ou o TTL decidir."""
        for s in sessoes:
            if s.provider_id == self.provider_id and s.session_id not in self._sessoes:
                self._sessoes[s.session_id] = replace(s, activity=Activity.UNKNOWN, attention=Attention.UNKNOWN)

    def sessoes(self, agora: datetime) -> Tuple[AgentSession, ...]:
        """As sessões vivas agora, já com as regras de tempo aplicadas."""
        vivas = []
        for sid, s in list(self._sessoes.items()):
            visto = s.updated_at or s.started_at
            if visto is not None and agora - visto > self.ttl_sessao:
                del self._sessoes[sid]  # abandonada
                self._ultimo.pop(sid, None)
                continue
            if s.activity is Activity.WORKING and visto is not None and agora - visto > self.ttl_trabalhando:
                s = replace(s, activity=Activity.UNKNOWN)  # sem notícia: não dá para dizer que segue trabalhando
            vivas.append(s)
        for sid in [k for k, t in self._fim.items() if agora - t > self.ttl_sessao]:
            del self._fim[sid]
        return tuple(vivas)
