"""Da camada de estado para a tela: o estado normalizado do Claude vira o UsoClaude de sempre.

O layout (layout_ombro.py, render.py) e o cloud (mascote.py) continuam recebendo o UsoClaude, então
nada visual muda; só que os dados agora chegam pelo provider e pelo StateStore. Este é o único
lugar que conhece os dois lados. Um layout multiagente, no futuro, pode ler o core direto.
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence, Tuple

from coletores.claude import Janela, UsoClaude, resumo_sessoes
from config import PROVIDERS
from core.models import Activity, AgentSession, Attention, ProviderSnapshot
from core.resumo import resumir_provider
from layout_multiagente import DETALHE_DA_CONDICAO, ROTULOS, estado_sessao
from layout_ombro import _desde
from providers.claude import JANELAS
from render import tokens

_Item = namedtuple("_Item", "status desde projeto")


def status_tela(s: AgentSession) -> str:
    """Estado normalizado -> status de sempre da tela ("busy", "waiting" ou "idle").

    Esperar você (resposta ou aprovação) vem antes de tudo: a bolinha âmbar e o cloud "esperando";
    depois, trabalhando. Desconhecido e erro aparecem como ocioso, como um status estranho do Claude
    Code sempre apareceu (o layout multiagente mostra o erro; os outros ficam como eram).
    """
    if s.attention in (Attention.WAITING_USER, Attention.WAITING_APPROVAL):
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
        u.escopo = q.scope
        for window_id, _, campo in JANELAS:
            w = q.window(window_id)
            if w is not None and w.used_percent is not None:
                setattr(u, campo, Janela(w.used_percent, w.resets_at))
            elif w is not None and w.expired and w.resets_at is not None:
                u.expiradas[campo] = w.resets_at
    if snap.usage is not None:
        m = snap.usage
        u.tokens_entrada, u.tokens_saida = m.input_tokens or 0, m.output_tokens or 0
        u.tokens_cache, u.mensagens_hoje = m.cache_tokens or 0, m.messages or 0
        if m.hourly_tokens is not None:
            u.tokens_por_hora = list(m.hourly_tokens)
    for nome, valor in resumo_sessoes(_Item(status_tela(s), s.status_since, s.project) for s in snap.sessions).items():
        setattr(u, nome, valor)
    return u


def uso_mascote(resumo, snap_claude: Optional[ProviderSnapshot]) -> UsoClaude:
    """Para o cloud do layout multiagente: os limites do Claude (alerta e reset, como sempre) com as
    contagens de todos os agentes. Ele espera se qualquer um espera você (ou parou com erro) e trabalha
    se qualquer um trabalha; a máquina de estados e as animações são as mesmas de sempre."""
    u = uso_claude(snap_claude)
    u.sessoes = resumo.total_sessoes
    u.trabalhando = resumo.trabalhando
    u.esperando = resumo.aguardando + resumo.erros
    urg = resumo.mais_urgente
    if urg is not None and urg.sessao is not None:
        u.espera_projeto = urg.sessao.project or urg.nome
        u.espera_desde = urg.sessao.status_since
    return u


# ---------- um agente escolhido para um layout de um agente só ----------
# O Claude, ligado, segue pelo caminho de sempre (uso_claude). Qualquer outro (e o Claude desligado, ou o automático
# sem candidato) passa por aqui: só o que aquele agente informa, com o que falta marcado como "—", nunca como zero,
# e nunca com dados de outro agente.

_CLASSE = {Attention.ERROR: 0, Attention.WAITING_APPROVAL: 1, Attention.WAITING_USER: 2}
# estados em que não há uma contagem de agora a mostrar
SEM_CONTAGEM = ("desligado", "sem_agente", "nao_instalado", "nao_configurado", "indisponivel", "desconectado",
                "sem_plugin", "incompativel", "conflito", "nao_verificado", "coletando")
_ESTADOS_NORMAIS = ("trabalhando", "ocioso", "sem_sessoes")
MENSAGENS = {
    "desligado": "ligue em Agentes e reinicie",
    "sem_agente": "nenhum agente com sessão agora",
    "nao_instalado": "não encontrado neste Mac",
    "nao_configurado": "integração do Ombro não instalada",
    "indisponivel": "a coleta falhou; veja o log",
    "desconectado": "só o último estado conhecido",
    "desconhecido": "estado não confirmado",
}
_TONS = {"erro": "erro", "aprovacao": "alerta", "aguardando": "alerta", "alerta": "erro", "trabalhando": "ativo",
         "ocioso": "ok", "incompativel": "alerta", "conflito": "alerta", "nao_verificado": "alerta",
         "indisponivel": "alerta"}


def _classe(s: AgentSession) -> int:
    """Erro, aprovação, espera; depois trabalhando; depois parada; por último, sem notícias."""
    if s.attention in _CLASSE:
        return _CLASSE[s.attention]
    return {Activity.WORKING: 3, Activity.IDLE: 4}.get(s.activity, 5)


def _prioridade(s: AgentSession) -> tuple:
    quando = s.updated_at or s.status_since
    return _classe(s), -quando.timestamp() if quando else float("inf")


def escolher_automatico(por_agente: Iterable[Tuple[str, Sequence[AgentSession]]]) -> Optional[str]:
    """O agente do "automatic": entre os ligados (os que vêm em `por_agente`) com uma sessão que não está desconectada,
    o da sessão que mais precisa de você; depois quem trabalha; depois a mais recente; no empate, a ordem de PROVIDERS.
    None: ninguém tem sessão agora. Função só das entradas: sem relógio, sem memória, sem preferir o Claude."""
    candidatos = []
    for pid, sessoes in por_agente:
        atuais = [s for s in sessoes if not s.disconnected]
        if atuais:
            candidatos.append((min(_prioridade(s) for s in atuais), PROVIDERS.index(pid), pid))
    return min(candidatos)[2] if candidatos else None


def condicao_de_agora(estado: str, sessoes: Sequence[AgentSession]) -> str:
    """O estado de core.resumo, sem chamar de ocioso o que não está confirmado: com uma sessão sem notícias e nada de
    agora que valha mais, é "desconhecido"."""
    if estado == "ocioso" and any(_ponto(s) == "unknown" for s in sessoes if not s.disconnected):
        return "desconhecido"
    return estado


def claude_sem_dados(estado: str, snap: Optional[ProviderSnapshot]) -> bool:
    """O Claude ligado só sai do desenho de sempre quando não há nada dele para mostrar: não instalado, sem a
    integração, ou a coleta falhando sem limites nem tokens."""
    return estado in ("nao_instalado", "nao_configurado") or (
        estado == "indisponivel" and (snap is None or (snap.quotas is None and snap.usage is None)))


def marcar_sem_confirmacao(u: UsoClaude, snap: Optional[ProviderSnapshot], estado: str) -> UsoClaude:
    """No desenho de sempre do Claude, diz o que não está confirmado: sem sessões, sessões sem notícias ou a coleta
    falhando. Os limites e os tokens dele continuam. Com tudo confirmado, `u` não muda em nada."""
    todas = snap.sessions if snap is not None else ()
    atuais = [s for s in todas if not s.disconnected]
    incertas = sum(1 for s in atuais if _ponto(s) == "unknown")
    condicao = ("indisponivel" if estado == "indisponivel" else "sem_sessoes" if not atuais
                else "desconhecido" if incertas else "")
    if not condicao:
        return u
    if len(atuais) != len(todas):  # o que é só o último estado conhecido não conta como sessão de agora
        for nome, valor in resumo_sessoes(_Item(status_tela(s), s.status_since, s.project) for s in atuais).items():
            setattr(u, nome, valor)
    u.status_sessoes = ["unknown" if _ponto(s) == "unknown" else status_tela(s) for s in atuais]
    u.condicao, u.sem_noticias = condicao, incertas
    u.sem_tokens = snap is None or snap.usage is None
    return u


def _ponto(s: AgentSession) -> str:
    if s.attention is Attention.ERROR:
        return "error"
    if s.activity is Activity.UNKNOWN and s.attention not in _CLASSE:
        return "unknown"
    return status_tela(s)


@dataclass(frozen=True)
class VisaoAgente:
    """O que se sabe do agente de um layout, numa coleta."""
    provider: Optional[str]  # None: o automático não achou ninguém
    nome: str
    estado: str  # um de core.resumo.ESTADOS, "desligado" ou "sem_agente"
    automatico: bool = False
    sessoes: Tuple[AgentSession, ...] = ()
    informa_espera: bool = True  # falso: o agente não conta quando espera você (o Cursor)
    tokens_total: Optional[int] = None
    tokens_saida: Optional[int] = None

    @property
    def atuais(self) -> Tuple[AgentSession, ...]:
        return tuple(s for s in self.sessoes if not s.disconnected)


def visao_agente(pid: Optional[str], nome: str, ligado: bool, snap: Optional[ProviderSnapshot],
                 sessoes: Sequence[AgentSession], informa_espera: bool = True, automatico: bool = False) -> VisaoAgente:
    if pid is None:
        return VisaoAgente(None, "nenhum agente", "sem_agente", True)
    if not ligado:
        return VisaoAgente(pid, nome, "desligado", automatico)
    m = snap.usage if snap is not None else None
    total = None
    if m is not None and (m.input_tokens is not None or m.output_tokens is not None):
        total = (m.input_tokens or 0) + (m.output_tokens or 0)
    estado = condicao_de_agora(resumir_provider(pid, nome, snap, sessoes).estado, sessoes)
    return VisaoAgente(pid, nome, estado, automatico, tuple(sessoes),
                       informa_espera, total, m.output_tokens if m is not None else None)


def uso_do_agente(v: VisaoAgente) -> UsoClaude:
    """O que o mascote lê: só as sessões de agora deste agente. Sem limites de uso (nada de alerta nem reset de outro).
    Um erro não conta como espera, como sempre no Claude: ele aparece em texto."""
    u = UsoClaude()
    atuais = v.atuais
    for nome, valor in resumo_sessoes(_Item(status_tela(s), s.status_since, s.project) for s in atuais).items():
        setattr(u, nome, valor)
    return u


@dataclass(frozen=True)
class FixoAgente:
    """A parte parada de um layout quando o agente não é o Claude de sempre, já em texto: serve aos três desenhos e é a
    chave do cache deles (o agente e a condição fazem parte)."""
    titulo: str  # "codex", ou "agentes" sem candidato
    nome: str
    modo: str  # "automático" ou "agente"
    estado: str
    rotulo: str
    tom: str  # "erro", "alerta", "ativo", "ok" ou "neutro"
    mensagem: str
    conta: str  # sessões de agora, ou "—"
    resumo: str
    pontos: tuple  # "error", "waiting", "busy", "idle" ou "unknown", um por sessão de agora
    linhas: tuple  # (projeto, estado, tom, há quanto tempo)
    tokens: str
    saida: str
    rodape: str
    tom_rodape: str


def _tom_da_sessao(s: AgentSession) -> str:
    return {"error": "erro", "waiting": "alerta", "busy": "ativo", "idle": "ok"}.get(
        "unknown" if s.disconnected else _ponto(s), "neutro")


def quadro_agente(v: VisaoAgente, agora: datetime) -> FixoAgente:
    atuais, antigas = v.atuais, len(v.sessoes) - len(v.atuais)
    if v.estado == "desligado":
        rotulo = "desligado"
    elif v.estado == "sem_agente":
        rotulo = "sem sessões"
    else:
        rotulo = ROTULOS[v.estado][0]
    mensagem = MENSAGENS.get(v.estado) or DETALHE_DA_CONDICAO.get(v.estado, "")
    if v.estado == "sem_sessoes":
        mensagem = f"{antigas} de origem desconectada" if antigas else "nenhuma sessão aberta"
    contar = bool(atuais) or v.estado not in SEM_CONTAGEM
    trabalhando = sum(1 for s in atuais if s.activity is Activity.WORKING)
    esperando = sorted((s for s in atuais if s.attention in (Attention.WAITING_USER, Attention.WAITING_APPROVAL)),
                       key=lambda s: s.status_since or datetime.max)
    erros = [s for s in atuais if s.attention is Attention.ERROR]
    if atuais:
        resumo = f"{trabalhando} trabalhando · {len(esperando)} esperando"
    else:
        resumo = f"{antigas} só do último estado" if antigas else ""
    if esperando:
        s = esperando[0]
        quanto = f" {_desde(s.status_since, agora)}" if s.status_since else ""
        rodape = f"{(s.project or 'sessão')[:24]} espera{quanto}".replace("espera há instantes", "espera agora")
        tom_rodape = "alerta"
    elif erros:
        rodape, tom_rodape = f"{(erros[0].project or 'sessão')[:24]} parou com erro", "erro"
    elif v.estado in _ESTADOS_NORMAIS + ("desconhecido",) and any(s.activity is Activity.UNKNOWN for s in atuais):
        rodape, tom_rodape = "há sessão sem notícias", "neutro"
    elif v.estado in _ESTADOS_NORMAIS and not v.informa_espera:
        rodape, tom_rodape = f"{v.nome} não informa esperas", "neutro"
    elif v.estado in _ESTADOS_NORMAIS:
        rodape, tom_rodape = "nenhuma sessão esperando", "ok"
    else:
        rodape, tom_rodape = f"{v.nome}: {rotulo}", "neutro"
    ordem = sorted(v.sessoes, key=lambda s: (s.disconnected, _prioridade(s), s.session_id))
    linhas = tuple((s.project or "sessão", estado_sessao(s)[0], _tom_da_sessao(s),
                    _desde(s.status_since, agora) if s.status_since and not s.disconnected else "") for s in ordem[:3])
    return FixoAgente(
        v.provider or "agentes", v.nome, "automático" if v.automatico else "agente", v.estado, rotulo,
        _TONS.get(v.estado, "neutro"), mensagem, str(len(atuais)) if contar else "—", resumo,
        tuple(_ponto(s) for s in atuais[:12]), linhas,
        tokens(v.tokens_total) if v.tokens_total is not None else "—",
        tokens(v.tokens_saida) if v.tokens_saida is not None else "—", rodape, tom_rodape)
