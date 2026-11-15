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
import platform
import signal
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

from config import (  # noqa: E402
    LAYOUTS, NOMES_LAYOUT, PROVIDERS, TRANSPORTES, VISTAS, ConfigInvalida, OmbroConfig, resolver,
)
from logging_config import curto, logger, sem_casa  # noqa: E402
from logging_config import configurar as configurar_logs  # noqa: E402
# o laço mora em ombro/app.py (o mesmo do ombrod); os nomes abaixo continuam importáveis daqui
from ombro.app import (  # noqa: E402,F401
    ESTATISTICAS_S, INTERVALO_CLAUDE, Aplicacao, Estatisticas, ao_vivo, banco_de_estado, montar_providers,
    pasta_de_eventos,
)
from ombro.instancia import OutraInstancia, Trava  # noqa: E402

log = logger("main")
log_cfg = logger("config")


def argumentos(argv=None, prog: str | None = None, descricao: str | None = None) -> tuple[OmbroConfig, list[str]]:
    """Opções da linha de comando juntadas com o arquivo de configuração (ver config.py)."""
    sim_nao = argparse.BooleanOptionalAction  # --usb e --no-usb: a linha de comando desliga o que o arquivo ligou
    p = argparse.ArgumentParser(
        prog=prog,
        description=descricao or "Hardware do Mac e uso do Claude Code numa tela USB de 3,5\".",
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
                        "com CPU e RAM em barras), multiagente (Claude Code, Codex e Cursor juntos) "
                        "ou classico (o painel antigo, que respeita --paisagem)")
    p.add_argument("--vista", choices=VISTAS, help="no layout multiagente: overview (padrão), focus ou attention")
    p.add_argument("--foco", choices=PROVIDERS, help="no layout multiagente, com --vista focus: qual agente")
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
    app = Aplicacao(cfg)
    if cfg.png:
        try:
            app.salvar_png(cfg.png)
        finally:
            app.fechar()
        return

    # Um Ombro ao vivo por usuário: este tela.py, o ombrod ou o LaunchAgent (ver ombro/instancia.py).
    trava = Trava("tela.py")
    if ao_vivo(cfg):
        try:
            trava.adquirir()
        except OutraInstancia as e:
            app.fechar()
            log.error("%s: pare-o antes (python3 -m ombro service stop, ou Ctrl+C no outro terminal), "
                      "ou rode com --demo ou --png, que não disputam a tela nem os eventos", e)
            raise SystemExit(75) from None  # EX_TEMPFAIL

    motivo = None

    def ao_sinal(sinal, _quadro):
        nonlocal motivo
        motivo = signal.Signals(sinal).name
        app.acordar()

    try:
        app.abrir_saida()
        signal.signal(signal.SIGINT, ao_sinal)
        signal.signal(signal.SIGTERM, ao_sinal)
        app.laco(parar=lambda: motivo is not None)
    finally:
        log.info("encerrando%s", f" ({motivo})" if motivo else "")
        app.fechar()
        trava.soltar()


if __name__ == "__main__":
    main()
