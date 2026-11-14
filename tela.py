#!/usr/bin/env python3
"""Ombro Screen: hardware do Mac + uso do Claude Code numa telinha USB de 3,5".

  python3 tela.py                 simulador no navegador (http://localhost:5680)
  python3 tela.py --demo          simulador com dados inventados
  python3 tela.py --usb           manda para a tela de verdade
  python3 tela.py                 layout do Ombro (480x320 deitada, com o cloud); é o padrão
  python3 tela.py --paisagem --layout classico   o painel antigo (anéis, CPU, GPU, rede e disco)
  python3 tela.py --demo --demo-ciclo 7          o cloud passa por todos os estados, 7 s cada
  python3 tela.py --png quadro.png    salva um quadro e sai
  python3 tela.py --usb --debug       logs detalhados, para diagnosticar problemas
"""
from __future__ import annotations

import argparse
import logging
import platform
import signal
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
INTERVALO_CLAUDE = 5.0  # segundos entre leituras dos arquivos do Claude Code
sys.path.insert(0, str(AQUI))

import psutil  # noqa: E402

from apresentacao import uso_claude  # noqa: E402
from coletores.claude import ColetorClaude  # noqa: E402
from coletores.demo import Demo  # noqa: E402
from coletores.hardware import ColetorHardware, Hardware  # noqa: E402
from config import LAYOUTS, NOMES_LAYOUT, TRANSPORTES, ConfigInvalida, OmbroConfig, pasta_ombro, resolver  # noqa: E402
from core.monitor import Monitor  # noqa: E402
from layout_minimal_v2 import desenhar_minimal  # noqa: E402
from layout_ombro import desenhar_ombro  # noqa: E402
from logging_config import SoQuandoMuda, curto, logger, sem_casa  # noqa: E402
from logging_config import configurar as configurar_logs  # noqa: E402
from mascote import Mascote  # noqa: E402
from providers.claude import PROVIDER_ID as CLAUDE  # noqa: E402
from providers.claude import ClaudeProvider, FonteDemo  # noqa: E402
from render import desenhar  # noqa: E402

log = logger("main")
log_cfg = logger("config")
log_hw = logger("hardware")
log_render = logger("render")
log_usb = logger("usb")
ESTATISTICAS_S = 60.0  # com --debug, um resumo do laço a cada minuto


def argumentos(argv=None) -> tuple[OmbroConfig, list[str]]:
    """Opções da linha de comando juntadas com o arquivo de configuração (ver config.py)."""
    sim_nao = argparse.BooleanOptionalAction  # --usb e --no-usb: a linha de comando desliga o que o arquivo ligou
    p = argparse.ArgumentParser(
        description="Hardware do Mac e uso do Claude Code numa tela USB de 3,5\".",
        epilog="Os padrões podem ficar em ~/.ombro-screen/config.json (veja o README); a linha de comando vence o arquivo.")
    p.add_argument("--usb", action=sim_nao, help="usar a tela USB (sem isto, abre o simulador)")
    p.add_argument("--revisao", help="revisão do hardware da tela: A, B, C, D ou WEACT_A (padrão: A)")
    p.add_argument("--porta", help="porta serial, ex.: /dev/cu.usbmodem1101 (padrão: AUTO)")
    p.add_argument("--transporte", choices=TRANSPORTES,
                   help="usb: direto no endpoint USB (revisão A; precisa de pyusb e libusb); serial: pela biblioteca; "
                        "auto (padrão): usb no Mac com revisão A, serial nos outros casos")
    p.add_argument("--brilho", type=int, help="brilho em %% (padrão 30; a revisão A esquenta acima de 50)")
    p.add_argument("--lib", type=Path, help="pasta da turing-smart-screen-python (padrão: dentro do projeto)")
    p.add_argument("--paisagem", action=sim_nao, help="tela deitada, 480x320 (padrão: em pé, 320x480)")
    p.add_argument("--girar", action=sim_nao, help="girar 180° (quando a tela fica de cabeça para baixo no case)")
    p.add_argument("--demo", action="store_true", default=None, help="usar dados inventados")
    p.add_argument("--sem-macmon", action=sim_nao, help="não usar o macmon (só psutil)")
    p.add_argument("--claude-dir", help="pasta de configuração do Claude Code (padrão: ~/.claude)")
    p.add_argument("--layout", choices=LAYOUTS,
                   help="ombro (480x320 deitada, com o cloud; é o padrão), minimal-v2 (o mesmo conteúdo, mais limpo, "
                        "com CPU e RAM em barras) ou classico (o painel antigo, que respeita --paisagem)")
    p.add_argument("--intervalo", type=float, help="segundos entre quadros (padrão: 1 no clássico, 0,2 no ombro)")
    p.add_argument("--demo-ciclo", type=float, metavar="SEG", help="com --demo: troca de cenário a cada SEG segundos")
    p.add_argument("--cor-mascote", metavar="#HEX", help="cor fixa do cloud (padrão: sorteia uma a cada estado)")
    p.add_argument("--porta-sim", type=int, help="porta do simulador (padrão 5680)")
    p.add_argument("--nao-abrir", action=sim_nao, help="não abrir o navegador no modo simulador")
    p.add_argument("--png", type=Path, help="salvar um único quadro neste arquivo e sair")
    p.add_argument("--config", type=Path, metavar="ARQ.json",
                   help="arquivo de configuração (padrão: ~/.ombro-screen/config.json, se existir)")
    p.add_argument("--debug", action=sim_nao, help="logs detalhados (nível DEBUG), para diagnosticar problemas")
    a = vars(p.parse_args(argv))
    arquivo = a.pop("config")
    try:
        return resolver(a, arquivo)
    except ConfigInvalida as e:
        p.exit(2, f"{p.prog}: erro: {e}\n")


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


