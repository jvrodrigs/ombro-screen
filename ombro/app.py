"""O Ombro rodando: coleta, estado, desenho e saída num laço só. O mesmo para o tela.py e o ombrod.

  app = Aplicacao(cfg)
  app.salvar_png(Path("q.png"))          # um quadro e pronto (--png)
  app.abrir_saida()                       # tela USB ou simulador
  app.laco(parar=lambda: sinal_chegou)    # até `parar()` dizer que chega
  app.fechar()

Quem está fora do laço (a IPC do ombrod, numa outra thread) nunca mexe nos objetos vivos: lê o que o
laço publica (`publicado`, `ultimo_quadro`) e pede mudanças com `executar(fn)`, que roda `fn` aqui
dentro, entre dois quadros. A espera entre quadros é a própria fila desses pedidos, então um pedido é
atendido na hora, sem esperar o próximo quadro, e sem o laço precisar de nenhuma trava.
"""
from __future__ import annotations

import logging
import queue
import random
import threading
import time
import webbrowser
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil

import avatares
from apresentacao import uso_claude, uso_mascote
from coletores.claude import ColetorClaude
from coletores.demo import Demo
from coletores.hardware import ColetorHardware, Hardware
from config import NOMES_LAYOUT, OmbroConfig, pasta_ombro
from core.models import Activity, Attention
from core.monitor import Monitor
from core.resumo import resumir
from layout_minimal_v2 import desenhar_minimal
from layout_multiagente import SeletorDeVista, desenhar_multiagente
from layout_ombro import desenhar_ombro
from logging_config import SoQuandoMuda, curto, logger, sem_casa
from mascote import Mascote
from providers.claude import PROVIDER_ID as CLAUDE
from providers.claude import ClaudeProvider, FonteDemo
from providers.codex import CodexProvider
from providers.cursor import CursorProvider
from providers.demo import codex_demo, cursor_demo, opencode_demo
from providers.opencode import PROVIDER_ID as OPENCODE
from providers.opencode import OpenCodeProvider
from render import desenhar

from .animacoes import Animacoes, Atividade, ler_aplicado
from .movimento import MovimentoReduzido

log = logger("main")
log_hw = logger("hardware")
log_render = logger("render")
log_usb = logger("usb")

INTERVALO_CLAUDE = 5.0  # segundos entre leituras dos arquivos do Claude Code (e dos eventos dos hooks)
ESTATISTICAS_S = 60.0  # com --debug, um resumo do laço a cada minuto
DO_CLAUDE = ("alerta", "reset")  # estados que vêm dos limites do Claude: no multiagente, o mascote é o dele
ANIMADOS = ("auto", "ombro", "minimal-v2", "multiagente")  # os layouts com o mascote (sempre 480x320)
SEM_TELA_S = 1.0  # com a tela USB fora, um quadro por segundo: a prévia do app segue viva sem gastar à toa
# o que muda a tela de quem olha (a orientação do quadro ou da tela física)
DA_ORIENTACAO = ("layout", "paisagem", "girar")


class Indisponivel(RuntimeError):
    """O laço não está atendendo pedidos (ainda não começou, ou está fechando)."""


class Estatisticas:
    """Contadores do laço, resumidos no log de depuração a cada minuto (nunca a cada quadro)."""

    def __init__(self, saida, agora: float):
        self.saida = saida
        self._zerar(agora)

    def _zerar(self, agora: float) -> None:
        self.inicio, self.quadros, self.pulados, self.ms_desenho = agora, 0, 0, 0.0
        self.bytes0 = getattr(self.saida, "bytes_enviados", 0)

    def contar(self, agora: float, ms_desenho: float | None) -> None:
        if ms_desenho is None:
            self.pulados += 1
        else:
            self.quadros += 1
            self.ms_desenho += ms_desenho
        dt = agora - self.inicio
        if dt < ESTATISTICAS_S:
            return
        if log.isEnabledFor(logging.DEBUG):
            enviados = getattr(self.saida, "bytes_enviados", 0) - self.bytes0
            log.debug("últimos %.0f s: %d quadros (%.1f/s), desenho %.1f ms em média, %d pulados com a tela fora, "
                      "%.0f KiB para a tela (%.1f KiB/s)", dt, self.quadros, self.quadros / dt,
                      self.ms_desenho / max(1, self.quadros), self.pulados, enviados / 1024, enviados / 1024 / dt)
        self._zerar(agora)


