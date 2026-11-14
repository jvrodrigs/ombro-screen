#!/usr/bin/env python3
"""Mede o custo de cada parte do Ombro sem precisar da tela USB.

  python3 scripts/benchmark.py                 todas as medições, com dados sintéticos
  python3 scripts/benchmark.py --segundos 20   laço completo por mais tempo
  python3 scripts/benchmark.py --claude-dir ~/.claude   coleta com os seus arquivos de verdade

As entradas são fixas (hardware, uso, horário e cor do cloud), então dá para comparar duas
versões do código na mesma máquina. Os números variam com a máquina e com o que mais estiver
rodando: compare execuções feitas lado a lado, não com os números de outra pessoa.

O envio para a tela é simulado: a "tela" falsa converte cada pedaço para RGB565 (o trabalho
que o Mac faz de verdade) e só conta os bytes. O tempo do USB em si não entra aqui; ele
depende do cabo, do hub e da revisão da tela.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from PIL import Image  # noqa: E402

import saida  # noqa: E402
import usb_direto  # noqa: E402
from coletores.claude import ColetorClaude, Janela, UsoClaude  # noqa: E402
from coletores.hardware import ColetorHardware, Hardware  # noqa: E402
from layout_ombro import desenhar_ombro  # noqa: E402
from mascote import ESTADOS, Mascote  # noqa: E402
from render import desenhar  # noqa: E402

AGORA = datetime(2026, 11, 13, 15, 30)
GB = 1024**3


def hardware_fixo() -> Hardware:
    return Hardware(cpu_pct=37.0, nucleos=[("E", 52.0), ("P", 26.0)], gpu_pct=14.0, ram_usada=int(14.2 * GB),
                    ram_total=24 * GB, swap_usada=int(.4 * GB), disco_pct=63, disco_livre=342 * GB,
                    rede_down=2.1 * 1024**2, rede_up=180 * 1024, temp_cpu=51.0, temp_gpu=44.0, watts_soc=8.2,
                    watts_sistema=15.0, hist_cpu=[30 + (i % 7) * 3 for i in range(60)],
                    hist_gpu=[10 + (i % 5) * 2 for i in range(60)], macmon=True)


def uso_fixo(estado: str = "esperando") -> UsoClaude:
    status = {"ocioso": ["idle"], "trabalhando": ["busy", "idle"], "esperando": ["busy", "waiting", "idle"],
              "alerta": ["busy"], "reset": ["idle"]}[estado]
    pct5 = 93 if estado == "alerta" else 42
    curva = [0, 0, 0, 0, 0, 0, 0, .1, .6, 1.4, 2.2, 1.8, .9, 1.2, 2.6, 3.1, 2.4, 1.6, .8, 1.1, 1.9, 1.3, .4, .2]
    return UsoClaude(
        cinco_horas=Janela(pct5, AGORA + timedelta(hours=2, minutes=14)), semana=Janela(67, AGORA + timedelta(days=2)),
        fonte="statusline", coletado_em=AGORA - timedelta(minutes=1), tokens_entrada=18_400_000, tokens_saida=212_000,
        tokens_cache=17_900_000, mensagens_hoje=486, sessoes=len(status), trabalhando=status.count("busy"),
        esperando=status.count("waiting"), status_sessoes=status,
        espera_projeto="projeto" if "waiting" in status else "", espera_desde=AGORA - timedelta(minutes=12),
        tokens_por_hora=[int(v * 1e6) if h <= AGORA.hour else None for h, v in enumerate(curva)], configurado=True)


def cronometrar(fn, n: int) -> dict:
    tempos = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        tempos.append((time.perf_counter() - t0) * 1000)
    tempos.sort()
    return {"n": n, "media_ms": round(statistics.mean(tempos), 2), "mediana_ms": round(statistics.median(tempos), 2),
            "p95_ms": round(tempos[int(len(tempos) * .95) - 1], 2)}


def mascote_animando(estado: str = "trabalhando") -> Mascote:
    m = Mascote(cor_fixa=(120, 200, 90))
    m.estado, m.inicio, m.anterior, m.cor = estado, 0.0, None, (120, 200, 90)
    return m


# ---------- renderização ----------
def medir_render(n: int) -> dict:
    h, u = hardware_fixo(), uso_fixo()
    m = mascote_animando("esperando")
    passo = iter(i * .2 for i in range(10**9))
    res = {"ombro": cronometrar(lambda: desenhar_ombro(h, u, m, AGORA, next(passo)), n)}
    res["cloud_sozinho"] = cronometrar(lambda: m.camada(next(passo)), n)
    res["classico_retrato"] = cronometrar(lambda: desenhar(h, u, AGORA, paisagem_=False), max(10, n // 4))
    res["classico_paisagem"] = cronometrar(lambda: desenhar(h, u, AGORA, paisagem_=True), max(10, n // 4))
    grande = Image.new("RGB", (960, 640), (18, 17, 16))
    res["reducao_2x_lanczos"] = cronometrar(lambda: grande.resize((480, 320), Image.LANCZOS), n)
    return res


# ---------- coleta ----------
def _claude_sintetico(pasta: Path, arquivos: int = 12, linhas: int = 400) -> None:
    proj = pasta / "projects"
    hoje = datetime.now().astimezone()
    for a in range(arquivos):
        d = proj / f"p{a % 4}"
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"s{a}.jsonl").open("w", encoding="utf-8") as f:
            for i in range(linhas):
                if i % 3:
                    f.write(json.dumps({"type": "user", "message": {"content": "x" * 200}}) + "\n")
                    continue
                f.write(json.dumps({"type": "assistant", "timestamp": hoje.isoformat(), "message": {
                    "id": f"m{a}-{i}", "usage": {"input_tokens": 10, "output_tokens": 50, "cache_read_input_tokens": 900}}}) + "\n")
    (pasta / "sessions").mkdir()


def medir_coleta(n: int, claude_dir: str | None) -> dict:
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if claude_dir is None:
            _claude_sintetico(tmp / "claude")
            claude_dir = str(tmp / "claude")
            res["claude_dados"] = "sintéticos (12 transcripts, 400 linhas cada)"
        else:
            res["claude_dados"] = "pasta informada em --claude-dir"
        (tmp / "tela").mkdir()
        with mock.patch("coletores.claude.PASTA_TELA", tmp / "tela"):
            c = ColetorClaude(claude_dir)
            t0 = time.perf_counter()
            c.ler()
            res["claude_primeira_leitura_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            res["claude_leituras_seguintes"] = cronometrar(c.ler, n)
    hw = ColetorHardware(usar_macmon=False)
    res["hardware_psutil"] = cronometrar(hw.ler, n)
    hw.parar()
    return res


# ---------- preparação do envio ----------
class LcdQueConta:
    """Faz o mesmo trabalho de CPU do envio direto (recorte + RGB565), sem USB."""

    bytes_enviados = 0
    envios = 0

    def __init__(self, com_port="AUTO", display_width=320, display_height=480):
        self.lcd_serial = object()

    @classmethod
    def auto_detect_com_port(cls):
        return True

    def Reset(self):
        pass

    def InitializeComm(self):
        pass

    def SetBrightness(self, level=25):
        pass

    def SetOrientation(self, orientation=None):
        pass

    def DisplayPILImage(self, image, x=0, y=0):
        LcdQueConta.bytes_enviados += len(usb_direto.rgb565_le(image))
        LcdQueConta.envios += 1

    def closeSerial(self):
        pass


def _tela_falsa():
    with mock.patch.object(usb_direto, "LcdRevAUsb", LcdQueConta):
        return saida.TelaUSB("A", "AUTO", 30, Path("/nao/existe"), paisagem=True, transporte="usb")


def medir_envio(n: int) -> dict:
    h = hardware_fixo()
    res = {}
    quadros = {}
    for estado in ESTADOS:
        m = mascote_animando(estado)
        u = uso_fixo(estado)
        quadros[estado] = [desenhar_ombro(h, u, m, AGORA, 10 + i * .2) for i in range(n + 1)]
    por_estado = {}
    for estado, qs in quadros.items():
        t = _tela_falsa()
        t.mostrar(qs[0])  # quadro inteiro
        LcdQueConta.bytes_enviados = LcdQueConta.envios = 0
        tempos = []
        for q in qs[1:]:
            t0 = time.perf_counter()
            t.mostrar(q)
            tempos.append((time.perf_counter() - t0) * 1000)
        por_estado[estado] = {"kib_por_quadro": round(LcdQueConta.bytes_enviados / len(qs[1:]) / 1024, 1),
                              "envios_por_quadro": round(LcdQueConta.envios / len(qs[1:]), 1),
                              "diff_mais_rgb565_ms": round(statistics.mean(tempos), 2)}
    res["animacao_por_estado"] = por_estado
    q = quadros["ocioso"][0]
    res["quadro_inteiro_kib"] = round(len(usb_direto.rgb565_le(q)) / 1024, 1)
    res["rgb565_quadro_inteiro"] = cronometrar(lambda: usb_direto.rgb565_le(q), n)
    return res


# ---------- laço completo ----------
def medir_laco(segundos: float, intervalo: float = .2) -> dict:
    """O mesmo trabalho do laço do tela.py (coleta, cloud, desenho, envio), com a tela falsa."""
    hw = ColetorHardware(usar_macmon=False)
    u = uso_fixo("trabalhando")
    m = mascote_animando("ocioso")
    t = _tela_falsa()
    LcdQueConta.bytes_enviados = 0
    quadros = 0
    cpu0, inicio = time.process_time(), time.monotonic()
    while time.monotonic() - inicio < segundos:
        t0 = time.monotonic()
        m.ir_para(m.decidir(u, t0), t0)
        t.mostrar(desenhar_ombro(hw.ler(), u, m, datetime.now(), t0))
        quadros += 1
        time.sleep(max(.05, intervalo - (time.monotonic() - t0)))
    duracao = time.monotonic() - inicio
    hw.parar()
    return {"segundos": round(duracao, 1), "quadros_por_segundo": round(quadros / duracao, 2),
            "cpu_pct_de_um_nucleo": round(100 * (time.process_time() - cpu0) / duracao, 1),
            "kib_por_segundo_para_a_tela": round(LcdQueConta.bytes_enviados / duracao / 1024, 1)}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("-n", type=int, default=60, help="repetições de cada medição (padrão 60)")
    p.add_argument("--segundos", type=float, default=10, help="duração do laço completo (padrão 10; 0 pula)")
    p.add_argument("--claude-dir", default=None, help="medir a coleta com esta pasta do Claude Code")
    p.add_argument("--json", action="store_true", help="saída em JSON")
    a = p.parse_args()
    res = {"python": sys.version.split()[0], "plataforma": sys.platform}
    res["render"] = medir_render(a.n)
    res["coleta"] = medir_coleta(a.n, a.claude_dir)
    res["envio"] = medir_envio(min(a.n, 30))
    if a.segundos > 0:
        res["laco"] = medir_laco(a.segundos)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    for secao, valores in res.items():
        if not isinstance(valores, dict):
            print(f"{secao}: {valores}")
            continue
        print(f"\n[{secao}]")
        for k, v in valores.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
