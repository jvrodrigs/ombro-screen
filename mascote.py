"""O mascote do Ombro Screen: uma animação por estado dos agentes, com o avatar de cada um.

A forma e os olhos vêm do avatar (avatars/, formato do open-bot-avatar; ver avatares.py): o padrão é o
Ombro Cloud, e cada agente pode ter o seu. As animações são as da biblioteca mascotinhos
(https://github.com/GilsonB12/mascotinhos), as mesmas da demonstração dela: o pacote "vivo" (olhos que mudam com o
estado, zzZ, linhas e engrenagem, "?", ✓ com faíscas), desenhado chapado. Alerta e reset continuam, por enquanto,
com as animações do pacote "classico" (as de antes); para passá-los ao "vivo", basta tirá-los de DO_CLASSICO.
O texto sai na JetBrains Mono Bold que vem com o mascotinhos (a mesma de fontes/).

O Animador do mascotinhos faz o resto do que é animação: a mistura entre estados, a troca de avatar, a cor e o
realce. O que é do Ombro continua aqui: qual estado mostrar (Mascote.decidir), qual avatar e qual cor, e onde o
mascote fica na tela.

Estados: ocioso, trabalhando, esperando, alerta, reset e concluido (o Claude terminou o que fazia;
dura até 5 min, ou até começar outra coisa, e depois o cloud volta a dormir).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import mascotinhos as oa
from mascotinhos import palco
from PIL import Image

import avatares
from render import S, VERDE

# ---------- as animações: as da biblioteca, sem nada refeito aqui ----------
PACOTE = "vivo"  # o da demonstração do mascotinhos
DO_CLASSICO = ("alerta", "reset")  # estes ainda usam o pacote "classico"; os outros, o PACOTE
_classico = oa.Biblioteca.carregar(pacote="classico")
BIBLIOTECA = oa.Biblioteca.carregar(pacote=PACOTE).com(*(_classico[e] for e in DO_CLASSICO))

# ---------- onde o mascote fica na tela de 480x320 ----------
COL_X, COL_Y, COL_W, COL_H = 316, 26, 156, 170  # coluna reservada; nada passa daqui
# A camada do mascote: a coluna, desenhada em S vezes; a janela da forma (com folga para os extras) cabe em 156x168,
# centrada. As unidades da camada (a área do avatar e os números do movimento) são as do palco do mascotinhos.
ENQUADRAMENTO = oa.Enquadramento(COL_W, COL_H, 168, S)
ESTILO = oa.Estilo(enquadramento=ENQUADRAMENTO, paleta=oa.Paleta(verde=VERDE), fonte=oa.fonte_jetbrains_mono(S))

BX0, BY0, BX1, BY1 = palco.BX0, palco.BY0, palco.BX1, palco.BY1  # a área onde o avatar cabe (unidades da camada)
CX, W, H = palco.CX, palco.W, palco.H
ESC = ENQUADRAMENTO.esc
ALTURA_OLHAR = palco.ALTURA_OLHAR  # na inclinação, os olhos andam como se estivessem a esta altura da base

ESTADOS = ("ocioso", "trabalhando", "esperando", "alerta", "reset", "concluido")
DURACAO_CONCLUIDO = 300.0  # segundos mostrando "concluído" depois que o Claude termina; depois, dorme
CALMOS = ("ocioso", "concluido")  # estados sem nenhuma sessão ativa
CAMPOS = oa.CAMPOS  # dx, dy, sx, sy, rot, kL, kW, olhaX, olhaY, pisca
DURACAO_MISTURA = oa.DURACAO_MISTURA  # s para passar de um estado ao outro
TRANSICAO = oa.TRANSICAO  # s: a troca de avatar. O corpo encolhe e fecha os olhos, troca no meio e volta
REALCE = oa.REALCE  # s: no modo "agente" (a cor não muda de um estado para outro), a cor clareia e volta na troca

Forma = oa.Forma  # um avatar já posto na área do mascote: os polígonos, os olhos e o contorno (x0, y0 = topo, x1)
cor_sorteada = oa.cor_sorteada  # matiz livre; saturação e luminosidade limitadas para ler bem sobre o fundo escuro
misturar = oa.misturar
_pt = ENQUADRAMENTO.pt  # coordenada da camada (unidades do palco) -> pixel da camada (já em S vezes)
_corpo = palco.corpo  # escala em torno da base, gira em torno da base e desloca
_olhar_x = palco.olhar_x  # o olhar horizontal mais o deslize que acompanha a inclinação


def parametros(estado: str, t: float) -> dict[str, float]:
    """A pose do estado no instante t (t = segundos desde que o estado começou; um estado que não existe fica parado)."""
    return BIBLIOTECA.parametros(estado, t)


def forma_de(av: "avatares.Avatar") -> Forma:
    """O avatar escalado para caber em BX0..BX1 x BY0..BY1, com a base em BY1 e centrado em CX (em cache)."""
    return oa.forma_de(av)


def _padrao() -> "avatares.Avatar":
    return avatares.catalogo().padrao_de(None)


def desenhar_cloud(p: dict, estado: str, t: float, cor: tuple[int, int, int], alpha_extras: float = 1.0,
                   forma: Forma | None = None) -> Image.Image:
    """Camada RGBA (COL_W x COL_H, em S vezes) com o mascote (o avatar `forma`) no estado e instante dados."""
    return oa.desenhar(forma or forma_de(_padrao()), p, BIBLIOTECA.get(estado), t, cor, alpha_extras, ESTILO)


# ---------- o mascote: o Animador do mascotinhos, com a decisão de estado do Ombro ----------
@dataclass
class Mascote(oa.Animador):
    """O Animador (mistura entre estados, troca de avatar, cor e realce) com as animações e o estilo de cima, mais o
    que é do Ombro: decidir o estado a partir dos agentes e pedir o avatar com a cor do agente dele."""
    forma: Forma = field(default_factory=lambda: forma_de(_padrao()))
    cor_base: tuple[int, int, int] = field(default_factory=lambda: avatares.cor_hex(_padrao().cor))
    biblioteca: oa.Biblioteca = field(default_factory=lambda: BIBLIOTECA, repr=False)
    estilo: oa.Estilo = field(default_factory=lambda: ESTILO, repr=False)
    _reseta_ant: dict[str, datetime] = field(default_factory=dict)  # último horário de reset visto, por janela
    _reset_ate: float = 0.0
    _calmo_desde: float | None = None
    duracao_concluido: float = DURACAO_CONCLUIDO
    _havia_trabalho: bool = False  # alguma sessão estava trabalhando na leitura anterior?
    _terminou_em: float | None = None  # quando a última sessão parou de trabalhar sem esperar você

    def usar(self, av: "avatares.Avatar", cor_agente: tuple[int, int, int] | None, agora: float, ja: bool = False) -> None:
        """Pede o avatar `av` com a cor do agente dele (sem ela, a do avatar). A troca é a do Animador: uma transição
        de TRANSICAO s (encolhe e pisca, a forma muda no meio); `ja` (uma mudança de preferência, um PNG) troca agora.
        Pedir o mesmo de novo não faz nada."""
        super().usar(forma_de(av), cor_agente or avatares.cor_hex(av.cor), agora, ja)

    def decidir(self, u, agora: float) -> str:
        """Estado do cloud a partir do que o Claude Code está fazendo."""
        j5, js = u.cinco_horas, u.semana
        # janela do plano (5h ou semana) que reiniciou: o horário de reset avançou mais de uma hora
        for nome, j in (("5h", j5), ("semana", js)):
            if not (j and j.reseta_em):
                continue
            ant = self._reseta_ant.get(nome)
            if ant and j.reseta_em - ant > timedelta(hours=1):
                self._reset_ate = agora + 3.0
            self._reseta_ant[nome] = j.reseta_em
        # terminou: havia sessão trabalhando e agora nenhuma trabalha nem espera você
        # (de "esperando" direto para parada não conta: você cancelou ou fechou a sessão)
        ativo = u.trabalhando > 0
        if self._havia_trabalho and not ativo and not u.esperando:
            self._terminou_em = agora
        self._havia_trabalho = ativo
        if agora < self._reset_ate:
            return "reset"
        if u.esperando:
            alvo = "esperando"
        elif any(j and j.pct >= 90 for j in (j5, js)):
            alvo = "alerta"
        elif u.trabalhando:
            alvo = "trabalhando"
        elif self._terminou_em is not None and agora - self._terminou_em < self.duracao_concluido:
            alvo = "concluido"
        else:
            alvo = "ocioso"
        # só sossega (concluído ou ocioso) depois de uns segundos sem atividade: o status oscila
        if alvo in CALMOS and self.estado not in CALMOS:
            if self._calmo_desde is None:
                self._calmo_desde = agora
            if agora - self._calmo_desde < 3.0:
                return self.estado
        else:
            self._calmo_desde = None
        return alvo
