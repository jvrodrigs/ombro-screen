"""O cloud, mascote do Ombro Screen: uma animação por estado do Claude Code.

A forma e os olhos vêm da KEY de formas (ver dados_cloud.py). O movimento é o do mockup
aprovado (cloud-v3): troca de estado suave, pulos com antecipação e aterrissagem, olhos com
atraso em relação ao corpo, e extras (zZ, “?”, três pontinhos, gota de suor, confete).

Estados: ocioso, trabalhando, esperando, alerta, reset.
"""
from __future__ import annotations

import colorsys
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PIL import Image, ImageDraw

from dados_cloud import BBOX, OLHOS, PONTOS
from render import S, fonte

TAU = math.tau

# ---------- onde o cloud fica na tela de 480x320 ----------
COL_X, COL_Y, COL_W, COL_H = 316, 26, 156, 170  # coluna reservada; nada passa daqui
BX0, BY0, BX1, BY1 = BBOX
CX = (BX0 + BX1) / 2
W, H = BX1 - BX0, BY1 - BY0
_VBX, _VBY, _VBW, _VBH = BX0 - 70, BY0 - 165, W + 140, H + 185  # janela da forma, com folga para os extras
ESC = min(COL_W / _VBW, 168 / _VBH)  # a janela cabe em 156x168, centrada
_OFF_X = (COL_W - _VBW * ESC) / 2
_OFF_Y = (168 - _VBH * ESC) / 2

ESTADOS = ("ocioso", "trabalhando", "esperando", "alerta", "reset")
CAMPOS = ("dx", "dy", "sx", "sy", "rot", "kL", "kW", "olhaX", "olhaY", "pisca")
DURACAO_MISTURA = 0.5  # segundos para passar de um estado ao outro


