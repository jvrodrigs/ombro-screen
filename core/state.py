"""O estado consolidado: o último snapshot de cada provider e as sessões que ele conhece.

Chega de dois jeitos:
  - apply_snapshot: o que um provider coletou (o Claude Code hoje, a cada 5 s);
  - apply_event: um evento de um provider que avisa mudanças na hora (nenhum ainda; prontos para hooks).

As mudanças viram eventos no EventBus. O StateStore não desenha nem fala com o USB.

Regras:
  - sessões são separadas por provider: a chave é (provider_id, session_id);
  - um snapshot mais velho que o último aplicado é ignorado (não apaga dado mais novo);
  - um snapshot igual ao anterior só atualiza o horário: nenhum evento;
  - uma parte que falhou na coleta mantém o último valor bom por até GRACA (30 s), marcada em
    `partes_velhas`; depois disso, as sessões ficam com estado desconhecido (não "acabaram": não se sabe);
  - sessões recuperadas do banco entram com estado desconhecido até a próxima coleta confirmar;
  - um evento mais velho que o último aplicado à mesma sessão, ou de uma sessão que já acabou, é ignorado.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from logging_config import logger

from .events import AgentEvent, EventBus, EventType
from .models import Activity, AgentSession, Attention, ProviderSnapshot, SnapshotStatus

log = logger("estado")
GRACA = timedelta(seconds=30)
LEMBRAR_FIM = timedelta(hours=1)  # por quanto tempo uma sessão encerrada recusa eventos atrasados
Chave = Tuple[str, str]


def desconhecida(s: AgentSession) -> AgentSession:
    return replace(s, activity=Activity.UNKNOWN, attention=Attention.UNKNOWN)


@dataclass
class ProviderState:
    provider_id: str
    snapshot: Optional[ProviderSnapshot] = None  # o efetivo: o último, com as partes que falharam completadas
    status: Optional[SnapshotStatus] = None  # o da última coleta
    sessions: Dict[Chave, AgentSession] = field(default_factory=dict)  # na ordem da fonte
    partes_velhas: frozenset = frozenset()  # partes repetidas da coleta anterior porque a atual falhou
    recuperado: bool = False  # sessões vieram do banco e ainda não foram confirmadas por uma coleta
    _ok_em: Dict[str, datetime] = field(default_factory=dict)  # última coleta boa de cada parte
    _evento_em: Dict[Chave, datetime] = field(default_factory=dict)  # última mudança aplicada por sessão
    _fim_em: Dict[Chave, datetime] = field(default_factory=dict)  # sessões encerradas há pouco


class StateStore:
    def __init__(self, bus: Optional[EventBus] = None, graca: timedelta = GRACA):
        self.bus = bus
        self.graca = graca
        self._providers: Dict[str, ProviderState] = {}

    # ---------- leitura ----------
    def provider(self, provider_id: str) -> Optional[ProviderState]:
        return self._providers.get(provider_id)

    def snapshot(self, provider_id: str) -> Optional[ProviderSnapshot]:
        st = self._providers.get(provider_id)
        return st.snapshot if st else None

    def sessions(self, provider_id: Optional[str] = None) -> List[AgentSession]:
        estados = [self._providers[provider_id]] if provider_id in self._providers else (
            [] if provider_id is not None else list(self._providers.values()))
        return [s for st in estados for s in st.sessions.values()]

    def _estado(self, provider_id: str) -> ProviderState:
        if provider_id not in self._providers:
            self._providers[provider_id] = ProviderState(provider_id)
        return self._providers[provider_id]

    # ---------- recuperação ----------
    def restore(self, sessoes: Iterable[AgentSession]) -> int:
        """Sessões salvas no banco, com estado desconhecido: estavam abertas, mas ninguém confirmou agora."""
        n = 0
        for s in sessoes:
            st = self._estado(s.provider_id)
            if s.key in st.sessions:
                continue
            st.sessions[s.key] = desconhecida(s)
            st.recuperado = True
            n += 1
        return n

    # ---------- snapshots ----------
    def apply_snapshot(self, snap: ProviderSnapshot) -> List[AgentEvent]:
        st = self._estado(snap.provider_id)
        anterior = st.snapshot
        if anterior is not None and snap.collected_at < anterior.collected_at:
            log.debug("snapshot atrasado de %s ignorado (%s < %s)", snap.provider_id, snap.collected_at, anterior.collected_at)
            return []
        falhou = {"sessions", "usage", "quotas"} if snap.status is SnapshotStatus.ERROR else set(snap.failed)
        valores, velhas = {}, set()
        for parte in ("sessions", "usage", "quotas"):
            valor = getattr(snap, parte)
            if parte in falhou:
                ok = st._ok_em.get(parte)
                if anterior is not None and ok is not None and snap.collected_at - ok <= self.graca:
                    valor = getattr(anterior, parte)  # falha passageira: repete o último valor bom
                    velhas.add(parte)
                elif parte == "sessions":
                    valor = tuple(desconhecida(s) for s in st.sessions.values())  # sem confirmação: estado desconhecido
                # uso e limites que falharam há mais tempo ficam sem dado (None), como sempre ficaram
            else:
                st._ok_em[parte] = snap.collected_at
            valores[parte] = valor
        efetivo = replace(snap, **valores)
        st.partes_velhas = frozenset(velhas)
        if anterior is not None and efetivo.mesmo_conteudo(anterior):
            st.snapshot, st.status = efetivo, snap.status  # só o horário mudou: nada a avisar
            return []
        eventos = self._diferencas(st, efetivo.sessions, snap.collected_at)
        if snap.status is not st.status:
            eventos.append(AgentEvent(EventType.PROVIDER_CHANGED, snap.provider_id, snap.collected_at, detail=snap.status.value))
        st.snapshot, st.status, st.recuperado = efetivo, snap.status, False
        self._esquecer_fins(st, snap.collected_at)
        self._publicar(eventos)
        return eventos

    def _diferencas(self, st: ProviderState, sessoes: Tuple[AgentSession, ...], quando: datetime) -> List[AgentEvent]:
        novas = {s.key: s for s in sessoes}
        eventos = []
        for chave, s in novas.items():
            velha = st.sessions.get(chave)
            if velha is None:
                eventos.append(AgentEvent(EventType.SESSION_STARTED, s.provider_id, quando, s.session_id, s))
            elif (velha.activity, velha.attention) != (s.activity, s.attention):
                eventos.append(AgentEvent(EventType.SESSION_CHANGED, s.provider_id, quando, s.session_id, s))
            else:
                continue
            st._evento_em[chave] = quando
        for chave, velha in st.sessions.items():
            if chave not in novas:
                eventos.append(AgentEvent(EventType.SESSION_ENDED, velha.provider_id, quando, velha.session_id))
                st._fim_em[chave] = quando
                st._evento_em.pop(chave, None)
        st.sessions = dict(novas)
        return eventos

    # ---------- eventos de providers que avisam na hora ----------
    def apply_event(self, ev: AgentEvent) -> bool:
        """Aplica um evento de sessão. Falso se ele não mudou nada (repetido, atrasado ou de sessão já encerrada)."""
        if ev.key is None:
            return False
        st = self._estado(ev.provider_id)
        chave = ev.key
        ultimo = st._evento_em.get(chave)
        if ultimo is not None and ev.occurred_at < ultimo:
            log.debug("evento atrasado ignorado: %s %s", ev.type.value, ev.provider_id)
            return False
        fim = st._fim_em.get(chave)
        if fim is not None and ev.occurred_at <= fim:
            log.debug("evento de sessão já encerrada ignorado: %s %s", ev.type.value, ev.provider_id)
            return False
        atual = st.sessions.get(chave)
        if ev.type is EventType.SESSION_ENDED:
            if atual is None:
                return False
            del st.sessions[chave]
            st._fim_em[chave] = ev.occurred_at
            st._evento_em.pop(chave, None)
        else:
            if atual == ev.session:
                return False
            st.sessions[chave] = ev.session
            st._fim_em.pop(chave, None)
            st._evento_em[chave] = ev.occurred_at
        base = st.snapshot or ProviderSnapshot(ev.provider_id, ev.occurred_at)
        st.snapshot = replace(base, sessions=tuple(st.sessions.values()))
        self._publicar([ev])
        return True

    # ---------- arrumação ----------
    def _esquecer_fins(self, st: ProviderState, agora: datetime) -> None:
        for chave in [c for c, t in st._fim_em.items() if agora - t > LEMBRAR_FIM]:
            del st._fim_em[chave]

    def _publicar(self, eventos: List[AgentEvent]) -> None:
        if self.bus is not None:
            for ev in eventos:
                self.bus.publish(ev)
