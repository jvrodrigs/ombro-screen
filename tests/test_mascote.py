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

    def test_so_sossega_depois_de_3_segundos(self):
        m = Mascote()
        m.ir_para(m.decidir(uso(trabalhando=1), 0), 0)
        self.assertEqual(m.decidir(uso(), 1.0), "trabalhando")
        self.assertEqual(m.decidir(uso(), 3.5), "trabalhando")
        self.assertEqual(m.decidir(uso(), 4.1), "concluido")  # terminou de trabalhar

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
        self.assertEqual(m.decidir(uso(), 6.1), "concluido")

    def test_esperando_volta_para_trabalhando_na_hora(self):
        m = Mascote()
        m.ir_para("esperando", 0)
        self.assertEqual(m.decidir(uso(trabalhando=1), 0.1), "trabalhando")  # só a volta ao ocioso espera


def passo(m, u, t):
    """Um quadro do laço do tela.py: decide e troca de estado."""
    m.ir_para(m.decidir(u, t), t)
    return m.estado


class Concluido(unittest.TestCase):
    """Quando o Claude termina, o cloud fica em "concluido" por até 5 min e depois volta a dormir."""

    def test_dura_5_minutos_e_depois_dorme(self):
        m = Mascote()
        self.assertEqual(passo(m, uso(trabalhando=1), 0), "trabalhando")
        self.assertEqual(passo(m, uso(), 10), "trabalhando")  # terminou às 10 s, mas espera o status assentar
        self.assertEqual(passo(m, uso(), 13.1), "concluido")
        self.assertEqual(passo(m, uso(), 10 + 299), "concluido")
        self.assertEqual(passo(m, uso(), 10 + 300.1), "ocioso")
        self.assertEqual(passo(m, uso(), 2000), "ocioso")

    def test_nova_atividade_interrompe_e_recomeca_a_contagem(self):
        m = Mascote()
        passo(m, uso(trabalhando=1), 0)
        passo(m, uso(), 10)
        self.assertEqual(passo(m, uso(), 100), "concluido")
        self.assertEqual(passo(m, uso(trabalhando=1), 200), "trabalhando")  # nova mensagem: volta na hora
        passo(m, uso(), 250)  # terminou de novo
        self.assertEqual(passo(m, uso(), 253.1), "concluido")
        self.assertEqual(passo(m, uso(), 250 + 299), "concluido")  # os 5 min contam do último término
        self.assertEqual(passo(m, uso(), 250 + 300.1), "ocioso")

    def test_cancelar_quando_ele_espera_voce_nao_e_concluir(self):
        m = Mascote()
        passo(m, uso(trabalhando=1), 0)
        self.assertEqual(passo(m, uso(esperando=1), 1), "esperando")
        passo(m, uso(), 2)
        self.assertEqual(passo(m, uso(), 5.1), "ocioso")

    def test_responder_e_ele_terminar_e_concluir(self):
        m = Mascote()
        passo(m, uso(trabalhando=1), 0)
        passo(m, uso(esperando=1), 1)
        passo(m, uso(trabalhando=1), 2)  # você respondeu, ele seguiu
        passo(m, uso(), 3)
        self.assertEqual(passo(m, uso(), 6.1), "concluido")

    def test_quem_espera_voce_e_o_plano_no_limite_vem_antes(self):
        m = Mascote()
        passo(m, uso(trabalhando=1), 0)
        passo(m, uso(), 1)
        self.assertEqual(passo(m, uso(), 4.1), "concluido")
        self.assertEqual(passo(m, uso(esperando=1), 5), "esperando")
        m = Mascote()
        passo(m, uso(trabalhando=1), 0)
        passo(m, uso(pct5=95), 1)
        self.assertEqual(passo(m, uso(pct5=95), 4.1), "alerta")

    def test_ao_abrir_sem_nada_rodando_dorme(self):
        m = Mascote()
        self.assertEqual(passo(m, uso(), 0), "ocioso")
        self.assertEqual(passo(m, uso(), 10), "ocioso")

    def test_duracao_ajustavel(self):
        m = Mascote(duracao_concluido=7)
        passo(m, uso(trabalhando=1), 0)
        passo(m, uso(), 1)
        self.assertEqual(passo(m, uso(), 4.1), "concluido")
        self.assertEqual(passo(m, uso(), 8.1), "ocioso")

    def test_ciclo_de_demonstracao_mostra_todos_os_estados(self):
        from coletores.demo import Demo

        ciclo = 7.0
        m = Mascote(duracao_concluido=ciclo)  # o que o tela.py faz com --demo-ciclo
        vistos = {}
        t = 0.0
        for volta in range(2):
            for nome, pct5, reset5, status in Demo._CENARIOS:
                u = uso(pct5=pct5, reset5=reset5, trabalhando=status.count("busy"), esperando=status.count("waiting"))
                fim = t + ciclo
                while t < fim:
                    vistos.setdefault(nome, set()).add(passo(m, u, t))
                    t += .2
        for nome in vistos:
            self.assertIn(nome, vistos[nome], f"o cenário {nome} não mostrou o estado {nome}: {vistos[nome]}")
        self.assertEqual(set(vistos), set(ESTADOS))


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

    def test_concluido_pula_uma_vez_e_depois_so_respira(self):
        self.assertLess(parametros("concluido", .6)["dy"], -20)  # o pulinho de satisfação
        respiro = [abs(parametros("concluido", 1.1 + i * .37)["dy"]) for i in range(100)]
        self.assertLessEqual(max(respiro), 1.2 + 1e-9)  # nos 5 min seguintes, nada agitado

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
