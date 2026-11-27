"""Layout multiagente (480x320): quem está trabalhando e quem precisa de você, em todos os agentes.

  python3 tela.py --layout multiagente                     visão geral
  python3 tela.py --layout multiagente --vista focus --foco claude
  python3 tela.py --layout multiagente --vista attention

Três visões:
  overview   uma linha por agente (estado, sessões e um detalhe), CPU e RAM, a atividade recente
  focus      um agente: só as métricas que ele de fato informa (o Claude tem limites, tokens e a curva;
             Codex e Cursor, as sessões); nada de barra vazia para o que não existe
  attention  a sessão que mais precisa de você, em destaque; sem nenhuma, volta à visão geral
Com rotação ligada, alterna visão geral -> cada agente com sessões -> visão geral, e fica parada na
"attention" enquanto alguém precisa de você (sem piscar entre alertas).

Reaproveita o Minimal v2 (tema, fontes, barras, CPU e RAM, ícones, limites, a curva) e o cloud de
sempre. A tela é primeiro "planejada" como uma tupla de itens (texto, linha, bloco); se o plano é igual
ao do quadro anterior, a imagem é reaproveitada e só CPU, RAM e o cloud são redesenhados.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Optional, Tuple

from PIL import Image, ImageDraw

import layout_minimal_v2 as v2
from coletores.claude import Janela
from coletores.hardware import Hardware
from core.models import Activity, AgentSession, Attention
from core.resumo import ResumoGlobal, ResumoProvider
from layout_ombro import _desde, _reseta
from layout_minimal_v2 import CLOUD_H, CLOUD_W, CLOUD_X, CLOUD_Y, X0, X1, Tema, _texto
from mascote import Mascote
from render import aviso_uso, tokens

LARGURA, ALTURA = 480, 320
COL_ESTADO, COL_DIREITA = 112, 330  # colunas das linhas de agente
DETALHE_MAX = 36  # caracteres que cabem na linha de detalhe (10 px)
# as linhas da visão geral: (primeira, passo, nome, detalhe, divisória), conforme quantos agentes aparecem. Com até
# três, as medidas de sempre; com quatro, linhas mais juntas para o resumo (y 166) continuar no lugar
LINHAS_GERAL = {3: (36, 42, 11, 27, 39), 4: (33, 32, 8, 21, 28)}
DETALHE_DA_CONDICAO = {"incompativel": "atualize o OpenCode (2.4.2 ou mais)", "conflito": "veja o Diagnóstico no app",
                       "coletando": "lendo as sessões abertas", "sem_plugin": "ferramenta fechada ou sem o plugin",
                       "nao_verificado": "instância não comprovada: nada lido"}
# o estado de uma sessão cuja fonte saiu do ar: sempre com "último", nunca como se fosse o de agora
ULTIMO = {Attention.ERROR: "último: erro", Activity.IDLE: "último: ocioso"}

ROTULOS = {  # estado agregado -> (texto, cor, forma do marcador); o texto diz o estado mesmo sem cor
    "erro": ("parou com erro", Tema.VERMELHO, "triangulo"),
    "aprovacao": ("aguarda aprovação", Tema.AMARELO, "triangulo"),
    "aguardando": ("aguarda você", Tema.AMARELO, "triangulo"),
    "alerta": ("limite no fim", Tema.VERMELHO, "triangulo"),
    "trabalhando": ("trabalhando", Tema.VERDE, "cheio"),
    "ocioso": ("ocioso", Tema.SECUNDARIO, "anel"),
    "desconhecido": ("sem notícias", Tema.TERCIARIO, "anel"),
    "sem_sessoes": ("sem sessões", Tema.TERCIARIO, "anel"),
    "nao_configurado": ("não configurado", Tema.TERCIARIO, "anel"),
    "indisponivel": ("indisponível", Tema.AMARELO, "anel"),
    "nao_instalado": ("não instalado", Tema.TERCIARIO, "anel"),
    "incompativel": ("versão sem suporte", Tema.AMARELO, "anel"),
    "conflito": ("origem em conflito", Tema.AMARELO, "anel"),
    "coletando": ("lendo sessões", Tema.TERCIARIO, "anel"),
    "sem_plugin": ("sem o plugin", Tema.TERCIARIO, "anel"),
    "nao_verificado": ("não comprovado", Tema.AMARELO, "anel"),
    "desconectado": ("desconectado", Tema.TERCIARIO, "anel"),
}
MOTIVOS = {"erro": "parou com erro", "aprovacao": "aguarda aprovação", "aguardando": "aguarda você",
           "alerta": "limite de uso no fim"}


def _cortar(s: str, n: int) -> str:
    return s if len(s) <= n else s[:max(1, n - 1)] + "…"


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def estado_sessao(s: AgentSession) -> Tuple[str, tuple]:
    if s.disconnected:
        return ULTIMO.get(s.attention) or ULTIMO.get(s.activity) or "sem notícias", Tema.TERCIARIO
    if s.attention is Attention.ERROR:
        return "parou com erro", Tema.VERMELHO
    if s.attention is Attention.WAITING_APPROVAL:
        return "aguarda aprovação", Tema.AMARELO
    if s.attention is Attention.WAITING_USER:
        return "aguarda você", Tema.AMARELO
    if s.activity is Activity.WORKING:
        return "trabalhando", Tema.VERDE
    if s.activity is Activity.IDLE:
        return "ocioso", Tema.SECUNDARIO
    return "sem notícias", Tema.TERCIARIO


# ---------- escolha da visão ----------
class SeletorDeVista:
    """Qual visão desenhar agora. Determinístico: depende só do resumo e do relógio passado."""

    def __init__(self, vista: str = "overview", foco: Optional[str] = None, rotacao: bool = False, intervalo: float = 15.0):
        self.vista, self.foco, self.rotacao, self.intervalo = vista, foco, rotacao, max(5.0, intervalo)

    def escolher(self, resumo: ResumoGlobal, agora_mono: float) -> Tuple[str, Optional[str]]:
        if self.vista == "attention" or (self.rotacao and resumo.mais_urgente is not None):
            return ("attention", None) if resumo.mais_urgente is not None else ("overview", None)
        if self.rotacao:
            ciclo = ["overview"] + [p.provider_id for p in resumo.providers if p.ativo]
            atual = ciclo[int(agora_mono // self.intervalo) % len(ciclo)]
            return ("overview", None) if atual == "overview" else ("focus", atual)
        if self.vista == "focus":
            if self.foco and resumo.provider(self.foco) is not None:
                return "focus", self.foco
            return "overview", None
        return "overview", None


# ---------- o plano da parte parada ----------
@dataclass(frozen=True)
class Texto:
    x: float
    y: float
    s: str
    px: int
    cor: tuple
    peso: str = "Regular"
    ancora: str = "lm"


@dataclass(frozen=True)
class Linha:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Marcador:
    forma: str  # "cheio", "anel" ou "triangulo"
    x: float
    y: float
    cor: tuple


@dataclass(frozen=True)
class Bloco:
    """Uma parte desenhada pelo Minimal v2: "quotas", "estatisticas", "grafico" ou "sistema"."""
    tipo: str
    dados: tuple


def _cabecalho(titulo: str, sub: str, direita: str, cor: tuple, forma: str) -> list:
    w = v2.fonte("Bold", 13).getlength(titulo)
    itens = [Texto(X0, v2.CAB_Y, titulo, 13, Tema.TEXTO, "Bold"), Texto(X0 + w + 8, v2.CAB_Y, sub, 13, Tema.TERCIARIO)]
    largura = v2.fonte("Bold", 11).getlength(direita)
    itens += [Texto(X1, v2.CAB_Y, direita, 11, cor, "Bold", "rm"), Marcador(forma, X1 - largura - 9, v2.CAB_Y, cor),
              Linha(X0, v2.CAB_DIV, X1, v2.CAB_DIV)]
    return itens


def _urgencia(resumo: ResumoGlobal) -> Tuple[str, tuple]:
    """Texto e cor do canto direito quando alguém precisa de você."""
    n = resumo.aguardando + resumo.erros
    cor = Tema.VERMELHO if resumo.mais_urgente.motivo in ("erro", "alerta") else Tema.AMARELO
    if n == 0:
        return "LIMITE NO FIM", cor
    return ("1 PRECISA DE VOCÊ" if n == 1 else f"{n} PRECISAM DE VOCÊ"), cor


def _detalhe(p: ResumoProvider, agora: datetime) -> str:
    snap = p.snapshot
    if p.estado == "desconectado":
        antigas = snap.sessions if snap else ()
        erros = sum(1 for s in antigas if s.attention is Attention.ERROR)
        if erros:
            return f"último conhecido: {erros} com erro"
        if any(s.activity is Activity.IDLE for s in antigas):
            return "último conhecido: ocioso"
        return "último estado: sem notícias" if antigas else "OpenCode fechado ou sem o plugin"
    if p.estado == "sem_sessoes" and p.desconectadas:
        return f"{p.desconectadas} de origem desconectada"
    if p.estado in DETALHE_DA_CONDICAO:
        return DETALHE_DA_CONDICAO[p.estado]
    if p.estado == "nao_configurado":
        return "plugin do Ombro não configurado" if p.provider_id == "opencode" else "hooks do Ombro não configurados"
    if p.estado == "indisponivel":
        return "a coleta falhou; veja o log"
    if p.estado == "nao_instalado":
        return "não encontrado neste Mac"
    partes = []
    if snap is not None and snap.quotas is not None:
        for w in snap.quotas.windows:
            if w.used_percent is not None:
                partes.append(f"{w.window_id} {w.used_percent:.0f}%")
    if snap is not None and snap.usage is not None and snap.usage.total_tokens:
        partes.append(f"{tokens(snap.usage.total_tokens)} hoje")
    if partes:
        return " · ".join(partes)
    sessoes = [s for s in (snap.sessions if snap else ()) if (s.status_since or s.updated_at) and not s.disconnected]
    if sessoes:  # a sessão que mudou de estado por último, e há quanto tempo está assim
        s = max(sessoes, key=lambda x: x.status_since or x.updated_at)
        return f"{s.project or 'sessão'} · {_desde(s.status_since or s.updated_at, agora)}"
    return "nenhuma sessão aberta"


def _linhas_atividade(resumo: ResumoGlobal, agora: datetime, excluir=None, n: int = 2) -> list:
    sessoes = []
    for p in resumo.providers:
        for s in (p.snapshot.sessions if p.snapshot else ()):
            if (p.provider_id, s.session_id) == excluir:
                continue
            urg = {Attention.ERROR: 0, Attention.WAITING_APPROVAL: 1, Attention.WAITING_USER: 2}.get(s.attention, 3)
            quando = s.status_since or s.updated_at or datetime.min
            sessoes.append((urg, -quando.timestamp() if quando != datetime.min else 0, p, s))
    # primeiro o que é de agora; o histórico de uma fonte fora do ar vem depois
    sessoes.sort(key=lambda x: (x[3].disconnected, x[0], x[1], x[2].provider_id, x[3].session_id))
    linhas = []
    for _, _, p, s in sessoes[:n]:
        rotulo, cor = estado_sessao(s)
        desde = _desde(s.status_since, agora) if s.status_since else ""
        linhas.append((p.provider_id.upper(), _cortar(s.project or "sessão", 16), rotulo, cor, desde))
    return linhas


def _rodape(resumo: ResumoGlobal, agora: datetime) -> list:
    itens = [Linha(X0, v2.ROD_DIV, X1, v2.ROD_DIV)]
    urg = resumo.mais_urgente
    if urg is not None:
        motivo = MOTIVOS[urg.motivo]
        quem = urg.nome
        if urg.sessao is not None:
            quem += f" ({_cortar(urg.sessao.project or 'sessão', 18)})"
            if urg.sessao.status_since:
                motivo += " " + _desde(urg.sessao.status_since, agora)
        msg = f"{quem} {motivo}"
        cor = Tema.VERMELHO if urg.motivo in ("erro", "alerta") else Tema.AMARELO
        forma = "triangulo"
    elif resumo.total_sessoes:
        msg, cor, forma = "Nenhum agente aguardando", Tema.SECUNDARIO, "check"
    else:
        msg, cor, forma = "Nenhum agente ativo", Tema.TERCIARIO, "anel"
    msg = _cortar(msg, 62)
    passo = v2.fonte("Regular", 11).getlength("M")
    x = (LARGURA - (len(msg) + 2) * passo) / 2
    itens += [Marcador(forma, x + passo / 2, v2.ROD_Y, cor if forma != "check" else Tema.VERDE),
              Texto(x + 2 * passo, v2.ROD_Y, msg, 11, cor, "Medium" if urg else "Regular")]
    return itens


def _sistema_e_atividade(resumo: ResumoGlobal, h_texto: str, agora: datetime, excluir=None, titulo="ATIVIDADE") -> list:
    itens = [Bloco("sistema", (h_texto,))]
    linhas = _linhas_atividade(resumo, agora, excluir)
    itens.append(Texto(X0, 256, titulo, 11, Tema.SECUNDARIO, "Medium"))
    if not linhas:
        itens.append(Texto(COL_ESTADO - 8, 256, "nenhuma sessão aberta", 10, Tema.TERCIARIO))
    # o nome mais comprido (OPENCODE) empurra a coluna do projeto; com os três de sempre, ela fica onde sempre esteve
    projeto_x = COL_ESTADO + (54 if any(len(linha[0]) > 6 for linha in linhas) else 44)
    for i, (prov, proj, rotulo, cor, desde) in enumerate(linhas):
        y = 256 + 18 * i
        itens += [Texto(COL_ESTADO - 8, y, prov, 10, Tema.SECUNDARIO, "Bold"), Texto(projeto_x, y, proj, 10, Tema.TEXTO),
                  Texto(COL_ESTADO + 152, y, rotulo, 10, cor, "Medium"), Texto(X1, y, desde, 10, Tema.TERCIARIO, ancora="rm")]
    return itens


def _plano_overview(resumo: ResumoGlobal, h_texto: str, agora: datetime) -> list:
    ativos = resumo.providers_ativos
    if resumo.mais_urgente is not None:
        (direita, cor), forma = _urgencia(resumo), "triangulo"
    elif ativos:
        direita, cor, forma = _plural(ativos, "AGENTE ATIVO", "AGENTES ATIVOS"), Tema.VERDE if resumo.trabalhando else Tema.SECUNDARIO, "cheio"
    else:
        direita, cor, forma = "NENHUM ATIVO", Tema.TERCIARIO, "anel"
    itens = _cabecalho("OMBRO", "~ agentes", direita, cor, forma)
    visiveis = [p for p in resumo.providers if p.estado != "nao_instalado"][:4]
    if not visiveis:
        itens.append(Texto(170, 96, "Nenhum agente encontrado", 12, Tema.TERCIARIO, ancora="mm"))
    for i, p in enumerate(visiveis):
        primeira, passo, nome_y, detalhe_y, divisoria_y = LINHAS_GERAL[4 if len(visiveis) == 4 else 3]
        y0 = primeira + passo * i
        rotulo, cor_p, forma_p = ROTULOS[p.estado]
        itens += [Texto(X0, y0 + nome_y, p.provider_id.upper(), 13, Tema.TEXTO, "Bold"),
                  Marcador(forma_p, COL_ESTADO - 8, y0 + nome_y, cor_p), Texto(COL_ESTADO, y0 + nome_y, rotulo, 11, cor_p, "Medium"),
                  Texto(COL_ESTADO, y0 + detalhe_y, _cortar(_detalhe(p, agora), DETALHE_MAX), 10, Tema.TERCIARIO)]
        if p.sessoes or p.desconectadas:
            quantas = (_plural(p.sessoes, "sessão", "sessões") if p.sessoes
                       else _plural(p.desconectadas, "conhecida", "conhecidas"))
            itens.append(Texto(COL_DIREITA, y0 + nome_y, quantas, 11, Tema.SECUNDARIO, ancora="rm"))
        if i < len(visiveis) - 1:
            itens.append(Linha(X0, y0 + divisoria_y, 334, y0 + divisoria_y))
    resumo_txt = (f"{_plural(resumo.total_sessoes, 'sessão', 'sessões')} · {resumo.trabalhando} trabalhando · "
                  f"{resumo.aguardando} aguardando") if resumo.total_sessoes else ""
    if resumo_txt:
        itens.append(Texto(X0, 166, resumo_txt, 10, Tema.TERCIARIO))
    return itens + _sistema_e_atividade(resumo, h_texto, agora) + _rodape(resumo, agora)


def _plano_focus(resumo: ResumoGlobal, pid: str, h_texto: str, agora: datetime) -> list:
    p = resumo.provider(pid)
    rotulo, cor, forma = ROTULOS[p.estado]
    itens = _cabecalho(f"{pid.upper()}@OMBRO", "~ foco", rotulo.upper(), cor, forma)
    snap = p.snapshot
    tem_quota = snap is not None and snap.quotas is not None and any(w.window_id in ("5h", "7d") for w in snap.quotas.windows)
    tem_uso = snap is not None and snap.usage is not None
    if tem_quota or (snap is not None and snap.quotas is None and tem_uso):
        janelas = []
        for wid in ("5h", "7d"):
            w = snap.quotas.window(wid) if snap.quotas else None
            janelas.append((w.used_percent, _reseta(Janela(w.used_percent, w.resets_at), agora)) if w and w.used_percent is not None else None)
        u = SimpleNamespace(configurado=snap.quotas is not None, desatualizado=bool(snap.quotas and snap.quotas.stale),
                            coletado_em=snap.quotas.fetched_at if snap.quotas else None,
                            expiradas=[w.window_id for w in snap.quotas.windows if w.expired] if snap.quotas else [])
        itens.append(Bloco("quotas", (tuple(janelas), aviso_uso(u, agora))))
    if tem_uso:
        m = snap.usage
        itens.append(Bloco("estatisticas", ((m.input_tokens or 0) + (m.output_tokens or 0), m.output_tokens or 0, p.sessoes)))
    if not (tem_quota or tem_uso):
        itens.append(Texto(X0, 46, f"SESSÕES ({p.sessoes})" if p.sessoes else "SESSÕES", 11, Tema.SECUNDARIO, "Medium"))
        sessoes = sorted(snap.sessions if snap else (), key=lambda s: (not s.disconnected, s.status_since or datetime.min),
                         reverse=True)
        if not sessoes:
            itens.append(Texto(X0, 70, _detalhe(p, agora) if p.estado != "sem_sessoes" else "nenhuma sessão aberta", 11, Tema.TERCIARIO))
        for i, s in enumerate(sessoes[:4]):
            y = 70 + 24 * i
            rot, cor_s = estado_sessao(s)
            itens += [Texto(X0, y, _cortar(s.project or "sessão", 15), 12, Tema.TEXTO, "Bold"), Texto(140, y, rot, 11, cor_s, "Medium"),
                      Texto(COL_DIREITA, y, _desde(s.status_since, agora) if s.status_since else "", 10, Tema.TERCIARIO, ancora="rm")]
        if len(sessoes) > 4:
            itens.append(Texto(X0, 166, f"+{len(sessoes) - 4} sessões", 10, Tema.TERCIARIO))
    itens.append(Bloco("sistema", (h_texto,)))
    if tem_uso and snap.usage.hourly_tokens is not None:
        itens.append(Bloco("grafico", (snap.usage.hourly_tokens,)))
    else:
        nao_tem = [n for n, c in (("tokens", p.snapshot and p.snapshot.usage), ("limites de uso", p.snapshot and p.snapshot.quotas)) if not c]
        itens.append(Texto(X0, 256, "MÉTRICAS", 11, Tema.SECUNDARIO, "Medium"))
        itens.append(Texto(COL_ESTADO - 8, 256, f"{' e '.join(nao_tem)}: o {p.nome} não informa" if nao_tem else "", 10, Tema.TERCIARIO))
    return itens + _rodape(resumo, agora)


def _plano_attention(resumo: ResumoGlobal, h_texto: str, agora: datetime) -> list:
    urg = resumo.mais_urgente
    precisam = resumo.aguardando + resumo.erros
    direita, cor = _urgencia(resumo)
    itens = _cabecalho("OMBRO", "~ atenção", direita, cor, "triangulo")
    itens += [Texto(X0, 56, urg.nome.upper(), 15, cor, "Bold"), Texto(X0, 82, MOTIVOS[urg.motivo], 13, Tema.TEXTO, "Bold")]
    excluir = None
    if urg.sessao is not None:
        excluir = (urg.provider_id, urg.sessao.session_id)
        itens.append(Texto(X0, 106, _cortar(urg.sessao.project or "sessão", 30), 12, Tema.SECUNDARIO))
        if urg.sessao.status_since:
            itens.append(Texto(X0, 126, _desde(urg.sessao.status_since, agora), 11, Tema.TERCIARIO))
    outras = precisam - (1 if urg.sessao is not None else 0)
    if outras > 0:
        itens.append(Texto(X0, 152, f"+{_plural(outras, 'outra sessão precisa', 'outras sessões precisam')} de você", 10, Tema.TERCIARIO))
    rodape = f"{resumo.trabalhando} trabalhando · {precisam} aguardando · {_plural(resumo.providers_ativos, 'agente', 'agentes')}"
    return (itens + _sistema_e_atividade(resumo, h_texto, agora, excluir, "OUTRAS") +
            [Linha(X0, v2.ROD_DIV, X1, v2.ROD_DIV), Texto(LARGURA / 2, v2.ROD_Y, rodape, 11, Tema.SECUNDARIO, ancora="mm")])


def plano(resumo: ResumoGlobal, vista: str, foco: Optional[str], h: Hardware, agora: datetime) -> tuple:
    h_texto = v2.texto_sistema(h)
    if vista == "attention" and resumo.mais_urgente is not None:
        return tuple(_plano_attention(resumo, h_texto, agora))
    if vista == "focus" and foco and resumo.provider(foco) is not None:
        return tuple(_plano_focus(resumo, foco, h_texto, agora))
    return tuple(_plano_overview(resumo, h_texto, agora))


# ---------- desenho ----------
def _marcador(d: ImageDraw.ImageDraw, m: Marcador) -> None:
    x, y, r = m.x, m.y, 3.5
    if m.forma == "cheio":
        d.ellipse((x - r, y - r, x + r, y + r), fill=m.cor)
    elif m.forma == "anel":
        d.ellipse((x - r, y - r, x + r, y + r), outline=m.cor, width=1)
    elif m.forma == "triangulo":
        s = 4.0
        d.polygon(((x, y - s), (x - s, y + s * .8), (x + s, y + s * .8)), fill=m.cor)
    elif m.forma == "check":
        d.line(((x - 3.5, y), (x - 1, y + 3)), fill=m.cor, width=2)
        d.line(((x - 1, y + 3), (x + 4, y - 3.5)), fill=m.cor, width=2)


def _desenhar_plano(img: Image.Image, itens: tuple) -> None:
    d = ImageDraw.Draw(img)
    for it in itens:
        if isinstance(it, Texto):
            _texto(d, it.x, it.y, it.s, it.px, it.cor, it.peso, it.ancora)
        elif isinstance(it, Linha):
            d.line(((it.x0, it.y0), (it.x1, it.y1)), fill=Tema.DIVISOR)
        elif isinstance(it, Marcador):
            _marcador(d, it)
        elif it.tipo == "quotas":
            v2._quotas(d, SimpleNamespace(janelas=it.dados[0], aviso=it.dados[1]))
        elif it.tipo == "estatisticas":
            total, saida, sessoes = it.dados
            v2._estatisticas(img, d, SimpleNamespace(tokens_total=total, tokens_saida=saida, sessoes=sessoes))
        elif it.tipo == "sistema":
            v2._sistema(img, d, SimpleNamespace(sistema=it.dados[0]))
        elif it.tipo == "grafico":
            v2._grafico(img, d, it.dados[0])


_ultimo: Optional[tuple] = None  # (plano, imagem)


def desenhar_multiagente(h: Hardware, resumo: ResumoGlobal, mascote: Mascote, agora: datetime, agora_mono: float,
                         vista: str = "overview", foco: Optional[str] = None, usar_cache: bool = True) -> Image.Image:
    global _ultimo
    itens = plano(resumo, vista, foco, h, agora)
    if usar_cache and _ultimo is not None and _ultimo[0] == itens:
        img = _ultimo[1].copy()
    else:
        img = Image.new("RGB", (LARGURA, ALTURA), Tema.FUNDO)
        _desenhar_plano(img, itens)
        if usar_cache:
            _ultimo = (itens, img.copy())
    v2._hardware(ImageDraw.Draw(img), h)  # CPU e RAM: as mesmas barras e cores do Minimal v2
    cam = mascote.camada(agora_mono).resize((CLOUD_W, CLOUD_H), Image.LANCZOS)
    img.paste(cam, (CLOUD_X, CLOUD_Y), cam)
    return img
