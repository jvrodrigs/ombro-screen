"""Da camada de estado para a tela: o estado normalizado do Claude vira o UsoClaude de sempre.

O layout (layout_ombro.py, render.py) e o cloud (mascote.py) continuam recebendo o UsoClaude, então
nada visual muda; só que os dados agora chegam pelo provider e pelo StateStore. Este é o único
lugar que conhece os dois lados. Um layout multiagente, no futuro, pode ler o core direto.
"""
from __future__ import annotations

from collections import namedtuple
from typing import Optional

from coletores.claude import Janela, UsoClaude, resumo_sessoes
from core.models import Activity, AgentSession, Attention, ProviderSnapshot
from providers.claude import JANELAS

_Item = namedtuple("_Item", "status desde projeto")


def status_tela(s: AgentSession) -> str:
    """Estado normalizado -> status de sempre da tela ("busy", "waiting" ou "idle").

    Esperar você vem antes de tudo (a bolinha âmbar e o cloud "esperando"); depois, trabalhando.
    Desconhecido aparece como ocioso, como um status estranho do Claude Code sempre apareceu.
    """
    if s.attention is Attention.WAITING_USER:
        return "waiting"
    if s.activity is Activity.WORKING:
        return "busy"
    return "idle"


def uso_claude(snap: Optional[ProviderSnapshot]) -> UsoClaude:
    """O snapshot efetivo do Claude (o do StateStore) no formato que o layout e o cloud usam."""
    u = UsoClaude()
    if snap is None:
        return u
    u.erros = list(snap.errors)
    if snap.quotas is not None:
        q = snap.quotas
        u.configurado, u.fonte, u.coletado_em, u.desatualizado = True, q.source, q.fetched_at, q.stale
        for window_id, _, campo in JANELAS:
            w = q.window(window_id)
            if w is not None and w.used_percent is not None:
                setattr(u, campo, Janela(w.used_percent, w.resets_at))
    if snap.usage is not None:
        m = snap.usage
        u.tokens_entrada, u.tokens_saida = m.input_tokens or 0, m.output_tokens or 0
        u.tokens_cache, u.mensagens_hoje = m.cache_tokens or 0, m.messages or 0
        if m.hourly_tokens is not None:
            u.tokens_por_hora = list(m.hourly_tokens)
    for nome, valor in resumo_sessoes(_Item(status_tela(s), s.status_since, s.project) for s in snap.sessions).items():
        setattr(u, nome, valor)
    return u
