"""Máquina de estados do cloud."""
import unittest
from datetime import datetime, timedelta

from coletores.claude import Janela, UsoClaude
import colorsys

from mascote import CAMPOS, DURACAO_MISTURA, ESTADOS, Mascote, cor_de_hex, cor_sorteada, desenhar_cloud, parametros

AGORA = datetime(2026, 11, 13, 15, 0)


def uso(pct5=40.0, pct_sem=50.0, reset5=timedelta(hours=2), reset_sem=timedelta(days=3), **kw):
    return UsoClaude(cinco_horas=Janela(pct5, AGORA + reset5), semana=Janela(pct_sem, AGORA + reset_sem), **kw)


class Decidir(unittest.TestCase):
    def test_prioridade_dos_estados(self):
        m = Mascote()
        self.assertEqual(m.decidir(uso(), 0), "ocioso")
        self.assertEqual(m.decidir(uso(trabalhando=1), 1), "trabalhando")
        self.assertEqual(m.decidir(uso(pct5=95, trabalhando=1), 2), "alerta")
        self.assertEqual(m.decidir(uso(pct_sem=91), 3), "alerta")
        self.assertEqual(m.decidir(uso(pct5=95, esperando=1), 4), "esperando")

    def test_so_volta_a_ocioso_depois_de_3_segundos(self):
        m = Mascote()
        m.ir_para(m.decidir(uso(trabalhando=1), 0), 0)
        self.assertEqual(m.decidir(uso(), 1.0), "trabalhando")
        self.assertEqual(m.decidir(uso(), 3.5), "trabalhando")
        self.assertEqual(m.decidir(uso(), 4.1), "ocioso")

    def test_reset_da_janela_de_5h(self):
        m = Mascote()
        m.decidir(uso(pct5=88), 0)
        self.assertEqual(m.decidir(uso(pct5=2, reset5=timedelta(hours=5)), 1), "reset")  # avançou 3h
        self.assertEqual(m.decidir(uso(pct5=2, reset5=timedelta(hours=5)), 3.9), "reset")
        self.assertEqual(m.decidir(uso(pct5=2, reset5=timedelta(hours=5)), 4.1), "ocioso")

    def test_reset_da_semana(self):
        m = Mascote()
        m.decidir(uso(pct_sem=97), 0)
        self.assertEqual(m.decidir(uso(pct_sem=1, reset_sem=timedelta(days=7)), 1), "reset")

    def test_horario_de_reset_que_oscila_pouco_nao_conta(self):
        m = Mascote()
        m.decidir(uso(), 0)
        self.assertEqual(m.decidir(uso(reset5=timedelta(hours=2, minutes=20)), 1), "ocioso")

    def test_sem_dados_de_uso(self):
        m = Mascote()
        self.assertEqual(m.decidir(UsoClaude(), 0), "ocioso")

    def test_cada_estado_isolado(self):
        casos = {
            "ocioso": uso(),
            "trabalhando": uso(trabalhando=2),
            "esperando": uso(esperando=1),
            "alerta": uso(pct5=90),  # 90 % já é alerta
        }
        for esperado, u in casos.items():
            self.assertEqual(Mascote().decidir(u, 0), esperado, esperado)
        self.assertEqual(Mascote().decidir(uso(pct5=89.9, pct_sem=89.9), 0), "ocioso")

    def test_reset_vence_todos_os_outros(self):
        m = Mascote()
        m.decidir(uso(pct5=95, esperando=1), 0)
        self.assertEqual(m.decidir(uso(pct5=1, esperando=1, reset5=timedelta(hours=5)), 1), "reset")

    def test_primeira_leitura_nao_dispara_reset(self):
        self.assertEqual(Mascote().decidir(uso(reset5=timedelta(hours=5)), 0), "ocioso")

    def test_janela_que_some_e_volta_nao_dispara_reset_sozinha(self):
        m = Mascote()
        m.decidir(uso(), 0)
        m.decidir(UsoClaude(), 1)  # a fonte sumiu por um tempo
        self.assertEqual(m.decidir(uso(), 2), "ocioso")

    def test_atividade_no_meio_da_calmaria_recomeca_a_contagem(self):
        m = Mascote()
        m.ir_para("trabalhando", 0)
        self.assertEqual(m.decidir(uso(), 1), "trabalhando")  # calmo desde 1 s
        self.assertEqual(m.decidir(uso(trabalhando=1), 2.5), "trabalhando")  # voltou a trabalhar
        self.assertEqual(m.decidir(uso(), 3), "trabalhando")  # calmo de novo, desde 3 s
        self.assertEqual(m.decidir(uso(), 5.5), "trabalhando")
        self.assertEqual(m.decidir(uso(), 6.1), "ocioso")

    def test_esperando_volta_para_trabalhando_na_hora(self):
        m = Mascote()
        m.ir_para("esperando", 0)
        self.assertEqual(m.decidir(uso(trabalhando=1), 0.1), "trabalhando")  # só a volta ao ocioso espera


