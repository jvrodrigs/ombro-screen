"""O mascote da telinha é o do mascotinhos: as animações do pacote da demonstração ("vivo"), o Animador dele para as
trocas e os quadros que a própria biblioteca desenha. Nada de movimento é refeito no Ombro.

A referência é o mascotinhos instalado (a versão fixada): cada teste compara o que o Ombro desenha com o que a
biblioteca desenha sozinha, com as peças dela, no mesmo ambiente. Por isso vale em qualquer máquina.
"""
from __future__ import annotations

import random
import sys
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import mascotinhos as oa  # noqa: E402

import avatares  # noqa: E402
import config  # noqa: E402
import mascote  # noqa: E402

NATIVOS = mascote.ESTADOS  # os seis estados: todos do pacote vivo, alerta e reset inclusive
VIVO = oa.Biblioteca.carregar(pacote="vivo")
CLASSICO = oa.Biblioteca.carregar(pacote="classico")
INSTANTES = (0.0, 0.2, 0.6, 1.0, 1.4, 2.2, 3.0, 4.6, 7.2, 12.4)  # a 5 quadros por segundo: múltiplos de 0,2 s
# (instante, estado novo ou None, avatar novo ou None): trocas no meio de pulos, troca de avatar antes e depois do meio
# da transição, o mesmo avatar pedido de novo, e troca de avatar junto com a de estado
ROTEIRO = ((0.0, "ocioso", None), (1.3, "trabalhando", None), (1.6, None, "orbita"), (1.75, None, "cubo"),
           (2.4, "esperando", None), (2.5, None, "pixel"), (2.85, None, "pixel"), (4.0, "alerta", None),
           (5.2, "reset", "nova"), (7.9, "concluido", None), (9.3, "ocioso", "ombro-cloud"), (10.0, "esperando", None))


def esperado(estado: str) -> oa.Biblioteca:
    """De qual pacote o estado vem: do vivo, todos (nenhum fica no clássico)."""
    return VIVO


def estilo_da_biblioteca() -> oa.Estilo:
    """O estilo padrão do mascotinhos (o enquadramento e as cores dele), com a fonte que vem nele."""
    return oa.Estilo(fonte=oa.fonte_jetbrains_mono())


class Pacote(unittest.TestCase):
    def test_o_pacote_e_o_da_demonstracao(self):
        self.assertEqual(mascote.PACOTE, "vivo")
        self.assertIn("vivo", oa.pacotes())
        self.assertFalse(hasattr(mascote, "DO_CLASSICO"))  # nenhum estado vem de outro pacote
        self.assertEqual(set(mascote.BIBLIOTECA.estados()), set(mascote.ESTADOS))

    def test_cada_estado_e_a_animacao_da_biblioteca(self):
        for e in mascote.ESTADOS:
            nosso, dela = mascote.BIBLIOTECA[e], esperado(e)[e]
            self.assertEqual(nosso.arquivo, dela.arquivo, e)  # o mesmo arquivo do pacote, sem cópia no Ombro
            self.assertEqual((nosso.olhos, nosso.extras), (dela.olhos, dela.extras), e)

    def test_os_seis_estados_sao_os_nativos_do_vivo(self):
        b = mascote.BIBLIOTECA
        self.assertEqual(b["ocioso"].olhos, "fechados")  # dorme
        self.assertIn("zzz", [x[0] for x in b["ocioso"].extras])
        self.assertEqual(b["trabalhando"].olhos, "semicerrados")
        self.assertEqual({x[0] for x in b["trabalhando"].extras}, {"linhas", "engrenagem"})
        self.assertEqual(b["esperando"].olhos, "redondos")
        self.assertEqual([x[1]["simbolo"] for x in b["esperando"].extras if x[0] == "sinal"], ["?"])
        self.assertEqual(b["concluido"].olhos, "felizes")
        self.assertIn("visto", [x[0] for x in b["concluido"].extras])
        self.assertEqual(b["alerta"].olhos, "redondos")  # aflito, vigiando a bateria
        self.assertEqual({x[0] for x in b["alerta"].extras}, {"bateria", "gotas"})
        self.assertEqual({x[0] for x in b["reset"].extras}, {"bateria", "faiscas"})  # a bateria enche de novo
        for e in ("alerta", "reset"):
            self.assertNotEqual(b[e].arquivo, CLASSICO[e].arquivo, e)

    def test_a_pose_e_a_da_biblioteca(self):
        for e in mascote.ESTADOS:
            for i in range(80):  # 16 s a 5 quadros por segundo
                t = round(i * 0.2, 6)
                self.assertEqual(mascote.parametros(e, t), esperado(e).parametros(e, t), f"{e} em t={t}")


