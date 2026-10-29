"""Desenha a tela com Pillow: retrato (320x480) ou paisagem (480x320).

Tudo é desenhado em 2x e reduzido no final, para os arcos e textos ficarem lisos.
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from coletores.claude import Janela, UsoClaude
from coletores.hardware import Hardware

LARGURA, ALTURA = 320, 480
S = 2  # fator de supersampling

# ---------- paleta ----------
FUNDO = (18, 17, 16)
PAINEL = (28, 26, 24)
TRILHO = (44, 41, 38)
TEXTO = (237, 232, 225)
APAGADO = (140, 133, 124)
FRACO = (92, 87, 81)
CORAL = (224, 122, 87)
AZUL = (111, 168, 220)
VERDE = (127, 183, 126)
AMBAR = (232, 176, 75)
VERMELHO = (229, 96, 79)

PASTA_FONTES = Path(__file__).parent / "fontes"
_cache_fontes: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def fonte(peso: str, tam: int) -> ImageFont.FreeTypeFont:
    chave = (peso, tam)
    if chave not in _cache_fontes:
        arq = PASTA_FONTES / f"JetBrainsMono-{peso}.ttf"
        try:
            _cache_fontes[chave] = ImageFont.truetype(str(arq), tam * S)
        except OSError:  # fonte ausente: usa a padrão do Pillow
            _cache_fontes[chave] = ImageFont.load_default(tam * S)
    return _cache_fontes[chave]


def misturar(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def cor_uso(pct: float):
    """Coral no uso normal; âmbar e vermelho perto do limite."""
    if pct >= 90:
        return VERMELHO
    if pct >= 75:
        return AMBAR
    return CORAL


def cor_carga(pct: float, base):
    if pct >= 90:
        return VERMELHO
    if pct >= 75:
        return AMBAR
    return base


# ---------- formatação (pt-BR) ----------
def br(n: float, casas: int = 1) -> str:
    return f"{n:.{casas}f}".replace(".", ",")


def tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return br(n / 1e9) + "B"
    if n >= 1_000_000:
        return br(n / 1e6) + "M"
    if n >= 1_000:
        return f"{n / 1e3:.0f}k"
    return str(n)


def gb(b: int, casas: int = 1) -> str:
    return br(b / 1024**3, casas)


def taxa(bps: float) -> str:
    if bps >= 1024**2:
        return br(bps / 1024**2) + " MB/s"
    return f"{bps / 1024:.0f} kB/s"


DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def quando_reseta(j: Janela | None, agora: datetime) -> str:
    if not j or not j.reseta_em:
        return ""
    falta = j.reseta_em - agora
    seg = int(falta.total_seconds())
    if seg < 0:
        return "reiniciando"
    h, m = seg // 3600, (seg % 3600) // 60
    if h < 24:
        return f"reseta em {h}h{m:02d}" if h else f"reseta em {m} min"
    return f"reseta {DIAS[j.reseta_em.weekday()]} {j.reseta_em:%H:%M}"


# ---------- primitivas (coordenadas em pixels da tela; multiplicadas por S aqui) ----------
class Tela:
    def __init__(self, largura: int = LARGURA, altura: int = ALTURA):
        self.w, self.h = largura, altura
        self.img = Image.new("RGB", (largura * S, altura * S), FUNDO)
        self.d = ImageDraw.Draw(self.img)

    def texto(self, x, y, s, peso="Regular", tam=12, cor=TEXTO, ancora="la"):
        self.d.text((x * S, y * S), s, font=fonte(peso, tam), fill=cor, anchor=ancora)

    def largura(self, s, peso="Regular", tam=12) -> float:
        return self.d.textlength(s, font=fonte(peso, tam)) / S

    def ret(self, x0, y0, x1, y1, cor, raio=0):
        self.d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), radius=raio * S, fill=cor)

    def linha(self, x0, y0, x1, y1, cor, esp=1):
        self.d.line((x0 * S, y0 * S, x1 * S, y1 * S), fill=cor, width=esp * S)

    def barra(self, x, y, w, h, pct, cor, trilho=TRILHO):
        self.ret(x, y, x + w, y + h, trilho, raio=h / 2)
        if pct > 0:
            self.ret(x, y, x + max(h, w * min(pct, 100) / 100), y + h, cor, raio=h / 2)

    def anel(self, cx, cy, r, esp, pct, cor):
        caixa = ((cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S)
        self.d.arc(caixa, 0, 360, fill=TRILHO, width=esp * S)
        if pct > 0:
            fim = -90 + 360 * min(pct, 100) / 100
            self.d.arc(caixa, -90, fim, fill=cor, width=esp * S)
            for ang in (-90, fim):  # pontas arredondadas
                a = math.radians(ang)
                px = cx + (r - esp / 2) * math.cos(a)
                py = cy + (r - esp / 2) * math.sin(a)
                rr = esp / 2
                self.d.ellipse(((px - rr) * S, (py - rr) * S, (px + rr) * S, (py + rr) * S), fill=cor)

    def grafico(self, x, y, w, h, valores, cor):
        """Gráfico de área dos últimos 60 s (0–100%)."""
        self.ret(x, y, x + w, y + h, PAINEL, raio=4)
        for frac in (0.5,):  # linha-guia em 50%
            gy = y + h * frac
            self.linha(x + 4, gy, x + w - 4, gy, TRILHO)
        if len(valores) < 2:
            return
        n = 60
        passo = (w - 8) / (n - 1)
        vs = valores[-n:]
        x0 = x + 4 + passo * (n - len(vs))
        pts = [((x0 + i * passo) * S, (y + h - 3 - (h - 6) * min(v, 100) / 100) * S) for i, v in enumerate(vs)]
        area = pts + [(pts[-1][0], (y + h - 3) * S), (pts[0][0], (y + h - 3) * S)]
        self.d.polygon(area, fill=misturar(PAINEL, cor, 0.22))
        self.d.line(pts, fill=cor, width=int(1.5 * S), joint="curve")

    def caber(self, s, peso="Bold", max_w=100, tamanhos=(12, 10, 9)) -> int:
        """Maior tamanho de fonte em que o texto cabe na largura."""
        for tam in tamanhos:
            if self.largura(s, peso, tam) <= max_w:
                return tam
        return tamanhos[-1]

    def final(self) -> Image.Image:
        return self.img.resize((self.w, self.h), Image.LANCZOS)


# ---------- seções ----------
def cabecalho(t: Tela, u: UsoClaude, agora: datetime):
    t.texto(16, 12, "CLAUDE", "Bold", 13, CORAL)
    t.texto(16 + t.largura("CLAUDE ", "Bold", 13), 12, "· uso", "Regular", 13, APAGADO)
    t.texto(t.w - 16, 12, f"{agora:%H:%M}", "Bold", 13, TEXTO, ancora="ra")


def bloco_uso(t: Tela, u: UsoClaude, agora: datetime):
    y_anel = 90
    for cx, rotulo, j in ((84, "SESSÃO 5H", u.cinco_horas), (236, "SEMANA", u.semana)):
        if j:
            cor = cor_uso(j.pct)
            t.anel(cx, y_anel, 46, 8, j.pct, cor)
            num = f"{j.pct:.0f}"
            wn, wp = t.largura(num, "Bold", 28), t.largura("%", "Medium", 12)
            x = cx - (wn + wp + 1) / 2
            t.texto(x, y_anel + 10, num, "Bold", 28, TEXTO, ancora="ls")
            t.texto(x + wn + 1, y_anel + 10, "%", "Medium", 12, APAGADO, ancora="ls")
        else:
            t.anel(cx, y_anel, 46, 8, 0, FRACO)
            t.texto(cx, y_anel, "—", "Bold", 24, FRACO, ancora="mm")
        t.texto(cx, y_anel + 62, rotulo, "Bold", 10, APAGADO, ancora="mm")
        t.texto(cx, y_anel + 76, quando_reseta(j, agora), "Regular", 10, FRACO, ancora="mm")

    y = 182
    if not u.configurado:
        t.texto(LARGURA / 2, y, "sem dados de uso: veja o README", "Regular", 9, AMBAR, ancora="mm")
    elif u.desatualizado and u.coletado_em:
        mins = int((agora - u.coletado_em).total_seconds() // 60)
        idade = f"{mins} min" if mins < 120 else f"{mins // 60} h"
        t.texto(LARGURA / 2, y, f"números de {idade} atrás", "Regular", 9, AMBAR, ancora="mm")

    # cartão "hoje"
    y0 = 192
    t.ret(12, y0, LARGURA - 12, y0 + 42, PAINEL, raio=8)
    if u.esperando and agora.second % 2 == 0:
        sess, cor_sess = f"{u.esperando} espera você", AMBAR  # pisca quando alguém precisa de você
    else:
        sess = f"{u.sessoes}" + (f" · {u.trabalhando} ativ." if u.trabalhando else "")
        cor_sess = TEXTO
    colunas = [
        ("TOKENS HOJE", tokens(u.tokens_entrada + u.tokens_saida), TEXTO),
        ("SAÍDA", tokens(u.tokens_saida), TEXTO),
        ("SESSÕES", sess, cor_sess),
    ]
    for x, (rot, val, cor) in zip((24, 124, 192), colunas):
        t.texto(x, y0 + 8, rot, "Bold", 8, FRACO)
        t.texto(x, y0 + 21, val, "Bold", 13 if t.largura(val, "Bold", 13) < 112 else 11, cor)


def bloco_hardware(t: Tela, h: Hardware):
    y = 246
    t.texto(16, y, "HARDWARE", "Bold", 10, APAGADO)
    t.linha(16 + t.largura("HARDWARE ", "Bold", 10) + 2, y + 7, LARGURA - 16, y + 7, TRILHO)

    def linha_carga(y, rotulo, pct, detalhe, hist, base):
        cor = cor_carga(pct, base) if pct is not None else FRACO
        t.texto(16, y, rotulo, "Bold", 10, APAGADO)
        valor = f"{pct:.0f}%" if pct is not None else "—"
        t.texto(16, y + 13, valor, "Bold", 22, TEXTO)
        if detalhe:
            t.texto(16, y + 38, detalhe, "Regular", 9, FRACO)
        t.grafico(112, y, LARGURA - 112 - 12, 46, hist, cor if pct is not None else FRACO)

    nucleos = "  ".join(f"{rot} {v:.0f}" for rot, v in h.nucleos)
    linha_carga(y + 16, "CPU", h.cpu_pct, nucleos, h.hist_cpu, CORAL)
    linha_carga(y + 68, "GPU", h.gpu_pct, "" if h.macmon else "sem macmon", h.hist_gpu, AZUL)

    # RAM
    yr = y + 122
    pct_ram = 100 * h.ram_usada / h.ram_total if h.ram_total else 0
    t.texto(16, yr, "RAM", "Bold", 10, APAGADO)
    t.texto(LARGURA - 16, yr, f"{gb(h.ram_usada)} / {gb(h.ram_total, 0)} GB", "Medium", 10, TEXTO, ancora="ra")
    t.barra(16, yr + 15, LARGURA - 32, 6, pct_ram, cor_carga(pct_ram, VERDE))

    # mosaico 2x2
    yt = yr + 30
    w, hh, gap = (LARGURA - 24 - 8) / 2, 34, 6
    temp = "—"
    if h.temp_cpu is not None:
        temp = f"{h.temp_cpu:.0f}°"
        if h.temp_gpu is not None:
            temp += f" / {h.temp_gpu:.0f}°"
    watts = f"{br(h.watts_soc)} W" if h.watts_soc is not None else "—"
    blocos = [
        ("TEMP CPU/GPU", temp),
        ("CHIP", watts),
        ("REDE ↓ / ↑", f"{taxa(h.rede_down)} / {taxa(h.rede_up)}"),
        ("DISCO LIVRE", f"{gb(h.disco_livre, 0)} GB · {h.disco_pct:.0f}% uso"),
    ]
    for i, (rot, val) in enumerate(blocos):
        x = 12 + (i % 2) * (w + 8)
        yy = yt + (i // 2) * (hh + gap)
        t.ret(x, yy, x + w, yy + hh, PAINEL, raio=6)
        t.texto(x + 8, yy + 6, rot, "Bold", 8, FRACO)
        tam = 12 if t.largura(val, "Bold", 12) < w - 14 else 10
        t.texto(x + 8, yy + 17, val, "Bold", tam, TEXTO)


# ---------- partes reaproveitadas pelas duas orientações ----------
def anel_uso(t: Tela, cx, cy, r, esp, j: Janela | None, tam_num: int):
    if j:
        t.anel(cx, cy, r, esp, j.pct, cor_uso(j.pct))
        num = f"{j.pct:.0f}"
        tam_p = max(9, tam_num * 3 // 7)
        wn, wp = t.largura(num, "Bold", tam_num), t.largura("%", "Medium", tam_p)
        x = cx - (wn + wp + 1) / 2
        base = cy + tam_num * 0.36
        t.texto(x, base, num, "Bold", tam_num, TEXTO, ancora="ls")
        t.texto(x + wn + 1, base, "%", "Medium", tam_p, APAGADO, ancora="ls")
    else:
        t.anel(cx, cy, r, esp, 0, FRACO)
        t.texto(cx, cy, "—", "Bold", tam_num - 4, FRACO, ancora="mm")


def aviso_uso(u: UsoClaude, agora: datetime) -> str:
    if not u.configurado:
        return "sem dados de uso: veja o README"
    if u.desatualizado and u.coletado_em:
        mins = int((agora - u.coletado_em).total_seconds() // 60)
        return f"números de {mins} min atrás" if mins < 120 else f"números de {mins // 60} h atrás"
    return ""


def texto_sessoes(u: UsoClaude, agora: datetime):
    if u.esperando and agora.second % 2 == 0:  # pisca quando alguém precisa de você
        return f"{u.esperando} espera você", AMBAR
    return f"{u.sessoes}" + (f" · {u.trabalhando} ativ." if u.trabalhando else ""), TEXTO


def mosaico(h: Hardware):
    temp = "—"
    if h.temp_cpu is not None:
        temp = f"{h.temp_cpu:.0f}°"
        if h.temp_gpu is not None:
            temp += f" / {h.temp_gpu:.0f}°"
    return [
        ("TEMP CPU/GPU", temp),
        ("CHIP", f"{br(h.watts_soc)} W" if h.watts_soc is not None else "—"),
        ("REDE ↓ / ↑", f"{taxa(h.rede_down)} / {taxa(h.rede_up)}"),
        ("DISCO LIVRE", f"{gb(h.disco_livre, 0)} GB · {h.disco_pct:.0f}%"),
    ]


# ---------- paisagem (480x320): uso à esquerda, hardware à direita ----------
def paisagem(t: Tela, h: Hardware, u: UsoClaude, agora: datetime):
    cabecalho(t, u, agora)
    div = 206  # divisória entre as colunas
    t.linha(div, 36, div, t.h - 14, TRILHO)

    # --- coluna Claude ---
    for cx, rotulo, j in ((58, "SESSÃO 5H", u.cinco_horas), (150, "SEMANA", u.semana)):
        anel_uso(t, cx, 86, 40, 7, j, 24)
        t.texto(cx, 142, rotulo, "Bold", 9, APAGADO, ancora="mm")
        t.texto(cx, 155, quando_reseta(j, agora), "Regular", 9, FRACO, ancora="mm")
    aviso = aviso_uso(u, agora)
    if aviso:
        t.texto(104, 171, aviso, "Regular", 8, AMBAR, ancora="mm")

    sess, cor_sess = texto_sessoes(u, agora)
    linhas = [
        ("TOKENS HOJE", tokens(u.tokens_entrada + u.tokens_saida), TEXTO),
        ("SAÍDA", tokens(u.tokens_saida), TEXTO),
        ("SESSÕES", sess, cor_sess),
    ]
    y0, alt = 182, 36
    t.ret(12, y0, div - 10, y0 + alt * 3, PAINEL, raio=8)
    for i, (rot, val, cor) in enumerate(linhas):
        y = y0 + i * alt
        if i:
            t.linha(22, y, div - 20, y, TRILHO)
        t.texto(22, y + alt / 2, rot, "Bold", 8, FRACO, ancora="lm")
        tam = t.caber(val, max_w=96, tamanhos=(14, 12, 10))
        t.texto(div - 20, y + alt / 2, val, "Bold", tam, cor, ancora="rm")

    # --- coluna hardware ---
    x0, x1 = div + 12, t.w - 12
    gx = x0 + 74  # início dos gráficos

    def carga(y, rotulo, pct, detalhe, hist, base):
        cor = cor_carga(pct, base) if pct is not None else FRACO
        t.texto(x0, y, rotulo, "Bold", 9, APAGADO)
        t.texto(x0, y + 11, f"{pct:.0f}%" if pct is not None else "—", "Bold", 20, TEXTO)
        if detalhe:
            t.texto(x0, y + 35, detalhe, "Regular", 8, FRACO)
        t.grafico(gx, y, x1 - gx, 44, hist, cor)

    nucleos = "  ".join(f"{rot} {v:.0f}" for rot, v in h.nucleos)
    carga(42, "CPU", h.cpu_pct, nucleos, h.hist_cpu, CORAL)
    carga(94, "GPU", h.gpu_pct, "" if h.macmon else "sem macmon", h.hist_gpu, AZUL)

    yr = 150
    pct_ram = 100 * h.ram_usada / h.ram_total if h.ram_total else 0
    t.texto(x0, yr, "RAM", "Bold", 9, APAGADO)
    t.texto(x1, yr, f"{gb(h.ram_usada)} / {gb(h.ram_total, 0)} GB", "Medium", 9, TEXTO, ancora="ra")
    t.barra(x0, yr + 14, x1 - x0, 6, pct_ram, cor_carga(pct_ram, VERDE))

    yt, alt_t, gap = 182, 50, 8
    w = (x1 - x0 - gap) / 2
    for i, (rot, val) in enumerate(mosaico(h)):
        x = x0 + (i % 2) * (w + gap)
        y = yt + (i // 2) * (alt_t + gap)
        t.ret(x, y, x + w, y + alt_t, PAINEL, raio=6)
        t.texto(x + 8, y + 8, rot, "Bold", 8, FRACO)
        t.texto(x + 8, y + 22, val, "Bold", t.caber(val, max_w=w - 14, tamanhos=(13, 11, 9)), TEXTO)


def desenhar(h: Hardware, u: UsoClaude, agora: datetime | None = None, paisagem_: bool = False) -> Image.Image:
    agora = agora or datetime.now()
    if paisagem_:
        t = Tela(ALTURA, LARGURA)  # 480x320
        paisagem(t, h, u, agora)
    else:
        t = Tela()
        cabecalho(t, u, agora)
        bloco_uso(t, u, agora)
        bloco_hardware(t, h)
    return t.final()