class Transicoes(unittest.TestCase):
    def test_trocar_de_estado_guarda_o_anterior_e_sorteia_cor(self):
        m = Mascote()
        m.ir_para("trabalhando", 2.0)
        self.assertEqual((m.estado, m.inicio, m.anterior), ("trabalhando", 2.0, ("ocioso", 0.0)))
        cor = m.cor
        m.ir_para("trabalhando", 9.0)  # o mesmo estado: nada muda
        self.assertEqual((m.inicio, m.cor), (2.0, cor))

    def test_mistura_entre_estados_e_depois_so_o_novo(self):
        m = Mascote(cor_fixa=(1, 2, 3))
        m.ir_para("alerta", 10.0)
        p, u = m.parametros_atuais(10.0 + DURACAO_MISTURA / 2)
        self.assertTrue(0 < u < 1)
        ant, novo = parametros("ocioso", 10.25), parametros("alerta", .25)
        self.assertTrue(all(min(ant[k], novo[k]) - 1e-9 <= p[k] <= max(ant[k], novo[k]) + 1e-9 for k in CAMPOS))
        p, u = m.parametros_atuais(10.0 + DURACAO_MISTURA + .01)
        self.assertEqual(u, 1.0)
        so_o_novo = parametros("alerta", DURACAO_MISTURA + .01)
        for k in CAMPOS:
            self.assertAlmostEqual(p[k], so_o_novo[k], places=9)

    def test_cor_fixa_vale_desde_o_comeco(self):
        # antes, a cor fixa só entrava na primeira troca de estado; até lá, o cloud saía com cor sorteada
        self.assertEqual(Mascote(cor_fixa=(1, 2, 3)).cor, (1, 2, 3))

    def test_cor_sorteada_le_bem_no_fundo_escuro(self):
        for _ in range(200):
            r, g, b = cor_sorteada()
            _, luz, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            self.assertTrue(.49 <= luz <= .61 and .56 <= sat <= .73, (r, g, b))

    def test_cor_de_hex(self):
        self.assertEqual(cor_de_hex("#3D7A4F"), (61, 122, 79))
        self.assertEqual(cor_de_hex("ff0000"), (255, 0, 0))


class Animacao(unittest.TestCase):
    def test_parametros_dependem_so_do_tempo(self):
        for estado in ESTADOS:
            self.assertEqual(parametros(estado, 1.37), parametros(estado, 1.37))
            self.assertEqual(set(parametros(estado, 0)), set(CAMPOS))

    def test_ocioso_pisca_a_cada_4_2_segundos(self):
        self.assertEqual(parametros("ocioso", 0.05)["pisca"], 1.0)
        self.assertEqual(parametros("ocioso", 1.0)["pisca"], 0.0)
        self.assertEqual(parametros("ocioso", 4.25)["pisca"], 1.0)

    def test_pulos(self):
        self.assertLess(parametros("esperando", .36)["dy"], -20)  # no ar
        self.assertEqual(parametros("esperando", 1.0)["dy"], 0.0)  # no chão
        self.assertLess(parametros("reset", .8)["dy"], -50)
        self.assertEqual(parametros("trabalhando", 0)["dy"], -12.0)  # começa no alto do quique
        self.assertAlmostEqual(parametros("trabalhando", .175)["dy"], 0.0)  # e encosta no chão

    def test_alerta_treme_de_um_lado_para_o_outro(self):
        lados = {parametros("alerta", t / 24)["dx"] > 0 for t in range(4)}
        self.assertEqual(lados, {True, False})


class Desenho(unittest.TestCase):
    def test_todos_os_estados_desenham(self):
        for estado in ESTADOS:
            for t in (0.0, 0.3, 1.1, 2.0):
                camada = desenhar_cloud(parametros(estado, t), estado, t, (120, 200, 90))
                self.assertEqual(camada.mode, "RGBA")
                self.assertIsNotNone(camada.getbbox(), f"{estado} em t={t} saiu vazio")

    def test_cor_fixa_permanece_ao_trocar_de_estado(self):
        m = Mascote(cor_fixa=(1, 2, 3))
        m.ir_para("trabalhando", 1.0)
        self.assertEqual(m.cor, (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
