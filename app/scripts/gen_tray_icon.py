"""Gera os ícones do Ombro a partir do avatar Ombro Cloud (avatars/defs/ombro-cloud.json):

  - app/src-tauri/icons/tray/tray.png    a Menu Bar: silhueta preta com os olhos vazados (imagem template)
  - app/src/assets/cloud.svg              a marca da barra lateral (currentColor, olhos vazados por máscara)
  - docs/icon.png                         o ícone do app (512 px; depois: npx tauri icon ../docs/icon.png)

  .venv/bin/python app/scripts/gen_tray_icon.py

A forma e os olhos saem do mesmo JSON que a telinha e o app usam (avatares.py), com os olhos do contrato do
open-bot-avatar (pílulas de 4x11 unidades vezes a escala, aumentadas como no motor).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import avatares  # noqa: E402

AVATAR = avatares.catalogo().avatares["ombro-cloud"]
DESTINO = RAIZ / "app" / "src-tauri" / "icons" / "tray"


def _olhos(k: float = 1.0):
    """Os dois olhos (cx, cy, largura, altura) na caixa 0..100, já com o aumento do motor."""
    o, boost = AVATAR.olhos, AVATAR.aumento_olhos() * k
    w, h = avatares.EYE_W * o.scale * boost, avatares.EYE_H * o.scale * boost
    return [(x, o.y, w, h) for x in (o.x - o.spacing / 2, o.x + o.spacing / 2)]


def silhueta(altura: int, cor=(0, 0, 0, 255), olho=(0, 0, 0, 0), margem: float = 0.0) -> Image.Image:
    """O avatar em RGBA com `altura` px de corpo (desenhado 4x maior e reduzido: bordas suaves)."""
    x0, y0, x1, y1 = AVATAR.limites
    sup = 4
    esc = altura / (y1 - y0) * sup
    larg = int((x1 - x0) * esc + 2 * margem * sup)
    alt = int((y1 - y0) * esc + 2 * margem * sup)
    img = Image.new("RGBA", (larg, alt), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def m(x, y):
        return (x - x0) * esc + margem * sup, (y - y0) * esc + margem * sup

    for poly in AVATAR.poligonos:
        d.polygon([m(x, y) for x, y in poly], fill=cor)
    for cx, cy, w, h in _olhos():
        (ax, ay), (bx, by) = m(cx - w / 2, cy - h / 2), m(cx + w / 2, cy + h / 2)
        d.rounded_rectangle((ax, ay, bx, by), radius=(bx - ax) / 2, fill=olho)
    return img.resize((larg // sup, alt // sup), Image.LANCZOS)


def svg() -> str:
    """A marca do app: o path do avatar (o mesmo do JSON) com os olhos vazados por máscara."""
    x0, y0, x1, y1 = AVATAR.limites
    olhos = "".join(f'<rect x="{cx - w / 2:.2f}" y="{cy - h / 2:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{w / 2:.2f}" '
                    f'fill="black"/>' for cx, cy, w, h in _olhos())
    vb = f"{x0:.1f} {y0:.1f} {x1 - x0:.1f} {y1 - y0:.1f}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" aria-hidden="true">'
            f'<mask id="olhos"><rect x="0" y="0" width="100" height="100" fill="white"/>{olhos}</mask>'
            f'<path d="{AVATAR.path}" fill="currentColor" mask="url(#olhos)"/></svg>\n')


def icone_app(tamanho: int = 512) -> Image.Image:
    """Quadrado arredondado escuro (como os ícones do macOS) com o Ombro Cloud laranja no meio."""
    sup = 2
    t = tamanho * sup
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, t - 1, t - 1), radius=int(t * 0.225), fill=(18, 18, 18, 255))
    corpo = silhueta(int(t * 0.5), cor=avatares.cor_hex(AVATAR.cor) + (255,), olho=(26, 27, 29, 255))
    img.alpha_composite(corpo, ((t - corpo.width) // 2, int(t * 0.51) - corpo.height // 2))
    return img.resize((tamanho, tamanho), Image.LANCZOS)


if __name__ == "__main__":
    DESTINO.mkdir(parents=True, exist_ok=True)
    silhueta(32).save(DESTINO / "tray.png")  # ~16 pt @2x de altura na Menu Bar
    marca = RAIZ / "app" / "src" / "assets" / "cloud.svg"
    marca.write_text(svg(), encoding="utf-8")
    icone_app().save(RAIZ / "docs" / "icon.png")
    print("ok:", DESTINO / "tray.png", marca, RAIZ / "docs" / "icon.png")