def banco_de_estado(cfg: OmbroConfig) -> Path | None:
    """Onde gravar o estado (sessões conhecidas) entre execuções. Dados inventados e quadros avulsos não gravam."""
    if cfg.demo or cfg.png:
        return None
    return pasta_ombro() / "state.db"


def main(argv=None):
    cfg, avisos = argumentos(argv)
    configurar_logs(debug=cfg.debug)
    for aviso in avisos:
        log_cfg.warning(aviso)
    log.info("Ombro Screen iniciando (Python %s, layout %s%s)", platform.python_version(),
             NOMES_LAYOUT[cfg.layout], ", dados de demonstração" if cfg.demo else "")
    log_cfg.info("configuração: %s", curto(cfg.arquivo) if cfg.arquivo else "padrões e linha de comando (sem arquivo)")
    log_cfg.debug("configuração resolvida: %s", {k: sem_casa(v) if isinstance(v, str) else v for k, v in cfg.resumo().items()})
    try:
        rodar(cfg)
    except Exception:
        log.critical("erro inesperado; o Ombro vai fechar", exc_info=True)
        raise SystemExit(1) from None


def rodar(cfg: OmbroConfig) -> None:
    demo = Demo() if cfg.demo else None
    hw = None if demo else ColetorHardware(usar_macmon=not cfg.sem_macmon)
    # Claude Code -> coletor -> ClaudeProvider -> StateStore (e banco) -> UsoClaude para a tela (apresentacao.py)
    fonte = FonteDemo(demo, cfg.demo_ciclo) if demo else ColetorClaude(cfg.claude_dir)
    monitor = Monitor.montar([ClaudeProvider(fonte)], banco=banco_de_estado(cfg))
    avisos_hw = SoQuandoMuda(log_hw)
    ultimo_hw = Hardware(disponivel=False)  # até a primeira leitura boa

    def ler_hw():
        nonlocal ultimo_hw
        if demo:
            return demo.hardware()
        try:
            ultimo_hw = hw.ler()
        except (OSError, psutil.Error) as e:  # leitura do sistema que falhou: repete a anterior e segue
            if avisos_hw.registrar("ler", logging.WARNING, "não consegui ler o hardware (%s: %s); repetindo a última leitura",
                                   type(e).__name__, sem_casa(e)):
                log_hw.debug("detalhes da falha do hardware", exc_info=True)
        else:
            avisos_hw.resolver("ler", "leitura do hardware voltou ao normal")
        return ultimo_hw

    def ler_claude():
        return uso_claude(monitor.atualizar(CLAUDE))

    animado, paisagem = cfg.animado, cfg.paisagem_efetiva
    intervalo = cfg.intervalo_efetivo
    mascote = Mascote(cor_fixa=cfg.cor_mascote_rgb)
    if demo and cfg.demo_ciclo > 0:  # no ciclo de demonstração, o "concluído" dura um cenário, não 5 min
        mascote.duracao_concluido = cfg.demo_ciclo
    mascote.inicio = time.monotonic()

    def desenhar_animado(h, uso, agora_mono):
        if cfg.layout == "minimal-v2":
            return desenhar_minimal(h, uso, mascote, datetime.now(), agora_mono, agente=CLAUDE)
        return desenhar_ombro(h, uso, mascote, datetime.now(), agora_mono)

    def quadro(h, uso, agora_mono):
        try:
            if animado:
                mascote.ir_para(mascote.decidir(uso, agora_mono), agora_mono)
                return desenhar_animado(h, uso, agora_mono)
            return desenhar(h, uso, paisagem_=paisagem)
        except Exception:
            # Um erro no desenho é bug, não falha recuperável: registra com o contexto e fecha.
            log_render.critical("falha ao desenhar o quadro (layout %s, cloud %s); o Ombro vai fechar",
                                NOMES_LAYOUT[cfg.layout], mascote.estado, exc_info=True)
            raise SystemExit(1) from None

    if cfg.png:
        if hw and hw.macmon:
            time.sleep(1.5)  # espera a primeira amostra do macmon
        ler_hw()
        time.sleep(0.5)
        agora = time.monotonic()
        img = quadro(ler_hw(), ler_claude(), agora)
        if animado:  # sem mistura com o estado anterior, e já no meio do movimento
            mascote.anterior = None
            img = desenhar_animado(ler_hw(), ler_claude(), agora + 1.0)
        img.save(cfg.png)
        log.info("quadro salvo em %s", curto(cfg.png))
        return

    if cfg.usb:
        from saida import TelaUSB, escolher_transporte

        transporte, aviso = escolher_transporte(cfg.transporte, cfg.revisao)
        if aviso:
            log_usb.warning(aviso)
        saida = TelaUSB(cfg.revisao, cfg.porta, cfg.brilho, cfg.lib, paisagem=paisagem, girar=cfg.girar, transporte=transporte)
        log.info("mandando para a tela (%s). Ctrl+C para sair.", "USB direto" if transporte == "usb" else "porta serial")
    else:
        from saida import Simulador

        saida = Simulador(cfg.porta_sim, paisagem=paisagem)
        log.info("simulador em %s  (Ctrl+C para sair)", saida.url)
        if not cfg.nao_abrir:
            webbrowser.open(saida.url)

    motivo = None

    def ao_sinal(sinal, _quadro):
        nonlocal motivo
        motivo = signal.Signals(sinal).name

    signal.signal(signal.SIGINT, ao_sinal)
    signal.signal(signal.SIGTERM, ao_sinal)

    uso, ultimo_uso = None, 0.0
    estat = Estatisticas(saida, time.monotonic())
    try:
        while motivo is None:
            inicio = time.monotonic()
            if not saida.pronta():  # tela USB fora e ainda não é hora de tentar de novo: nada a desenhar
                estat.contar(inicio, None)
                time.sleep(max(0.05, intervalo))
                continue
            if uso is None or inicio - ultimo_uso >= INTERVALO_CLAUDE or (demo and cfg.demo_ciclo > 0):
                uso, ultimo_uso = ler_claude(), inicio  # problemas na leitura vão para o log do coletor
            saida.mostrar(quadro(ler_hw(), uso, inicio))
            estat.contar(time.monotonic(), (time.monotonic() - inicio) * 1000)
            time.sleep(max(0.05, intervalo - (time.monotonic() - inicio)))
    finally:
        log.info("encerrando%s", f" ({motivo})" if motivo else "")
        saida.fechar()
        monitor.fechar()
        if hw:
            hw.parar()


if __name__ == "__main__":
    main()
