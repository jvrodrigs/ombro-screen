"""Layout Minimal v2 (480x320): o mesmo conteúdo do Ombro, mais limpo e com CPU e RAM em barras.

  python3 tela.py --layout minimal-v2

Desenhado direto na resolução da tela (sem tela 2x reduzida no fim): o texto sai nítido no tamanho
real. Só duas peças pequenas são desenhadas maiores e reduzidas, para as curvas não serrilharem:
o cloud (a mesma camada de sempre do mascote.py) e a linha de tokens por hora.

Regiões (y em pixels da tela):
   0-28   cabeçalho: agente e estado (o mesmo estado do cloud)
  32-130  limites de uso (5 h e semana), com aviso de dados velhos; o cloud à direita (até 178)
 132-172  tokens hoje, saída e sessões, com ícones
 180-240  sistema (macOS e RAM total), CPU e RAM com barras proporcionais e cores por nível
 244-288  tokens por hora
 292-319  quem espera você
"""
from __future__ import annotations

import math
import platform
from dataclasses import dataclass
from functools import lru_cache
from datetime import datetime
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from coletores.claude import Janela, UsoClaude
from coletores.hardware import Hardware
from layout_ombro import _desde, _grafico_tokens, _reseta
from mascote import Mascote
from render import PASTA_FONTES, S, Tela, aviso_uso, gb, tokens

LARGURA, ALTURA = 480, 320
X0, X1 = 12, 468  # margens


# ---------- tema ----------
class Tema:
    FUNDO = (0x10, 0x10, 0x10)
    TEXTO = (0xE5, 0xE5, 0xE5)
    SECUNDARIO = (0xA5, 0xA5, 0xA5)
    TERCIARIO = (0x85, 0x85, 0x85)
    DIVISOR = (0x30, 0x30, 0x30)
    LARANJA = (0xDF, 0x85, 0x59)
    AZUL = (0x79, 0xA9, 0xD8)
    VERDE = (0x83, 0xB5, 0x96)
    AMARELO = (0xE7, 0xAF, 0x5E)
    VERMELHO = (0xE5, 0x6B, 0x6B)


