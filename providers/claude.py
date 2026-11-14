"""Provider do Claude Code: o coletor de sempre (coletores/claude.py), no formato do core.

A leitura dos arquivos continua toda no coletor: aqui só se converte o UsoClaude em
ProviderSnapshot. As regras de antes valem iguais: fonte de uso mais recente, números com mais de
30 min marcados como velhos, janela já reiniciada descartada, tokens do dia sem repetir mensagem,
sessões só com processo vivo.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from core.models import (
    Activity, AgentSession, Attention, ProviderSnapshot, QuotaReport, QuotaWindow, SnapshotStatus, UsageMetrics,
)
from core.providers import ProviderAdapter, ProviderCapabilities

PROVIDER_ID = "claude"
JANELAS = (("5h", "sessão 5h", "cinco_horas"), ("7d", "semana", "semana"))  # (window_id, rótulo, campo do UsoClaude)
ETAPAS = {"uso do plano": "quotas", "tokens": "usage", "sessões": "sessions"}  # etapa do coletor -> parte do snapshot
STATUS = {  # status do sessions/<pid>.json -> (atividade, atenção)
    "busy": (Activity.WORKING, Attention.NONE),
    "waiting": (Activity.IDLE, Attention.WAITING_USER),
    "idle": (Activity.IDLE, Attention.NONE),
}


def sessao(s) -> AgentSession:
    """SessaoClaude -> AgentSession. Um status que o Ombro não conhece fica desconhecido (a tela mostra como ociosa)."""
    atividade, atencao = STATUS.get(s.status_bruto, (Activity.UNKNOWN, Attention.UNKNOWN))
    return AgentSession(PROVIDER_ID, s.id, atividade, atencao, project=s.projeto, started_at=s.iniciada,
                        status_since=s.desde, updated_at=s.atualizada)


def snapshot_de_uso(u, agora: datetime) -> ProviderSnapshot:
    """UsoClaude (do coletor ou do --demo) -> ProviderSnapshot."""
    falhas = frozenset(ETAPAS[nome] for nome in u.falhas if nome in ETAPAS)
    usage = None
    if "usage" not in falhas:
        usage = UsageMetrics("today", input_tokens=u.tokens_entrada, output_tokens=u.tokens_saida,
                             cache_tokens=u.tokens_cache, messages=u.mensagens_hoje, hourly_tokens=tuple(u.tokens_por_hora))
    quotas = None
    if u.configurado and u.fonte and u.coletado_em:
        janelas = tuple(QuotaWindow(wid, rotulo, j.pct, j.reseta_em)
                        for wid, rotulo, campo in JANELAS for j in (getattr(u, campo),) if j is not None)
        quotas = QuotaReport(u.fonte, u.coletado_em, stale=u.desatualizado, windows=janelas)
    return ProviderSnapshot(
        PROVIDER_ID, agora,
        status=SnapshotStatus.PARTIAL if falhas else SnapshotStatus.OK,
        sessions=tuple(sessao(s) for s in u.lista_sessoes),
        usage=usage, quotas=quotas, failed=falhas, errors=tuple(u.erros),
    )


class ClaudeProvider(ProviderAdapter):
    provider_id = PROVIDER_ID
    display_name = "Claude Code"
    capabilities = ProviderCapabilities(sessions=True, attention=True, token_usage=True, quota_windows=True)

    def __init__(self, fonte, relogio: Callable[[], datetime] = datetime.now):
        """`fonte`: qualquer coisa com `ler() -> UsoClaude`; o ColetorClaude, ou a FonteDemo do --demo."""
        self.fonte = fonte
        self._relogio = relogio

    @classmethod
    def da_pasta(cls, claude_dir: Optional[str] = None) -> "ClaudeProvider":
        from coletores.claude import ColetorClaude

        return cls(ColetorClaude(claude_dir))

    def collect(self) -> ProviderSnapshot:
        return snapshot_de_uso(self.fonte.ler(), self._relogio())


class FonteDemo:
    """Os dados inventados do --demo, pelo mesmo caminho dos de verdade."""

    def __init__(self, demo, ciclo: float = 0):
        self.demo, self.ciclo = demo, ciclo

    def ler(self):
        return self.demo.claude(self.ciclo)
