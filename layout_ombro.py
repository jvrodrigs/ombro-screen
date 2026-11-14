"""A tela do Ombro (480x320, paisagem): uso do Claude, CPU e RAM, tokens por hora e o cloud.

Reproduz o mockup aprovado (exemplo-b.html). O texto é desenhado caractere a caractere com o
passo da fonte no navegador (7,8 px a 13 px) para os blocos das barras emendarem do mesmo jeito.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from coletores.claude import Janela, UsoClaude
from coletores.hardware import Hardware
from mascote import COL_X, COL_Y, Mascote
from render import (
    AMBAR, APAGADO, AZUL, CORAL, FRACO, S, TEXTO, VERDE, Tela, cor_uso, gb, quando_reseta, tokens,
)

LARGURA, ALTURA = 480, 320
TAM = 13
PASSO = 7.8  # largura de um caractere a 13 px (0,6 em)
LINHA = 20.15  # 1,55 x 13
X0, Y0 = 16, 12  # recuo da tela


def _yc(i: float) -> float:
    """Centro vertical da linha `i`."""
    return Y0 + LINHA * (i + .5)


def _largura(segs) -> float:
    return sum(len(s) * PASSO for s, _, _ in segs)


def _linha(t: Tela, x: float, y: float, segs) -> float:
    """Escreve segmentos (texto, cor, peso) um caractere por vez, no passo fixo; devolve o x final."""
    for s, cor, peso in segs:
        for ch in s:
            if ch != " ":
                t.texto(x, y, ch, peso, TAM, cor, ancora="lm")
            x += PASSO
    return x


def _barra(pct: float, n: int) -> str:
    cheio = max(0, min(n, round(pct / 100 * n)))
    return "█" * cheio + "░" * (n - cheio)


def _reseta(j: Janela | None, agora: datetime) -> str:
    if not j or not j.reseta_em:
        return "—"
    if (j.reseta_em - agora).total_seconds() < 24 * 3600:
        return f"{quando_reseta(j, agora)} ({j.reseta_em:%H:%M})"
    return quando_reseta(j, agora)


def rotulo_estado(estado: str, u: UsoClaude) -> str:
    return {
        "ocioso": "nenhuma ativa",
        "trabalhando": f"{u.trabalhando} trabalhando",
        "esperando": f"{u.esperando} esperando resposta",
        "alerta": "plano quase no fim",
        "reset": "plano resetou",
        "concluido": "concluído",
    }[estado]


def _desde(quando: datetime | None, agora: datetime) -> str:
    if quando is None:
        return ""
    seg = max(0, int((agora - quando).total_seconds()))
    if seg < 60:
        return "há instantes"
    m = seg // 60
    return f"há {m} min" if m < 60 else f"há {m // 60}h{m % 60:02d}"


def _grafico_tokens(t: Tela, por_hora: list, x: float, y: float, w: float, h: float) -> tuple[str, float]:
    """Gráfico de linha do dia: uma linha fina e um ponto branco na hora atual."""
    vals = [(hora, v) for hora, v in enumerate(por_hora) if v is not None]
    pico = max((v for _, v in vals), default=0)
    if len(vals) < 2 or pico <= 0:
        return "—", 0
    passo, pad = w / 24, 4
    pts = [((x + hora * passo + passo / 2) * S, (y + h - pad - v / pico * (h - 2 * pad)) * S) for hora, v in vals]
    t.d.line(pts, fill=CORAL, width=int(1.7 * S), joint="curve")
    for px, py in (pts[0], pts[-1]):  # pontas arredondadas
        r = .85 * S
        t.d.ellipse((px - r, py - r, px + r, py + r), fill=CORAL)
    ux, uy = pts[-1]
    r = 2.6 * S
    t.d.ellipse((ux - r, uy - r, ux + r, uy + r), fill=TEXTO)
    hora_pico = max(vals, key=lambda hv: hv[1])[0]
    return f"pico {hora_pico}h", pico


def _x_aviso(msg: str) -> float:
    return (LARGURA - (2 + len(msg)) * PASSO) / 2


def _aviso(t: Tela, espera: str | None) -> None:
    """Uma linha, centralizada: quem espera você e há quanto tempo; senão, um 'tudo certo' discreto.

    O ▲ de quem espera pulsa a cada quadro: ele é desenhado à parte, em `_triangulo`.
    """
    y = 304
    if espera:
        _linha(t, _x_aviso(espera) + 2 * PASSO, y, [(espera, AMBAR, "Regular")])
    else:
        msg = "nenhuma sessão esperando"
        x = _x_aviso(msg)
        cx, cy = x + PASSO / 2, y  # ✓ desenhado à mão
        t.d.line([((cx - 3.2) * S, cy * S), ((cx - 1) * S, (cy + 2.6) * S), ((cx + 3.4) * S, (cy - 3) * S)], fill=FRACO, width=int(1.4 * S), joint="curve")
        _linha(t, x + 2 * PASSO, y, [(msg, FRACO, "Regular")])


def _triangulo(t: Tela, espera: str, agora_mono: float) -> None:
    """O ▲ antes de quem espera você, pulsando devagar (opacidade 0,35 a 1)."""
    pulso = .675 + .325 * math.cos(math.tau * agora_mono / 1.8)
    cor = tuple(int(AMBAR[i] * pulso + 18 * (1 - pulso)) for i in range(3))
    tx, ty, s = _x_aviso(espera) + PASSO / 2, 304, 3.6  # desenhado à mão: não depende de a fonte ter o glifo
    t.d.polygon([(tx * S, (ty - s) * S), ((tx - s) * S, (ty + s * .8) * S), ((tx + s) * S, (ty + s * .8) * S)], fill=cor)


# ---------- a parte da tela que não anima ----------
@dataclass(frozen=True)
class Fixo:
    """Tudo o que a parte parada da tela mostra, já como texto e números.

    É a única entrada de `_desenhar_fixo`: se o do quadro novo é igual ao do anterior, a imagem
    também é, e não precisa desenhar as ~200 letras de novo. Só a linha de CPU e RAM, o ▲ que
    pulsa e o cloud mudam a cada quadro.
    """
    janelas: tuple  # 5h e semana: (pct, texto do reset) ou None
    aviso: str
    tokens_total: int
    tokens_saida: int
    sessoes: int
    status: tuple
    rotulo: str
    por_hora: tuple
    espera: str | None  # "projeto espera há 12 min", ou None


def fixo(u: UsoClaude, estado: str, agora: datetime) -> Fixo:
    janelas = tuple((j.pct, _reseta(j, agora)) if j else None for j in (u.cinco_horas, u.semana))
    aviso = ("sem dados de uso: veja o README" if not u.configurado
             else (f"números de {int((agora - u.coletado_em).total_seconds() // 60)} min atrás" if u.desatualizado and u.coletado_em else ""))
    espera = None
    if u.esperando:
        proj = u.espera_projeto or "sessão"
        espera = f"{proj} espera {_desde(u.espera_desde, agora)}".replace("espera há instantes", "espera agora")
    return Fixo(janelas, aviso, u.tokens_entrada + u.tokens_saida, u.tokens_saida, u.sessoes, tuple(u.status_sessoes[:6]),
                rotulo_estado(estado, u), tuple(u.tokens_por_hora), espera)


def _desenhar_fixo(t: Tela, f: Fixo) -> None:
    # cabeçalho
    _linha(t, X0, _yc(0), [("claude@ombro", CORAL, "Regular"), (" ~ uso", APAGADO, "Regular")])

    # sessão de 5h e semana
    for i, (rot, jan, base) in enumerate((("sessão 5h", f.janelas[0], None), ("semana   ", f.janelas[1], AZUL))):
        y = _yc(2 + 2 * i)
        pct = jan[0] if jan else 0
        cor = (cor_uso(pct) if base is None else (cor_uso(pct) if pct >= 75 else base)) if jan else FRACO
        _linha(t, X0, y, [(rot + "  ", APAGADO, "Regular"), (_barra(pct, 20), cor, "Regular"), (" ", TEXTO, "Regular"),
                          (f"{pct:.0f}%" if jan else "—", TEXTO, "Bold")])
        _linha(t, X0 + 12 * PASSO, _yc(3 + 2 * i), [(jan[1] if jan else "—", FRACO, "Regular")])
    if f.aviso:
        _linha(t, X0, _yc(6), [(f.aviso, AMBAR, "Regular")])

    # tokens e sessões
    _linha(t, X0, _yc(7), [("tokens hoje", APAGADO, "Regular"), (" " + tokens(f.tokens_total) + "   ", TEXTO, "Regular"),
                           ("saída", APAGADO, "Regular"), (" " + tokens(f.tokens_saida), TEXTO, "Regular")])
    y = _yc(8)
    x = _linha(t, X0, y, [("sessões", APAGADO, "Regular"), (f"     {f.sessoes} ", TEXTO, "Regular")])
    cores_status = {"busy": CORAL, "waiting": AMBAR, "idle": VERDE}
    for st in f.status:  # uma bolinha por sessão, uma célula cada
        r = 3.3
        cx = x + PASSO / 2
        t.d.ellipse(((cx - r) * S, (y - r) * S, (cx + r) * S, (y + r) * S), fill=cores_status.get(st, FRACO))
        x += PASSO
    _linha(t, x + PASSO, y, [(f.rotulo, CORAL, "Regular")])

    # separador
    t.ret(X0, _yc(10) - .75, X0 + 45 * PASSO, _yc(10) + .75, (74, 70, 66))

    # tokens por hora: rótulo à esquerda, linha do dia à direita
    pico_txt, _ = _grafico_tokens(t, list(f.por_hora), 90, 257, 374, 32)
    _linha(t, X0, _yc(12), [("tokens/h", APAGADO, "Regular")])
    _linha(t, X0, _yc(13), [(pico_txt, FRACO, "Regular")])

    _aviso(t, f.espera)


def _cpu_ram(t: Tela, h: Hardware) -> None:
    pct_ram = 100 * h.ram_usada / h.ram_total if h.ram_total else 0
    _linha(t, X0, _yc(11), [("cpu", APAGADO, "Regular"), (" ", TEXTO, "Regular"), (_barra(h.cpu_pct, 10), CORAL, "Regular"), (f" {h.cpu_pct:.0f}%   ", TEXTO, "Regular"),
                            ("ram", APAGADO, "Regular"), (" ", TEXTO, "Regular"), (_barra(pct_ram, 10), AZUL, "Regular"),
                            (f" {gb(h.ram_usada)}/{gb(h.ram_total, 0)}G", TEXTO, "Regular")])


_ultimo_fixo: tuple[Fixo, Image.Image] | None = None  # a parte parada do último quadro, em 2x


def desenhar_ombro(h: Hardware, u: UsoClaude, mascote: Mascote, agora: datetime, agora_mono: float,
                   usar_cache: bool = True) -> Image.Image:
    """O quadro inteiro. A parte parada sai do quadro anterior quando nada nela mudou (ver `Fixo`).

    As partes que mudam a cada quadro (CPU e RAM, o ▲, o cloud) não se sobrepõem às paradas, então
    desenhá-las por cima da parte parada dá os mesmos pixels que desenhar tudo de uma vez.
    """
    global _ultimo_fixo
    f = fixo(u, mascote.estado, agora)
    if usar_cache and _ultimo_fixo is not None and _ultimo_fixo[0] == f:
        t = Tela(LARGURA, ALTURA, img=_ultimo_fixo[1].copy())
    else:
        t = Tela(LARGURA, ALTURA)
        _desenhar_fixo(t, f)
        if usar_cache:
            _ultimo_fixo = (f, t.img.copy())
    _cpu_ram(t, h)
    if f.espera:
        _triangulo(t, f.espera, agora_mono)
    cam = mascote.camada(agora_mono)  # o cloud, na coluna reservada à direita
    t.img.paste(cam, (COL_X * S, COL_Y * S), cam)
    return t.final()
