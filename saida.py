"""Para onde vai cada quadro: a tela USB de verdade ou um simulador no navegador."""
from __future__ import annotations

import io
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops

FAIXA = 40  # altura das faixas comparadas entre quadros


class Simulador:
    """Serve o quadro atual em http://localhost:<porta>, atualizando sozinho."""

    PAGINA = """<!doctype html><html><head><meta charset="utf-8"><title>Ombro Screen</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0a09}
.moldura{padding:14px;border-radius:14px;background:#26231f;box-shadow:0 10px 40px #000a}
img{display:block;width:__W__px;height:__H__px;image-rendering:pixelated}
p{color:#8c857c;font:12px ui-monospace,monospace;text-align:center;margin:10px 0 0}</style></head>
<body><div><div class="moldura"><img id="q" src="/quadro.png"></div><p>simulador · __W__x__H__</p></div>
<script>const q=document.getElementById('q');
setInterval(()=>{const n=new Image();n.onload=()=>q.src=n.src;n.src='/quadro.png?'+Date.now()},250)</script>
</body></html>"""

    def __init__(self, porta: int = 5680, paisagem: bool = False):
        w, h = (480, 320) if paisagem else (320, 480)
        pagina = self.PAGINA.replace("__W__", str(w)).replace("__H__", str(h)).encode("utf-8")
        self.png = b""
        self._trava = threading.Lock()
        sim = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/quadro.png"):
                    with sim._trava:
                        corpo, tipo = sim.png, "image/png"
                else:
                    corpo, tipo = pagina, "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(corpo)

        self.servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.url = f"http://localhost:{porta}"

    def mostrar(self, img: Image.Image) -> None:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        with self._trava:
            self.png = buf.getvalue()

    def fechar(self) -> None:
        self.servidor.shutdown()


class TelaUSB:
    """A telinha de verdade, pela biblioteca turing-smart-screen-python.

    Só reenvia os pedaços que mudaram desde o último quadro: a ligação serial é lenta
    e um quadro inteiro leva perto de 1 s.
    """

    REVISOES = {
        "A": ("lcd_comm_rev_a", "LcdCommRevA"),  # Turing 3.5" e UsbPCMonitor
        "B": ("lcd_comm_rev_b", "LcdCommRevB"),  # XuanFang 3.5"
        "C": ("lcd_comm_rev_c", "LcdCommRevC"),  # Turing 5"
        "D": ("lcd_comm_rev_d", "LcdCommRevD"),  # Kipye Qiye 3.5"
        "WEACT_A": ("lcd_comm_weact_a", "LcdCommWeActA"),  # WeAct 3.5"
    }

    def __init__(self, revisao: str, porta: str, brilho: int, pasta_lib: Path, paisagem: bool = False, girar: bool = False):
        if not (pasta_lib / "library").is_dir():
            raise SystemExit(
                f"Biblioteca da tela não encontrada em {pasta_lib}.\n"
                "Rode: git clone https://github.com/mathoudebine/turing-smart-screen-python.git"
            )
        sys.path.insert(0, str(pasta_lib))
        import importlib

        if revisao not in self.REVISOES:
            raise SystemExit(f"Revisão desconhecida: {revisao}. Use uma de: {', '.join(self.REVISOES)}")
        modulo, classe = self.REVISOES[revisao]
        lcd_mod = importlib.import_module(f"library.lcd.{modulo}")
        from library.lcd.lcd_comm import Orientation

        # As dimensões vão sempre em retrato; SetOrientation gira a tela para paisagem.
        self.lcd = getattr(lcd_mod, classe)(com_port=porta, display_width=320, display_height=480)
        self.lcd.Reset()
        self.lcd.InitializeComm()
        self.lcd.SetBrightness(level=brilho)
        if paisagem:
            orientacao = Orientation.REVERSE_LANDSCAPE if girar else Orientation.LANDSCAPE
        else:
            orientacao = Orientation.REVERSE_PORTRAIT if girar else Orientation.PORTRAIT
        self.lcd.SetOrientation(orientation=orientacao)
        self.anterior: Image.Image | None = None

    def mostrar(self, img: Image.Image) -> None:
        if self.anterior is None:
            self.lcd.DisplayPILImage(img, 0, 0)
        else:
            for y in range(0, img.height, FAIXA):
                caixa = (0, y, img.width, min(y + FAIXA, img.height))
                bbox = ImageChops.difference(img.crop(caixa), self.anterior.crop(caixa)).getbbox()
                if bbox:
                    x0, y0, x1, y1 = bbox
                    pedaco = img.crop((x0, y + y0, x1, y + y1))
                    self.lcd.DisplayPILImage(pedaco, x0, y + y0)
        self.anterior = img

    def fechar(self) -> None:
        try:
            self.lcd.closeSerial()
        except Exception:
            pass