def _ss(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def _lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def _sn(x: float) -> float:
    return math.sin(x)


# ---------- movimento por estado (t = segundos desde que o estado começou) ----------
def parametros(estado: str, t: float) -> dict[str, float]:
    o = dict(dx=0.0, dy=0.0, sx=1.0, sy=1.0, rot=0.0, kL=1.0, kW=1.0, olhaX=0.0, olhaY=0.0, pisca=0.0)
    if estado == "ocioso":
        a = _sn(TAU * t / 4)
        o["sy"], o["sx"], o["dy"], o["kL"] = 1 + .03 * a, 1 - .02 * a, -1.5 * a, .8
        o["pisca"] = 1.0 if (t % 4.2) < .14 else 0.0
        u = (t % 6.3) - 5.0  # de vez em quando olha de relance
        if 0 < u < 1.1:
            e = _sn(math.pi * u / 1.1)
            o["olhaX"], o["olhaY"], o["kL"] = 12 * e, -7 * e, .9
    elif estado == "trabalhando":
        b = abs(_sn(TAU * t / .7 - math.pi / 2))
        u = 1 - b
        o["dy"], o["sy"], o["sx"] = -12 * b, 1 - .06 * u * u, 1 + .04 * u * u
        o["olhaX"], o["olhaY"], o["kL"] = 16 * _sn(TAU * t / 1.6), 5 * b, .95
    elif estado == "esperando":
        c = t % 1.9
        o["rot"], o["kL"], o["kW"], o["olhaY"] = -6 + 2 * _sn(TAU * t / 2.2), 1.15, 1.08, -4
        if c < .14:  # agacha
            q = _ss(c / .14)
            o["sy"], o["sx"], o["olhaY"] = 1 - .1 * q, 1 + .07 * q, -1
        elif c < .58:  # no ar
            q = (c - .14) / .44
            e = _sn(math.pi * q)
            o["dy"] = -26 * e
            o["sy"] = _lerp(.9, 1, _ss(q * 2.2)) + .07 * e
            o["sx"] = _lerp(1.07, 1, _ss(q * 2.2)) - .04 * e
            o["olhaY"] = -4 - 3 * e
        elif c < .8:  # amassa ao pousar
            q = (c - .58) / .22
            o["sy"], o["sx"] = 1 - .09 * _sn(math.pi * q), 1 + .06 * _sn(math.pi * q)
        else:  # balanço que se amortece
            d = math.exp(-(c - .8) * 9) * _sn((c - .8) * 30)
            o["sy"], o["sx"] = 1 + .03 * d, 1 - .02 * d
    elif estado == "alerta":
        amp = 4 + 1.5 * _sn(TAU * t / 1.1)
        k = int(t * 12) % 2
        o["dx"], o["sy"], o["sx"], o["kL"], o["kW"] = amp * (1 if k else -1), .97, 1.03, 1.28, 1.1
    elif estado == "reset":
        c = t % 2.6
        if c < .18:
            q = _ss(c / .18)
            o["sy"], o["sx"] = 1 - .12 * q, 1 + .08 * q
        elif c < 1.45:
            q = (c - .18) / 1.27
            e, w = _sn(math.pi * q), _sn(TAU * q)
            o["dy"], o["sy"], o["sx"] = -60 * e, 1 + .14 * w, 1 - .08 * w
        else:
            q = (c - 1.45) / 1.15
            d = math.exp(-q * 5) * _sn(TAU * q * 2.5)
            o["sy"], o["sx"] = 1 + .12 * d, 1 - .07 * d
        o["kL"], o["kW"], o["olhaY"] = .6, 1.05, -6
    return o


def misturar(a: dict, b: dict, u: float) -> dict:
    return {k: _lerp(a[k], b[k], u) for k in CAMPOS}


# ---------- desenho: tudo numa camada RGBA do tamanho da coluna, em 2x ----------
def _pt(x: float, y: float) -> tuple[float, float]:
    """Coordenada da forma (pixels da KEY) -> pixel da camada (já em 2x)."""
    return (_OFF_X + (x - _VBX) * ESC) * S, (_OFF_Y + (y - _VBY) * ESC) * S


def _corpo(p: dict, x: float, y: float) -> tuple[float, float]:
    """escala em torno da base, gira em torno da base e desloca (a ordem do mockup)."""
    x = CX + (x - CX) * p["sx"]
    y = BY1 + (y - BY1) * p["sy"]
    a = math.radians(p["rot"])
    dx, dy = x - CX, y - BY1
    x = CX + dx * math.cos(a) - dy * math.sin(a)
    y = BY1 + dx * math.sin(a) + dy * math.cos(a)
    return x + p["dx"], y + p["dy"]


def _capsula(cx: float, cy: float, larg: float, comp: float, ang: float) -> list[tuple[float, float]]:
    """Pílula (retângulo de cantos totalmente redondos) girada `ang` graus em torno do centro."""
    r = min(larg, comp) / 2
    pts: list[tuple[float, float]] = []
    if comp >= larg:  # em pé
        d = comp / 2 - r
        for i in range(13):  # semicírculo de cima
            a = math.pi + math.pi * i / 12
            pts.append((r * math.cos(a), -d + r * math.sin(a)))
        for i in range(13):  # semicírculo de baixo
            a = math.pi * i / 12
            pts.append((r * math.cos(a), d + r * math.sin(a)))
    else:  # deitada
        d = larg / 2 - r
        for i in range(13):
            a = -math.pi / 2 + math.pi * i / 12
            pts.append((d + r * math.cos(a), r * math.sin(a)))
        for i in range(13):
            a = math.pi / 2 + math.pi * i / 12
            pts.append((-d + r * math.cos(a), r * math.sin(a)))
    a = math.radians(ang)
    return [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)) for x, y in pts]


def _bezier(p0, p1, p2, p3, n=14):
    return [
        (
            (1 - u) ** 3 * p0[0] + 3 * u * (1 - u) ** 2 * p1[0] + 3 * u * u * (1 - u) * p2[0] + u**3 * p3[0],
            (1 - u) ** 3 * p0[1] + 3 * u * (1 - u) ** 2 * p1[1] + 3 * u * u * (1 - u) * p2[1] + u**3 * p3[1],
        )
        for u in (i / n for i in range(n + 1))
    ]


