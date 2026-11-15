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
import threading
import time
import webbrowser
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import psutil

from apresentacao import uso_claude, uso_mascote
from coletores.claude import ColetorClaude
from coletores.demo import Demo
from coletores.hardware import ColetorHardware, Hardware
from config import NOMES_LAYOUT, OmbroConfig, pasta_ombro
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
from providers.demo import codex_demo, cursor_demo
from render import desenhar

log = logger("main")
log_hw = logger("hardware")
log_render = logger("render")
log_usb = logger("usb")

INTERVALO_CLAUDE = 5.0  # segundos entre leituras dos arquivos do Claude Code (e dos eventos dos hooks)
ESTATISTICAS_S = 60.0  # com --debug, um resumo do laço a cada minuto
ANIMADOS = ("auto", "ombro", "minimal-v2", "multiagente")  # layouts que dá para trocar sem reiniciar


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


def ao_vivo(cfg: OmbroConfig) -> bool:
    """Este Ombro lê eventos, grava o banco ou fala com a tela USB? Então precisa do lock de instância."""
    return not cfg.png and (not cfg.demo or cfg.usb)


class _Pedido:
    __slots__ = ("fn", "feito", "resultado", "erro", "cancelado")

    def __init__(self, fn: Optional[Callable]):
        self.fn, self.feito, self.resultado, self.erro, self.cancelado = fn, threading.Event(), None, None, False

    def executar(self) -> None:
        if self.fn is None or self.cancelado:
            self.feito.set()
            return
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
        self._avisos_hw = SoQuandoMuda(log_hw)
        self._ultimo_hw = Hardware(disponivel=False)  # até a primeira leitura boa
        self.mascote = Mascote(cor_fixa=cfg.cor_mascote_rgb)
        if self.demo and cfg.demo_ciclo > 0:  # no ciclo de demonstração, o "concluído" dura um cenário, não 5 min
            self.mascote.duracao_concluido = cfg.demo_ciclo
        self.mascote.inicio = time.monotonic()
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
        self.ao_coletar: Optional[Callable[[], None]] = None  # chamado no laço depois de cada coleta

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
        return uso_claude(snap_claude)

    # ---------- desenho ----------
    def desenhar_animado(self, h, uso, agora_mono):
        if self.multi:
            vista, foco = self.seletor.escolher(self.resumo, agora_mono)
            return desenhar_multiagente(h, self.resumo, self.mascote, datetime.now(), agora_mono, vista, foco)
        if self.cfg.layout == "minimal-v2":
            return desenhar_minimal(h, uso, self.mascote, datetime.now(), agora_mono, agente=CLAUDE)
        return desenhar_ombro(h, uso, self.mascote, datetime.now(), agora_mono)

    def quadro(self, h, uso, agora_mono):
        try:
            if self.cfg.animado:
                base = self.uso_multi if self.multi else uso  # no multiagente, o cloud reage a todos os agentes
                self.mascote.ir_para(self.mascote.decidir(base, agora_mono), agora_mono)
                return self.desenhar_animado(h, uso, agora_mono)
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
        img = self.quadro(self.ler_hw(), self.ler_claude(), agora)
        if self.cfg.animado:  # sem mistura com o estado anterior, e já no meio do movimento
            self.mascote.anterior = None
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
        intervalo, demo_ciclo = self.cfg.intervalo_efetivo, bool(self.demo and self.cfg.demo_ciclo > 0)
        estat = Estatisticas(self.saida, time.monotonic())
        self._atendendo = True
        try:
            while not parar():
                inicio = time.monotonic()
                if not self.saida.pronta():  # tela USB fora e ainda não é hora de tentar de novo: nada a desenhar
                    estat.contar(inicio, None)
                    self.esperar(max(0.05, intervalo), parar)
                    continue
                if self._uso is None or inicio - self._ultimo_uso >= INTERVALO_CLAUDE or demo_ciclo:
                    self._uso, self._ultimo_uso = self.ler_claude(), inicio  # problemas na leitura vão para o log
                    self.coletado_em = datetime.now()
                    if self.ao_coletar is not None:
                        self.ao_coletar()
                img = self.quadro(self.ler_hw(), self._uso, inicio)
                self.saida.mostrar(img)
                self.ultimo_quadro, self.ultimo_quadro_em = img, datetime.now()
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
            if parar():
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
            pedido.cancelado = True
            raise TimeoutError("o laço do Ombro não respondeu a tempo")
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
    def definir_brilho(self, valor: int) -> bool:
        """Muda o brilho. Verdadeiro se chegou a uma tela de verdade agora (no simulador, não há brilho)."""
        self.cfg = replace(self.cfg, brilho=valor)
        definir = getattr(self.saida, "definir_brilho", None)
        return bool(definir(valor)) if definir is not None else False

    def definir_layout(self, layout: str) -> None:
        if layout not in ANIMADOS or self.cfg.layout not in ANIMADOS:
            raise ValueError("trocar de ou para o layout clássico muda a orientação da tela: reinicie o Ombro")
        if layout == self.cfg.layout:
            return
        self.cfg = replace(self.cfg, layout=layout)
        self._uso = None  # a próxima volta do laço coleta de novo (o multiagente precisa do resumo)

    def definir_cor(self, cor: Optional[str]) -> None:
        self.cfg = replace(self.cfg, cor_mascote=cor)
        self.mascote.cor_fixa = self.cfg.cor_mascote_rgb
        if self.mascote.cor_fixa:
            self.mascote.cor = self.mascote.cor_fixa

    def definir_vista(self, **mudancas) -> None:
        self.cfg = replace(self.cfg, **mudancas)
        c = self.cfg
        self.seletor = SeletorDeVista(c.vista, c.foco, c.rotacao, c.intervalo_rotacao)

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