def _mistura(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------- política de cores de CPU e RAM (testável sem desenhar nada) ----------
LIMIARES_CPU = (75.0, 90.0)  # atenção a partir de 75 %, crítico a partir de 90 %
LIMIARES_RAM = (85.0, 94.0)  # atenção a partir de 85 %, crítico a partir de 94 %
LIMIARES_QUOTA = (75.0, 90.0)  # os mesmos do layout do Ombro
MARGEM = 2.0  # para baixar de nível, o valor tem de cair 2 pontos abaixo do limiar (evita piscar)
NORMAL, ATENCAO, CRITICO = 0, 1, 2
NIVEL_PRESSAO = {"normal": NORMAL, "atencao": ATENCAO, "critico": CRITICO}


def nivel(valor: Optional[float], limiares: Sequence[float]) -> int:
    """0 normal, 1 atenção, 2 crítico, só pelo valor (sem histerese)."""
    if valor is None:
        return NORMAL
    return sum(valor >= lim for lim in limiares)


class Nivel:
    """Nível com histerese: sobe na hora, desce só quando o valor sai da margem abaixo do limiar."""

    def __init__(self, limiares: Sequence[float], margem: float = MARGEM):
        self.limiares, self.margem, self.atual = tuple(limiares), margem, NORMAL

    def avaliar(self, valor: Optional[float]) -> int:
        if valor is None:
            self.atual = NORMAL
            return NORMAL
        bruto = nivel(valor, self.limiares)
        if bruto >= self.atual:
            self.atual = bruto
        else:
            while self.atual > bruto and valor < self.limiares[self.atual - 1] - self.margem:
                self.atual -= 1
        return self.atual


def cor_nivel(n: int, base):
    return (base, Tema.AMARELO, Tema.VERMELHO)[n]


def _valido(v) -> Optional[float]:
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return max(0.0, min(100.0, float(v)))


def pct_cpu(h: Hardware) -> Optional[float]:
    """CPU em %, de 0 a 100; None se não há leitura (a tela mostra "—", não 0 %)."""
    return _valido(h.cpu_pct) if h.disponivel else None


def pct_ram(h: Hardware) -> Optional[float]:
    """RAM usada sobre a RAM total real do Mac (não um total fixo), de 0 a 100; None se não há leitura."""
    if not h.disponivel or not h.ram_total or h.ram_total <= 0:
        return None
    return _valido(100 * h.ram_usada / h.ram_total)


def texto_ram(h: Hardware) -> str:
    """"14,2/16 GB": usada sobre o total real do Mac."""
    return f"{gb(h.ram_usada)}/{gb(h.ram_total, 0)} GB" if pct_ram(h) is not None else "—"


def mensagem_espera(u: UsoClaude, agora: datetime, max_chars: int) -> str:
    """"projeto espera há 12 min", cortando só o nome do projeto (com …) para caber em `max_chars`."""
    sufixo = f" espera {_desde(u.espera_desde, agora)}".replace("espera há instantes", "espera agora")
    proj = u.espera_projeto or "sessão"
    cabe = max_chars - len(sufixo)
    if len(proj) > cabe:
        proj = proj[:max(1, cabe - 1)] + "…"
    return (proj + sufixo).rstrip()


def nivel_ram(ocupacao: int, pressao: Optional[str]) -> int:
    """O pior entre a ocupação e a Memory Pressure do macOS (quando ela existe). A pressão nunca é
    inventada a partir da ocupação: sem leitura dela, vale só a ocupação."""
    return max(ocupacao, NIVEL_PRESSAO.get(pressao, NORMAL))


_nivel_cpu, _nivel_ram = Nivel(LIMIARES_CPU), Nivel(LIMIARES_RAM)


def reiniciar_niveis() -> None:
    """Zera a histerese (testes e quadros de referência)."""
    _nivel_cpu.atual = _nivel_ram.atual = NORMAL


# ---------- estado do cabeçalho: o mesmo do cloud ----------
ESTADOS = {
    "trabalhando": ("TRABALHANDO", Tema.VERDE),
    "esperando": ("AGUARDANDO", Tema.AMARELO),
    "ocioso": ("OCIOSO", Tema.TERCIARIO),
    "alerta": ("ALERTA", Tema.VERMELHO),
    "reset": ("RESETOU", Tema.LARANJA),
    "concluido": ("CONCLUÍDO", Tema.VERDE),
}


# ---------- texto no tamanho real ----------
_fontes: dict = {}


def fonte(peso: str, px: int) -> ImageFont.FreeTypeFont:
    chave = (peso, px)
    if chave not in _fontes:
        try:
            _fontes[chave] = ImageFont.truetype(str(PASTA_FONTES / f"JetBrainsMono-{peso}.ttf"), px)
        except OSError:  # fonte ausente: a padrão do Pillow
            _fontes[chave] = ImageFont.load_default(px)
    return _fontes[chave]


def _texto(d: ImageDraw.ImageDraw, x, y, s: str, px: int, cor, peso: str = "Regular", ancora: str = "lm") -> float:
    """Escreve e devolve a largura."""
    d.text((x, y), s, font=fonte(peso, px), fill=cor, anchor=ancora)
    return d.textlength(s, font=fonte(peso, px))


def _barra(d: ImageDraw.ImageDraw, x, y, w: int, h: int, pct: Optional[float], cor, segmentos: int) -> None:
    """Barra segmentada: preenchimento proporcional ao pixel, com cortes finos entre os segmentos."""
    vazio = _mistura(Tema.FUNDO, cor if pct is not None else Tema.TERCIARIO, .2)
    d.rectangle((x, y, x + w - 1, y + h - 1), fill=vazio)
    cheio = round(w * pct / 100) if pct else 0
    if cheio > 0:
        d.rectangle((x, y, x + cheio - 1, y + h - 1), fill=cor)
    for k in range(1, segmentos):
        gx = x + round(k * w / segmentos)
        d.line(((gx, y), (gx, y + h - 1)), fill=Tema.FUNDO)


# ---------- regiões ----------
# Coordenadas (y) de cada linha, em pixels da tela. CPU e RAM ficam inteiras entre y 200 e 240: a
# TelaUSB compara o quadro em faixas de 40 px, e assim elas não dividem faixa com o cloud (que acaba
# em 178), o que faria cada quadro reenviar uma faixa quase da largura toda.
CAB_Y, CAB_DIV = 14, 28
QUOTA_Y, QUOTA_PASSO, RESET_DY, AVISO_Y = 48, 42, 17, 126
EST_DIV, EST_ROT, EST_VAL = 132, 145, 162
HW_DIV, SIS_Y, CPU_Y, RAM_Y = 180, 192, 214, 232
ICONE = 16  # lado dos ícones, em pixels da tela
GRAF_X, GRAF_Y, GRAF_W, GRAF_H = 112, 244, 356, 42
GRAF_ROT, GRAF_PICO = 258, 274
ROD_DIV, ROD_Y = 292, 306
BARRA_X, BARRA_W, BARRA_H = 92, 176, 12  # as duas barras de uso: mesmo início, largura e altura
PCT_X = 330  # percentuais de uso alinhados à direita aqui
HW_VALOR_X, HW_BARRA_X, HW_BARRA_W, HW_BARRA_H = 150, 160, 252, 10
CLOUD_X, CLOUD_Y, CLOUD_W, CLOUD_H = 340, 30, 136, 148  # mesma proporção da camada do mascote (156x170)
ROD_PX = 11


@dataclass(frozen=True)
class Fixo:
    """Tudo o que a parte parada da tela mostra, já como texto e números.

    É a única entrada do que se desenha em `_desenhar_fixo`: se o do quadro novo é igual ao do
    anterior, a imagem também é. A cada quadro só mudam CPU e RAM, o ▲ que pulsa e o cloud, que não
    se sobrepõem à parte parada.
    """
    agente: str
    estado: str
    janelas: tuple  # 5h e semana: (pct, texto do reset) ou None
    aviso: str
    tokens_total: int
    tokens_saida: int
    sessoes: int
    por_hora: tuple
    espera: Optional[str]  # "projeto espera há 12 min", ou None
    sistema: str  # "macOS 27.0 • 16 GB"


@lru_cache(maxsize=1)
def nome_sistema() -> str:
    """"macOS 27.0", "Linux" ou "Windows": lido uma vez, sem processo externo."""
    so = platform.system()
    if so == "Darwin":
        versao = ".".join(platform.mac_ver()[0].split(".")[:2])
        return f"macOS {versao}" if versao else "macOS"
    return so or "sistema"


def texto_sistema(h: Hardware) -> str:
    total = h.ram_total if h.disponivel and h.ram_total and h.ram_total > 0 else None
    return f"{nome_sistema()} • {gb(total, 0)} GB" if total else nome_sistema()


def fixo(u: UsoClaude, estado: str, agora: datetime, agente: str, h: Optional[Hardware] = None) -> Fixo:
    janelas = tuple((_valido(j.pct), _reseta(j, agora)) if isinstance(j, Janela) else None for j in (u.cinco_horas, u.semana))
    passo = fonte("Regular", ROD_PX).getlength("M")
    espera = mensagem_espera(u, agora, int((X1 - X0) / passo) - 2) if u.esperando else None  # o tempo nunca é cortado
    return Fixo(agente, estado, janelas, aviso_uso(u, agora), u.tokens_entrada + u.tokens_saida, u.tokens_saida, u.sessoes,
                tuple(u.tokens_por_hora), espera, texto_sistema(h or Hardware(disponivel=False)))


def _cabecalho(d, f: Fixo) -> None:
    w = _texto(d, X0, CAB_Y, f"{f.agente.upper()}@OMBRO", 13, Tema.TEXTO, "Bold")
    _texto(d, X0 + w + 8, CAB_Y, "~ uso", 13, Tema.TERCIARIO)
    rotulo, cor = ESTADOS.get(f.estado, (f.estado.upper(), Tema.SECUNDARIO))
    wr = _texto(d, X1, CAB_Y, rotulo, 11, cor, "Bold", "rm")
    cx, r = X1 - wr - 9, 3.5  # o ponto colorido; o texto diz o estado mesmo sem cor
    d.ellipse((cx - r, CAB_Y - r, cx + r, CAB_Y + r), fill=cor)
    d.line(((X0, CAB_DIV), (X1, CAB_DIV)), fill=Tema.DIVISOR)


def _quotas(d, f: Fixo) -> None:
    for i, (rotulo, jan, base) in enumerate((("SESSÃO 5H", f.janelas[0], Tema.LARANJA), ("SEMANAL", f.janelas[1], Tema.AZUL))):
        y = QUOTA_Y + QUOTA_PASSO * i
        _texto(d, X0, y, rotulo, 11, Tema.SECUNDARIO, "Medium")
        pct = jan[0] if jan else None
        cor = cor_nivel(nivel(pct, LIMIARES_QUOTA), base)
        _barra(d, BARRA_X, y - BARRA_H // 2, BARRA_W, BARRA_H, pct, cor, 12)
        _texto(d, PCT_X, y, f"{pct:.0f}%" if pct is not None else "—", 15, Tema.TEXTO if pct is not None else Tema.TERCIARIO,
               "Bold", "rm")
        _texto(d, BARRA_X, y + RESET_DY, jan[1] if jan else "—", 10, Tema.TERCIARIO)
    if f.aviso:
        _texto(d, X0, AVISO_Y, f.aviso, 10, Tema.AMARELO)


# ---------- ícones: traço fino, desenhados em 4x e reduzidos (não dependem de a fonte ter o glifo) ----------
def _icone_camadas(d, u, w):  # tokens: camadas empilhadas
    d.polygon([(.5 * u, .1 * u), (.94 * u, .34 * u), (.5 * u, .58 * u), (.06 * u, .34 * u)], outline=255, width=w)
    d.line([(.06 * u, .56 * u), (.5 * u, .8 * u), (.94 * u, .56 * u)], fill=255, width=w, joint="curve")


def _icone_saida(d, u, w):  # saída: seta para cima e para a direita
    d.line([(.16 * u, .84 * u), (.82 * u, .18 * u)], fill=255, width=w)
    d.line([(.36 * u, .18 * u), (.82 * u, .18 * u), (.82 * u, .64 * u)], fill=255, width=w, joint="curve")


def _icone_sessoes(d, u, w):  # sessões: balão de conversa com reticências
    d.rounded_rectangle((.06 * u, .1 * u, .94 * u, .7 * u), radius=.16 * u, outline=255, width=w)
    d.polygon([(.26 * u, .66 * u), (.2 * u, .94 * u), (.5 * u, .66 * u)], fill=255)
    for cx in (.3, .5, .7):
        r = .065 * u
        d.ellipse((cx * u - r, .4 * u - r, cx * u + r, .4 * u + r), fill=255)


def _icone_sistema(d, u, w):  # sistema: monitor
    d.rounded_rectangle((.04 * u, .08 * u, .96 * u, .68 * u), radius=.08 * u, outline=255, width=w)
    d.line([(.5 * u, .68 * u), (.5 * u, .86 * u)], fill=255, width=w)
    d.line([(.26 * u, .88 * u), (.74 * u, .88 * u)], fill=255, width=w)


_DESENHO_ICONE = {"tokens": _icone_camadas, "saida": _icone_saida, "sessoes": _icone_sessoes, "sistema": _icone_sistema}


@lru_cache(maxsize=None)
def icone(nome: str, cor: tuple, lado: int = ICONE) -> Image.Image:
    """Ícone RGBA de `lado` x `lado`, na cor dada. Feito uma vez e guardado."""
    u = lado * 4
    mascara = Image.new("L", (u, u), 0)
    _DESENHO_ICONE[nome](ImageDraw.Draw(mascara), u, max(1, round(u * .1)))
    camada = Image.new("RGBA", (lado, lado), cor + (0,))
    camada.putalpha(mascara.resize((lado, lado), Image.LANCZOS))
    return camada


def _colar_icone(img: Image.Image, nome: str, x: int, y_centro: int, cor) -> None:
    ic = icone(nome, cor)
    img.paste(ic, (x, y_centro - ic.height // 2), ic)


def _estatisticas(img: Image.Image, d, f: Fixo) -> None:
    d.line(((X0, EST_DIV), (334, EST_DIV)), fill=Tema.DIVISOR)
    sufixo = " ativa" if f.sessoes == 1 else (" ativas" if f.sessoes else "")
    colunas = ((X0, "tokens", "TOKENS HOJE", tokens(f.tokens_total), ""), (124, "saida", "SAÍDA", tokens(f.tokens_saida), ""),
               (236, "sessoes", "SESSÕES", str(f.sessoes), sufixo))
    meio = (EST_ROT + EST_VAL) // 2
    for i, (x, nome, rotulo, valor, suf) in enumerate(colunas):
        if i:
            d.line(((x - 10, EST_DIV + 8), (x - 10, EST_VAL + 8)), fill=Tema.DIVISOR)
        _colar_icone(img, nome, x, meio, Tema.SECUNDARIO)
        xt = x + ICONE + 5
        _texto(d, xt, EST_ROT, rotulo, 10, Tema.TERCIARIO, "Medium")
        w = _texto(d, xt, EST_VAL, valor, 15, Tema.TEXTO, "Bold")
        if suf:
            _texto(d, xt + w + 1, EST_VAL + 1, suf, 10, Tema.SECUNDARIO)


def _sistema(img: Image.Image, d, f: Fixo) -> None:
    d.line(((X0, HW_DIV), (X1, HW_DIV)), fill=Tema.DIVISOR)
    _colar_icone(img, "sistema", X0, SIS_Y, Tema.SECUNDARIO)
    _texto(d, X0 + ICONE + 6, SIS_Y, "SISTEMA", 11, Tema.SECUNDARIO, "Bold")
    _texto(d, X1, SIS_Y, f.sistema, 10, Tema.TERCIARIO, ancora="rm")


def _grafico(img: Image.Image, d, por_hora) -> None:
    # A linha é desenhada em 2x só neste pedaço e reduzida, com o desenho de sempre do layout do Ombro.
    t = Tela(GRAF_W, GRAF_H, img=Image.new("RGB", (GRAF_W * S, GRAF_H * S), Tema.FUNDO))
    pico, _ = _grafico_tokens(t, list(por_hora), 0, 0, GRAF_W, GRAF_H, cor=Tema.LARANJA, cor_ponto=Tema.TEXTO)
    img.paste(t.final(), (GRAF_X, GRAF_Y))
    _texto(d, X0, GRAF_ROT, "TOKENS/H", 11, Tema.SECUNDARIO, "Medium")
    _texto(d, X0, GRAF_PICO, pico if pico != "—" else "sem dados", 10, Tema.TERCIARIO)


def _x_rodape(d, msg: str) -> tuple[float, float]:
    passo = d.textlength("M", font=fonte("Regular", ROD_PX))
    return (LARGURA - (len(msg) + 2) * passo) / 2, passo


def _rodape(d, f: Fixo) -> None:
    """A mensagem de baixo. O ▲ de quem espera pulsa a cada quadro: ele fica em `_triangulo`."""
    d.line(((X0, ROD_DIV), (X1, ROD_DIV)), fill=Tema.DIVISOR)
    if f.espera:
        x, passo = _x_rodape(d, f.espera)
        _texto(d, x + 2 * passo, ROD_Y, f.espera, ROD_PX, Tema.AMARELO, "Medium")
    else:
        msg = "Nenhuma sessão aguardando"
        x, passo = _x_rodape(d, msg)
        cx, y = x + passo / 2, ROD_Y
        d.line(((cx - 3.5, y), (cx - 1, y + 3)), fill=Tema.VERDE, width=2)
        d.line(((cx - 1, y + 3), (cx + 4, y - 3.5)), fill=Tema.VERDE, width=2)
        _texto(d, x + 2 * passo, ROD_Y, msg, ROD_PX, Tema.SECUNDARIO)


def _triangulo(d, espera: str, agora_mono: float) -> None:
    x, passo = _x_rodape(d, espera)
    pulso = .675 + .325 * math.cos(math.tau * agora_mono / 1.8)  # pulsa devagar, como no layout do Ombro
    tx, y, s = x + passo / 2, ROD_Y, 4.0
    d.polygon(((tx, y - s), (tx - s, y + s * .8), (tx + s, y + s * .8)), fill=_mistura(Tema.FUNDO, Tema.AMARELO, pulso))


def _desenhar_fixo(img: Image.Image, f: Fixo) -> None:
    d = ImageDraw.Draw(img)
    _cabecalho(d, f)
    _quotas(d, f)
    _estatisticas(img, d, f)
    _sistema(img, d, f)
    _grafico(img, d, f.por_hora)
    _rodape(d, f)


def _hardware(d, h: Hardware) -> tuple[int, int]:
    cpu, ram = pct_cpu(h), pct_ram(h)
    n_cpu = _nivel_cpu.avaliar(cpu)
    n_ocupacao = _nivel_ram.avaliar(ram)
    n_ram = nivel_ram(n_ocupacao, h.pressao_memoria if ram is not None else None)
    for y, rotulo, pct, n, base, valor in (
        (CPU_Y, "CPU", cpu, n_cpu, Tema.LARANJA, f"{cpu:.0f}%" if cpu is not None else "—"),
        (RAM_Y, "RAM", ram, n_ram, Tema.AZUL, texto_ram(h)),
    ):
        _texto(d, X0, y, rotulo, 11, Tema.SECUNDARIO, "Medium")
        _texto(d, HW_VALOR_X, y, valor, 12, Tema.TEXTO if pct is not None else Tema.TERCIARIO, "Bold", "rm")
        cor = cor_nivel(n, base)
        _barra(d, HW_BARRA_X, y - HW_BARRA_H // 2, HW_BARRA_W, HW_BARRA_H, pct, cor, 20)
        if rotulo == "RAM" and pct is not None:
            # quando a cor vem da Memory Pressure, e não da ocupação, a tela diz isso no lugar do %
            direita = "pressão" if n_ram > n_ocupacao else f"{pct:.0f}%"
            _texto(d, X1, y, direita, 12, cor if n else Tema.SECUNDARIO, "Bold", "rm")
    return n_cpu, n_ram


_ultimo_fixo: Optional[tuple] = None  # (Fixo, imagem) do último quadro


def desenhar_minimal(h: Hardware, u: UsoClaude, mascote: Mascote, agora: datetime, agora_mono: float,
                     agente: str = "claude", usar_cache: bool = True) -> Image.Image:
    global _ultimo_fixo
    f = fixo(u, mascote.estado, agora, agente, h)
    if usar_cache and _ultimo_fixo is not None and _ultimo_fixo[0] == f:
        img = _ultimo_fixo[1].copy()
    else:
        img = Image.new("RGB", (LARGURA, ALTURA), Tema.FUNDO)
        _desenhar_fixo(img, f)
        if usar_cache:
            _ultimo_fixo = (f, img.copy())
    d = ImageDraw.Draw(img)
    _hardware(d, h)
    if f.espera:
        _triangulo(d, f.espera, agora_mono)
    cam = mascote.camada(agora_mono).resize((CLOUD_W, CLOUD_H), Image.LANCZOS)  # o cloud de sempre, menor
    img.paste(cam, (CLOUD_X, CLOUD_Y), cam)
    return img
