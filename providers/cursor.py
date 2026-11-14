"""Provider do Cursor, pelos hooks oficiais (~/.cursor/hooks.json).

Eventos usados (https://cursor.com/docs/hooks): sessionStart, beforeSubmitPrompt, postToolUse,
postToolUseFailure, stop e sessionEnd. O id da sessão é o `conversation_id` (o mesmo `session_id`
do sessionStart). Só eventos que não bloqueiam nada: hooks como preToolUse e beforeShellExecution
tratam uma saída inválida como recusa, então o Ombro fica longe deles.

O Cursor não tem evento de "esperando você aprovar", nem dá tokens ou limites aos hooks: a atenção das
sessões fica desconhecida (exceto quando o agente para com erro) e as métricas, indisponíveis.
"""
from __future__ import annotations

from typing import Optional

from core.caixa import EventoLocal
from core.models import Activity, AgentSession, Attention
from core.providers import ProviderCapabilities
from core.rastreador import Transicao

from .hooks import ProviderPorHooks

PROVIDER_ID = "cursor"


class CursorProvider(ProviderPorHooks):
    provider_id = PROVIDER_ID
    display_name = "Cursor"
    capabilities = ProviderCapabilities(sessions=True, events=True)
    executaveis = ("cursor-agent",)
    apps = ("/Applications/Cursor.app",)
    configs = ("~/.cursor/hooks.json",)

    def mapear(self, ev: EventoLocal, atual: Optional[AgentSession]) -> Optional[Transicao]:
        nome = ev.event
        if nome == "sessionEnd":
            return Transicao(fim=True)
        if nome == "sessionStart":
            return Transicao(Activity.IDLE, Attention.UNKNOWN) if atual is None else None
        if nome in ("beforeSubmitPrompt", "postToolUse", "postToolUseFailure"):
            return Transicao(Activity.WORKING, Attention.UNKNOWN)
        if nome == "stop":
            return Transicao(Activity.IDLE, Attention.ERROR if ev.detail == "error" else Attention.UNKNOWN)
        return None
