"""Gera o ícone da Menu Bar (template monocromático) e a marca SVG do app a partir do contorno do cloud
em dados_cloud.py.

  .venv/bin/python app/scripts/gen_tray_icon.py

Silhueta preta com os olhos vazados, sobre fundo transparente: o macOS pinta conforme o tema da barra.
Mesma forma do mascote da tela (nada redesenhado à mão).
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import dados_cloud as d  # noqa: E402

DESTINO = RAIZ / "app" / "src-tauri" / "icons" / "tray"


def pilula(draw, cx, cy, comp, larg, ang, escala, ox, oy, fill):
    """Uma pílula inclinada: retângulo com pontas redondas, como os olhos do mascote."""
    a = math.radians(ang)
    dx, dy = math.cos(a) * (comp - larg) / 2, math.sin(a) * (comp - larg) / 2
    r = larg / 2 * escala
    p0 = ((cx - dx - ox) * escala, (cy - dy - oy) * escala)
    p1 = ((cx + dx - ox) * escala, (cy + dy - oy) * escala)
    draw.line([p0, p1], fill=fill, width=int(round(2 * r)))
    for x, y in (p0, p1):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def gerar(altura: int) -> Image.Image:
    x0, y0, x1, y1 = d.BBOX
    escala = altura / (y1 - y0)
    super_ = 4  # desenha 4x maior e reduz: bordas suaves
    largura = math.ceil((x1 - x0) * escala)
    img = Image.new("L", (largura * super_, altura * super_), 0)
    dr = ImageDraw.Draw(img)
    e = escala * super_
    dr.polygon([((x - x0) * e, (y - y0) * e) for x, y in d.PONTOS], fill=255)
    for o in d.OLHOS:
        pilula(dr, o["cx"], o["cy"], o["comp"], o["larg"], o["ang"], e, x0, y0, 0)
    alfa = img.resize((largura, altura), Image.LANCZOS)
    out = Image.new("RGBA", alfa.size, (0, 0, 0, 0))
    out.putalpha(alfa)
    return out


def svg() -> str:
    """O cloud em SVG (currentColor), com os olhos vazados por máscara: a marca da barra lateral do app."""
    x0, y0, x1, y1 = d.BBOX
    w, h = x1 - x0, y1 - y0
    pts = " ".join(f"{x - x0:.1f},{y - y0:.1f}" for x, y in d.PONTOS)
    olhos = []
    for o in d.OLHOS:
        a = math.radians(o["ang"])
        dx, dy = math.cos(a) * (o["comp"] - o["larg"]) / 2, math.sin(a) * (o["comp"] - o["larg"]) / 2
        cx, cy = o["cx"] - x0, o["cy"] - y0
        olhos.append(f'<line x1="{cx - dx:.1f}" y1="{cy - dy:.1f}" x2="{cx + dx:.1f}" y2="{cy + dy:.1f}" '
                     f'stroke="black" stroke-width="{o["larg"]:.1f}" stroke-linecap="round"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" aria-hidden="true">'
            f'<mask id="olhos"><rect width="{w:.0f}" height="{h:.0f}" fill="white"/>{"".join(olhos)}</mask>'
            f'<polygon points="{pts}" fill="currentColor" mask="url(#olhos)"/></svg>\n')


if __name__ == "__main__":
    DESTINO.mkdir(parents=True, exist_ok=True)
    gerar(36).save(DESTINO / "tray.png")  # 18 pt @2x
    marca = RAIZ / "app" / "src" / "assets" / "cloud.svg"
    marca.parent.mkdir(parents=True, exist_ok=True)
    marca.write_text(svg(), encoding="utf-8")
    print("ok:", DESTINO / "tray.png", marca)
