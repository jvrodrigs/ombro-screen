#!/usr/bin/env python3
"""Mede o custo do Ombro Hub: subir o ombrod, latência da IPC, várias conexões, a CLI, e CPU/memória do
ombrod comparados aos do tela.py rodando o mesmo laço.

  python3 scripts/benchmark_hub.py                 20 s de medição de CPU para cada um
  python3 scripts/benchmark_hub.py --segundos 60

Roda com --demo, numa pasta temporária (OMBRO_SCREEN_DIR) e com o simulador numa porta livre: não toca
na sua configuração, no seu banco, nos seus agentes nem na tela USB. Compare execuções na mesma máquina.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import psutil  # noqa: E402

from ombro import ipc  # noqa: E402


def porta_livre() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def percentis(amostras_ms):
    a = sorted(amostras_ms)
    return f"p50 {statistics.median(a):.2f} ms, p95 {a[int(len(a) * 0.95) - 1]:.2f} ms, máx {a[-1]:.2f} ms"


def medir_processo(args, env, segundos):
    """CPU média (% de um núcleo) e memória (RSS) depois de 3 s de aquecimento."""
    p = subprocess.Popen([sys.executable, *args], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        proc = psutil.Process(p.pid)
        proc.cpu_percent(None)
        rss = []
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            time.sleep(1)
            rss.append(proc.memory_info().rss)
        cpu = proc.cpu_percent(None)
        return cpu, max(rss) / 1024 / 1024
    finally:
        p.send_signal(signal.SIGTERM)
        p.wait(10)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--segundos", type=float, default=20.0)
    a = ap.parse_args()
    pasta = tempfile.mkdtemp(prefix="ombench", dir="/tmp" if os.path.isdir("/tmp") else None)
    env = dict(os.environ, OMBRO_SCREEN_DIR=pasta, PYTHONDONTWRITEBYTECODE="1")
    os.environ["OMBRO_SCREEN_DIR"] = pasta
    sock = Path(pasta) / "run" / "ombro.sock"
    p = None
    try:
        # 1. subir o ombrod até a primeira resposta
        t0 = time.monotonic()
        p = subprocess.Popen([sys.executable, str(RAIZ / "ombrod.py"), "--demo", "--porta-sim", str(porta_livre())],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while True:
            try:
                ipc.chamar("ping", caminho=sock)
                break
            except ipc.DaemonIndisponivel:
                time.sleep(0.01)
        print(f"ombrod: primeira resposta da IPC em {(time.monotonic() - t0) * 1000:.0f} ms")
        time.sleep(1.5)

        # 2. latência de uma chamada (conexão reaproveitada e conexão nova)
        with ipc.ClienteIPC(sock) as c:
            for metodo in ("ping", "status", "sessions.list"):
                amostras = []
                for _ in range(300):
                    t = time.perf_counter()
                    c.chamar(metodo)
                    amostras.append((time.perf_counter() - t) * 1000)
                print(f"IPC {metodo:<14} (mesma conexão): {percentis(amostras)}")
        amostras = []
        for _ in range(200):
            t = time.perf_counter()
            ipc.chamar("status", caminho=sock)
            amostras.append((time.perf_counter() - t) * 1000)
        print(f"IPC status         (conexão nova):  {percentis(amostras)}")
        amostras = []
        for _ in range(20):
            t = time.perf_counter()
            ipc.chamar("display.preview", caminho=sock)
            amostras.append((time.perf_counter() - t) * 1000)
        print(f"IPC display.preview (PNG 480x320):  {percentis(amostras)}")

        # 3. várias conexões ao mesmo tempo
        tempos, trava = [], threading.Lock()

        def cliente():
            with ipc.ClienteIPC(sock) as c:
                for _ in range(100):
                    t = time.perf_counter()
                    c.chamar("status")
                    with trava:
                        tempos.append((time.perf_counter() - t) * 1000)

        ts = [threading.Thread(target=cliente) for _ in range(8)]
        t = time.perf_counter()
        for x in ts:
            x.start()
        for x in ts:
            x.join()
        print(f"IPC 8 clientes x 100 status:        {percentis(tempos)} ({len(tempos) / (time.perf_counter() - t):.0f} pedidos/s)")

        # 4. a CLI, de ponta a ponta (inclui subir o Python)
        amostras = []
        for _ in range(5):
            t = time.perf_counter()
            subprocess.run([sys.executable, "-m", "ombro", "status"], cwd=RAIZ, env=env, capture_output=True, check=True)
            amostras.append((time.perf_counter() - t) * 1000)
        print(f"CLI python3 -m ombro status:        {percentis(amostras)}")
        p.send_signal(signal.SIGTERM)
        t = time.monotonic()
        p.wait(10)
        print(f"ombrod: encerramento com SIGTERM em {(time.monotonic() - t) * 1000:.0f} ms")

        # 5. CPU e memória: o ombrod e o tela.py com o mesmo laço (--demo, layout padrão, simulador)
        for nome, script in (("tela.py", "tela.py"), ("ombrod", "ombrod.py")):
            extra = ["--nao-abrir"] if script == "tela.py" else []
            cpu, rss = medir_processo([str(RAIZ / script), "--demo", "--porta-sim", str(porta_livre()), *extra], env, a.segundos)
            print(f"{nome:<8} em {a.segundos:.0f} s: CPU {cpu:.1f} % de um núcleo, memória (RSS) {rss:.0f} MiB")
    finally:
        # um erro ou Ctrl+C no meio não pode deixar o ombrod de teste vivo (e sem a pasta dele)
        if p is not None and p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(pasta, ignore_errors=True)


if __name__ == "__main__":
    main()