def _quad(p0, c, p2, n=8):
    """Curva quadrática (como o `q` do SVG), convertida para cúbica."""
    p1 = (p0[0] + 2 / 3 * (c[0] - p0[0]), p0[1] + 2 / 3 * (c[1] - p0[1]))
    p2c = (p2[0] + 2 / 3 * (c[0] - p2[0]), p2[1] + 2 / 3 * (c[1] - p2[1]))
    return _bezier(p0, p1, p2c, p2, n)


def _com_alpha(cor: tuple[int, int, int], a: float) -> tuple[int, int, int, int]:
    return cor[0], cor[1], cor[2], int(255 * max(0.0, min(1.0, a)))


def _extras(d: ImageDraw.ImageDraw, estado: str, t: float, cor, a: float) -> None:
    """zZ, “?”, três pontinhos, gota de suor e confete (escolhas minhas, não vêm da KEY)."""
    topo = BY0
    branco = (237, 232, 225)
    if estado == "ocioso":
        u = (t % 2.6) / 2.6
        d.text(_pt(BX1 - 30, topo - 6 - u * 34), "z", font=fonte("Bold", 34 * ESC), fill=_com_alpha(branco, (1 - u) * a), anchor="ls")
        d.text(_pt(BX1 + 4, topo - 40 - u * 28), "Z", font=fonte("Bold", 50 * ESC), fill=_com_alpha(cor, (1 - u) * a), anchor="ls")
    elif estado == "trabalhando":
        for i in range(3):
            w = max(0.0, _sn(TAU * t / 1.2 - i * 1.1))
            cx, cy, r = _pt(CX - 44 + i * 44, topo - 34 - 10 * w), None, (9 + 6 * w) * ESC * S
            d.ellipse((cx[0] - r, cx[1] - r, cx[0] + r, cx[1] + r), fill=_com_alpha(cor, (.35 + .65 * w) * a))
    elif estado == "esperando":
        p = .5 + .5 * _sn(TAU * t / 1.9)
        sc, op = .9 + .25 * p, (.6 + .4 * p) * a
        ox, oy = CX, topo - 46

        def loc(x, y):
            return _pt(ox + x * sc, oy + y * sc)

        curva = _bezier((-17, -14), (-17, -40), (20, -40), (20, -16)) + _bezier((20, -16), (20, -2), (0, -2), (0, 18))[1:]
        pts = [loc(x, y) for x, y in curva]
        larg = 13 * sc * ESC * S
        cor_q = _com_alpha(branco, op)
        d.line(pts, fill=cor_q, width=int(larg), joint="curve")
        for px, py in (pts[0], pts[-1]):  # pontas redondas
            d.ellipse((px - larg / 2, py - larg / 2, px + larg / 2, py + larg / 2), fill=cor_q)
        qx, qy = loc(0, 40)
        r = 8 * sc * ESC * S
        d.ellipse((qx - r, qy - r, qx + r, qy + r), fill=cor_q)
    elif estado == "alerta":
        u = (t % 1.1) / 1.1
        x, y = BX1 - 14, topo + 50 + u * 80
        forma = _quad((x, y), (x - 14, y + 22), (x, y + 34))[:-1] + _quad((x, y + 34), (x + 14, y + 22), (x, y))
        d.polygon([_pt(px, py) for px, py in forma], fill=_com_alpha((207, 233, 247), (1 - u) * a))
    elif estado == "reset":
        c = t % 2.6
        if .5 < c < 2.4:
            q = (c - .5) / 1.9
            cores = [cor, branco, (232, 176, 75), (111, 168, 220)]
            for i in range(12):
                ang = i * .9 + .2
                r = 40 + q * (150 + i * 8)
                x, y = CX + math.cos(ang) * r, BY0 + 40 + math.sin(ang) * r * .7 + q * q * 90
                giro = math.radians(i * 40 + q * 200)
                meio = 6  # confete de 12x12 (unidades da KEY)
                quad = [(-meio, -meio), (meio, -meio), (meio, meio), (-meio, meio)]
                pts = [_pt(x + qx * math.cos(giro) - qy * math.sin(giro), y + qx * math.sin(giro) + qy * math.cos(giro)) for qx, qy in quad]
                d.polygon(pts, fill=_com_alpha(cores[i % 4], max(0.0, min(1.0, 1 - q * 1.05)) * a))


