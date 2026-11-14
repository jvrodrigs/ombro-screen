"""Provider do OpenAI Codex, pelos hooks oficiais (~/.codex/hooks.json).

Eventos usados (https://learn.chatgpt.com/docs/hooks): SessionStart, UserPromptSubmit,
PermissionRequest, PostToolUse, PostModelRequest, Stop, Interrupt e SessionEnd. O id da sessão é o
`session_id` que o Codex manda (o mesmo em todos os eventos da sessão; subagentes usam o do pai).

Não usa o App Server: ele exige que o Ombro abra o seu próprio processo do Codex, só observa as
threads que esse processo abrir (não as do seu terminal ou do app) e a documentação não garante
que ler limites não tenha efeito. Por isso tokens e limites do Codex ficam indisponíveis. Também não
lê os arquivos internos de sessão (~/.codex/sessions), que a própria OpenAI diz não serem estáveis.
"""
from __future__ import annotations

from typing import Optional

from core.caixa import EventoLocal
from core.models import Activity, AgentSession, Attention
from core.providers import ProviderCapabilities
from core.rastreador import Transicao

from .hooks import ProviderPorHooks

PROVIDER_ID = "codex"
NORMAL = Transicao(Activity.IDLE, Attention.NONE)
TRABALHANDO = Transicao(Activity.WORKING, Attention.NONE)


class CodexProvider(ProviderPorHooks):
    provider_id = PROVIDER_ID
    display_name = "Codex"
    capabilities = ProviderCapabilities(sessions=True, attention=True, events=True)
    executaveis = ("codex",)
    apps = ("/Applications/Codex.app",)
    configs = ("~/.codex/hooks.json", "~/.codex/config.toml")

    def mapear(self, ev: EventoLocal, atual: Optional[AgentSession]) -> Optional[Transicao]:
        nome = ev.event
        if nome == "SessionEnd":
            return Transicao(fim=True)
        if nome == "SessionStart":
            return NORMAL if atual is None else None  # retomar uma sessão não diz o que ela está fazendo
        if nome in ("UserPromptSubmit", "PostToolUse"):
            return TRABALHANDO  # PostToolUse depois de uma aprovação: voltou a trabalhar
        if nome == "PermissionRequest":
            return Transicao(Activity.WORKING, Attention.WAITING_APPROVAL)  # o turno segue aberto, parado em você
        if nome == "PostModelRequest":
            return Transicao(attention=Attention.ERROR) if ev.detail == "failed" else None
        if nome in ("Stop", "Interrupt"):
            erro = atual is not None and atual.attention is Attention.ERROR and nome == "Stop"
            return Transicao(Activity.IDLE, Attention.ERROR if erro else Attention.NONE)  # o erro fica até o próximo prompt
        return None
