"""Para onde vai cada quadro: a tela USB de verdade ou um simulador no navegador."""
from __future__ import annotations

import io
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops

FAIXA = 40  # altura das faixas comparadas entre quadros
RETENTAR_S = 3.0  # segundos entre tentativas de reconectar a tela USB
TRANSPORTES = ("auto", "usb", "serial")


def escolher_transporte(pedido: str, revisao: str, plataforma: str | None = None) -> tuple[str, str]:
    """Decide entre o envio direto pelo USB (usb_direto.py) e a porta serial da biblioteca.

    Devolve (transporte, aviso). No modo "auto", Mac com revisão A usa o USB direto, porque a
    serial do macOS embaralha a imagem dessa tela; nos outros casos, a serial.
    """
    plataforma = plataforma or sys.platform
    if pedido == "serial":
        return "serial", ""
    if revisao != "A":
        if pedido == "usb":
            raise SystemExit("--transporte usb só funciona com a revisão A")
        return "serial", ""
    if pedido == "auto" and plataforma != "darwin":
        return "serial", ""
    from usb_direto import problema

    falta = problema()
    if falta is None:
        return "usb", ""
    if pedido == "usb":
        raise SystemExit(f"--transporte usb indisponível: {falta}")
    return "serial", (f"aviso: usando a porta serial, que no macOS costuma embaralhar a imagem desta tela; "
                      f"para o envio direto pelo USB, {falta}")


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

    Se a tela sumir (cabo solto, Mac acordando), o programa segue vivo e tenta reconectar
    a cada RETENTAR_S segundos; ao voltar, reconfigura a tela e manda o quadro inteiro.
    """

    REVISOES = {
        "A": ("lcd_comm_rev_a", "LcdCommRevA"),  # Turing 3.5" e UsbPCMonitor
        "B": ("lcd_comm_rev_b", "LcdCommRevB"),  # XuanFang 3.5"
        "C": ("lcd_comm_rev_c", "LcdCommRevC"),  # Turing 5"
        "D": ("lcd_comm_rev_d", "LcdCommRevD"),  # Kipye Qiye 3.5"
        "WEACT_A": ("lcd_comm_weact_a", "LcdCommWeActA"),  # WeAct 3.5"
    }

    def __init__(self, revisao: str, porta: str, brilho: int, pasta_lib: Path, paisagem: bool = False, girar: bool = False,
                 transporte: str = "serial"):
        if revisao not in self.REVISOES:
            raise SystemExit(f"Revisão desconhecida: {revisao}. Use uma de: {', '.join(self.REVISOES)}")
        self.usb_direto = transporte == "usb"
        if self.usb_direto:  # não precisa da biblioteca da tela
            from usb_direto import LcdRevAUsb, Orientation

            self._classe = LcdRevAUsb
        else:
            if not (pasta_lib / "library").is_dir():
                raise SystemExit(
                    f"Biblioteca da tela não encontrada em {pasta_lib}.\n"
                    "Rode: git clone https://github.com/mathoudebine/turing-smart-screen-python.git"
                )
            sys.path.insert(0, str(pasta_lib))
            import importlib

            modulo, classe = self.REVISOES[revisao]
            lcd_mod = importlib.import_module(f"library.lcd.{modulo}")
            from library.lcd.lcd_comm import Orientation

            self._classe = getattr(lcd_mod, classe)
        self.porta, self.brilho = porta, brilho
        if paisagem:
            self._orientacao = Orientation.REVERSE_LANDSCAPE if girar else Orientation.LANDSCAPE
        else:
            self._orientacao = Orientation.REVERSE_PORTRAIT if girar else Orientation.PORTRAIT
        self.lcd = None
        self._serial = None  # a porta aberta, para notar quando a biblioteca a reabre sozinha
        self.anterior: Image.Image | None = None
        self._proxima_tentativa = 0.0
        if not self._conectar():
            self._avisar(f"tela USB não encontrada; tentando de novo a cada {RETENTAR_S:.0f} s (Ctrl+C para sair)")

    @staticmethod
    def _avisar(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    @staticmethod
    def _motivo(e: BaseException) -> str:
        if isinstance(e, SystemExit):  # a biblioteca chama sys.exit() quando desiste de abrir a porta
            return "porta indisponível"
        return str(e) or type(e).__name__

    def _porta_presente(self) -> bool:
        """Checagem rápida antes de chamar a biblioteca, que espera ~10 s quando a porta não existe."""
        if self.usb_direto or self.porta.upper() == "AUTO":
            return bool(self._classe.auto_detect_com_port())
        return Path(self.porta).exists()

    def _configurar(self) -> None:
        self.lcd.SetBrightness(level=self.brilho)
        self.lcd.SetOrientation(orientation=self._orientacao)

    def _conectar(self) -> bool:
        if not self._porta_presente():
            return False
        lcd = None
        try:
            # As dimensões vão sempre em retrato; SetOrientation gira a tela para paisagem.
            lcd = self._classe(com_port=self.porta, display_width=320, display_height=480)
            self.lcd = lcd
            lcd.Reset()
            lcd.InitializeComm()
            self._configurar()
        except (Exception, SystemExit) as e:
            self._avisar(f"aviso: não consegui iniciar a tela USB ({self._motivo(e)})")
            self._desconectar()
            return False
        self._serial = lcd.lcd_serial
        self.anterior = None  # a tela foi reiniciada: o próximo quadro vai inteiro
        return True

    def _desconectar(self) -> None:
        if self.lcd is not None:
            try:
                self.lcd.closeSerial()
            except Exception:
                pass
        self.lcd, self._serial, self.anterior = None, None, None
        self._proxima_tentativa = time.monotonic() + RETENTAR_S

    def _enviar(self, img: Image.Image) -> None:
        if self.anterior is None:
            self.lcd.DisplayPILImage(img, 0, 0)
            return
        for y in range(0, img.height, FAIXA):
            caixa = (0, y, img.width, min(y + FAIXA, img.height))
            bbox = ImageChops.difference(img.crop(caixa), self.anterior.crop(caixa)).getbbox()
            if bbox:
                x0, y0, x1, y1 = bbox
                pedaco = img.crop((x0, y + y0, x1, y + y1))
                self.lcd.DisplayPILImage(pedaco, x0, y + y0)

    def mostrar(self, img: Image.Image) -> None:
        if self.lcd is None:
            if time.monotonic() < self._proxima_tentativa:
                return
            if not self._conectar():
                self._proxima_tentativa = time.monotonic() + RETENTAR_S
                return
            self._avisar("tela USB conectada")
        try:
            self._enviar(img)
            if self.lcd.lcd_serial is not self._serial:
                # A biblioteca reabriu a porta no meio do envio (a tela pode ter reiniciado):
                # reconfigura e manda o quadro inteiro na próxima vez.
                self._serial = self.lcd.lcd_serial
                self._configurar()
                self.anterior = None
                return
        except (Exception, SystemExit) as e:
            self._avisar(f"aviso: a tela USB parou de responder ({self._motivo(e)}); "
                         f"tentando reconectar a cada {RETENTAR_S:.0f} s")
            self._desconectar()
            return
        self.anterior = img

    def fechar(self) -> None:
        self._desconectar()
