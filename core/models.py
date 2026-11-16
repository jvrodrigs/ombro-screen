"""Modelo unificado: o que um provider (hoje só o Claude Code) entrega ao core.

Só biblioteca padrão: nada de Pillow, USB ou macOS. Nada de apresentação também (cor do cloud,
animação, posição na tela): isso é da camada de apresentação. Os modelos são imutáveis
(`frozen`), então dois snapshots com o mesmo conteúdo são iguais e dá para deduplicar.

Horários são `datetime` sem fuso, na hora local, como o resto do Ombro.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import FrozenSet, Optional, Tuple

PARTES = ("sessions", "usage", "quotas")  # partes de um snapshot que podem falhar sozinhas


class Activity(str, Enum):
    """O que a sessão está fazendo."""
    UNKNOWN = "unknown"  # o provider não disse, ou o Ombro ainda não confirmou (depois de reiniciar)
    IDLE = "idle"
    WORKING = "working"


class Attention(str, Enum):
    """Se a sessão precisa de você. Separado de Activity: uma sessão parada pode ou não esperar você."""
    UNKNOWN = "unknown"
    NONE = "none"
    WAITING_USER = "waiting_user"  # espera você (uma resposta, ou uma permissão quando a fonte não diz qual)
    WAITING_APPROVAL = "waiting_approval"  # espera você aprovar uma ação (quando a fonte distingue)
    ERROR = "error"  # parou por erro e vale a pena olhar


INTEGRACOES = ("ok", "not_installed", "not_configured")  # a ferramenta está instalada? a integração está ligada?


class SnapshotStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"  # algumas partes falharam (ver ProviderSnapshot.failed)
    ERROR = "error"  # a coleta toda falhou: nenhum dado novo


def _texto(nome: str, v) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{nome} deve ser um texto não vazio (veio {v!r})")
    return v


def _hora(nome: str, v, opcional: bool = True) -> Optional[datetime]:
    if v is None and opcional:
        return None
    if not isinstance(v, datetime):
        raise TypeError(f"{nome} deve ser um datetime (veio {type(v).__name__})")
    return v


def _inteiro(nome: str, v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"{nome} deve ser um inteiro maior ou igual a 0 (veio {v!r})")
    return v


def _percentual(nome: str, v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v) or not 0 <= v <= 100:
        raise ValueError(f"{nome} deve estar entre 0 e 100 (veio {v!r})")
    return float(v)


def _definir(obj, nome: str, valor) -> None:
    object.__setattr__(obj, nome, valor)  # normalizar campos de um dataclass congelado


@dataclass(frozen=True)
class AgentSession:
    """Uma sessão de um agente. A chave é (provider_id, session_id): ids só são únicos dentro do provider."""
    provider_id: str
    session_id: str  # estável enquanto a sessão existe (nunca sorteado a cada coleta)
    activity: Activity = Activity.UNKNOWN
    attention: Attention = Attention.UNKNOWN
    project: Optional[str] = None  # nome curto da pasta; é sensível, então não vai para o banco
    started_at: Optional[datetime] = None
    status_since: Optional[datetime] = None  # desde quando está no status atual
    updated_at: Optional[datetime] = None  # última atualização vista na fonte

    def __post_init__(self):
        _texto("provider_id", self.provider_id)
        _texto("session_id", self.session_id)
        _definir(self, "activity", Activity(self.activity))
        _definir(self, "attention", Attention(self.attention))
        if self.project is not None:
            _texto("project", self.project)
        for nome in ("started_at", "status_since", "updated_at"):
            _hora(nome, getattr(self, nome))

    @property
    def key(self) -> Tuple[str, str]:
        return self.provider_id, self.session_id


@dataclass(frozen=True)
class UsageMetrics:
    """Uso de tokens num período (`scope`). Campo None = o provider não informa; não é zero."""
    scope: str  # "today": o dia local de hoje. Números de providers diferentes não são comparáveis entre si.
    input_tokens: Optional[int] = None  # no Claude Code, inclui o cache (leitura + escrita)
    output_tokens: Optional[int] = None
    cache_tokens: Optional[int] = None
    messages: Optional[int] = None
    hourly_tokens: Optional[Tuple[Optional[int], ...]] = None  # 24 valores (0h..23h); None = hora que não chegou
    context_percent: Optional[float] = None  # uso da janela de contexto; o Claude Code não informa hoje
    cost_usd: Optional[float] = None  # custo; o Claude Code não informa hoje

    def __post_init__(self):
        _texto("scope", self.scope)
        for nome in ("input_tokens", "output_tokens", "cache_tokens", "messages"):
            _inteiro(nome, getattr(self, nome))
        if self.hourly_tokens is not None:
            horas = tuple(self.hourly_tokens)
            if len(horas) != 24:
                raise ValueError(f"hourly_tokens deve ter 24 valores (veio {len(horas)})")
            for h in horas:
                _inteiro("hourly_tokens", h)
            _definir(self, "hourly_tokens", horas)
        _percentual("context_percent", self.context_percent)
        if self.cost_usd is not None and (isinstance(self.cost_usd, bool) or not isinstance(self.cost_usd, (int, float))
                                          or self.cost_usd < 0):
            raise ValueError(f"cost_usd deve ser um número maior ou igual a 0 (veio {self.cost_usd!r})")

    @property
    def total_tokens(self) -> Optional[int]:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass(frozen=True)
class QuotaWindow:
    """Uma janela de limite de uso (ex.: as 5 h e a semana do Claude). Só existe se há fonte válida."""
    window_id: str  # estável dentro do provider: "5h", "7d"
    label: str
    used_percent: Optional[float] = None
    resets_at: Optional[datetime] = None
    # a janela reiniciou depois da última observação: o percentual de antes não vale mais e o novo ainda não
    # chegou (nunca se assume 0%); `resets_at` é quando ela reiniciou
    expired: bool = False

    def __post_init__(self):
        _texto("window_id", self.window_id)
        _texto("label", self.label)
        _definir(self, "used_percent", _percentual("used_percent", self.used_percent))
        _hora("resets_at", self.resets_at)


@dataclass(frozen=True)
class QuotaReport:
    """De onde vieram os limites e quando. `windows` vazio é diferente de não ter fonte (quotas=None)."""
    source: str  # ex.: "statusline", "habblaud", "/usage"
    fetched_at: datetime
    stale: bool = False  # velho demais para confiar (cada provider define o prazo)
    windows: Tuple[QuotaWindow, ...] = ()
    # o que os percentuais cobrem: "account" (a assinatura inteira, no instante da observação) ou "unknown"
    scope: str = "unknown"
    # a fonte se atualiza sozinha? Falso: só há número novo quando o agente (o CLI) faz algo que o produz
    auto_update: bool = False

    def __post_init__(self):
        _texto("source", self.source)
        _hora("fetched_at", self.fetched_at, opcional=False)
        _definir(self, "windows", tuple(self.windows))
        ids = [w.window_id for w in self.windows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"janelas repetidas: {ids}")

    def window(self, window_id: str) -> Optional[QuotaWindow]:
        return next((w for w in self.windows if w.window_id == window_id), None)


@dataclass(frozen=True)
class ProviderSnapshot:
    """Tudo o que um provider sabia num instante.

    status OK sem sessões = não há sessões. PARTIAL = as partes em `failed` falharam e vieram vazias
    (não quer dizer que estão vazias de verdade). ERROR = nada novo; vale o que se sabia antes.
    """
    provider_id: str
    collected_at: datetime
    status: SnapshotStatus = SnapshotStatus.OK
    sessions: Tuple[AgentSession, ...] = ()
    usage: Optional[UsageMetrics] = None
    quotas: Optional[QuotaReport] = None
    failed: FrozenSet[str] = frozenset()
    errors: Tuple[str, ...] = ()  # mensagens curtas, sem conteúdo de arquivos
    integration: str = "ok"  # "not_installed" (a ferramenta não está aqui) ou "not_configured" (sem hooks do Ombro)

    def __post_init__(self):
        _texto("provider_id", self.provider_id)
        _hora("collected_at", self.collected_at, opcional=False)
        _definir(self, "status", SnapshotStatus(self.status))
        _definir(self, "sessions", tuple(self.sessions))
        _definir(self, "failed", frozenset(self.failed))
        _definir(self, "errors", tuple(self.errors))
        if self.integration not in INTEGRACOES:
            raise ValueError(f"integration deve ser um de: {', '.join(INTEGRACOES)} (veio {self.integration!r})")
        desconhecidas = self.failed - set(PARTES)
        if desconhecidas:
            raise ValueError(f"partes desconhecidas em failed: {sorted(desconhecidas)}")
        if self.status is SnapshotStatus.PARTIAL and not self.failed:
            raise ValueError("snapshot PARTIAL precisa dizer o que falhou")
        if self.status is SnapshotStatus.OK and self.failed:
            raise ValueError("snapshot OK não pode ter partes que falharam")
        chaves = set()
        for s in self.sessions:
            if s.provider_id != self.provider_id:
                raise ValueError(f"sessão de {s.provider_id} num snapshot de {self.provider_id}")
            if s.key in chaves:
                raise ValueError(f"sessão repetida no snapshot: {s.session_id}")
            chaves.add(s.key)

    def mesmo_conteudo(self, outro: Optional["ProviderSnapshot"]) -> bool:
        """Igual a `outro` em tudo, menos no horário da coleta."""
        return outro is not None and replace(outro, collected_at=self.collected_at) == self
