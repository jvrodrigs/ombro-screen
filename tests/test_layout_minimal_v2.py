"""Layout Minimal v2: tamanho, estados, dados ausentes e extremos, e a política de cores de CPU e RAM.

As cores são conferidas pelas regras (limiares) e por pixels de cor sólida das barras, que não
dependem da versão do Pillow ou do FreeType. Para conferir pixel a pixel na sua máquina, use
scripts/quadros_referencia.py (o Minimal v2 tem 14 quadros lá).
"""
import ctypes
import math
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest import mock

import layout_minimal_v2 as v2
from coletores import hardware
from coletores.claude import Janela, UsoClaude
from coletores.hardware import Hardware
from layout_minimal_v2 import (
    ATENCAO, CRITICO, LIMIARES_CPU, LIMIARES_RAM, NORMAL, Nivel, Tema, desenhar_minimal, mensagem_espera, nivel,
    nivel_ram, pct_cpu, pct_ram, texto_ram,
)
from mascote import ESTADOS, Mascote

AGORA = datetime(2026, 11, 14, 15, 30)
GB = 1024**3


def hw(**kw):
    base = dict(cpu_pct=37.0, ram_usada=int(9.5 * GB), ram_total=16 * GB)
    base.update(kw)
    return Hardware(**base)


def uso(pct5=42.0, **kw):
    base = dict(cinco_horas=Janela(pct5, AGORA + timedelta(hours=2)), semana=Janela(67.0, AGORA + timedelta(days=2)),
                fonte="statusline", coletado_em=AGORA, configurado=True, sessoes=2, trabalhando=1,
                status_sessoes=["busy", "idle"], tokens_entrada=18_400_000, tokens_saida=212_000,
                tokens_por_hora=[1000 * h if h <= 15 else None for h in range(24)])
    base.update(kw)
    return UsoClaude(**base)


def mascote(estado="trabalhando"):
    m = Mascote(cor_fixa=(120, 200, 90))
    m.estado = estado
    return m


def cores(img):
    """As cores (ou valores) de uma imagem, sem Image.getdata (obsoleto no Pillow 12, sem substituto no 11)."""
    return {c for _, c in img.getcolors(img.width * img.height)}


def quadro(h=None, u=None, estado="trabalhando", t=1.1):
    v2.reiniciar_niveis()
    return desenhar_minimal(h or hw(), u or uso(), mascote(estado), AGORA, t)


class Limiares(unittest.TestCase):
    def test_cpu(self):
        esperado = {0: NORMAL, 74: NORMAL, 74.99: NORMAL, 75: ATENCAO, 89: ATENCAO, 89.99: ATENCAO, 90: CRITICO, 100: CRITICO}
        for valor, n in esperado.items():
            self.assertEqual(nivel(valor, LIMIARES_CPU), n, valor)

    def test_ram(self):
        esperado = {50: NORMAL, 84.9: NORMAL, 85: ATENCAO, 93.9: ATENCAO, 94: CRITICO, 100: CRITICO}
        for valor, n in esperado.items():
            self.assertEqual(nivel(valor, LIMIARES_RAM), n, valor)

    def test_sem_valor_e_normal(self):
        self.assertEqual(nivel(None, LIMIARES_CPU), NORMAL)

    def test_histerese_sobe_na_hora_e_desce_com_margem(self):
        n = Nivel(LIMIARES_CPU)
        self.assertEqual(n.avaliar(75), ATENCAO)  # sobe na hora
        self.assertEqual(n.avaliar(74), ATENCAO)  # perto do limiar: não pisca
        self.assertEqual(n.avaliar(73.5), ATENCAO)
        self.assertEqual(n.avaliar(72.9), NORMAL)  # 2 pontos abaixo: desce
        self.assertEqual(n.avaliar(95), CRITICO)
        self.assertEqual(n.avaliar(80), ATENCAO)  # caiu de 90 - 2: desce um nível, não dois
        self.assertEqual(n.avaliar(None), NORMAL)

    def test_pressao_de_memoria_so_piora(self):
        self.assertEqual(nivel_ram(NORMAL, "critico"), CRITICO)
        self.assertEqual(nivel_ram(NORMAL, "atencao"), ATENCAO)
        self.assertEqual(nivel_ram(CRITICO, "normal"), CRITICO)  # a ocupação ainda vale
        self.assertEqual(nivel_ram(ATENCAO, None), ATENCAO)  # sem leitura da pressão: só a ocupação
        self.assertEqual(nivel_ram(NORMAL, "estranho"), NORMAL)

    def test_cores(self):
        self.assertEqual([v2.cor_nivel(n, Tema.AZUL) for n in (NORMAL, ATENCAO, CRITICO)],
                         [Tema.AZUL, Tema.AMARELO, Tema.VERMELHO])


