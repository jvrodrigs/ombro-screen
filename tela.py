#!/usr/bin/env python3
"""Ombro Screen: hardware do Mac + uso do Claude Code numa telinha USB de 3,5".

  python3 tela.py                 simulador no navegador (http://localhost:5680)
  python3 tela.py --demo          simulador com dados inventados
  python3 tela.py --usb           manda para a tela de verdade
  python3 tela.py                 layout do Ombro (480x320 deitada, com o cloud); é o padrão
  python3 tela.py --paisagem --layout classico   o painel antigo (anéis, CPU, GPU, rede e disco)
  python3 tela.py --demo --demo-ciclo 7          o cloud passa por todos os estados, 7 s cada
  python3 tela.py --png quadro.png    salva um quadro e sai
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

from coletores.claude import ColetorClaude  # noqa: E402
from coletores.demo import Demo  # noqa: E402
from coletores.hardware import ColetorHardware  # noqa: E402
from layout_ombro import desenhar_ombro  # noqa: E402
from mascote import Mascote, cor_de_hex  # noqa: E402
from render import desenhar  # noqa: E402


def argumentos():
    p = argparse.ArgumentParser(description="Hardware do Mac e uso do Claude Code numa tela USB de 3,5\".")
    p.add_argument("--usb", action="store_true", help="usar a tela USB (sem isto, abre o simulador)")
    p.add_argument("--revisao", default="A", help="revisão do hardware da tela: A, B, C, D ou WEACT_A (padrão: A)")
    p.add_argument("--porta", default="AUTO", help="porta serial, ex.: /dev/cu.usbmodem1101 (padrão: AUTO)")
    p.add_argument("--brilho", type=int, default=30, help="brilho em %% (padrão 30; a revisão A esquenta acima de 50)")
    p.add_argument("--lib", type=Path, default=AQUI / "turing-smart-screen-python", help="pasta da turing-smart-screen-python")
    p.add_argument("--paisagem", action="store_true", help="tela deitada, 480x320 (padrão: em pé, 320x480)")
    p.add_argument("--girar", action="store_true", help="girar 180° (quando a tela fica de cabeça para baixo no case)")
    p.add_argument("--demo", action="store_true", help="usar dados inventados")
    p.add_argument("--sem-macmon", action="store_true", help="não usar o macmon (só psutil)")
    p.add_argument("--claude-dir", default=None, help="pasta de configuração do Claude Code (padrão: ~/.claude)")
    p.add_argument("--layout", choices=("auto", "ombro", "classico"), default="auto",
                   help="ombro (480x320 deitada, com o cloud; é o padrão) ou classico (o painel antigo, que respeita --paisagem)")
    p.add_argument("--intervalo", type=float, default=None, help="segundos entre quadros (padrão: 1 no clássico, 0,2 no ombro)")
    p.add_argument("--demo-ciclo", type=float, default=0, metavar="SEG", help="com --demo: troca de cenário a cada SEG segundos")
    p.add_argument("--cor-mascote", default=None, metavar="#HEX", help="cor fixa do cloud (padrão: sorteia uma a cada estado)")
    p.add_argument("--porta-sim", type=int, default=5680, help="porta do simulador (padrão 5680)")
    p.add_argument("--nao-abrir", action="store_true", help="não abrir o navegador no modo simulador")
    p.add_argument("--png", type=Path, help="salvar um único quadro neste arquivo e sair")
    return p.parse_args()


def main():
    a = argumentos()
    demo = Demo() if a.demo else None
    hw = None if demo else ColetorHardware(usar_macmon=not a.sem_macmon)
    cl = None if demo else ColetorClaude(a.claude_dir)

    def ler_hw():
        return demo.hardware() if demo else hw.ler()

    def ler_claude():
        return demo.claude(a.demo_ciclo) if demo else cl.ler()

    ombro = a.layout in ("auto", "ombro")
    if ombro:
        a.paisagem = True  # o layout do Ombro é sempre 480x320
    intervalo = a.intervalo if a.intervalo is not None else (0.2 if ombro else 1.0)
    mascote = Mascote(cor_fixa=cor_de_hex(a.cor_mascote) if a.cor_mascote else None)
    mascote.inicio = time.monotonic()

    def quadro(h, uso, agora_mono):
        if ombro:
            mascote.ir_para(mascote.decidir(uso, agora_mono), agora_mono)
            return desenhar_ombro(h, uso, mascote, datetime.now(), agora_mono)
        return desenhar(h, uso, paisagem_=a.paisagem)

    if a.png:
        if hw and hw.macmon:
            time.sleep(1.5)  # espera a primeira amostra do macmon
        ler_hw()
        time.sleep(0.5)
        agora = time.monotonic()
        img = quadro(ler_hw(), ler_claude(), agora)
        if ombro:  # sem mistura com o estado anterior, e já no meio do movimento
            mascote.anterior = None
            img = desenhar_ombro(ler_hw(), ler_claude(), mascote, datetime.now(), agora + 1.0)
        img.save(a.png)
        print(f"quadro salvo em {a.png}")
        return

    if a.usb:
        from saida import TelaUSB

        saida = TelaUSB(a.revisao.upper(), a.porta, a.brilho, a.lib, paisagem=a.paisagem, girar=a.girar)
        print("mandando para a tela USB. Ctrl+C para sair.")
    else:
        from saida import Simulador

        saida = Simulador(a.porta_sim, paisagem=a.paisagem)
        print(f"simulador em {saida.url}  (Ctrl+C para sair)")
        if not a.nao_abrir:
            webbrowser.open(saida.url)

    parar = False

    def ao_sinal(*_):
        nonlocal parar
        parar = True

    signal.signal(signal.SIGINT, ao_sinal)
    signal.signal(signal.SIGTERM, ao_sinal)

    uso, ultimo_uso = None, 0.0
    try:
        while not parar:
            inicio = time.monotonic()
            if uso is None or inicio - ultimo_uso >= 5 or (demo and a.demo_ciclo > 0):  # arquivos do Claude: a cada 5 s basta
                uso, ultimo_uso = ler_claude(), inicio
                for e in uso.erros:
                    print("aviso:", e, file=sys.stderr)
            saida.mostrar(quadro(ler_hw(), uso, inicio))
            time.sleep(max(0.05, intervalo - (time.monotonic() - inicio)))
    finally:
        saida.fechar()
        if hw:
            hw.parar()


if __name__ == "__main__":
    main()
