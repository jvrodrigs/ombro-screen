"""Tela revisão A (Turing 3.5" / UsbMonitor) falando direto com o endpoint USB, sem a porta serial.

Por que existe: no macOS, o driver serial (CDC) reagrupa os bytes em pacotes de 64 bytes do jeito
dele, e o firmware da tela só reconhece um comando no início de um pacote. Um byte fora do lugar
dessincroniza a tela, que passa a mostrar faixas e ruído até ser desligada e religada. É um
problema conhecido da turing-smart-screen-python (issues #7 e #512) que ajustes na serial (flush,
pausas, baud, rtscts) não resolvem. Escrevendo direto no endpoint bulk com o libusb, cada comando
abre um envio novo e os pixels vão com o tamanho exato.

Precisa do pyusb (pip) e do libusb (`brew install libusb`).

A classe imita a interface da LcdCommRevA que a `saida.TelaUSB` usa: Reset, InitializeComm,
SetBrightness, SetOrientation, DisplayPILImage, closeSerial, auto_detect_com_port e lcd_serial.
"""
from __future__ import annotations

import time
from enum import IntEnum
from pathlib import Path

from PIL import Image

VID, PID = 0x1A86, 0x5722  # Turing 3.5" e UsbMonitor (revisão A)
INTERFACE = 1  # interface de dados do CDC
ENDPOINT_OUT = 0x03  # bulk OUT, pacotes de 64 bytes
TIMEOUT_MS = 10_000
PAUSA_ORIENTACAO = 0.05  # segundos depois de SET_ORIENTATION
PAUSA_BITMAP = 0.02  # segundos depois de cada bitmap, antes do próximo comando
ESPERA_RESET = 5.0  # a tela reinicia e volta a aparecer no USB
LIBUSB_HOMEBREW = ("/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib")


class Orientation(IntEnum):  # mesmos valores da turing-smart-screen-python
    PORTRAIT = 0
    REVERSE_PORTRAIT = 1
    LANDSCAPE = 2
    REVERSE_LANDSCAPE = 3


class Comando(IntEnum):
    RESET = 101
    SCREEN_ON = 109
    SET_BRIGHTNESS = 110
    SET_ORIENTATION = 121
    DISPLAY_BITMAP = 197


def _backend():
    """Backend do libusb; no Mac, o ctypes nem sempre acha a biblioteca do Homebrew sozinho."""
    import usb.backend.libusb1 as libusb1

    be = libusb1.get_backend()
    for caminho in LIBUSB_HOMEBREW:
        if be is not None:
            break
        if Path(caminho).exists():
            be = libusb1.get_backend(find_library=lambda _nome, c=caminho: c)
    return be


def problema() -> str | None:
    """Por que o envio direto não está disponível (ou None, se está)."""
    try:
        import usb.core  # noqa: F401
    except ImportError:
        return "falta o pyusb (pip install pyusb)"
    if _backend() is None:
        return "falta o libusb (brew install libusb)"
    return None


def cabecalho(cmd: int, x: int, y: int, ex: int, ey: int) -> bytes:
    """Os 6 bytes de um comando: coordenadas de 10 bits empacotadas e o código no fim."""
    return bytes((
        x >> 2,
        ((x & 3) << 6) + (y >> 4),
        ((y & 15) << 4) + (ex >> 6),
        ((ex & 63) << 2) + (ey >> 8),
        ey & 255,
        cmd,
    ))


def rgb565_le(img: Image.Image) -> bytes:
    import numpy as np

    rgb = np.asarray(img.convert("RGB")).reshape(-1, 3).astype(np.uint16)
    px = ((rgb[:, 0] >> 3) << 11) | ((rgb[:, 1] >> 2) << 5) | (rgb[:, 2] >> 3)
    return px.astype("<u2").tobytes()