def desenhar_cloud(p: dict, estado: str, t: float, cor: tuple[int, int, int], alpha_extras: float = 1.0) -> Image.Image:
    """Camada RGBA (COL_W x COL_H, em 2x) com o cloud no estado e instante dados."""
    camada = Image.new("RGBA", (COL_W * S, COL_H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    d.polygon([_pt(*_corpo(p, x, y)) for x, y in PONTOS], fill=cor + (255,))
    for e in OLHOS:
        comp = max(10.0, e["comp"] * p["kL"] * (1 - .9 * p["pisca"]))
        larg = e["larg"] * p["kW"]
        pts = _capsula(e["cx"] + p["olhaX"], e["cy"] + p["olhaY"], larg, comp, e["ang"])
        d.polygon([_pt(*_corpo(p, x, y)) for x, y in pts], fill=(26, 27, 29, 255))
    _extras(d, estado, t, cor, alpha_extras)
    return camada


# ---------- máquina de estados ----------
def cor_sorteada() -> tuple[int, int, int]:
    """Matiz livre; saturação e luminosidade limitadas para ler bem sobre o fundo escuro."""
    r, g, b = colorsys.hls_to_rgb(random.random(), .5 + random.random() * .1, .58 + random.random() * .14)
    return int(r * 255), int(g * 255), int(b * 255)


def cor_de_hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@dataclass
class Mascote:
    cor_fixa: tuple[int, int, int] | None = None
    estado: str = "ocioso"
    inicio: float = 0.0
    anterior: tuple[str, float] | None = None  # (estado, quando começou) enquanto mistura
    cor: tuple[int, int, int] = field(default_factory=cor_sorteada)
    _pct_ant: float | None = None
    _reseta_ant: datetime | None = None
    _reset_ate: float = 0.0
    _calmo_desde: float | None = None

    def ir_para(self, novo: str, agora: float) -> None:
        if novo == self.estado:
            return
        self.anterior = (self.estado, self.inicio)
        self.estado, self.inicio = novo, agora
        self.cor = self.cor_fixa or cor_sorteada()

    def decidir(self, u, agora: float) -> str:
        """Estado do cloud a partir do que o Claude Code está fazendo."""
        j5, js = u.cinco_horas, u.semana
        # janela do plano que reiniciou: o horário de reset avançou mais de uma hora
        if j5 and j5.reseta_em and self._reseta_ant and (j5.reseta_em - self._reseta_ant) > timedelta(hours=1):
            self._reset_ate = agora + 3.0
        if j5:
            self._pct_ant, self._reseta_ant = j5.pct, j5.reseta_em
        if agora < self._reset_ate:
            return "reset"
        if u.esperando:
            alvo = "esperando"
        elif any(j and j.pct >= 90 for j in (j5, js)):
            alvo = "alerta"
        elif u.trabalhando:
            alvo = "trabalhando"
        else:
            alvo = "ocioso"
        # só volta a ficar ocioso depois de uns segundos sem atividade (o status oscila)
        if alvo == "ocioso" and self.estado != "ocioso":
            self._calmo_desde = self._calmo_desde or agora
            if agora - self._calmo_desde < 3.0:
                return self.estado
        else:
            self._calmo_desde = None
        return alvo

    def parametros_atuais(self, agora: float) -> tuple[dict, float]:
        t = agora - self.inicio
        u = _ss(t / DURACAO_MISTURA)
        atual = parametros(self.estado, t)
        if self.anterior and u < 1:
            ant = parametros(self.anterior[0], agora - self.anterior[1])
            return misturar(ant, atual, u), u
        return atual, 1.0

    def camada(self, agora: float) -> Image.Image:
        p, u = self.parametros_atuais(agora)
        return desenhar_cloud(p, self.estado, agora - self.inicio, self.cor, alpha_extras=u)
