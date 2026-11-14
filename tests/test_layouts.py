"""Os layouts desenham com dados de demonstração, vazios e extremos, no tamanho certo.

Não há comparação pixel a pixel com imagens guardadas: outra versão do Pillow ou do FreeType
desenha as letras com pixels um pouco diferentes. Os testes conferem o que não depende disso
(tamanho, determinismo, cores sólidas das barras, textos); para conferir que uma mudança não
alterou nenhum pixel na sua máquina, use scripts/quadros_referencia.py.
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from coletores.claude import Janela, UsoClaude
from coletores.demo import Demo
from coletores.hardware import Hardware
import layout_ombro
from layout_ombro import ALTURA, LARGURA, PASSO, X0, _barra, _desde, _reseta, _yc, desenhar_ombro, rotulo_estado
from mascote import COL_H, COL_W, COL_X, COL_Y, ESTADOS, Mascote
from render import (
    CORAL, VERMELHO, aviso_uso, br, cor_carga, desenhar, gb, quando_reseta, taxa, texto_sessoes, tokens,
)

AGORA = datetime(2026, 11, 13, 15, 30)
GB = 1024**3


def mascote(estado="ocioso"):
    m = Mascote(cor_fixa=(120, 200, 90))
    m.estado = estado
    return m


def uso(pct5=42.0, **kw):
    base = dict(cinco_horas=Janela(pct5, AGORA + timedelta(hours=2)), semana=Janela(60.0, AGORA + timedelta(days=3)),
                fonte="statusline", coletado_em=AGORA, configurado=True, sessoes=1, status_sessoes=["idle"],
                tokens_por_hora=[1000 * h if h <= 15 else None for h in range(24)])
    base.update(kw)
    return UsoClaude(**base)


def extremos():
    h = Hardware(cpu_pct=100.0, nucleos=[("E", 100.0)] * 4, gpu_pct=100.0, ram_usada=128 * GB, ram_total=128 * GB,
                 disco_pct=100, rede_down=10 * 1024**3, temp_cpu=110.0, temp_gpu=110.0, watts_soc=999.9,
                 hist_cpu=[100.0] * 200, hist_gpu=[0.0] * 200, macmon=True)
    u = uso(100.0, semana=Janela(100.0, AGORA + timedelta(days=6)), desatualizado=True,
            coletado_em=AGORA - timedelta(hours=9), tokens_entrada=10**13, tokens_saida=10**12, sessoes=40,
            trabalhando=20, esperando=20, status_sessoes=["busy", "waiting"] * 20,
            espera_projeto="p" * 80, espera_desde=AGORA - timedelta(days=2), tokens_por_hora=[10**12] * 24)
    return h, u


class Layouts(unittest.TestCase):
    def test_ombro_em_todos_os_cenarios_do_demo(self):
        demo = Demo()
        for i, _ in enumerate(Demo._CENARIOS):
            demo.t0 -= 1  # avança um cenário (ciclo de 1 s)
            m = Mascote()
            u = demo.claude(ciclo=1)
            m.ir_para(m.decidir(u, i), i)
            img = desenhar_ombro(demo.hardware(), u, m, datetime.now(), i + .5)
            self.assertEqual(img.size, (480, 320))

    def test_demo_sem_ciclo_fica_esperando(self):
        u = Demo().claude()  # o README promete: com --demo, o cloud fica em "esperando"
        self.assertEqual((u.esperando, u.espera_projeto), (1, "ombro-screen"))
        self.assertEqual(Mascote().decidir(u, 0), "esperando")

    def test_ombro_sem_dados(self):
        m = Mascote()
        for estado in ESTADOS:
            m.estado = estado
            img = desenhar_ombro(Hardware(), UsoClaude(), m, datetime.now(), 1.0)
            self.assertEqual(img.size, (480, 320))

    def test_classico_nas_duas_orientacoes(self):
        demo = Demo()
        for paisagem, tamanho in ((False, (320, 480)), (True, (480, 320))):
            self.assertEqual(desenhar(demo.hardware(), demo.claude(), paisagem_=paisagem).size, tamanho)
            self.assertEqual(desenhar(Hardware(), UsoClaude(), paisagem_=paisagem).size, tamanho)

    def test_formatacao_de_tokens(self):
        self.assertEqual(tokens(950), "950")
        self.assertEqual(tokens(51_000), "51k")
        self.assertEqual(tokens(3_200_000), "3,2M")
        self.assertEqual(tokens(1_500_000_000), "1,5B")


    def test_valores_extremos(self):
        h, u = extremos()
        for estado in ESTADOS:
            self.assertEqual(desenhar_ombro(h, u, mascote(estado), AGORA, 1.0).size, (LARGURA, ALTURA))
        for paisagem, tamanho in ((False, (320, 480)), (True, (480, 320))):
            self.assertEqual(desenhar(h, u, AGORA, paisagem_=paisagem).size, tamanho)

    def test_mesmas_entradas_mesmo_quadro(self):
        h = Demo().hardware()
        for estado in ESTADOS:
            a = desenhar_ombro(h, uso(), mascote(estado), AGORA, 3.3).tobytes()
            b = desenhar_ombro(h, uso(), mascote(estado), AGORA, 3.3).tobytes()
            self.assertEqual(a, b, estado)
        self.assertEqual(desenhar(h, uso(), AGORA).tobytes(), desenhar(h, uso(), AGORA).tobytes())

    def test_o_cloud_fica_dentro_da_tela(self):
        self.assertLessEqual(COL_X + COL_W, LARGURA)
        self.assertLessEqual(COL_Y + COL_H, ALTURA)
        m = mascote("reset")
        for t in (0, .5, .9, 1.3, 2.0):  # o pulo mais alto e o confete
            self.assertEqual(m.camada(t).size, (COL_W * 2, COL_H * 2))

    def test_barra_muda_de_cor_perto_do_limite(self):
        x, y = round(X0 + 12 * PASSO), round(_yc(2))  # dentro do 2.º bloco da barra de 5h

        def cor(pct):
            return desenhar_ombro(Hardware(), uso(pct), mascote(), AGORA, 1.0).getpixel((x, y))

        for pct, esperada in ((42, CORAL), (93, VERMELHO), (100, VERMELHO)):
            self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(cor(pct), esperada)), (pct, cor(pct)))


class ParteParada(unittest.TestCase):
    """O quadro com a parte parada reaproveitada tem de ser idêntico ao desenhado do zero."""

    def sequencia(self):
        h = Hardware(ram_total=16 * GB)
        espera = dict(esperando=1, espera_projeto="proj", espera_desde=AGORA - timedelta(minutes=3), status_sessoes=["waiting"])
        passos = []
        for i in range(40):
            agora = AGORA + timedelta(seconds=13 * i)  # passa de minuto: o "há N min" e o "reseta em" mudam
            hw = Hardware(cpu_pct=float(i * 7 % 100), ram_usada=(i % 9) * GB, ram_total=16 * GB) if i % 3 else h
            u = uso(**espera) if 10 <= i < 25 else uso(pct5=91.0 if i > 30 else 42.0)
            estado = ESTADOS[i // 8 % len(ESTADOS)]
            passos.append((hw, u, estado, agora, 100 + i * .2))
        return passos

    def test_com_e_sem_reaproveitar_dao_o_mesmo_quadro(self):
        for i, (hw, u, estado, agora, mono) in enumerate(self.sequencia()):
            com = desenhar_ombro(hw, u, mascote(estado), agora, mono)
            sem = desenhar_ombro(hw, u, mascote(estado), agora, mono, usar_cache=False)
            self.assertEqual(com.tobytes(), sem.tobytes(), f"quadro {i} ({estado})")

    def test_so_redesenha_quando_algo_parado_muda(self):
        h, u = Hardware(ram_total=16 * GB), uso()
        with mock.patch.object(layout_ombro, "_desenhar_fixo", wraps=layout_ombro._desenhar_fixo) as fixo:
            layout_ombro._ultimo_fixo = None
            for i in range(10):  # o cloud anima e a CPU muda, mas o resto fica igual
                desenhar_ombro(Hardware(cpu_pct=i * 10.0, ram_total=16 * GB), u, mascote("trabalhando"), AGORA, i * .2)
            self.assertEqual(fixo.call_count, 1)
            desenhar_ombro(h, uso(tokens_saida=1), mascote("trabalhando"), AGORA, 3.0)
            self.assertEqual(fixo.call_count, 2)
            desenhar_ombro(h, uso(tokens_saida=1), mascote("ocioso"), AGORA, 3.2)  # o rótulo do estado muda
            self.assertEqual(fixo.call_count, 3)


class Textos(unittest.TestCase):
    def test_barra_de_blocos(self):
        self.assertEqual(_barra(0, 20), "░" * 20)
        self.assertEqual(_barra(100, 20), "█" * 20)
        self.assertEqual(_barra(150, 10), "█" * 10)
        self.assertEqual(_barra(-5, 10), "░" * 10)
        self.assertEqual(_barra(42, 20), "█" * 8 + "░" * 12)

    def test_quando_reseta(self):
        self.assertEqual(quando_reseta(None, AGORA), "")
        self.assertEqual(quando_reseta(Janela(1, AGORA - timedelta(seconds=1)), AGORA), "reiniciando")
        self.assertEqual(quando_reseta(Janela(1, AGORA + timedelta(minutes=9, seconds=59)), AGORA), "reseta em 9 min")
        self.assertEqual(quando_reseta(Janela(1, AGORA + timedelta(hours=2, minutes=5)), AGORA), "reseta em 2h05")
        self.assertEqual(quando_reseta(Janela(1, datetime(2026, 11, 16, 9, 0)), AGORA), "reseta seg 09:00")

    def test_reseta_no_layout_do_ombro(self):
        self.assertEqual(_reseta(None, AGORA), "—")
        self.assertEqual(_reseta(Janela(1, AGORA + timedelta(hours=2)), AGORA), "reseta em 2h00 (17:30)")
        self.assertEqual(_reseta(Janela(1, datetime(2026, 11, 16, 9, 0)), AGORA), "reseta seg 09:00")

    def test_desde(self):
        self.assertEqual(_desde(None, AGORA), "")
        self.assertEqual(_desde(AGORA - timedelta(seconds=30), AGORA), "há instantes")
        self.assertEqual(_desde(AGORA - timedelta(minutes=12), AGORA), "há 12 min")
        self.assertEqual(_desde(AGORA - timedelta(hours=1, minutes=5), AGORA), "há 1h05")
        self.assertEqual(_desde(AGORA + timedelta(minutes=1), AGORA), "há instantes")  # relógio adiantado

    def test_rotulos_de_estado(self):
        u = uso(trabalhando=2, esperando=1)
        self.assertEqual([rotulo_estado(e, u) for e in ESTADOS],
                         ["nenhuma ativa", "2 trabalhando", "1 esperando resposta", "plano quase no fim", "plano resetou",
                          "concluído"])

    def test_aviso_de_uso(self):
        self.assertEqual(aviso_uso(UsoClaude(), AGORA), "sem dados de uso: veja o README")
        self.assertEqual(aviso_uso(uso(), AGORA), "")
        self.assertEqual(aviso_uso(uso(desatualizado=True, coletado_em=AGORA - timedelta(minutes=45)), AGORA),
                         "números de 45 min atrás")
        self.assertEqual(aviso_uso(uso(desatualizado=True, coletado_em=AGORA - timedelta(hours=3)), AGORA),
                         "números de 3 h atrás")

    def test_sessoes_piscam_quando_alguem_espera(self):
        u = uso(esperando=1, sessoes=2, trabalhando=1)
        self.assertEqual(texto_sessoes(u, AGORA.replace(second=2))[0], "1 espera você")
        self.assertEqual(texto_sessoes(u, AGORA.replace(second=3))[0], "2 · 1 ativ.")

    def test_numeros_em_pt_br(self):
        self.assertEqual(br(3.14159), "3,1")
        self.assertEqual(gb(int(14.25 * GB)), "14,2")
        self.assertEqual(taxa(2.5 * 1024**2), "2,5 MB/s")
        self.assertEqual(taxa(300 * 1024), "300 kB/s")
        self.assertEqual(cor_carga(74.9, CORAL), CORAL)
        self.assertEqual(cor_carga(90, CORAL), VERMELHO)

if __name__ == "__main__":
    unittest.main()
