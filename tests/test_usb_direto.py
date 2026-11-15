"""usb_direto.py com um dispositivo USB falso: bytes exatos de cada comando, um envio por comando."""
import importlib.util
import unittest
from unittest import mock

from PIL import Image

import saida
import usb_direto
from usb_direto import ENDPOINT_OUT, Comando, LcdRevAUsb, Orientation, cabecalho, rgb565_le


class DispositivoFalso:
    def __init__(self):
        self.envios = []

    def write(self, endpoint, dados, timeout):
        self.envios.append((endpoint, bytes(dados)))
        return len(dados)


class LcdFalso(LcdRevAUsb):
    presente = True

    @classmethod
    def _encontrar(cls):
        return DispositivoFalso() if cls.presente else None

    @staticmethod
    def _assumir(dev):
        pass

    @staticmethod
    def _soltar(dev):
        pass


class Protocolo(unittest.TestCase):
    def setUp(self):
        LcdFalso.presente = True
        for nome in ("PAUSA_BITMAP", "PAUSA_ORIENTACAO", "ESPERA_RESET"):
            p = mock.patch.object(usb_direto, nome, 0)
            p.start()
            self.addCleanup(p.stop)
        self.lcd = LcdFalso()

    def envios(self):
        return [d for _, d in self.lcd.lcd_serial.envios]

    def test_cabecalho_igual_ao_da_biblioteca(self):
        # x=316, y=26, ex=471, ey=195 empacotados em 10 bits, como no SendCommand da LcdCommRevA
        self.assertEqual(cabecalho(197, 316, 26, 471, 195), bytes([79, 1, 167, 92, 195, 197]))
        self.assertEqual(cabecalho(Comando.RESET, 0, 0, 0, 0), bytes([0, 0, 0, 0, 0, 101]))

    def test_rgb565_little_endian(self):
        img = Image.new("RGB", (2, 1))
        img.putpixel((0, 0), (255, 0, 0))
        img.putpixel((1, 0), (0, 0, 255))
        self.assertEqual(rgb565_le(img), bytes([0x00, 0xF8, 0x1F, 0x00]))

    def test_orientacao_em_11_bytes(self):
        self.lcd.SetOrientation(Orientation.LANDSCAPE)
        self.assertEqual(self.envios(), [bytes([0, 0, 0, 0, 0, 121, 102, 1, 224, 1, 64])])  # 480x320
        self.assertEqual((self.lcd.get_width(), self.lcd.get_height()), (480, 320))

    def test_brilho(self):
        self.lcd.SetBrightness(30)
        self.assertEqual(self.envios(), [cabecalho(Comando.SET_BRIGHTNESS, 178, 0, 0, 0)])
        with self.assertRaises(ValueError):
            self.lcd.SetBrightness(101)

    def test_bitmap_comando_e_pixels_em_envios_separados(self):
        self.lcd.SetOrientation(Orientation.LANDSCAPE)
        self.lcd.lcd_serial.envios.clear()
        self.lcd.DisplayPILImage(Image.new("RGB", (156, 170), (18, 17, 16)), 316, 26)
        cmd, pixels = self.envios()
        self.assertEqual(cmd, cabecalho(Comando.DISPLAY_BITMAP, 316, 26, 471, 195))
        self.assertEqual(len(pixels), 156 * 170 * 2)
        self.assertTrue(all(ep == ENDPOINT_OUT for ep, _ in self.lcd.lcd_serial.envios))

    def test_bitmap_maior_que_a_tela_e_cortado(self):
        self.lcd.SetOrientation(Orientation.LANDSCAPE)
        self.lcd.lcd_serial.envios.clear()
        self.lcd.DisplayPILImage(Image.new("RGB", (100, 100)), 440, 300)
        cmd, pixels = self.envios()
        self.assertEqual(cmd, cabecalho(Comando.DISPLAY_BITMAP, 440, 300, 479, 319))
        self.assertEqual(len(pixels), 40 * 20 * 2)

    def test_envio_incompleto_vira_erro(self):
        self.lcd.lcd_serial.write = lambda ep, dados, timeout: len(dados) - 1
        with self.assertRaises(OSError):
            self.lcd.SetBrightness(10)

    def test_reset_reabre_o_dispositivo(self):
        antes = self.lcd.lcd_serial
        self.lcd.Reset()
        self.assertEqual(antes.envios, [(ENDPOINT_OUT, cabecalho(Comando.RESET, 0, 0, 0, 0))])
        self.assertIsNot(self.lcd.lcd_serial, antes)

    def test_sem_tela(self):
        LcdFalso.presente = False
        self.assertFalse(LcdFalso.auto_detect_com_port())
        with self.assertRaises(OSError):
            LcdFalso()

    def test_posicao_fora_da_tela_e_erro_de_programacao(self):
        self.lcd.SetOrientation(Orientation.LANDSCAPE)
        with self.assertRaises(ValueError):
            self.lcd.DisplayPILImage(Image.new("RGB", (10, 10)), 480, 0)

    def test_retrato_por_padrao(self):
        self.assertEqual((self.lcd.get_width(), self.lcd.get_height()), (320, 480))
        self.lcd.SetOrientation(Orientation.REVERSE_PORTRAIT)
        self.assertEqual(self.envios()[-1][6:], bytes([101, 1, 64, 1, 224]))

    def test_reset_desiste_se_a_tela_nao_voltar(self):
        relogio = [0.0]
        with mock.patch.object(usb_direto.time, "monotonic", lambda: relogio[0]), \
                mock.patch.object(usb_direto.time, "sleep", lambda s: relogio.__setitem__(0, relogio[0] + s)):
            LcdFalso.presente = False  # depois do reset, a tela some de vez
            with self.assertRaises(OSError):
                self.lcd.Reset()
        self.assertGreater(relogio[0], 5)
        self.assertIsNone(self.lcd.lcd_serial)

    def test_fechar_duas_vezes_e_falha_ao_soltar(self):
        with mock.patch.object(LcdFalso, "_soltar", side_effect=RuntimeError("libusb")):
            self.lcd.closeSerial()  # não levanta
        self.assertIsNone(self.lcd.lcd_serial)
        self.lcd.closeSerial()