class LcdRevAUsb:
    def __init__(self, com_port: str = "AUTO", display_width: int = 320, display_height: int = 480):
        self.display_width, self.display_height = display_width, display_height  # sempre em retrato
        self.orientation = Orientation.PORTRAIT
        self.lcd_serial = None  # o dispositivo USB aberto (o nome vem da interface da biblioteca)
        self._abrir()

    # ---------- acesso ao USB (isolado para os testes) ----------
    @staticmethod
    def _encontrar():
        import usb.core

        return usb.core.find(idVendor=VID, idProduct=PID, backend=_backend())

    @staticmethod
    def _assumir(dev) -> None:
        import usb.util

        usb.util.claim_interface(dev, INTERFACE)

    @staticmethod
    def _soltar(dev) -> None:
        import usb.util

        try:
            usb.util.release_interface(dev, INTERFACE)
        finally:
            usb.util.dispose_resources(dev)

    @classmethod
    def auto_detect_com_port(cls) -> bool:
        try:
            return cls._encontrar() is not None
        except Exception:
            return False

    def _abrir(self) -> None:
        dev = self._encontrar()
        if dev is None:
            raise OSError("tela não encontrada no USB")
        self._assumir(dev)
        self.lcd_serial = dev

    def _escrever(self, dados: bytes) -> None:
        enviado = self.lcd_serial.write(ENDPOINT_OUT, dados, TIMEOUT_MS)
        if enviado != len(dados):
            raise OSError(f"envio USB incompleto: {enviado} de {len(dados)} bytes")

    def _comando(self, cmd: int, x: int = 0, y: int = 0, ex: int = 0, ey: int = 0) -> None:
        self._escrever(cabecalho(cmd, x, y, ex, ey))  # sempre num envio próprio

    # ---------- a interface da LcdCommRevA ----------
    def get_width(self) -> int:
        return self.display_height if self.orientation in (Orientation.LANDSCAPE, Orientation.REVERSE_LANDSCAPE) else self.display_width

    def get_height(self) -> int:
        return self.display_width if self.orientation in (Orientation.LANDSCAPE, Orientation.REVERSE_LANDSCAPE) else self.display_height

    def Reset(self) -> None:
        """Reinicia a tela (limpa e sai de um estado estranho). A tela some e volta ao USB."""
        self._comando(Comando.RESET)
        self.closeSerial()
        fim = time.monotonic() + ESPERA_RESET + 5
        time.sleep(ESPERA_RESET)
        while True:
            try:
                self._abrir()
                return
            except Exception:
                if time.monotonic() > fim:
                    raise
                time.sleep(0.5)

    def InitializeComm(self) -> None:
        # O HELLO da biblioteca só serve para descobrir o tamanho (3,5" / 5" / 7"), e a Turing 3,5"
        # oficial nem responde; aqui o tamanho é fixo. Só garante a tela ligada, como o app da biblioteca.
        self._comando(Comando.SCREEN_ON)

    def SetBrightness(self, level: int = 25) -> None:
        if not 0 <= level <= 100:
            raise ValueError("brilho deve estar entre 0 e 100")
        self._comando(Comando.SET_BRIGHTNESS, int(255 - level / 100 * 255))  # 0 = mais claro

    def SetOrientation(self, orientation: Orientation = Orientation.PORTRAIT) -> None:
        self.orientation = Orientation(orientation)
        w, h = self.get_width(), self.get_height()
        # 11 bytes (a biblioteca manda 16; os 5 zeros a mais podem virar lixo no firmware)
        extra = bytes((self.orientation + 100, w >> 8, w & 255, h >> 8, h & 255))
        self._escrever(cabecalho(Comando.SET_ORIENTATION, 0, 0, 0, 0) + extra)
        time.sleep(PAUSA_ORIENTACAO)

    def DisplayPILImage(self, image: Image.Image, x: int = 0, y: int = 0) -> None:
        w, h = self.get_width(), self.get_height()
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"posição ({x}, {y}) fora da tela {w}x{h}")
        lw, lh = min(image.width, w - x), min(image.height, h - y)
        if (lw, lh) != image.size:
            image = image.crop((0, 0, lw, lh))
        self._comando(Comando.DISPLAY_BITMAP, x, y, x + lw - 1, y + lh - 1)
        self._escrever(rgb565_le(image))  # exatamente lw*lh*2 bytes, sem nada no meio
        time.sleep(PAUSA_BITMAP)

    def closeSerial(self) -> None:
        dev, self.lcd_serial = self.lcd_serial, None
        if dev is not None:
            try:
                self._soltar(dev)
            except Exception:
                pass