class SemMovimentoProprio(unittest.TestCase):
    def test_o_mascote_e_o_animador_da_biblioteca(self):
        self.assertTrue(issubclass(mascote.Mascote, oa.Animador))
        for nome in ("ir_para", "cor_atual", "_avancar_troca", "_aplicar", "definir_cor_modo",
                     "_cor_do_estado"):
            self.assertIs(getattr(mascote.Mascote, nome), getattr(oa.Animador, nome), nome)
        proprios = {n for n, v in vars(mascote.Mascote).items() if callable(v) and not n.startswith("__")}
        # o avatar com a cor do agente, a decisão de estado e a troca de pacote (que só mistura a pose do pacote de
        # antes com a do novo, com o misturar da biblioteca, e cruza os dois desenhos do oa.desenhar: ver test_animacoes)
        self.assertEqual(proprios, {"usar", "decidir", "usar_pacote", "momento_seguro", "parametros_atuais", "camada"})

    def test_fora_de_uma_troca_de_pacote_a_pose_e_a_do_animador(self):
        m = mascote.Mascote(cor_fixa=(1, 2, 3))
        for e in mascote.ESTADOS:
            m.ir_para(e, 0.0)
            for t in (0.0, 0.3, 1.1, 4.0):
                self.assertEqual(m.parametros_atuais(t), oa.Animador.parametros_atuais(m, t), f"{e} {t}")
                self.assertEqual(m.camada(t).tobytes(), oa.Animador.camada(m, t).tobytes(), f"{e} {t}")

    def test_as_constantes_das_trocas_sao_as_da_biblioteca(self):
        self.assertEqual((mascote.TRANSICAO, mascote.REALCE, mascote.DURACAO_MISTURA, mascote.CAMPOS),
                         (oa.TRANSICAO, oa.REALCE, oa.DURACAO_MISTURA, oa.CAMPOS))
        self.assertIs(mascote.misturar, oa.misturar)
        self.assertIs(mascote.cor_sorteada, oa.cor_sorteada)


class Quadros(unittest.TestCase):
    def test_o_enquadramento_e_o_palco_da_biblioteca(self):
        self.assertEqual(mascote.ENQUADRAMENTO, oa.Enquadramento())  # a coluna do Ombro é o palco padrão da demo
        self.assertEqual(mascote.ESTILO.enquadramento.tamanho, (mascote.COL_W * mascote.S, mascote.COL_H * mascote.S))
        self.assertEqual(mascote.ESTILO.paleta, oa.Paleta())
        self.assertIsNone(mascote.ESTILO.volume)  # chapado, como a demonstração abre
        self.assertIsNone(mascote.ESTILO.terminal)

    def test_os_quadros_sao_os_que_a_biblioteca_desenha(self):
        """Cada avatar do Ombro em cada estado: o desenho do Ombro = o quadro pronto do mascotinhos, com a forma de
        exemplo dele (a mesma do avatars/ do Ombro) e o estilo padrão dele."""
        estilo, ex = estilo_da_biblioteca(), oa.exemplos()
        for aid, av in avatares.catalogo().avatares.items():
            f, cor = mascote.forma_de(av), avatares.cor_hex(av.cor)
            for e in mascote.ESTADOS:
                for t in INSTANTES:
                    nosso = mascote.desenhar_cloud(mascote.parametros(e, t), e, t, cor, 1.0, f)
                    dela = oa.quadro(ex[aid], e, t, cor, estilo=estilo, biblioteca=esperado(e))
                    self.assertEqual(nosso.tobytes(), dela.tobytes(), f"{aid}/{e} em t={t}")

    def test_os_extras_vem_da_biblioteca(self):
        """Os extras (zzZ, engrenagem, "?", ✓…) aparecem e somem com o alpha, como a biblioteca os desenha."""
        f, cor = mascote.forma_de(avatares.catalogo().avatares["ombro-cloud"]), (223, 133, 89)
        for e in NATIVOS:
            p = mascote.parametros(e, 2.2)
            sem = mascote.desenhar_cloud(p, e, 2.2, cor, 0.0, f)
            com = mascote.desenhar_cloud(p, e, 2.2, cor, 1.0, f)
            self.assertNotEqual(sem.tobytes(), com.tobytes(), f"{e}: os extras não apareceram")
            meio = mascote.desenhar_cloud(p, e, 2.2, cor, 0.37, f)
            dela = oa.desenhar(f, p, VIVO[e], 2.2, cor, 0.37, estilo_da_biblioteca())
            self.assertEqual(meio.tobytes(), dela.tobytes(), e)

    def test_nada_passa_da_coluna(self):
        w, h = mascote.COL_W * mascote.S, mascote.COL_H * mascote.S
        for aid, av in avatares.catalogo().avatares.items():
            f, cor = mascote.forma_de(av), avatares.cor_hex(av.cor)
            for e in mascote.ESTADOS:
                for i in range(0, 60, 3):
                    t = i * 0.2
                    x0, y0, x1, y1 = mascote.desenhar_cloud(mascote.parametros(e, t), e, t, cor, 1.0, f).getbbox()
                    self.assertTrue(x0 > 0 and y0 > 0 and x1 < w and y1 < h, f"{aid}/{e} em t={t} encosta na borda")