def montar_providers(cfg: OmbroConfig, demo) -> list:
    """Os agentes ligados na configuração (todos, por padrão). No --demo, com dados inventados."""
    feitos = {
        "claude": lambda: ClaudeProvider(FonteDemo(demo, cfg.demo_ciclo) if demo else ColetorClaude(cfg.claude_dir)),
        "codex": lambda: codex_demo(cfg.demo_ciclo) if demo else CodexProvider(),
        "cursor": lambda: cursor_demo(cfg.demo_ciclo) if demo else CursorProvider(),
        "opencode": lambda: opencode_demo(cfg.demo_ciclo) if demo else OpenCodeProvider(),
    }
    return [feitos[pid]() for pid in cfg.providers]


def pasta_de_eventos(cfg: OmbroConfig) -> Path | None:
    """Onde os hooks deixam os eventos. Dados inventados e quadros avulsos não leem (nem apagam) eventos."""
    if cfg.demo or cfg.png:
        return None
    return pasta_ombro() / "eventos"


def banco_de_estado(cfg: OmbroConfig) -> Path | None:
    """Onde gravar o estado (sessões conhecidas) entre execuções. Dados inventados e quadros avulsos não gravam."""
    if cfg.demo or cfg.png:
        return None
    return pasta_ombro() / "state.db"


def arquivo_da_animacao(cfg: OmbroConfig) -> Path | None:
    """Onde gravar o pacote de animações em uso entre execuções (o mesmo critério do banco de estado)."""
    if cfg.demo or cfg.png:
        return None
    return pasta_ombro() / "animacao.json"


def ao_vivo(cfg: OmbroConfig) -> bool:
    """Este Ombro lê eventos, grava o banco ou fala com a tela USB? Então precisa do lock de instância."""
    return not cfg.png and (not cfg.demo or cfg.usb)


class _Pedido:
    __slots__ = ("fn", "feito", "resultado", "erro", "cancelado", "iniciado", "_trava")

    def __init__(self, fn: Optional[Callable]):
        self.fn, self.feito, self.resultado, self.erro, self.cancelado = fn, threading.Event(), None, None, False
        self.iniciado, self._trava = False, threading.Lock()

    def cancelar(self) -> bool:
        """Desiste do pedido, se o laço ainda não começou a atendê-lo. Falso: já está rodando (vai terminar)."""
        with self._trava:
            if self.iniciado:
                return False
            self.cancelado = True
            return True

    def executar(self) -> None:
        with self._trava:
            if self.fn is None or self.cancelado:
                self.feito.set()
                return
            self.iniciado = True
        try:
            self.resultado = self.fn()
        except Exception as e:  # noqa: BLE001 - o erro volta para quem pediu; o laço segue
            self.erro = e
        finally:
            self.feito.set()


