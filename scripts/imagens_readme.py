"""Gera as imagens do README a partir do desenho de verdade (o mesmo do `tela.py --demo --png`):

  docs/tela.png          o layout Minimal v2, 480x320
  docs/layouts.png       o layout do Ombro e o Minimal v2 lado a lado
  docs/multiagente.png   o multiagente: visão geral e atenção
  docs/avatares.png      os avatares (avatars/) como a telinha os desenha, cada um num estado

  .venv/bin/python scripts/imagens_readme.py

Roda com dados de demonstração e numa pasta temporária (OMBRO_SCREEN_DIR): o seu config.json não entra.
O ícone (docs/icon.png) vem de app/scripts/gen_tray_icon.py.
"""
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
os.environ["OMBRO_SCREEN_DIR"] = tempfile.mkdtemp(prefix="ombro-readme-")
sys.path.insert(0, str(RAIZ))

from PIL import Image, ImageDraw  # noqa: E402

from config import resolver  # noqa: E402
from ombro.app import Aplicacao  # noqa: E402
from render import S, fonte  # noqa: E402

DOCS = RAIZ / "docs"
FUNDO, BORDA, TITULO, SUB = (16, 16, 16), (48, 48, 48), (229, 229, 229), (133, 133, 133)


def quadro(**opcoes) -> Image.Image:
    cfg, _ = resolver({"demo": True, "sem_macmon": True, "nao_abrir": True, **opcoes})
    app = Aplicacao(cfg)
    try:
        destino = Path(os.environ["OMBRO_SCREEN_DIR"]) / "q.png"
        app.salvar_png(destino)
        return Image.open(destino).convert("RGB")
    finally:
        app.fechar()


def lado_a_lado(itens) -> Image.Image:
    """Dois quadros 480x320 com título e o comando em cima, como no README de sempre."""
    img = Image.new("RGB", (1004, 384), FUNDO)
    d = ImageDraw.Draw(img)
    for i, (titulo, sub, q) in enumerate(itens):
        x = 16 + i * 492
        d.text((x, 14), titulo, font=fonte("Bold", 15 / S), fill=TITULO)  # fonte() já multiplica por S
        d.text((x, 35), sub, font=fonte("Regular", 12 / S), fill=SUB)
        img.paste(q, (x, 60))
        d.rectangle((x - 1, 59, x + 480, 380), outline=BORDA)
    return img


def galeria() -> Image.Image:
    """Os avatares do catálogo como o mascote da telinha os desenha (o mesmo renderer), com o nome embaixo."""
    import avatares
    import mascote

    cat = avatares.catalogo()
    estados = ("ocioso", "trabalhando", "esperando", "concluido", "ocioso", "alerta", "trabalhando", "concluido", "esperando",
               "concluido", "trabalhando", "esperando")
    colunas = 6  # os doze em duas linhas
    agente = {v: k for k, v in cat.por_agente.items()}
    cw, ch = mascote.COL_W, mascote.COL_H
    linhas = -(-len(cat.avatares) // colunas)
    img = Image.new("RGB", (16 + colunas * (cw + 8), linhas * (ch + 46) + 8), FUNDO)
    d = ImageDraw.Draw(img)
    for i, av in enumerate(cat.avatares.values()):
        x, y = 16 + (i % colunas) * (cw + 8), 8 + (i // colunas) * (ch + 46)
        est = estados[i % len(estados)]
        cor = avatares.cor_hex(cat.cor_agente[agente[av.id]] if av.id in agente else av.cor)
        cam = mascote.desenhar_cloud(mascote.parametros(est, 1.2), est, 1.2, cor, forma=mascote.forma_de(av))
        cam = cam.resize((cw, ch), Image.LANCZOS)
        img.paste(cam, (x, y), cam)
        d.text((x + cw // 2, y + ch + 6), av.nome, font=fonte("Bold", 13 / S), fill=TITULO, anchor="mt")
        quem = f"padrão do {dict(claude='Claude', codex='Codex', cursor='Cursor')[agente[av.id]]}" if av.id in agente else est
        d.text((x + cw // 2, y + ch + 24), quem, font=fonte("Regular", 11 / S), fill=SUB, anchor="mt")
    return img


if __name__ == "__main__":
    minimal = quadro(layout="minimal-v2")
    minimal.save(DOCS / "tela.png")
    lado_a_lado([("Ombro", "padrão: python3 tela.py", quadro(layout="ombro")),
                 ("Minimal v2", "python3 tela.py --layout minimal-v2", minimal)]).save(DOCS / "layouts.png")
    lado_a_lado([("Visão geral", "python3 tela.py --layout multiagente", quadro(layout="multiagente", vista="overview")),
                 ("Atenção", "--layout multiagente (vista attention)", quadro(layout="multiagente", vista="attention"))]
                ).save(DOCS / "multiagente.png")
    galeria().save(DOCS / "avatares.png")
    print("ok: docs/tela.png, docs/layouts.png, docs/multiagente.png, docs/avatares.png")
