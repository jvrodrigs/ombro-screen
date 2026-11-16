"""Provider do Claude Code: o coletor de sempre (coletores/claude.py), no formato do core.

A leitura dos arquivos continua toda no coletor: aqui só se converte o UsoClaude em
ProviderSnapshot.

Com os hooks do Ombro ligados (opcional, ver docs/providers/claude.md), dois eventos refinam o que os
arquivos dizem, sem nunca contradizê-los: PermissionRequest separa "esperando aprovação" de "esperando
resposta" (nos arquivos, as duas são "waiting") e StopFailure marca a sessão parada por erro. Só vale
um evento mais novo que o status atual da sessão no arquivo; sem hooks, tudo segue como antes. As regras de antes valem iguais: fonte de uso mais recente, números com mais de
30 min marcados como velhos, janela já reiniciada descartada, tokens do dia sem repetir mensagem,
sessões só com processo vivo.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, Optional

from core.models import (
    Activity, AgentSession, Attention, ProviderSnapshot, QuotaReport, QuotaWindow, SnapshotStatus, UsageMetrics,
)
from core.caixa import EventoLocal
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
        janelas = []
        for wid, rotulo, campo in JANELAS:
            j, reiniciou = getattr(u, campo), getattr(u, "expiradas", {}).get(campo)
            if j is not None:
                janelas.append(QuotaWindow(wid, rotulo, j.pct, j.reseta_em))
            elif reiniciou is not None:  # reiniciou depois da última observação: sem número até a próxima
                janelas.append(QuotaWindow(wid, rotulo, None, reiniciou, expired=True))
        quotas = QuotaReport(u.fonte, u.coletado_em, stale=u.desatualizado, windows=tuple(janelas),
                             scope=getattr(u, "escopo", "unknown"))
    return ProviderSnapshot(
        PROVIDER_ID, agora,
        status=SnapshotStatus.PARTIAL if falhas else SnapshotStatus.OK,
        sessions=tuple(sessao(s) for s in u.lista_sessoes),
        usage=usage, quotas=quotas, failed=falhas, errors=tuple(u.erros),
    )


class ClaudeProvider(ProviderAdapter):
    provider_id = PROVIDER_ID
    display_name = "Claude Code"
    capabilities = ProviderCapabilities(sessions=True, attention=True, token_usage=True, quota_windows=True, events=True)
    FOLGA = timedelta(seconds=2)  # o hook e o arquivo da sessão não são gravados no mesmo instante

    def __init__(self, fonte, relogio: Callable[[], datetime] = datetime.now):
        """`fonte`: qualquer coisa com `ler() -> UsoClaude`; o ColetorClaude, ou a FonteDemo do --demo."""
        self.fonte = fonte
        self._relogio = relogio
        self._aprovacao: Dict[str, datetime] = {}  # sessão -> último PermissionRequest
        self._erro: Dict[str, datetime] = {}  # sessão -> último StopFailure
        self.ultimo_evento: Optional[datetime] = None

    def receber(self, eventos: Iterable[EventoLocal]) -> int:
        n = 0
        for ev in sorted(eventos, key=lambda e: e.quando):
            if ev.provider != PROVIDER_ID:
                continue
            n += 1
            self.ultimo_evento = max(self.ultimo_evento or ev.quando, ev.quando)
            if ev.event == "PermissionRequest":
                self._aprovacao[ev.session] = ev.quando
            elif ev.event == "StopFailure":
                self._erro[ev.session] = ev.quando
            elif ev.event == "Stop":
                self._erro.pop(ev.session, None)
            elif ev.event == "SessionEnd":
                self._aprovacao.pop(ev.session, None)
                self._erro.pop(ev.session, None)
        return n

    def _refinar(self, s: AgentSession) -> AgentSession:
        """Mais detalhe para uma sessão que os arquivos já descrevem, se um hook recente disser."""
        sid = s.session_id.split("#")[0]  # "id#pid" quando a mesma conversa está em dois processos
        desde = s.status_since

        def recente(t: Optional[datetime]) -> bool:
            return t is not None and (desde is None or t >= desde - self.FOLGA)

        if s.attention is Attention.WAITING_USER and recente(self._aprovacao.get(sid)):
            return replace(s, attention=Attention.WAITING_APPROVAL)
        if s.activity is Activity.IDLE and s.attention is Attention.NONE and recente(self._erro.get(sid)):
            return replace(s, attention=Attention.ERROR)
        return s

    @classmethod
    def da_pasta(cls, claude_dir: Optional[str] = None) -> "ClaudeProvider":
        from coletores.claude import ColetorClaude

        return cls(ColetorClaude(claude_dir))

    def collect(self) -> ProviderSnapshot:
        snap = snapshot_de_uso(self.fonte.ler(), self._relogio())
        if not (self._aprovacao or self._erro):
            return snap
        return replace(snap, sessions=tuple(self._refinar(s) for s in snap.sessions))


class FonteDemo:
    """Os dados inventados do --demo, pelo mesmo caminho dos de verdade."""

    def __init__(self, demo, ciclo: float = 0):
        self.demo, self.ciclo = demo, ciclo

    def ler(self):
        return self.demo.claude(self.ciclo)