class EscolherTransporte(unittest.TestCase):
    def test_auto_no_mac_com_revisao_a_usa_usb(self):
        with mock.patch.object(usb_direto, "problema", return_value=None):
            self.assertEqual(saida.escolher_transporte("auto", "A", "darwin"), ("usb", ""))

    def test_auto_fora_do_mac_ou_outra_revisao_usa_serial(self):
        self.assertEqual(saida.escolher_transporte("auto", "A", "linux"), ("serial", ""))
        self.assertEqual(saida.escolher_transporte("auto", "C", "darwin"), ("serial", ""))
        self.assertEqual(saida.escolher_transporte("serial", "A", "darwin"), ("serial", ""))

    def test_sem_libusb_cai_para_serial_com_aviso(self):
        with mock.patch.object(usb_direto, "problema", return_value="falta o libusb (brew install libusb)"):
            transporte, aviso = saida.escolher_transporte("auto", "A", "darwin")
        self.assertEqual(transporte, "serial")
        self.assertIn("brew install libusb", aviso)

    def test_usb_pedido_explicitamente_sem_libusb_encerra(self):
        with mock.patch.object(usb_direto, "problema", return_value="falta o pyusb (pip install pyusb)"):
            with self.assertRaises(SystemExit):
                saida.escolher_transporte("usb", "A", "darwin")
        with self.assertRaises(SystemExit):
            saida.escolher_transporte("usb", "B", "darwin")


class TelaUSBComUsbDireto(unittest.TestCase):
    def test_tela_usb_usa_o_driver_direto_sem_a_biblioteca(self):
        LcdFalso.presente = True
        with mock.patch.object(usb_direto, "LcdRevAUsb", LcdFalso), \
                mock.patch.object(usb_direto, "PAUSA_BITMAP", 0), mock.patch.object(usb_direto, "PAUSA_ORIENTACAO", 0), \
                mock.patch.object(usb_direto, "ESPERA_RESET", 0):
            from pathlib import Path

            t = saida.TelaUSB("A", "AUTO", 30, Path("/nao/existe"), paisagem=True, transporte="usb")
            t.mostrar(Image.new("RGB", (480, 320)))
            pixels = [d for _, d in t.lcd.lcd_serial.envios if len(d) > 16]
        self.assertEqual([len(p) for p in pixels], [480 * 320 * 2])


if __name__ == "__main__":
    unittest.main()


class LibusbEmbarcada(unittest.TestCase):
    """No Ombro.app não há Homebrew: a libusb vem do pacote libusb-package."""

    @unittest.skipUnless(importlib.util.find_spec("usb"), "sem o pyusb (a CI no Linux não o instala)")
    def test_a_embarcada_vem_antes_da_do_sistema(self):
        import usb.backend.libusb1 as libusb1

        chamadas = []

        def get_backend(find_library=None):
            chamadas.append(find_library("usb-1.0") if find_library else None)
            return object()

        with mock.patch.object(libusb1, "get_backend", side_effect=get_backend), \
                mock.patch.object(usb_direto, "_libusb_embarcada", return_value="/app/libusb-1.0.dylib"):
            self.assertIsNotNone(usb_direto._backend())
        self.assertEqual(chamadas, ["/app/libusb-1.0.dylib"])  # nem chega a procurar a do sistema

    @unittest.skipUnless(importlib.util.find_spec("usb"), "sem o pyusb (a CI no Linux não o instala)")
    def test_sem_a_embarcada_procura_a_do_sistema(self):
        import usb.backend.libusb1 as libusb1

        with mock.patch.object(libusb1, "get_backend", return_value=object()) as gb, \
                mock.patch.object(usb_direto, "_libusb_embarcada", return_value=None):
            self.assertIsNotNone(usb_direto._backend())
        gb.assert_called_once_with()

    def test_sem_o_pacote_e_none(self):
        with mock.patch.dict("sys.modules", {"libusb_package": None}):
            self.assertIsNone(usb_direto._libusb_embarcada())
