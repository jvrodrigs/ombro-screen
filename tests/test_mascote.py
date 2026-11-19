"""Máquina de estados do cloud."""
import unittest
from datetime import datetime, timedelta

from coletores.claude import Janela, UsoClaude
import colorsys

import mascote
from mascote import CAMPOS, DURACAO_MISTURA, ESTADOS, Mascote, cor_sorteada, desenhar_cloud, parametros

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

    def _reset_na_tela(self, depois):
        """Como o daemon faz: decide e vai para o estado a cada 0,1 s. Devolve quanto tempo o reset ficou na tela e
        o estado seguinte."""
        m = Mascote()
        m.ir_para(m.decidir(uso(pct5=93, trabalhando=1), 0), 0)
        inicio = fim = seguinte = None
        for k in range(1, 120):
            t = round(k * 0.1, 1)
            u = uso(pct5=2, reset5=timedelta(hours=5), **depois) if t >= 1 else uso(pct5=93, trabalhando=1)
            e = m.decidir(u, t)
            m.ir_para(e, t)
            if e == "reset" and inicio is None:
                inicio = t
            if inicio is not None and e != "reset" and fim is None:
                fim, seguinte = t, e
        return round(fim - inicio, 1), seguinte

    def test_reset_seguido_de_calma_dura_o_mesmo_que_no_app(self):
        # o reset chega junto com o fim do trabalho: dura DURACAO_RESET, não DURACAO_RESET + os 3 s de calma
        self.assertEqual(self._reset_na_tela({}), (mascote.DURACAO_RESET, "concluido"))

    def test_reset_seguido_de_trabalho_volta_a_trabalhar(self):
        self.assertEqual(self._reset_na_tela({"trabalhando": 1}), (mascote.DURACAO_RESET, "trabalhando"))

    def test_reset_seguido_de_espera_vai_para_esperando(self):
        self.assertEqual(self._reset_na_tela({"esperando": 1}), (mascote.DURACAO_RESET, "esperando"))

    def test_reset_junto_com_o_fim_do_trabalho_depois_concluido_ate_o_prazo_dele(self):
        m = Mascote()
        m.ir_para(m.decidir(uso(trabalhando=1), 0), 0)
        for k in range(1, 31):  # 3 s de reset, lidos a cada 0,1 s (retratos repetidos)
            t = round(1 + k * 0.1 - 0.1, 1)
            m.ir_para(m.decidir(uso(pct5=2, reset5=timedelta(hours=5)), t), t)
            self.assertEqual(m.estado, "reset", t)
        m.ir_para(m.decidir(uso(pct5=2, reset5=timedelta(hours=5)), 4.0), 4.0)
        self.assertEqual(m.estado, "concluido")  # o concluído conta do fim do trabalho (t=1), não do fim do reset
        self.assertEqual(m.decidir(uso(pct5=2, reset5=timedelta(hours=5)), 1 + m.duracao_concluido - 0.1), "concluido")
        self.assertEqual(m.decidir(uso(pct5=2, reset5=timedelta(hours=5)), 1 + m.duracao_concluido + 0.1), "ocioso")

    def test_leituras_repetidas_nao_recomecam_o_reset(self):
        m = Mascote()
        m.decidir(uso(), 0)
        for t in (1.0, 1.5, 2.0, 3.5, 3.9):
            self.assertEqual(m.decidir(uso(reset5=timedelta(hours=7)), t), "reset")
        self.assertEqual(m.decidir(uso(reset5=timedelta(hours=7)), 1 + mascote.DURACAO_RESET), "ocioso")

    def test_reconexao_nao_reapresenta_reset_antigo(self):
        # a fonte some (o arquivo do statusline sumiu, o daemon perdeu a leitura) e volta com o horário novo
        m = Mascote()
        m.decidir(uso(), 0)
        m.decidir(UsoClaude(), 1)
        self.assertEqual(m.decidir(uso(reset5=timedelta(hours=7)), 2), "ocioso")
        # e o daemon reiniciado (Mascote novo) só lembra o que vê primeiro
        self.assertEqual(Mascote().decidir(uso(reset5=timedelta(hours=7)), 0), "ocioso")

    def test_o_ciclo_da_demonstracao_reseta_a_cada_volta(self):
        # 2h14 -> 30 min (alerta) -> 5 h (reset), e de novo: o horário volta atrás entre um ciclo e outro
        m = Mascote()
        for ciclo in range(3):
            t = ciclo * 48.0
            m.decidir(uso(reset5=timedelta(hours=2, minutes=14)), t)
            m.decidir(uso(pct5=93, reset5=timedelta(minutes=30), trabalhando=1), t + 32)
            self.assertEqual(m.decidir(uso(pct5=4, reset5=timedelta(hours=5)), t + 40), "reset", ciclo)

    def test_janela_expirada_reseta_uma_vez_quando_o_numero_novo_chega(self):
        # como o app vê: a janela que reiniciou sem número novo fica com o horário em que reiniciou
        m = Mascote()
        m.decidir(uso(), 0)
        expirada = UsoClaude(cinco_horas=None, semana=Janela(50.0, AGORA + timedelta(days=3)),
                             expiradas={"cinco_horas": AGORA + timedelta(hours=2)})
        self.assertEqual(m.decidir(expirada, 1), "ocioso")
        self.assertEqual(m.decidir(uso(pct5=1, reset5=timedelta(hours=7)), 2), "reset")
        self.assertEqual(m.decidir(uso(pct5=1, reset5=timedelta(hours=7)), 9), "ocioso")

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