class Transicoes(unittest.TestCase):
    def _tocar(self, a, usar) -> list:
        cat, quadros, i, t = avatares.catalogo(), [], 0, 0.0
        while t < 11.0:
            while i < len(ROTEIRO) and ROTEIRO[i][0] <= t + 1e-9:
                _, estado, aid = ROTEIRO[i]
                i += 1
                if aid:
                    usar(a, cat.avatares[aid], t)
                if estado:
                    a.ir_para(estado, t)
            quadros.append((a.camada(t).tobytes(), a.estado, a.forma.avatar, a.cor, a.cor_atual(t)))
            t = round(t + 0.2, 6)  # 5 quadros por segundo, como a telinha
        return quadros

    def test_as_trocas_sao_as_do_animador_da_biblioteca(self):
        """O Mascote e um Animador montado só com peças do mascotinhos tocam o mesmo roteiro e dão os mesmos quadros,
        nos três modos de cor: a mistura entre estados, a troca de avatar e o realce são os da biblioteca."""
        cat = avatares.catalogo()
        nuvem = cat.avatares["ombro-cloud"]
        bib = VIVO
        for kw in (dict(cor_modo="agente"), dict(cor_modo="sorteada"), dict(cor_fixa=(223, 133, 89))):
            random.seed(1234)
            m = mascote.Mascote(**kw)
            m.usar(nuvem, None, 0.0, ja=True)
            nosso = self._tocar(m, lambda a, av, t: a.usar(av, None, t))
            random.seed(1234)
            a = oa.Animador(biblioteca=bib, estilo=estilo_da_biblioteca(), forma=oa.forma_de(nuvem),
                            cor_base=avatares.cor_hex(nuvem.cor), **kw)
            dela = self._tocar(a, lambda a, av, t: a.usar(av, avatares.cor_hex(av.cor), t))
            self.assertEqual(len(nosso), len(dela))
            diferentes = [round(k * 0.2, 6) for k, (x, y) in enumerate(zip(nosso, dela)) if x != y]
            self.assertEqual(diferentes, [], f"{kw}: instantes diferentes do Animador")


class CincoQuadrosPorSegundo(unittest.TestCase):
    def test_o_ombro_desenha_a_cada_0_2_s(self):
        for layout in ("ombro", "minimal-v2", "multiagente"):
            self.assertEqual(config.OmbroConfig(layout=layout).intervalo_efetivo, 0.2, layout)

    def test_a_5_fps_cada_estado_se_mexe(self):
        """A 5 quadros por segundo (como a demonstração no modo "telinha"), cada estado muda de quadro na maior parte
        dos passos: nenhum fica parado nem pisca só de vez em quando."""
        f, cor = mascote.forma_de(avatares.catalogo().avatares["ombro-cloud"]), (223, 133, 89)
        for e in mascote.ESTADOS:
            quadros = [mascote.desenhar_cloud(mascote.parametros(e, i * 0.2), e, i * 0.2, cor, 1.0, f).tobytes()
                       for i in range(25)]  # 5 s
            mudou = sum(a != b for a, b in zip(quadros, quadros[1:]))
            self.assertGreaterEqual(mudou, 18, f"{e}: só {mudou} de 24 passos mudaram")

    def test_um_quadro_cabe_folgado_em_0_2_s(self):
        m = mascote.Mascote()
        m.camada(0.0)
        inicio = time.perf_counter()
        for i, e in enumerate(NATIVOS * 5):
            m.ir_para(e, i * 0.8)
            m.camada(i * 0.8 + 0.3)
        media = (time.perf_counter() - inicio) / 20
        self.assertLess(media, 0.1, f"{media * 1000:.1f} ms por quadro do mascote")


if __name__ == "__main__":
    unittest.main()
