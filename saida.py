"""Para onde vai cada quadro: a tela USB de verdade ou um simulador no navegador."""
from __future__ import annotations

import io
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops

from logging_config import SoQuandoMuda, logger

log = logger("usb")
log_sim = logger("simulador")

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
    return "serial", (f"usando a porta serial, que no macOS costuma embaralhar a imagem desta tela; "
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
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", tipo)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(corpo)
                except (BrokenPipeError, ConnectionResetError):  # a aba do navegador fechou no meio do envio
                    log_sim.debug("o navegador fechou a conexão no meio do envio")

        self.servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.url = f"http://localhost:{porta}"

    def pronta(self) -> bool:
        return True

    def mostrar(self, img: Image.Image) -> None:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        with self._trava:
            self.png = buf.getvalue()

    def fechar(self) -> None:
        self.servidor.shutdown()
        self.servidor.server_close()  # solta a porta


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
        self.revisao = revisao
        self.lcd = None
        self._serial = None  # a porta aberta, para notar quando a biblioteca a reabre sozinha
        self.anterior: Image.Image | None = None
        self._proxima_tentativa = 0.0
        self._avisos = SoQuandoMuda(log)
        self._ja_conectou = False
        self.bytes_enviados = 0  # pixels mandados à tela desde o início (para medir; ver o log em --debug)
        self._conectar()

    def _descricao(self) -> str:
        return "USB direto" if self.usb_direto else f"porta serial, revisão {self.revisao}"

    def pronta(self) -> bool:
        """Vale a pena desenhar um quadro agora? Não, se a tela está fora e ainda não é hora de tentar de novo."""
        return self.lcd is not None or time.monotonic() >= self._proxima_tentativa

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
            self._avisos.registrar("conexao", logging.WARNING,
                                   "tela USB não encontrada; tentando de novo a cada %.0f s (Ctrl+C para sair)", RETENTAR_S)
            self._proxima_tentativa = time.monotonic() + RETENTAR_S
            return False
        lcd = None
        try:
            # As dimensões vão sempre em retrato; SetOrientation gira a tela para paisagem.
            lcd = self._classe(com_port=self.porta, display_width=320, display_height=480)
            self.lcd = lcd
            lcd.Reset()
            lcd.InitializeComm()
            self._configurar()
        except (Exception, SystemExit) as e:  # a biblioteca chama sys.exit() quando desiste da porta
            if self._avisos.registrar("conexao", logging.WARNING, "não consegui iniciar a tela USB (%s); "
                                      "tentando de novo a cada %.0f s", self._motivo(e), RETENTAR_S):
                log.debug("detalhes da falha ao iniciar a tela", exc_info=True)
            self._desconectar()
            return False
        self._serial = lcd.lcd_serial
        self.anterior = None  # a tela foi reiniciada: o próximo quadro vai inteiro
        self._avisos.resolver("conexao")
        log.info("tela USB %s (%s)", "reconectada" if self._ja_conectou else "conectada", self._descricao())
        self._ja_conectou = True
        return True

    def _desconectar(self) -> None:
        if self.lcd is not None:
            try:
                self.lcd.closeSerial()
            except Exception:  # noqa: BLE001 - a conexão já estava perdida; fechar é só arrumação
                log.debug("erro ao fechar a tela USB (ignorado)", exc_info=True)
        self.lcd, self._serial, self.anterior = None, None, None
        self._proxima_tentativa = time.monotonic() + RETENTAR_S

    def _pedaco(self, img: Image.Image, x: int, y: int) -> None:
        self.lcd.DisplayPILImage(img, x, y)
        self.bytes_enviados += img.width * img.height * 2  # RGB565

    def _enviar(self, img: Image.Image) -> None:
        if self.anterior is None:
            self._pedaco(img, 0, 0)
            return
        for y in range(0, img.height, FAIXA):
            caixa = (0, y, img.width, min(y + FAIXA, img.height))
            bbox = ImageChops.difference(img.crop(caixa), self.anterior.crop(caixa)).getbbox()
            if bbox:
                x0, y0, x1, y1 = bbox
                self._pedaco(img.crop((x0, y + y0, x1, y + y1)), x0, y + y0)

    def mostrar(self, img: Image.Image) -> None:
        if self.lcd is None:
            if time.monotonic() < self._proxima_tentativa:
                return
            if not self._conectar():
                return
        try:
            self._enviar(img)
            if self.lcd.lcd_serial is not self._serial:
                # A biblioteca reabriu a porta no meio do envio (a tela pode ter reiniciado):
                # reconfigura e manda o quadro inteiro na próxima vez.
                log.info("a biblioteca reabriu a porta serial; reconfigurando a tela")
                self._serial = self.lcd.lcd_serial
                self._configurar()
                self.anterior = None
                return
        except (Exception, SystemExit) as e:
            # Sem o cabo, a biblioteca da tela levanta de tudo (até SystemExit): qualquer erro aqui vira
            # reconexão, para o Ombro não cair. O traceback fica no log de depuração.
            log.warning("a tela USB parou de responder (%s); tentando reconectar a cada %.0f s", self._motivo(e), RETENTAR_S)
            log.debug("detalhes da falha no envio", exc_info=True)
            self._desconectar()
            return
        self.anterior = img

    def fechar(self) -> None:
        self._desconectar()