class Animacao(unittest.TestCase):
    def test_parametros_dependem_so_do_tempo(self):
        for estado in ESTADOS:
            self.assertEqual(parametros(estado, 1.37), parametros(estado, 1.37))
            self.assertLessEqual(set(CAMPOS), set(parametros(estado, 0)))  # o vivo pode trazer campos a mais (olhoAng)

    # os seis estados do padrão são os do pacote vivo (test_mascote_mascotinhos.py); o clássico é o movimento de antes
    # (test_mascote_referencia.py). O que o Ombro espera de alerta e reset vale nos dois.
    def test_reset_pula_alto(self):
        self.assertLess(parametros("reset", 2.1)["dy"], -45)  # vivo: o pulo feliz, depois que a bateria enche
        self.assertLess(mascote.biblioteca("classico").parametros("reset", .8)["dy"], -50)  # clássico: o pulão

    def test_alerta_treme_de_um_lado_para_o_outro(self):
        for pacote in mascote.PACOTES:
            lados = {mascote.biblioteca(pacote).parametros("alerta", t / 24)["dx"] > 0 for t in range(8)}
            self.assertEqual(lados, {True, False}, pacote)


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


class Mascotinhos(unittest.TestCase):
    """O desenho vem da biblioteca mascotinhos: a versão fixada, os seis estados e a JetBrains Mono Bold."""

    def test_a_versao_instalada_e_a_fixada(self):
        import re
        from pathlib import Path

        import mascotinhos

        raiz = Path(__file__).resolve().parent.parent
        req = re.search(r"^mascotinhos==(\S+)", (raiz / "requirements.txt").read_text(encoding="utf-8"), re.M)
        lock = re.search(r"^mascotinhos==(\S+) --hash=sha256:[0-9a-f]{64}$",
                         (raiz / "app" / "backend" / "requirements.lock").read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(req, "requirements.txt deve fixar mascotinhos==X.Y.Z")
        self.assertIsNotNone(lock, "o requirements.lock deve fixar mascotinhos com o hash do wheel")
        self.assertEqual(req.group(1), lock.group(1))
        self.assertEqual(mascotinhos.__version__, req.group(1), "instale as dependências: pip install -r requirements.txt")

    def test_a_biblioteca_tem_os_seis_estados(self):
        import mascote

        self.assertEqual(set(mascote.BIBLIOTECA.estados()), set(ESTADOS))

    def test_o_zz_usa_a_jetbrains_mono_bold_igual_a_de_fontes(self):
        import hashlib
        from pathlib import Path

        import mascote
        import mascotinhos
        from render import S

        f = mascote.ESTILO.fonte(20)
        self.assertEqual(f.getname(), ("JetBrains Mono", "Bold"))
        self.assertEqual(f.size, 20 * S)
        nossa = Path(__file__).resolve().parent.parent / "fontes" / "JetBrainsMono-Bold.ttf"
        self.assertEqual(hashlib.sha256(mascotinhos.arquivo_jetbrains_mono().read_bytes()).hexdigest(),
                         hashlib.sha256(nossa.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
