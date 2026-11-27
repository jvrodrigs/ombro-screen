"""Codex, Cursor e OpenCode inventados, para o --demo mostrar o layout multiagente sem nenhum deles instalado.

Mesmos ids, nomes e capacidades dos providers de verdade; as sessões vêm de cenários fixos. Com
--demo-ciclo, trocam de cenário no mesmo ritmo do Claude de demonstração.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable, Sequence, Tuple

from core.models import Activity as A
from core.models import AgentSession
from core.models import Attention as T
from core.models import ProviderSnapshot
from core.providers import ProviderAdapter

from .codex import CodexProvider
from .cursor import CursorProvider
from .opencode import OpenCodeProvider

Cenario = Sequence[Tuple[str, str, A, T, int]]  # (id, projeto, atividade, atenção, minutos no estado)

CODEX: Sequence[Cenario] = (
    (("demo-codex-1", "api-gateway", A.WORKING, T.WAITING_APPROVAL, 3),),
    (("demo-codex-1", "api-gateway", A.WORKING, T.NONE, 1),),
    (("demo-codex-1", "api-gateway", A.IDLE, T.NONE, 4), ("demo-codex-2", "infra", A.WORKING, T.NONE, 0)),
)
CURSOR: Sequence[Cenario] = (
    (("demo-cursor-1", "site-docs", A.WORKING, T.UNKNOWN, 2),),
    (("demo-cursor-1", "site-docs", A.IDLE, T.UNKNOWN, 6),),
    (("demo-cursor-1", "site-docs", A.IDLE, T.ERROR, 1),),
)
OPENCODE: Sequence[Cenario] = (
    (("demo-opencode-1", "mobile-app", A.WORKING, T.NONE, 2), ("demo-opencode-2", "cli", A.IDLE, T.NONE, 7)),
    (("demo-opencode-1", "mobile-app", A.WORKING, T.NONE, 3),),
    (("demo-opencode-1", "mobile-app", A.IDLE, T.NONE, 1),),
)


class ProviderDemo(ProviderAdapter):
    def __init__(self, base, cenarios: Sequence[Cenario], ciclo: float = 0, relogio: Callable[[], datetime] = datetime.now):
        self.provider_id, self.display_name, self.capabilities = base.provider_id, base.display_name, base.capabilities
        self.cenarios, self.ciclo, self._relogio, self._t0 = cenarios, ciclo, relogio, time.time()

    def collect(self) -> ProviderSnapshot:
        agora = self._relogio()
        i = int((time.time() - self._t0) // self.ciclo) % len(self.cenarios) if self.ciclo > 0 else 0
        sessoes = tuple(AgentSession(self.provider_id, sid, a, t, project=proj, started_at=agora - timedelta(minutes=30),
                                     status_since=agora - timedelta(minutes=m), updated_at=agora)
                        for sid, proj, a, t, m in self.cenarios[i])
        return ProviderSnapshot(self.provider_id, agora, sessions=sessoes)


def codex_demo(ciclo: float = 0) -> ProviderDemo:
    return ProviderDemo(CodexProvider, CODEX, ciclo)


def cursor_demo(ciclo: float = 0) -> ProviderDemo:
    return ProviderDemo(CursorProvider, CURSOR, ciclo)


def opencode_demo(ciclo: float = 0) -> ProviderDemo:
    return ProviderDemo(OpenCodeProvider, OPENCODE, ciclo)
