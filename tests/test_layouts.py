"""Os layouts desenham com dados de demonstração e com dados vazios, no tamanho certo."""
import unittest
from datetime import datetime

from coletores.claude import UsoClaude
from coletores.demo import Demo
from coletores.hardware import Hardware
from layout_ombro import desenhar_ombro
from mascote import ESTADOS, Mascote
from render import desenhar, tokens


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


if __name__ == "__main__":
    unittest.main()
