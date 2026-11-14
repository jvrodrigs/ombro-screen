#!/usr/bin/env python3
"""Confere que uma mudança no código não mudou nenhum pixel da tela.

  python3 scripts/quadros_referencia.py --salvar antes.json     no código de antes
  python3 scripts/quadros_referencia.py --comparar antes.json   no código de depois
  python3 scripts/quadros_referencia.py --png pasta/            salva os quadros como PNG para olhar

Desenha um conjunto fixo de quadros (os dois layouts, todos os estados do cloud, dados vazios e
valores extremos) com entradas fixas, e guarda o hash de cada um. Compare só na mesma máquina e
com as mesmas versões de Pillow e FreeType: outra versão pode desenhar as letras com pixels
diferentes sem que nada esteja errado. Por isso isto não roda na CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

from benchmark import AGORA, GB, hardware_fixo, mascote_animando, uso_fixo  # noqa: E402
from dataclasses import replace  # noqa: E402

from coletores.claude import Janela, UsoClaude  # noqa: E402
from coletores.hardware import Hardware  # noqa: E402
from layout_minimal_v2 import desenhar_minimal, reiniciar_niveis  # noqa: E402
from layout_ombro import desenhar_ombro  # noqa: E402
from mascote import ESTADOS  # noqa: E402
from render import desenhar  # noqa: E402


def extremos() -> tuple[Hardware, UsoClaude]:
    h = Hardware(cpu_pct=100.0, nucleos=[("E", 100.0), ("P", 100.0)], gpu_pct=100.0, ram_usada=64 * GB, ram_total=64 * GB,
                 disco_pct=100, disco_livre=0, rede_down=950 * 1024**2, rede_up=0, temp_cpu=105.0, temp_gpu=99.0,
                 watts_soc=88.8, hist_cpu=[100.0] * 60, hist_gpu=[0.0] * 60, macmon=True)
    u = UsoClaude(cinco_horas=Janela(100.0, AGORA + timedelta(minutes=1)), semana=Janela(100.0, AGORA + timedelta(days=6)),
                  fonte="/usage", coletado_em=AGORA - timedelta(hours=5), desatualizado=True,
                  tokens_entrada=9_999_000_000, tokens_saida=1_234_567_890, sessoes=9, trabalhando=4, esperando=5,
                  status_sessoes=["busy", "waiting"] * 4 + ["idle"], espera_projeto="um-projeto-com-nome-bem-comprido",
                  espera_desde=AGORA - timedelta(hours=3), tokens_por_hora=[10**9] * 16 + [None] * 8, configurado=True)
    return h, u


def cenarios_minimal():
    """Os estados que o Minimal v2 precisa mostrar bem, inclusive os difíceis (nome: (hardware, uso, estado do cloud))."""
    h, hx, ux = hardware_fixo(), *extremos()
    longo = replace(uso_fixo("esperando"), espera_projeto="um-projeto-com-um-nome-comprido-demais-para-caber-na-linha")
    varias = replace(uso_fixo("trabalhando"), sessoes=6, trabalhando=3, esperando=2,
                     status_sessoes=["busy", "waiting", "busy", "idle", "waiting", "busy"])
    return {
        "trabalhando": (h, uso_fixo("trabalhando"), "trabalhando"),
        "ocioso": (h, uso_fixo("ocioso"), "ocioso"),
        "esperando": (h, uso_fixo("esperando"), "esperando"),
        "alerta-quota": (h, uso_fixo("alerta"), "alerta"),
        "reset": (h, uso_fixo("reset"), "reset"),
        "concluido": (h, uso_fixo("concluido"), "concluido"),
        "cpu-alta": (replace(h, cpu_pct=92.0), uso_fixo("trabalhando"), "trabalhando"),
        "ram-alta": (replace(h, ram_total=16 * GB, ram_usada=int(14.1 * GB)), uso_fixo("trabalhando"), "trabalhando"),
        "ram-critica-32gb": (replace(h, ram_total=32 * GB, ram_usada=int(30.5 * GB)), uso_fixo("ocioso"), "ocioso"),
        "pressao-critica": (replace(h, pressao_memoria="critico"), uso_fixo("trabalhando"), "trabalhando"),
        "sem-dados": (Hardware(disponivel=False), UsoClaude(), "ocioso"),
        "sessoes-simultaneas": (h, varias, "esperando"),
        "nome-longo": (h, longo, "esperando"),
        "extremos": (hx, ux, "esperando"),
    }


def quadros_minimal():
    for nome, (h, u, estado) in cenarios_minimal().items():
        reiniciar_niveis()  # cada quadro sem a histerese do anterior
        yield f"minimal-{nome}", desenhar_minimal(h, u, mascote_animando(estado), AGORA, 11.1)


def quadros_multiagente():
    import sys as _sys
    _sys.path.insert(0, str(RAIZ / "tests"))
    from test_multiagente import NOMES, resumo_demo  # os mesmos dados dos testes do layout

    import layout_multiagente as lm
    from core.resumo import resumir
    from core.state import StateStore

    h = hardware_fixo()
    for nome, (vista, foco, urgente) in {"overview": ("overview", None, True), "overview-calmo": ("overview", None, False),
                                         "attention": ("attention", None, True), "focus-claude": ("focus", "claude", True),
                                         "focus-codex": ("focus", "codex", True), "focus-cursor": ("focus", "cursor", True)}.items():
        reiniciar_niveis()
        yield f"multi-{nome}", lm.desenhar_multiagente(h, resumo_demo(urgente), mascote_animando("esperando"), AGORA, 11.1,
                                                       vista, foco, usar_cache=False)
    reiniciar_niveis()
    yield "multi-vazio", lm.desenhar_multiagente(Hardware(disponivel=False), resumir(StateStore(), NOMES),
                                                 mascote_animando("ocioso"), AGORA, 11.1, usar_cache=False)


def quadros():
    h = hardware_fixo()
    for estado in ESTADOS:
        u = uso_fixo(estado)
        for t in (0.0, 0.3, 1.1, 2.0, 3.7):
            yield f"ombro-{estado}-{t}", desenhar_ombro(h, u, mascote_animando(estado), AGORA, 10 + t)
    m = mascote_animando("ocioso")
    m.anterior = ("trabalhando", 9.0)  # no meio da mistura entre dois estados
    yield "ombro-mistura", desenhar_ombro(h, uso_fixo("ocioso"), m, AGORA, 10.2)
    yield "ombro-vazio", desenhar_ombro(Hardware(), UsoClaude(), mascote_animando("ocioso"), AGORA, 10)
    hx, ux = extremos()
    yield "ombro-extremos", desenhar_ombro(hx, ux, mascote_animando("esperando"), AGORA, 10)
    yield from quadros_minimal()
    yield from quadros_multiagente()
    for paisagem in (False, True):
        nome = "paisagem" if paisagem else "retrato"
        yield f"classico-{nome}", desenhar(h, uso_fixo("trabalhando"), AGORA, paisagem_=paisagem)
        yield f"classico-{nome}-vazio", desenhar(Hardware(), UsoClaude(), AGORA, paisagem_=paisagem)
        yield f"classico-{nome}-extremos", desenhar(hx, ux, AGORA.replace(second=0), paisagem_=paisagem)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--salvar", type=Path, metavar="ARQ.json")
    g.add_argument("--comparar", type=Path, metavar="ARQ.json")
    g.add_argument("--png", type=Path, metavar="PASTA")
    a = p.parse_args()
    hashes = {}
    for nome, img in quadros():
        hashes[nome] = hashlib.sha256(img.tobytes()).hexdigest()
        if a.png:
            a.png.mkdir(parents=True, exist_ok=True)
            img.save(a.png / f"{nome}.png")
    if a.salvar:
        a.salvar.write_text(json.dumps(hashes, indent=1) + "\n")
        print(f"{len(hashes)} quadros salvos em {a.salvar}")
    elif a.comparar:
        antes = json.loads(a.comparar.read_text())
        diferentes = [n for n in hashes if n in antes and antes[n] != hashes[n]]
        faltando = sorted(set(antes) - set(hashes))
        novos = sorted(set(hashes) - set(antes))
        for n in diferentes:
            print(f"diferente: {n}")
        for n in faltando:
            print(f"sumiu: {n}")
        if novos:
            print(f"{len(novos)} quadros novos (sem referência para comparar): {', '.join(novos)}")
        if diferentes or faltando:
            sys.exit(1)
        print(f"{len(hashes) - len(novos)} quadros idênticos")
    else:
        print(f"{len(hashes)} quadros em {a.png}")


if __name__ == "__main__":
    main()
