"""O mascote do Ombro Screen: uma animação por estado dos agentes, com o avatar de cada um.

A forma e os olhos vêm do avatar (avatars/, formato do open-bot-avatar; ver avatares.py): o padrão é o
Ombro Cloud, e cada agente pode ter o seu. As animações são as da biblioteca mascotinhos
(https://github.com/GilsonB12/mascotinhos), num dos pacotes dela (a preferência `pacote_animacao`): o "vivo", o
padrão (olhos que mudam com o estado, zzZ, linhas e engrenagem, "?", bateria, ✓ com faíscas), ou o "classico"; os
dois desenhados chapados. Os seis estados vêm do pacote escolhido, sem misturar um no outro.
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
from functools import lru_cache

import mascotinhos as oa
from mascotinhos import palco
from mascotinhos.movimento import suave
from PIL import Image

import avatares
from config import PACOTES_ANIMACAO, PACOTES_EM_AVALIACAO
from render import S, VERDE

# ---------- as animações: as da biblioteca, sem nada refeito aqui ----------
# Os pacotes que dá para escolher (config.PACOTES_ANIMACAO), na ordem da interface; o primeiro, "vivo", é o padrão (o
# da demonstração do mascotinhos, o de sempre desde a 0.2.0). Os dois são desenhados chapados, iguais na telinha e no
# app, e cada um tem os seis estados. Os em avaliação ("3d", "terminal") trazem cada um o seu jeito de desenhar
# (volume; letras) e continuam fora da escolha (os motivos: ombro/animacoes.py e docs/avatars.md).
PACOTES, EM_AVALIACAO = PACOTES_ANIMACAO, PACOTES_EM_AVALIACAO
PACOTE = PACOTES[0]


@lru_cache(maxsize=None)
def biblioteca(pacote: str = PACOTE) -> oa.Biblioteca:
    """Os seis estados de um pacote, como a biblioteca os traz (carregada uma vez por pacote). Levanta ValueError com
    um pacote fora de PACOTES, e o erro da biblioteca se ela não conseguir carregá-lo."""
    if pacote not in PACOTES:
        raise ValueError(f"pacote de animações fora da escolha: {pacote!r} (há: {', '.join(PACOTES)})")
    return oa.Biblioteca.carregar(pacote=pacote)


BIBLIOTECA = biblioteca(PACOTE)

# ---------- onde o mascote fica na tela de 480x320 ----------
COL_X, COL_Y, COL_W, COL_H = 316, 26, 156, 170  # coluna reservada; nada passa daqui
# A camada do mascote: a coluna, desenhada em S vezes; a janela da forma (com folga para os extras) cabe em 156x168,
# centrada. As unidades da camada (a área do avatar e os números do movimento) são as do palco do mascotinhos.
ENQUADRAMENTO = oa.Enquadramento(COL_W, COL_H, 168, S)
ESTILO = oa.Estilo(enquadramento=ENQUADRAMENTO, paleta=oa.Paleta(verde=VERDE), fonte=oa.fonte_jetbrains_mono(S))

BX0, BY0, BX1, BY1 = palco.BX0, palco.BY0, palco.BX1, palco.BY1  # a área onde o avatar cabe (unidades da camada)
CX, W, H = palco.CX, palco.W, palco.H
ALTURA_OLHAR = palco.ALTURA_OLHAR  # na inclinação, os olhos andam como se estivessem a esta altura da base

ESTADOS = ("ocioso", "trabalhando", "esperando", "alerta", "reset", "concluido")
DURACAO_CONCLUIDO = 300.0  # segundos mostrando "concluído" depois que o Claude termina; depois, dorme
DURACAO_RESET = 3.0  # segundos comemorando a janela de limite que reiniciou (o RESET_MS do app)
# A comemoração do "concluído" (o pulo, o ✓ se desenhando e as faíscas) acaba antes disto, em qualquer pacote (um
# teste confere nos arquivos da biblioteca); depois, o mascote só balança. É o ponto seguro para trocar de pacote.
FIM_DA_CONCLUSAO = 2.0
# Os estados em que dá para trocar de pacote sem cortar nada: os que repetem sem uma história com começo e fim
# (ocioso; alerta, que pode durar horas perto do limite: a troca cruza a bateria e o suor de um pacote com os do
# outro) e o concluído depois da comemoração. Trabalhando, esperando e reset nunca.
TROCA_SEGURA = ("ocioso", "alerta", "concluido")
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
    pacote: str = PACOTE  # o pacote de animações em uso (o da `biblioteca`)
    _troca_pacote: tuple | None = None  # (biblioteca de antes, desde quando) enquanto mistura um pacote no outro
    _pose_da_troca: tuple | None = field(default=None, repr=False)  # (pose, u) do último quadro da troca de pacote

    def usar(self, av: "avatares.Avatar", cor_agente: tuple[int, int, int] | None, agora: float, ja: bool = False) -> None:
        """Pede o avatar `av` com a cor do agente dele (sem ela, a do avatar). A troca é a do Animador: uma transição
        de TRANSICAO s (encolhe e pisca, a forma muda no meio); `ja` (uma mudança de preferência, um PNG) troca agora.
        Pedir o mesmo de novo não faz nada."""
        super().usar(forma_de(av), cor_agente or avatares.cor_hex(av.cor), agora, ja)

    # ---------- o pacote de animações ----------
    def usar_pacote(self, pacote: str, agora: float, ja: bool = False) -> None:
        """Passa a usar o pacote `pacote`. A troca é a mesma mistura de DURACAO_MISTURA s que a biblioteca faz entre
        dois estados: a pose do pacote de antes vira a do novo, no mesmo instante do estado (nada recomeça), e os olhos e
        os extras de um se cruzam com os do outro (ver camada). `ja` troca sem misturar (ao iniciar). Quem decide QUANDO trocar é quem usa (ver
        momento_seguro). Levanta o erro de biblioteca() se o pacote não carregar: nada muda."""
        nova = biblioteca(pacote)
        if nova is self.biblioteca:
            self.pacote = pacote
            return
        self._troca_pacote = None if ja else (self.biblioteca, agora)
        self.biblioteca, self.pacote = nova, pacote

    def momento_seguro(self, agora: float) -> bool:
        """Trocar de pacote agora não corta nada do que o mascote mostra? Só num estado de TROCA_SEGURA, já assentado
        (fora da mistura de uma troca de estado ou de pacote) e, no concluído, depois da comemoração."""
        if self.estado not in TROCA_SEGURA or self._troca_pacote is not None:
            return False
        t = agora - self.inicio
        return t >= (FIM_DA_CONCLUSAO if self.estado == "concluido" else DURACAO_MISTURA)

    def parametros_atuais(self, agora: float) -> tuple[dict, float]:
        """Os do Animador; durante uma troca de pacote, misturados com os do pacote de antes (oa.misturar, como entre
        dois estados). O `u` (o quanto dos extras do estado já vale) é o do estado: a troca de pacote não apaga extras."""
        p, u = super().parametros_atuais(agora)
        troca = self._troca_pacote
        if troca is None:
            return p, u
        antiga, desde = troca
        v = (agora - desde) / DURACAO_MISTURA
        if v >= 1:
            self._troca_pacote = self._pose_da_troca = None
            return p, u
        nova, self.biblioteca = self.biblioteca, antiga
        try:
            antes, _ = super().parametros_atuais(agora)
        finally:
            self.biblioteca = nova
        w = suave(max(0.0, v))  # a mesma curva da mistura entre estados
        pose = misturar(antes, p, w)
        self._pose_da_troca = (pose, u)
        return pose, u

    def camada(self, agora: float) -> Image.Image:
        """O quadro do Animador. Numa troca de pacote, um cruzamento: o mesmo corpo (a pose misturada), com os olhos e
        os extras do pacote de antes sumindo enquanto os do novo aparecem (desenhados pelo oa.desenhar, como o Animador
        desenha). Assim nenhum extra pisca: o ✓, o zzZ ou o "?" de um vira o do outro."""
        img = super().camada(agora)
        troca, pose = self._troca_pacote, self._pose_da_troca
        if troca is None or pose is None or self._troca is not None:  # fora da troca, ou trocando de avatar junto
            return img
        antiga, desde = troca
        w = suave(min(1.0, max(0.0, (agora - desde) / DURACAO_MISTURA)))
        p, u = pose
        antes = oa.desenhar(self.forma, p, antiga.get(self.estado), agora - self.inicio, self.cor_atual(agora),
                            alpha_extras=u, estilo=self.estilo)
        if w <= 0:
            return antes
        # em alfa pré-multiplicado: o corpo (igual nas duas) fica igual; o que só uma tem esmaece sem escurecer
        return Image.blend(antes.convert("RGBa"), img.convert("RGBa"), w).convert("RGBA")

    def decidir(self, u, agora: float) -> str:
        """Estado do cloud a partir do que o Claude Code está fazendo."""
        j5, js = u.cinco_horas, u.semana
        # janela do plano (5h ou semana) que reiniciou: o horário de reset avançou mais de uma hora. As janelas são as
        # mesmas que o app recebe (providers/claude.py): a que expirou sem número novo vale pelo horário em que
        # reiniciou; a que sumiu (a fonte caiu) é esquecida, e a volta dela não é reset. Assim um reset antigo não
        # reaparece depois de uma reconexão (como no app, que só compara retratos seguidos).
        expiradas = getattr(u, "expiradas", {})
        vistos = {}
        for nome, campo, j in (("5h", "cinco_horas", j5), ("semana", "semana", js)):
            t = j.reseta_em if j else expiradas.get(campo)
            if not t:
                continue
            ant = self._reseta_ant.get(nome)
            if ant and t - ant > timedelta(hours=1):
                self._reset_ate = agora + DURACAO_RESET
            vistos[nome] = t
        self._reseta_ant = vistos
        # terminou: havia sessão trabalhando e agora nenhuma trabalha nem espera você
        # (de "esperando" direto para parada não conta: você cancelou ou fechou a sessão)
        ativo = u.trabalhando > 0
        if self._havia_trabalho and not ativo and not u.esperando:
            self._terminou_em = agora
        self._havia_trabalho = ativo
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
        # só sossega (concluído ou ocioso) depois de uns segundos sem atividade: o status oscila. A contagem corre
        # também durante o reset, para que ele dure DURACAO_RESET na telinha (como o RESET_MS no app), e não o
        # dobro: antes, os 3 s de calma só começavam a contar quando o reset acabava.
        segurar = False
        if alvo in CALMOS and self.estado not in CALMOS:
            if self._calmo_desde is None:
                self._calmo_desde = agora
            segurar = agora - self._calmo_desde < 3.0
        else:
            self._calmo_desde = None
        if agora < self._reset_ate:
            return "reset"
        return self.estado if segurar else alvo
