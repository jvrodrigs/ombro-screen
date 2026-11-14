"""Máquina de estados do cloud."""
import unittest
from datetime import datetime, timedelta

from coletores.claude import Janela, UsoClaude
from mascote import ESTADOS, Mascote, desenhar_cloud, parametros

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