class Aplicacao:
    def __init__(self, cfg: OmbroConfig, saida=None):
        self.cfg = cfg
        self.demo = Demo() if cfg.demo else None
        self.hw = None if self.demo else ColetorHardware(usar_macmon=not cfg.sem_macmon)
        # agentes -> providers -> StateStore (e banco) -> apresentação (UsoClaude, ou o resumo de todos) -> tela
        self.monitor = Monitor.montar(montar_providers(cfg, self.demo), banco=banco_de_estado(cfg),
                                      eventos=pasta_de_eventos(cfg))
        self.nomes = [(p.provider_id, p.display_name) for p in self.monitor.registry]
        self.seletor = SeletorDeVista(cfg.vista, cfg.foco, cfg.rotacao, cfg.intervalo_rotacao)
        self.resumo, self.uso_multi = None, None
        self._sem_confirmacao = 0  # a soma dos ProviderSnapshot.unconfirmed na última coleta
        self._trabalhando_oc: set = set()  # sessões do OpenCode trabalhando na última coleta
        self._avisos_hw = SoQuandoMuda(log_hw)
        self._ultimo_hw = Hardware(disponivel=False)  # até a primeira leitura boa
        self.semente = random.getrandbits(32)  # o "aleatório ao iniciar": sorteado agora, o mesmo até reiniciar
        self.mascote = Mascote(cor_fixa=cfg.cor_mascote_rgb, cor_modo=cfg.cor_modo)
        self.mascote.definir_entradas(self.entradas_de_cor())
        self.movimento = MovimentoReduzido()  # o "reduzir movimento" do macOS, lido só se algum avatar usa o ciclo "rgb"
        self.sujeito_atual: tuple = (None, None)  # (agente, sessão) de quem o mascote está falando agora
        if self.demo and cfg.demo_ciclo > 0:  # no ciclo de demonstração, o "concluído" dura um cenário, não 5 min
            self.mascote.duracao_concluido = cfg.demo_ciclo
        self.mascote.inicio = time.monotonic()
        # o pacote de animações: o escolhido (cfg) e o que estava em uso quando o Ombro parou. Um reinício começa com o
        # de antes; o escolhido, se for outro, espera um momento seguro como qualquer troca (ombro/animacoes.py)
        arq_animacao = arquivo_da_animacao(cfg)
        aplicado = ler_aplicado(arq_animacao) if arq_animacao else cfg.pacote_animacao
        self.animacoes = Animacoes(cfg.pacote_animacao, aplicado, arq_animacao)
        try:
            self.mascote.usar_pacote(self.animacoes.aplicado, self.mascote.inicio, ja=True)
        except Exception as e:  # noqa: BLE001 - um pacote que não carrega não impede o Ombro de abrir: fica o padrão
            self.animacoes.recusar(self.animacoes.aplicado, e)
            self.animacoes.aplicado = self.mascote.pacote
        self.animacao_publicada = self.animacoes.como_dict()  # o que a IPC lê (trocado inteiro, nunca alterado)
        self.saida = saida
        self._uso, self._ultimo_uso = None, 0.0
        # pedidos de fora do laço; SimpleQueue.put pode ser chamado de um tratador de sinal
        self._pedidos: "queue.SimpleQueue[_Pedido]" = queue.SimpleQueue()
        self._atendendo = False
        # o que quem está fora do laço pode ler (trocados inteiros, nunca alterados no lugar)
        self.ultimo_quadro = None  # a última imagem mandada para a saída
        self.ultimo_quadro_em: Optional[datetime] = None
        self.quadros = 0
        self.coletado_em: Optional[datetime] = None
        self.ultimo_hw: Optional[Hardware] = None
        self.ao_coletar: Optional[Callable[[], None]] = None  # chamado no laço depois de cada coleta
        self._redesenhar = False  # uma preferência mudou: o próximo quadro sai já, sem esperar o intervalo
        self._ultimo_desenho = 0.0

    # ---------- leitura ----------
    @property
    def multi(self) -> bool:
        return self.cfg.layout == "multiagente"

    def ler_hw(self) -> Hardware:
        if self.demo:
            return self.demo.hardware()
        try:
            self._ultimo_hw = self.hw.ler()
        except (OSError, psutil.Error) as e:  # leitura do sistema que falhou: repete a anterior e segue
            if self._avisos_hw.registrar("ler", logging.WARNING,
                                         "não consegui ler o hardware (%s: %s); repetindo a última leitura",
                                         type(e).__name__, sem_casa(e)):
                log_hw.debug("detalhes da falha do hardware", exc_info=True)
        else:
            self._avisos_hw.resolver("ler", "leitura do hardware voltou ao normal")
        return self._ultimo_hw

    def ler_claude(self):
        """Coleta todos os agentes (e os eventos dos hooks); devolve o que os layouts de sempre usam."""
        self.monitor.atualizar_todos()
        snap_claude = self.monitor.store.snapshot(CLAUDE)
        if self.multi:
            self.resumo = resumir(self.monitor.store, self.nomes)
            self.uso_multi = uso_mascote(self.resumo, snap_claude)
            perdas = sum(p.snapshot.unconfirmed for p in self.resumo.providers if p.snapshot is not None)
            # sumiu sem fim confirmado (desconhecida, apagada, coleta com erro): não é "concluído"
            agora_oc = {s.session_id: s.activity for s in self.monitor.store.sessions(OPENCODE)}
            sumiu = any(agora_oc.get(sid) not in (Activity.WORKING, Activity.IDLE) for sid in self._trabalhando_oc)
            self.uso_multi.sem_confirmacao = sumiu or perdas != self._sem_confirmacao
            self._sem_confirmacao = perdas
            self._trabalhando_oc = {sid for sid, a in agora_oc.items() if a is Activity.WORKING}
        return uso_claude(snap_claude)

    # ---------- desenho ----------
    def desenhar_animado(self, h, uso, agora_mono):
        if self.multi:
            vista, foco = self.seletor.escolher(self.resumo, agora_mono)
            return desenhar_multiagente(h, self.resumo, self.mascote, datetime.now(), agora_mono, vista, foco)
        if self.cfg.layout == "minimal-v2":
            return desenhar_minimal(h, uso, self.mascote, datetime.now(), agora_mono, agente=CLAUDE)
        return desenhar_ombro(h, uso, self.mascote, datetime.now(), agora_mono)

    def sincronizar_movimento(self) -> None:
        """Atualiza o "reduzir movimento" do mascote (a leitura do macOS, guardada por alguns segundos: nada de consulta
        por quadro) quando o avatar desenhado, ou o que entra numa troca, está no ciclo "rgb". Sem ciclo, nem consulta."""
        if self.mascote.usa_ciclo_rgb:
            self.mascote.movimento_reduzido = self.movimento.reduzido()

    def quadro(self, h, uso, agora_mono):
        try:
            if self.cfg.animado:
                base = self.uso_multi if self.multi else uso  # no multiagente, o cloud reage a todos os agentes
                antes = self.mascote.estado
                self.mascote.ir_para(self.mascote.decidir(base, agora_mono), agora_mono)
                if self.multi and (antes in DO_CLAUDE) != (self.mascote.estado in DO_CLAUDE):
                    self.atualizar_avatar(agora_mono)  # entrou ou saiu de alerta/reset: muda de quem é o mascote
                self.trocar_pacote_se_seguro(agora_mono)
                self.sincronizar_movimento()
                return self.desenhar_animado(h, uso, agora_mono)
            self.trocar_pacote_se_seguro(agora_mono)  # sem o mascote na tela, os do app ainda trocam
            return desenhar(h, uso, paisagem_=self.cfg.paisagem_efetiva)
        except Exception:
            # Um erro no desenho é bug, não falha recuperável: registra com o contexto e fecha.
            log_render.critical("falha ao desenhar o quadro (layout %s, cloud %s); o Ombro vai fechar",
                                NOMES_LAYOUT[self.cfg.layout], self.mascote.estado, exc_info=True)
            raise SystemExit(1) from None

    def salvar_png(self, destino: Path) -> None:
        if self.hw and self.hw.macmon:
            time.sleep(1.5)  # espera a primeira amostra do macmon
        self.ler_hw()
        time.sleep(0.5)
        agora = time.monotonic()
        uso = self.ler_claude()
        self.atualizar_avatar(agora, ja=True)  # o avatar de quem o mascote representa, como no laço
        img = self.quadro(self.ler_hw(), uso, agora)
        if self.cfg.animado:  # sem mistura com o estado anterior, e já no meio do movimento
            self.mascote.anterior = None
            self.sincronizar_movimento()  # o "reduzir movimento" vale também no quadro final (o ciclo "rgb" fica parado)
            img = self.desenhar_animado(self.ler_hw(), self.ler_claude(), agora + 1.0)
        img.save(destino)
        log.info("quadro salvo em %s", curto(destino))

    # ---------- saída ----------
    def abrir_saida(self, abrir_navegador: Optional[bool] = None):
        """A tela USB (com --usb) ou o simulador. Um simulador sem a porta livre levanta OSError."""
        cfg = self.cfg
        if cfg.usb:
            from saida import TelaUSB, escolher_transporte

            transporte, aviso = escolher_transporte(cfg.transporte, cfg.revisao)
            if aviso:
                log_usb.warning(aviso)
            self.saida = TelaUSB(cfg.revisao, cfg.porta, cfg.brilho, cfg.lib, paisagem=cfg.paisagem_efetiva,
                                 girar=cfg.girar, transporte=transporte)
            log.info("mandando para a tela (%s). Ctrl+C para sair.", "USB direto" if transporte == "usb" else "porta serial")
        else:
            from saida import Simulador

            self.saida = Simulador(cfg.porta_sim, paisagem=cfg.paisagem_efetiva)
            log.info("simulador em %s  (Ctrl+C para sair)", self.saida.url)
            abrir = (not cfg.nao_abrir) if abrir_navegador is None else abrir_navegador
            if abrir:
                webbrowser.open(self.saida.url)
        return self.saida

    # ---------- o laço ----------
    def laco(self, parar: Callable[[], bool]) -> None:
        demo_ciclo = bool(self.demo and self.cfg.demo_ciclo > 0)
        estat = Estatisticas(self.saida, time.monotonic())
        if hasattr(self.saida, "interromper"):  # a tela USB para de mandar um quadro quando chega um pedido
            self.saida.interromper = lambda: not self._pedidos.empty()
        self._atendendo = True
        try:
            while not parar():
                inicio = time.monotonic()
                intervalo = self.cfg.intervalo_efetivo  # lido a cada volta: o layout (e o intervalo) mudam ao vivo
                pronta = self.saida.pronta()
                # tela USB fora e ainda não é hora de tentar de novo: nada a mandar. O quadro ainda é desenhado de
                # vez em quando (e logo depois de uma mudança de preferência), para a prévia não parar no passado
                if not pronta and not self._redesenhar and inicio - self._ultimo_desenho < SEM_TELA_S:
                    estat.contar(inicio, None)
                    self.esperar(max(0.05, intervalo), parar)
                    continue
                self._redesenhar = False
                if self._uso is None or inicio - self._ultimo_uso >= INTERVALO_CLAUDE or demo_ciclo:
                    self._uso, self._ultimo_uso = self.ler_claude(), inicio  # problemas na leitura vão para o log
                    self.coletado_em = datetime.now()
                    self.atualizar_avatar(inicio)
                    if self.ao_coletar is not None:
                        self.ao_coletar()
                self.ultimo_hw = self.ler_hw()  # o ombrod publica CPU e RAM a partir desta leitura
                img = self.quadro(self.ultimo_hw, self._uso, inicio)
                if pronta:
                    self.saida.mostrar(img)
                self.ultimo_quadro, self.ultimo_quadro_em = img, datetime.now()
                self._ultimo_desenho = inicio
                self.quadros += 1
                estat.contar(time.monotonic(), (time.monotonic() - inicio) * 1000)
                self.esperar(max(0.05, intervalo - (time.monotonic() - inicio)), parar)
        finally:
            self._atendendo = False

    def esperar(self, segundos: float, parar: Callable[[], bool] = lambda: False) -> None:
        """Dorme até `segundos`, atendendo os pedidos que chegarem nesse meio tempo."""
        fim = time.monotonic() + segundos
        while True:
            resta = fim - time.monotonic()
            if resta <= 0:
                return
            try:
                pedido = self._pedidos.get(timeout=resta)
            except queue.Empty:
                return
            pedido.executar()
            if parar() or self._redesenhar:  # fechando, ou uma preferência mudou: o quadro novo sai agora
                return

    def acordar(self) -> None:
        """Interrompe a espera entre quadros (para um sinal ser atendido na hora). Seguro num tratador de sinal."""
        self._pedidos.put(_Pedido(None))

    def executar(self, fn: Callable, timeout: float = 2.0):
        """Roda `fn()` no laço, entre dois quadros, e devolve o resultado. Chamado de outra thread."""
        if not self._atendendo:
            raise Indisponivel("o Ombro não está atendendo pedidos agora")
        pedido = _Pedido(fn)
        self._pedidos.put(pedido)
        if not pedido.feito.wait(timeout):
            if pedido.cancelar():
                raise TimeoutError("o laço do Ombro não respondeu a tempo")
            pedido.feito.wait()  # o laço já começou a atender: espera terminar, para não dizer "nada mudou" errado
        if pedido.erro is not None:
            raise pedido.erro
        return pedido.resultado

    def recusar_pedidos(self) -> None:
        """Para de aceitar pedidos e libera quem estava esperando um (fechando)."""
        self._atendendo = False
        while True:
            try:
                pedido = self._pedidos.get_nowait()
            except queue.Empty:
                return
            pedido.erro = Indisponivel("o Ombro está fechando")
            pedido.feito.set()

    # ---------- mudanças ao vivo (só dentro do laço, via executar) ----------
    def aplicar_cfg(self, novo: OmbroConfig) -> list:
        """O único caminho de uma mudança de preferência com o Ombro rodando: põe `novo` em uso e acerta tudo o
        que depende dele (brilho e orientação da tela, layout, cores, avatares, visão). Não reabre a tela nem mexe
        nos agentes, nas sessões ou no banco. Voltar atrás é chamar de novo com a configuração de antes.

        Devolve as chaves cujo efeito na tela física ficou para quando ela reconectar (a tela USB está fora): a
        configuração já vale, o desenho e a prévia já mudaram, e a tela recebe brilho e orientação ao voltar."""
        antes, self.cfg = self.cfg, novo
        adiadas = []
        if novo.brilho != antes.brilho:
            definir = getattr(self.saida, "definir_brilho", None)
            if definir is not None and not definir(novo.brilho):
                adiadas.append("brilho")
        if (novo.paisagem_efetiva, novo.girar) != (antes.paisagem_efetiva, antes.girar):
            definir = getattr(self.saida, "definir_orientacao", None)
            if definir is not None and not definir(novo.paisagem_efetiva, novo.girar):
                adiadas += [k for k in DA_ORIENTACAO if getattr(novo, k) != getattr(antes, k)]
        if novo.layout != antes.layout:
            self._uso = None  # a próxima volta do laço coleta de novo (o multiagente precisa do resumo)
            log.info("layout: %s", NOMES_LAYOUT[novo.layout])
        if novo.cor_mascote != antes.cor_mascote:
            self.mascote.cor_fixa = novo.cor_mascote_rgb
            self.mascote.cor = self.mascote._cor_do_estado()  # sem cor fixa, volta à do agente (ou a uma sorteada)
        if novo.cor_modo != antes.cor_modo:
            self.mascote.definir_cor_modo(novo.cor_modo)
        if (novo.vista, novo.foco, novo.rotacao, novo.intervalo_rotacao) != (antes.vista, antes.foco, antes.rotacao,
                                                                              antes.intervalo_rotacao):
            self.seletor = SeletorDeVista(novo.vista, novo.foco, novo.rotacao, novo.intervalo_rotacao)
        if novo.pacote_animacao != antes.pacote_animacao:
            self.animacoes.pedir(novo.pacote_animacao)  # só a última escolha vale
            self.trocar_pacote_se_seguro(time.monotonic())  # nada em andamento: troca já (app e telinha)
        if novo.cores_avatares != antes.cores_avatares:
            # só as entradas de cor: nenhuma troca imediata de avatar (uma edição de outro id não corta a transição em
            # andamento); a do avatar desenhado e a do que entra no meio da troca são lidas na hora de desenhar
            self.mascote.definir_entradas(self.entradas_de_cor())
        if (novo.avatares, novo.avatar_sorteio, novo.cor_modo, novo.layout, novo.vista, novo.foco) != (
                antes.avatares, antes.avatar_sorteio, antes.cor_modo, antes.layout, antes.vista, antes.foco):
            self.atualizar_avatar(time.monotonic(), ja=True)  # o mascote troca sem esperar o estado mudar
        self._redesenhar = True
        return adiadas

    def definir_brilho(self, valor: int) -> bool:
        """Muda o brilho. Verdadeiro se chegou a uma tela de verdade agora (no simulador, não há brilho)."""
        adiadas = self.aplicar_cfg(replace(self.cfg, brilho=valor))
        return hasattr(self.saida, "definir_brilho") and "brilho" not in adiadas

    def definir_layout(self, layout: str) -> None:
        self.aplicar_cfg(replace(self.cfg, layout=layout))

    def definir_cor(self, cor: Optional[str]) -> None:
        self.aplicar_cfg(replace(self.cfg, cor_mascote=cor))

    def definir_vista(self, **mudancas) -> None:
        self.aplicar_cfg(replace(self.cfg, **mudancas))

    # ---------- o pacote de animações (só dentro do laço) ----------
    def atividade(self) -> Atividade:
        """As sessões em andamento de todos os agentes ligados, seja qual for o layout ou a visão em foco."""
        store = self.monitor.store
        return Atividade.das_sessoes(self.coletado_em is not None,
                                     ((p.provider_id, store.sessions(p.provider_id)) for p in self.monitor.registry))

    def trocar_pacote_se_seguro(self, agora: float) -> bool:
        """Se há uma troca de pacote pendente e é um momento seguro (ombro/animacoes.py), troca agora: a telinha no
        próximo quadro e o app no próximo retrato. Num layout sem o mascote (o clássico), só a atividade dos agentes
        conta. Verdadeiro se trocou. Chamado a cada quadro, então também acompanha há quanto tempo está tudo quieto."""
        a = self.animacoes
        na_tela = self.cfg.animado
        trocou = False
        if a.pode_trocar(agora, self.atividade(), lambda: not na_tela or self.mascote.momento_seguro(agora)):
            try:
                self.mascote.usar_pacote(a.desejado, agora, ja=not na_tela)
            except Exception as e:  # noqa: BLE001 - o pacote não carregou: segue o que está em uso
                a.recusar(a.desejado, e)
            else:
                a.confirmar(a.desejado)
                self._redesenhar = trocou = True
        publicado = a.como_dict()
        if publicado != self.animacao_publicada:
            self.animacao_publicada = publicado
        return trocou

    # ---------- o avatar do mascote ----------
    def _sessao_relevante(self, provider: str) -> Optional[str]:
        """A sessão que o mascote representa num agente: a que espera você, senão a que trabalha, senão a mais recente."""
        lista = [s for s in self.monitor.store.sessions(provider) if not s.disconnected]
        if not lista:
            return None
        def peso(s):
            esperando = s.attention in (Attention.WAITING_USER, Attention.WAITING_APPROVAL, Attention.ERROR)
            return (esperando, s.activity is Activity.WORKING, s.updated_at or datetime.min)
        return max(lista, key=peso).session_id

    def sujeito(self) -> tuple:
        """(agente, sessão) de quem o mascote fala. Nos layouts de um agente, o Claude. No multiagente: o Claude em
        alerta e reset (são os limites dele); senão o mais urgente; senão o da visão em foco; senão o único que
        trabalha (ou o único com sessões); senão ninguém (None: o avatar do Ombro)."""
        if not self.multi or self.mascote.estado in DO_CLAUDE:
            return CLAUDE, self._sessao_relevante(CLAUDE)
        r = self.resumo
        if r is None:
            return None, None
        if r.mais_urgente is not None:
            urg = r.mais_urgente
            return urg.provider_id, urg.sessao.session_id if urg.sessao else self._sessao_relevante(urg.provider_id)
        if self.cfg.vista == "focus" and self.cfg.foco:
            return self.cfg.foco, self._sessao_relevante(self.cfg.foco)
        for grupo in ([p for p in r.providers if p.trabalhando], [p for p in r.providers if p.ativo]):
            if len(grupo) == 1:
                return grupo[0].provider_id, self._sessao_relevante(grupo[0].provider_id)
        return None, None

    def avatar_de(self, provider: Optional[str], sessao: Optional[str] = None) -> "avatares.Avatar":
        pref = self.cfg.avatar(provider or "padrao")
        return avatares.escolher(pref, provider, self.cfg.avatar_sorteio, self.semente, sessao)

    def cor_de(self, provider: Optional[str], av: "avatares.Avatar") -> tuple:
        cat = avatares.catalogo()
        return avatares.cor_hex(cat.cor_agente.get(provider, av.cor) if provider else av.cor)

    def entradas_de_cor(self) -> dict:
        """As cores pedidas por id de avatar (config.cores_avatares) como o mascote as lê: "default" -> ("padrao",), a cor
        original (a do agente, por cima do cor_mascote/cor_modo); "rgb" -> ("rgb",), o ciclo; "#RRGGBB" -> ("fixa", rgb).
        Sem entrada, o avatar herda o que valia antes."""
        out = {}
        for aid, valor in self.cfg.cores_avatares:
            if valor == "default":
                out[aid] = ("padrao",)
            elif valor == "rgb":
                out[aid] = ("rgb",)
            else:
                out[aid] = ("fixa", avatares.cor_hex(valor))
        return out

    def atualizar_avatar(self, agora: float, ja: bool = False) -> None:
        """Pede ao mascote o avatar de quem ele representa agora (entra na próxima troca de estado)."""
        provider, sessao = self.sujeito_atual = self.sujeito()
        av = self.avatar_de(provider, sessao)  # a cor pedida é do avatar já resolvido (inclusive o aleatório por sessão)
        self.mascote.definir_entradas(self.entradas_de_cor())
        self.mascote.usar(av, self.cor_de(provider, av), agora, ja=ja)

    def avatares_resolvidos(self) -> dict:
        """O avatar e a cor de cada agente agora (o "aleatório ao iniciar" já sorteado), para a IPC."""
        out = {}
        for p in (*[pid for pid, _ in self.nomes], None):
            av = self.avatar_de(p)
            out[p or "padrao"] = {"avatar": av.id, "color": "#%02X%02X%02X" % self.cor_de(p, av),
                                  "choice": self.cfg.avatar(p or "padrao")}
        return out

    def definir_avatar(self, chave: str, valor) -> None:
        """Mudou uma preferência de avatar ou de cor: vale na hora (o mascote troca sem esperar o estado mudar)."""
        from .preferencias import aplicar_em

        self.aplicar_cfg(aplicar_em(self.cfg, {chave: valor}))

    # ---------- fim ----------
    def fechar(self) -> None:
        """Solta tudo, nesta ordem: pedidos pendentes, tela, banco (pelo monitor) e o leitor de hardware."""
        self.recusar_pedidos()
        if self.saida is not None:
            try:
                self.saida.fechar()
            except Exception:  # noqa: BLE001 - fechar é arrumação; o resto ainda precisa fechar
                log.debug("erro ao fechar a saída (ignorado)", exc_info=True)
        self.monitor.fechar()
        if self.hw:
            self.hw.parar()