class Memoria(unittest.TestCase):
    def test_total_real_de_cada_mac(self):
        for total in (8, 16, 18, 24, 32, 36, 64, 96):
            h = hw(ram_total=total * GB, ram_usada=int(total * GB * .5))
            self.assertAlmostEqual(pct_ram(h), 50.0, places=3)
            self.assertTrue(texto_ram(h).endswith(f"/{total} GB"), texto_ram(h))
        self.assertEqual(texto_ram(hw(ram_total=16 * GB, ram_usada=int(14.2 * GB))), "14,2/16 GB")

    def test_indisponivel(self):
        self.assertIsNone(pct_ram(hw(ram_total=0)))
        self.assertIsNone(pct_ram(Hardware(disponivel=False, ram_total=16 * GB, ram_usada=GB)))
        self.assertEqual(texto_ram(hw(ram_total=0)), "—")

    def test_valores_fora_da_faixa(self):
        self.assertEqual(pct_ram(hw(ram_usada=40 * GB, ram_total=16 * GB)), 100.0)
        self.assertEqual(pct_cpu(hw(cpu_pct=130.0)), 100.0)
        self.assertEqual(pct_cpu(hw(cpu_pct=-3.0)), 0.0)
        self.assertIsNone(pct_cpu(hw(cpu_pct=math.nan)))
        self.assertIsNone(pct_cpu(Hardware(disponivel=False)))  # sem leitura não vira 0 %

    def test_pressao_lida_do_kernel(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIsNone(hardware.pressao_memoria())

        class LibcFalsa:
            def __init__(self, valor, erro=0):
                self.valor, self.erro = valor, erro

            def sysctlbyname(self, nome, ptr, tam, novo, novo_tam):
                ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int)).contents.value = self.valor
                return self.erro

        with mock.patch.object(sys, "platform", "darwin"):
            for valor, esperado in ((1, "normal"), (2, "atencao"), (4, "critico"), (9, None)):
                with mock.patch.object(hardware, "_libc", LibcFalsa(valor)):
                    self.assertEqual(hardware.pressao_memoria(), esperado)
            with mock.patch.object(hardware, "_libc", LibcFalsa(4, erro=-1)):
                self.assertIsNone(hardware.pressao_memoria())

    @unittest.skipUnless(sys.platform == "darwin", "só no macOS")
    def test_pressao_neste_mac(self):
        self.assertIn(hardware.pressao_memoria(), ("normal", "atencao", "critico"))


class Rodape(unittest.TestCase):
    def test_nome_longo_corta_so_o_projeto(self):
        u = uso(esperando=1, espera_projeto="p" * 200, espera_desde=AGORA - timedelta(minutes=12))
        msg = mensagem_espera(u, AGORA, 60)
        self.assertLessEqual(len(msg), 60)
        self.assertTrue(msg.endswith("… espera há 12 min"))

    def test_nome_curto_e_agora(self):
        u = uso(esperando=1, espera_projeto="ombro", espera_desde=AGORA - timedelta(seconds=20))
        self.assertEqual(mensagem_espera(u, AGORA, 60), "ombro espera agora")
        self.assertEqual(mensagem_espera(uso(esperando=1), AGORA, 60), "sessão espera")  # sem horário


