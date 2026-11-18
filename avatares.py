"""Os avatares do Ombro: o catálogo, a geometria e a escolha de qual avatar cada agente usa.

Formato: o do open-bot-avatar (https://github.com/DominikScholz/open-bot-avatar, MIT), contrato v1. Cada
avatar é um JSON em avatars/defs/<id>.json com um path SVG numa caixa 0..100 e o par de olhos; o
avatars/catalog.json traz o que é do Ombro (descrição, cor, se entra no sorteio, o padrão de cada agente).
O Ombro.app lê os mesmos arquivos (com o mascotinhos para JavaScript); este módulo é o adaptador mínimo para a
telinha: transforma o path em polígonos e decide qual avatar e qual cor usar. Só biblioteca padrão.

Escolha, por agente (preferência no config.json, bloco "avatars"):
  "padrao"      o avatar padrão do agente no catálogo
  "<id>"        um avatar específico; um id que não existe cai no padrão do agente (nunca quebra a tela)
  "aleatorio"   um dos avatares marcados para sorteio: o mesmo enquanto o Ombro roda ("inicio") ou um por
                sessão ("sessao": o mesmo para a mesma sessão, sempre, pelo hash do id dela)
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

PASTA = Path(__file__).resolve().parent / "avatars"
ESPECIAIS = ("padrao", "aleatorio")
SORTEIOS = ("inicio", "sessao")
CORES_MODO = ("agente", "sorteada")
GLOBAL = "padrao"  # a "preferência" de quando não há agente definido (o avatar do Ombro)
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_COR = re.compile(r"#[0-9A-Fa-f]{6}")
EYE_W, EYE_H = 4.0, 11.0  # olho neutro com scale 1 (contrato v1)
EYE_BOOST = 1.45  # o motor aumenta os olhos até 1,45x onde o corpo tem espaço
EYE_PAD = 3.0  # folga do olho aumentado até o contorno

Ponto = Tuple[float, float]


class AvatarInvalido(ValueError):
    pass


# ---------- path SVG (o mesmo subconjunto do open-bot-avatar: M L H V C S Q T A Z) ----------
_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|([\s,]+)")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def _tokens(d: str) -> list:
    out, i = [], 0
    while i < len(d):
        m = _TOKEN.match(d, i)
        if not m:
            raise AvatarInvalido(f"caractere não suportado no path: {d[i]!r}")
        if m.group(1):
            out.append(m.group(1))
        elif m.group(2):
            out.append(float(m.group(2)))
        i = m.end()
    return out


def _cubica(p0: Ponto, p1: Ponto, p2: Ponto, p3: Ponto, n: int = 16) -> List[Ponto]:
    pts = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def _quadratica(p0: Ponto, p1: Ponto, p2: Ponto, n: int = 12) -> List[Ponto]:
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(1, n + 1))]


def _arco(p0: Ponto, rx: float, ry: float, rot: float, grande: bool, horario: bool, p1: Ponto) -> List[Ponto]:
    """Arco elíptico do SVG (parametrização por extremos -> centro), em segmentos de ~6 graus."""
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [p1]
    phi = math.radians(rot % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if grande == horario:
        co = -co
    cx1, cy1 = co * rx * y1 / ry, -co * ry * x1 / rx
    cx = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2
    cy = sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not horario and dt > 0:
        dt -= math.tau
    elif horario and dt < 0:
        dt += math.tau
    n = max(2, int(abs(dt) / math.radians(6)) + 1)
    pts = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((cp * x - sp * y + cx, sp * x + cp * y + cy))
    pts[-1] = p1
    return pts


def poligonos(d: str) -> List[List[Ponto]]:
    """Os subpaths do path como polígonos fechados (curvas achatadas), na caixa 0..100."""
    toks = _tokens(d)
    if not toks or toks[0] not in ("M", "m"):
        raise AvatarInvalido("o path deve começar com M")
    polys: List[List[Ponto]] = []
    atual: List[Ponto] = []
    cx = cy = sx = sy = 0.0
    ctrl: Optional[Ponto] = None  # último ponto de controle (para S e T)
    ultimo = ""
    i = 0
    cmd = ""
    while i < len(toks):
        if isinstance(toks[i], str):
            cmd = toks[i]
            i += 1
        elif not cmd:
            raise AvatarInvalido("número sem comando no path")
        up, rel = cmd.upper(), cmd.islower()
        if up == "Z":
            if atual:
                polys.append(atual)
            atual = []
            cx, cy = sx, sy
            ctrl, ultimo = None, "Z"
            cmd = ""
            continue
        n = _ARGS[up]
        if i + n > len(toks) or any(isinstance(t, str) for t in toks[i:i + n]):
            raise AvatarInvalido(f"argumentos faltando no comando {cmd}")
        a = toks[i:i + n]
        i += n
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if up == "M":
            if atual:
                polys.append(atual)
            cx, cy = a[0] + ox, a[1] + oy
            sx, sy = cx, cy
            atual = [(cx, cy)]
            cmd = "l" if rel else "L"  # pares seguintes são L implícitos
            ctrl = None
        elif up in ("L", "H", "V"):
            if up == "L":
                cx, cy = a[0] + ox, a[1] + oy
            elif up == "H":
                cx = a[0] + (cx if rel else 0)
            else:
                cy = a[0] + (cy if rel else 0)
            atual.append((cx, cy))
            ctrl = None
        elif up in ("C", "S"):
            if up == "C":
                p1 = (a[0] + ox, a[1] + oy)
                p2, p3 = (a[2] + ox, a[3] + oy), (a[4] + ox, a[5] + oy)
            else:
                p1 = (2 * cx - ctrl[0], 2 * cy - ctrl[1]) if ctrl and ultimo in ("C", "S") else (cx, cy)
                p2, p3 = (a[0] + ox, a[1] + oy), (a[2] + ox, a[3] + oy)
            atual += _cubica((cx, cy), p1, p2, p3)
            ctrl, (cx, cy) = p2, p3
        elif up in ("Q", "T"):
            if up == "Q":
                p1, p2 = (a[0] + ox, a[1] + oy), (a[2] + ox, a[3] + oy)
            else:
                p1 = (2 * cx - ctrl[0], 2 * cy - ctrl[1]) if ctrl and ultimo in ("Q", "T") else (cx, cy)
                p2 = (a[0] + ox, a[1] + oy)
            atual += _quadratica((cx, cy), p1, p2)
            ctrl, (cx, cy) = p1, p2
        elif up == "A":
            p1 = (a[5] + ox, a[6] + oy)
            atual += _arco((cx, cy), a[0], a[1], a[2], bool(a[3]), bool(a[4]), p1)
            cx, cy = p1
            ctrl = None
        ultimo = up
    if atual:
        polys.append(atual)
    polys = [p for p in polys if len(p) >= 3]
    if not polys:
        raise AvatarInvalido("o path não tem nenhuma forma fechada")
    return polys


def _area(p: Sequence[Ponto]) -> float:
    return sum(p[i - 1][0] * p[i][1] - p[i][0] * p[i - 1][1] for i in range(len(p))) / 2


def _enrolamento(polys: Sequence[Sequence[Ponto]], x: float, y: float) -> int:
    """Número de voltas (regra nonzero do SVG): != 0 é dentro."""
    w = 0
    for p in polys:
        for i in range(len(p)):
            (x0, y0), (x1, y1) = p[i - 1], p[i]
            if y0 <= y < y1 and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) > 0:
                w += 1
            elif y1 <= y < y0 and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) < 0:
                w -= 1
    return w


def dentro(polys: Sequence[Sequence[Ponto]], x: float, y: float) -> bool:
    return _enrolamento(polys, x, y) != 0


# ---------- o avatar ----------
@dataclass(frozen=True)
class Olhos:
    x: float
    y: float
    spacing: float
    scale: float


@dataclass(frozen=True)
class Avatar:
    id: str
    nome: str
    descricao: str
    cor: str
    aleatorio: bool
    tags: Tuple[str, ...]
    path: str
    olhos: Olhos
    poligonos: Tuple[Tuple[Ponto, ...], ...] = field(repr=False)

    @property
    def limites(self) -> Tuple[float, float, float, float]:
        xs = [x for p in self.poligonos for x, _ in p]
        ys = [y for p in self.poligonos for _, y in p]
        return min(xs), min(ys), max(xs), max(ys)

    def aumento_olhos(self) -> float:
        """O quanto os olhos podem crescer (até 1,45x) mantendo a folga do contorno, como no motor."""
        for k in (EYE_BOOST, 1.38, 1.3, 1.22, 1.15, 1.08, 1.0):
            w, h = EYE_W * self.olhos.scale * k / 2 + EYE_PAD, EYE_H * self.olhos.scale * k / 2 + EYE_PAD
            ok = all(dentro(self.poligonos, cx + dx, self.olhos.y + dy)
                     for cx in (self.olhos.x - self.olhos.spacing / 2, self.olhos.x + self.olhos.spacing / 2)
                     for dx in (-w, 0, w) for dy in (-h, 0, h))
            if ok:
                return k
        return 1.0


def ler_definicao(dados: dict, esperado: Optional[str] = None) -> Tuple[str, str, str, Olhos, list]:
    """Confere uma definição v1 (as regras do contrato que importam para desenhar) e devolve as partes."""
    if not isinstance(dados, dict):
        raise AvatarInvalido("a definição deve ser um objeto")
    extras = set(dados) - {"version", "id", "name", "body", "eyes"}
    if extras:
        raise AvatarInvalido(f"campos desconhecidos: {', '.join(sorted(extras))}")
    if dados.get("version") != 1:
        raise AvatarInvalido("version deve ser 1")
    aid, nome = dados.get("id"), dados.get("name")
    if not isinstance(aid, str) or not _ID.fullmatch(aid):
        raise AvatarInvalido(f"id inválido: {aid!r}")
    if esperado and aid != esperado:
        raise AvatarInvalido(f"o arquivo {esperado}.json traz o id {aid!r}")
    if not isinstance(nome, str) or not nome.strip() or len(nome) > 40:
        raise AvatarInvalido("name deve ser um texto curto")
    body = dados.get("body")
    if not isinstance(body, dict) or set(body) != {"path"} or not isinstance(body["path"], str):
        raise AvatarInvalido("body deve ser {\"path\": \"M ... Z\"}")
    if len(body["path"]) > 6000:
        raise AvatarInvalido("path grande demais")
    e = dados.get("eyes")
    if not isinstance(e, dict) or set(e) != {"x", "y", "spacing", "scale"} or not all(
            isinstance(e[k], (int, float)) and not isinstance(e[k], bool) for k in e):
        raise AvatarInvalido("eyes deve ter x, y, spacing e scale numéricos")
    olhos = Olhos(float(e["x"]), float(e["y"]), float(e["spacing"]), float(e["scale"]))
    polys = poligonos(body["path"])
    for x, y in [p for poly in polys for p in poly]:
        if not (-20 <= x <= 120 and -20 <= y <= 120):
            raise AvatarInvalido("o path sai da caixa 0..100 além do permitido")
    for cx in (olhos.x - olhos.spacing / 2, olhos.x + olhos.spacing / 2):
        if not dentro(polys, cx, olhos.y):
            raise AvatarInvalido("os olhos precisam ficar dentro do corpo")
    return aid, nome.strip(), body["path"], olhos, polys


@dataclass(frozen=True)
class Catalogo:
    avatares: Dict[str, Avatar]
    padrao: str  # o avatar de quando não há agente definido
    por_agente: Dict[str, str]  # provider -> avatar padrão
    cor_agente: Dict[str, str]  # provider -> cor padrão

    def ids(self) -> Tuple[str, ...]:
        return tuple(self.avatares)

    def sorteaveis(self) -> Tuple[str, ...]:
        """Os do sorteio, em ordem de id (a mesma ordem no app, para o hash dar o mesmo avatar)."""
        return tuple(sorted(a for a, av in self.avatares.items() if av.aleatorio))

    def padrao_de(self, provider: Optional[str]) -> Avatar:
        return self.avatares[self.por_agente.get(provider or "", self.padrao)]


def carregar(pasta: Path = PASTA) -> Catalogo:
    cat = json.loads((pasta / "catalog.json").read_text(encoding="utf-8"))
    avatares: Dict[str, Avatar] = {}
    for meta in cat["avatars"]:
        aid = meta["id"]
        dados = json.loads((pasta / "defs" / f"{aid}.json").read_text(encoding="utf-8"))
        aid, nome, path, olhos, polys = ler_definicao(dados, esperado=aid)
        if not _COR.fullmatch(meta.get("color", "")):
            raise AvatarInvalido(f"{aid}: color deve ser #RRGGBB")
        avatares[aid] = Avatar(aid, nome, meta.get("description", ""), meta["color"].upper(), bool(meta.get("random", True)),
                               tuple(meta.get("tags", ())), path, olhos, tuple(tuple(p) for p in polys))
    padrao = cat["default"]
    por_agente = {p: v["avatar"] for p, v in cat.get("providers", {}).items()}
    cor_agente = {p: v["color"].upper() for p, v in cat.get("providers", {}).items()}
    for aid in [padrao, *por_agente.values()]:
        if aid not in avatares:
            raise AvatarInvalido(f"o catálogo aponta para um avatar que não existe: {aid}")
    return Catalogo(avatares, padrao, por_agente, cor_agente)


@lru_cache(maxsize=1)
def catalogo() -> Catalogo:
    return carregar()


# ---------- a escolha ----------
def fnv1a(texto: str) -> int:
    """FNV-1a de 32 bits sobre o UTF-8 (o app usa a mesma conta: avatar por sessão igual nos dois)."""
    h = 0x811C9DC5
    for b in texto.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def escolher(preferencia: Optional[str], provider: Optional[str], sorteio: str = "inicio", semente: int = 0,
             sessao: Optional[str] = None, cat: Optional[Catalogo] = None) -> Avatar:
    """O avatar de um agente (ou do Ombro, com provider None). Nunca falha: o que não existe vira o padrão."""
    cat = cat or catalogo()
    if preferencia == "aleatorio":
        pool = cat.sorteaveis()
        if pool:
            chave = f"{provider or 'ombro'}:{sessao}" if sorteio == "sessao" and sessao else f"{provider or 'ombro'}:{semente}"
            return cat.avatares[pool[fnv1a(chave) % len(pool)]]
    elif preferencia and preferencia not in ESPECIAIS and preferencia in cat.avatares:
        return cat.avatares[preferencia]
    if provider is None and preferencia and preferencia in cat.avatares:
        return cat.avatares[preferencia]
    return cat.padrao_de(provider)


def cor_hex(cor: str) -> Tuple[int, int, int]:
    h = cor.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
