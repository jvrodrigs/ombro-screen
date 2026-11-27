"""O resumo de todos os agentes, para quem desenha: quantos trabalham, quem precisa de você.

Função pura sobre o StateStore: as mesmas entradas dão o mesmo resumo (sem relógio escondido).

Prioridade de atenção (só no layout multiagente; os outros mantêm a do cloud de sempre):
  erro > esperando aprovação > esperando resposta > limite de uso no fim > trabalhando > ocioso
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Tuple

from .models import Activity, AgentSession, Attention, ProviderSnapshot, SnapshotStatus

LIMITE_QUOTA = 90.0  # % de uma janela de uso a partir do qual é alerta (o mesmo do cloud)

# estado agregado de um provider, do mais urgente para o menos
ESTADOS = ("erro", "aprovacao", "aguardando", "alerta", "trabalhando", "ocioso", "desconhecido", "sem_sessoes",
           "nao_configurado", "indisponivel", "nao_instalado", "incompativel", "conflito", "nao_verificado", "coletando",
           "sem_plugin", "desconectado")
# a condição da integração (ProviderSnapshot.condition) -> estado, quando não há atividade confirmada a mostrar
DA_CONDICAO = {"incompatible": "incompativel", "conflict": "conflito", "unverified": "nao_verificado",
               "collecting": "coletando", "offline": "desconectado", "never": "sem_plugin",
               "unregistered": "nao_configurado"}
URGENCIA = {Attention.ERROR: 0, Attention.WAITING_APPROVAL: 1, Attention.WAITING_USER: 2}


@dataclass(frozen=True)
class ResumoProvider:
    provider_id: str
    nome: str
    estado: str  # um de ESTADOS
    sessoes: int = 0  # as de agora: as de uma fonte fora do ar contam em `desconectadas`
    trabalhando: int = 0
    aguardando: int = 0  # resposta ou aprovação
    erros: int = 0
    quota_max: Optional[float] = None  # a janela de uso mais cheia, se o provider informa
    snapshot: Optional[ProviderSnapshot] = None
    desconectadas: int = 0  # sessões com só o último estado conhecido

    @property
    def ativo(self) -> bool:
        return self.sessoes > 0


@dataclass(frozen=True)
class Urgente:
    provider_id: str
    nome: str
    motivo: str  # "erro", "aprovacao", "aguardando" ou "alerta"
    sessao: Optional[AgentSession] = None


@dataclass(frozen=True)
class ResumoGlobal:
    providers: Tuple[ResumoProvider, ...]
    total_sessoes: int
    trabalhando: int
    aguardando: int
    erros: int
    mais_urgente: Optional[Urgente]

    @property
    def providers_ativos(self) -> int:
        return sum(1 for p in self.providers if p.ativo)

    def provider(self, provider_id: str) -> Optional[ResumoProvider]:
        return next((p for p in self.providers if p.provider_id == provider_id), None)


def _quota_max(snap: Optional[ProviderSnapshot]) -> Optional[float]:
    if snap is None or snap.quotas is None:
        return None
    pcts = [w.used_percent for w in snap.quotas.windows if w.used_percent is not None]
    return max(pcts) if pcts else None


def resumir_provider(provider_id: str, nome: str, snap: Optional[ProviderSnapshot],
                     sessoes: Iterable[AgentSession]) -> ResumoProvider:
    sessoes = list(sessoes)
    atuais = [s for s in sessoes if not s.disconnected]  # o histórico de uma fonte fora do ar não é estado de agora
    trabalhando = sum(1 for s in atuais if s.activity is Activity.WORKING)
    aprovacao = sum(1 for s in atuais if s.attention is Attention.WAITING_APPROVAL)
    aguardando = sum(1 for s in atuais if s.attention in (Attention.WAITING_USER, Attention.WAITING_APPROVAL))
    erros = sum(1 for s in atuais if s.attention is Attention.ERROR)
    quota = _quota_max(snap)
    if snap is not None and snap.integration == "not_installed" and not sessoes:
        estado = "nao_instalado"
    elif snap is not None and snap.integration == "not_configured" and not sessoes:
        estado = "nao_configurado"
    elif snap is None or (snap.status is SnapshotStatus.ERROR and not sessoes):
        estado = "indisponivel"
    elif snap.condition in ("incompatible", "conflict", "unverified") and not trabalhando and not aguardando:
        estado = DA_CONDICAO[snap.condition]
    elif snap.condition == "offline" or (snap.condition == "unregistered" and sessoes and not atuais):
        estado = "desconectado"
    elif erros:
        estado = "erro"
    elif aprovacao:
        estado = "aprovacao"
    elif aguardando:
        estado = "aguardando"
    elif quota is not None and quota >= LIMITE_QUOTA:
        estado = "alerta"
    elif trabalhando:
        estado = "trabalhando"
    elif (snap.condition in ("collecting", "never", "unregistered")
          and not any(s.activity is not Activity.UNKNOWN for s in atuais)):
        estado = DA_CONDICAO[snap.condition]
    elif not atuais:
        estado = "sem_sessoes"
    elif any(s.activity is Activity.IDLE for s in atuais):
        estado = "ocioso"
    else:
        estado = "desconhecido"
    return ResumoProvider(provider_id, nome, estado, len(atuais), trabalhando, aguardando, erros, quota, snap,
                          len(sessoes) - len(atuais))


def _mais_urgente(provs: Iterable[ResumoProvider], sessoes_por: dict) -> Optional[Urgente]:
    candidatas = []
    for p in provs:
        for s in sessoes_por.get(p.provider_id, ()):
            if s.attention in URGENCIA and not s.disconnected:
                desde = s.status_since or s.updated_at or datetime.max
                candidatas.append((URGENCIA[s.attention], desde, p.provider_id, s.session_id, p, s))
    if candidatas:
        _, _, _, _, p, s = min(candidatas)  # o mais urgente; no empate, quem espera há mais tempo
        motivo = {Attention.ERROR: "erro", Attention.WAITING_APPROVAL: "aprovacao", Attention.WAITING_USER: "aguardando"}
        return Urgente(p.provider_id, p.nome, motivo[s.attention], s)
    alertas = [p for p in provs if p.estado == "alerta"]
    if alertas:
        p = max(alertas, key=lambda x: (x.quota_max or 0, x.provider_id))
        return Urgente(p.provider_id, p.nome, "alerta")
    return None


def resumir(store, providers: Iterable[Tuple[str, str]]) -> ResumoGlobal:
    """`providers`: (provider_id, nome) na ordem em que aparecem na tela."""
    provs, sessoes_por = [], {}
    for pid, nome in providers:
        sessoes = store.sessions(pid)
        sessoes_por[pid] = sessoes
        provs.append(resumir_provider(pid, nome, store.snapshot(pid), sessoes))
    return ResumoGlobal(
        providers=tuple(provs),
        total_sessoes=sum(p.sessoes for p in provs),
        trabalhando=sum(p.trabalhando for p in provs),
        aguardando=sum(p.aguardando for p in provs),
        erros=sum(p.erros for p in provs),
        mais_urgente=_mais_urgente(provs, sessoes_por),
    )