class Desenho(unittest.TestCase):
    def test_tamanho_em_todos_os_estados(self):
        for estado in ESTADOS:
            for t in (0.0, .6, 2.2):
                self.assertEqual(quadro(estado=estado, t=t).size, (480, 320))

    def test_todo_estado_do_cloud_tem_rotulo_no_cabecalho(self):
        self.assertEqual(set(v2.ESTADOS), set(ESTADOS))

    def test_sem_dados_e_extremos(self):
        self.assertEqual(quadro(Hardware(disponivel=False), UsoClaude(), "ocioso").size, (480, 320))
        extremo_h = hw(cpu_pct=100.0, ram_total=128 * GB, ram_usada=128 * GB, pressao_memoria="critico")
        extremo_u = uso(100.0, semana=Janela(100.0, AGORA + timedelta(days=6)), tokens_entrada=10**13, tokens_saida=10**12,
                        sessoes=99, esperando=40, espera_projeto="x" * 300, espera_desde=AGORA - timedelta(days=3),
                        tokens_por_hora=[10**12] * 24, desatualizado=True, coletado_em=AGORA - timedelta(hours=9))
        for estado in ESTADOS:
            self.assertEqual(quadro(extremo_h, extremo_u, estado).size, (480, 320))

    def test_mesmas_entradas_mesmo_quadro(self):
        self.assertEqual(quadro().tobytes(), quadro().tobytes())

    def test_nao_mexe_nas_bordas(self):
        img = quadro(hw(cpu_pct=100.0), uso(100.0), "reset", t=.9)  # o maior pulo e o confete
        for x in range(480):
            self.assertEqual(img.getpixel((x, 319)), Tema.FUNDO)
        self.assertGreaterEqual(v2.CLOUD_X, 336)  # o cloud não invade as colunas de números
        self.assertLessEqual(v2.CLOUD_Y + v2.CLOUD_H, v2.HW_DIV)  # nem a região de CPU e RAM
        self.assertLessEqual(v2.CLOUD_Y + v2.CLOUD_H, 200)  # e não divide faixa de 40 px com ela (envio USB)
        self.assertTrue(200 <= v2.CPU_Y - 8 and v2.RAM_Y + 8 <= 240)

    def cor_na_barra(self, img, y, x=v2.HW_BARRA_X + 3):
        return img.getpixel((x, y))

    def test_cores_das_barras_de_cpu_e_ram(self):
        for cpu, cor in ((40.0, Tema.LARANJA), (80.0, Tema.AMARELO), (95.0, Tema.VERMELHO)):
            self.assertEqual(self.cor_na_barra(quadro(hw(cpu_pct=cpu)), v2.CPU_Y), cor, cpu)
        for usada, cor in ((8, Tema.AZUL), (14, Tema.AMARELO), (15.5, Tema.VERMELHO)):
            self.assertEqual(self.cor_na_barra(quadro(hw(ram_usada=int(usada * GB))), v2.RAM_Y), cor, usada)
        self.assertEqual(self.cor_na_barra(quadro(hw(pressao_memoria="critico")), v2.RAM_Y), Tema.VERMELHO)

    def test_pressao_aparece_escrita_quando_e_ela_que_pinta(self):
        com, sem = quadro(hw(pressao_memoria="critico")), quadro(hw(pressao_memoria="normal"))
        canto = (v2.HW_BARRA_X + v2.HW_BARRA_W + 4, v2.RAM_Y - 8, 480, v2.RAM_Y + 8)
        self.assertNotEqual(com.crop(canto).tobytes(), sem.crop(canto).tobytes())
        self.assertIn(Tema.VERMELHO, cores(com.crop(canto)))

    def test_barra_proporcional_ao_total(self):
        img = quadro(hw(ram_total=32 * GB, ram_usada=8 * GB))  # 25 %
        cheio = sum(1 for x in range(v2.HW_BARRA_X, v2.HW_BARRA_X + v2.HW_BARRA_W) if img.getpixel((x, v2.RAM_Y)) == Tema.AZUL)
        self.assertAlmostEqual(cheio / v2.HW_BARRA_W, .25, delta=.03)  # os cortes entre segmentos tiram um pouco

    def test_barras_de_uso_mudam_de_cor_perto_do_limite(self):
        x, y = v2.BARRA_X + 3, v2.QUOTA_Y
        self.assertEqual(quadro(u=uso(42.0)).getpixel((x, y)), Tema.LARANJA)
        self.assertEqual(quadro(u=uso(80.0)).getpixel((x, y)), Tema.AMARELO)
        self.assertEqual(quadro(u=uso(93.0)).getpixel((x, y)), Tema.VERMELHO)

    def test_cabecalho_segue_o_estado_do_cloud(self):
        d = v2.ImageDraw.Draw(v2.Image.new("RGB", (10, 10)))
        for estado, (rotulo, cor) in v2.ESTADOS.items():
            cx = v2.X1 - d.textlength(rotulo, font=v2.fonte("Bold", 11)) - 9
            self.assertEqual(quadro(estado=estado).getpixel((round(cx), v2.CAB_Y)), cor, estado)

    def test_transicao_de_estado_atualiza_o_cabecalho(self):
        m = Mascote(cor_fixa=(1, 2, 3))
        m.ir_para("esperando", 0)
        a = desenhar_minimal(hw(), uso(esperando=1), m, AGORA, .1)
        m.ir_para("trabalhando", 1)
        b = desenhar_minimal(hw(), uso(), m, AGORA, 1.1)
        self.assertNotEqual(a.crop((300, 0, 480, 28)).tobytes(), b.crop((300, 0, 480, 28)).tobytes())


class IconesESistema(unittest.TestCase):
    def test_icones(self):
        for nome in ("tokens", "saida", "sessoes", "sistema"):
            ic = v2.icone(nome, Tema.SECUNDARIO)
            self.assertEqual((ic.mode, ic.size), ("RGBA", (v2.ICONE, v2.ICONE)))
            alfa = ic.getchannel("A")
            self.assertGreater(sum(n for n, a in alfa.getcolors(256) if a > 128), 15, nome)  # tem traço
            self.assertEqual(alfa.getpixel((0, 0)) if nome != "sistema" else 0, 0)  # e fundo transparente
            self.assertIs(v2.icone(nome, Tema.SECUNDARIO), ic)  # feito uma vez só

    def test_icones_aparecem_nas_colunas(self):
        img = quadro()
        meio = (v2.EST_ROT + v2.EST_VAL) // 2
        for x in (v2.X0, 124, 236):
            regiao = img.crop((x, meio - 8, x + v2.ICONE, meio + 8))
            self.assertTrue(cores(regiao) - {Tema.FUNDO}, x)

    def test_nome_do_sistema_e_ram_total(self):
        self.assertTrue(v2.texto_sistema(hw(ram_total=16 * GB)).endswith(" • 16 GB"))
        self.assertTrue(v2.texto_sistema(hw(ram_total=36 * GB)).endswith(" • 36 GB"))
        self.assertEqual(v2.texto_sistema(Hardware(disponivel=False)), v2.nome_sistema())  # sem RAM: só o nome
        v2.nome_sistema.cache_clear()
        try:
            with mock.patch.object(v2.platform, "system", return_value="Darwin"), \
                    mock.patch.object(v2.platform, "mac_ver", return_value=("26.1.2", ("", "", ""), "arm64")):
                self.assertEqual(v2.nome_sistema(), "macOS 26.1")
            v2.nome_sistema.cache_clear()
            with mock.patch.object(v2.platform, "system", return_value="Linux"):
                self.assertEqual(v2.nome_sistema(), "Linux")
        finally:
            v2.nome_sistema.cache_clear()

    @unittest.skipUnless(sys.platform == "darwin", "só no macOS")
    def test_neste_mac(self):
        self.assertTrue(v2.nome_sistema().startswith("macOS"))


class ParteParada(unittest.TestCase):
    """Reaproveitar a parte parada tem de dar o mesmo quadro que desenhar tudo do zero."""

    def test_com_e_sem_reaproveitar_dao_o_mesmo_quadro(self):
        v2._ultimo_fixo = None
        for i in range(40):
            agora = AGORA + timedelta(seconds=13 * i)  # passa de minuto: os textos de reset e de espera mudam
            h = hw(cpu_pct=float(i * 7 % 100), ram_usada=(i % 16) * GB)
            u = uso(esperando=1, espera_projeto="proj", espera_desde=AGORA - timedelta(minutes=3)) if 10 <= i < 25 else uso(
                91.0 if i > 30 else 42.0)
            estado = ESTADOS[i // 7 % len(ESTADOS)]
            v2.reiniciar_niveis()
            com = desenhar_minimal(h, u, mascote(estado), agora, 100 + i * .2)
            v2.reiniciar_niveis()
            sem = desenhar_minimal(h, u, mascote(estado), agora, 100 + i * .2, usar_cache=False)
            self.assertEqual(com.tobytes(), sem.tobytes(), f"quadro {i} ({estado})")

    def test_so_redesenha_quando_algo_parado_muda(self):
        with mock.patch.object(v2, "_desenhar_fixo", wraps=v2._desenhar_fixo) as fixo:
            v2._ultimo_fixo = None
            for i in range(10):  # o cloud anima e CPU e RAM mudam, o resto fica
                desenhar_minimal(hw(cpu_pct=i * 10.0), uso(), mascote(), AGORA, i * .2)
            self.assertEqual(fixo.call_count, 1)
            desenhar_minimal(hw(), uso(tokens_saida=1), mascote(), AGORA, 3.0)
            desenhar_minimal(hw(), uso(tokens_saida=1), mascote("ocioso"), AGORA, 3.2)  # o estado do cabeçalho mudou
            self.assertEqual(fixo.call_count, 3)


class Opcao(unittest.TestCase):
    def test_layout_na_configuracao(self):
        from config import OmbroConfig, validar

        self.assertEqual(validar("layout", "minimal-v2"), "minimal-v2")
        cfg = OmbroConfig(layout="minimal-v2", paisagem=False)
        self.assertTrue(cfg.animado and cfg.paisagem_efetiva)
        self.assertFalse(cfg.ombro)
        self.assertEqual(cfg.intervalo_efetivo, .2)
        self.assertEqual(OmbroConfig().layout, "auto")  # o padrão continua o layout do Ombro
        self.assertTrue(replace(OmbroConfig(), layout="ombro").ombro)


if __name__ == "__main__":
    unittest.main()
