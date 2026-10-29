"""Coleta de hardware do Mac.

- psutil: CPU total, RAM, disco, rede (funciona em qualquer sistema).
- macmon (opcional, recomendado no Apple Silicon): uso por tipo de núcleo, GPU,
  temperaturas e consumo em watts, sem sudo. Instale com `brew install macmon`.
  Sem o macmon, esses campos ficam vazios e a tela mostra "—".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import psutil

HISTORICO = 60  # amostras guardadas para os gráficos (1 por segundo)


@dataclass
class Hardware:
    cpu_pct: float = 0.0
    nucleos: list[tuple[str, float]] = field(default_factory=list)  # [("E", 23.0), ("S", 61.0)]
    gpu_pct: float | None = None
    ram_usada: int = 0
    ram_total: int = 0
    swap_usada: int = 0
    disco_pct: float = 0.0
    disco_livre: int = 0
    rede_down: float = 0.0  # bytes/s
    rede_up: float = 0.0
    temp_cpu: float | None = None
    temp_gpu: float | None = None
    watts_soc: float | None = None  # CPU + GPU + ANE
    watts_sistema: float | None = None
    hist_cpu: list[float] = field(default_factory=list)
    hist_gpu: list[float] = field(default_factory=list)
    macmon: bool = False


class LeitorMacmon(threading.Thread):
    """Roda `macmon pipe` em segundo plano e guarda a última amostra."""

    def __init__(self, intervalo_ms: int = 1000):
        super().__init__(daemon=True)
        self.intervalo_ms = intervalo_ms
        self.ultimo: dict | None = None
        self.erro: str | None = None
        self._proc: subprocess.Popen | None = None

    @staticmethod
    def disponivel() -> bool:
        return shutil.which("macmon") is not None

    def run(self) -> None:
        while True:
            try:
                self._proc = subprocess.Popen(
                    ["macmon", "pipe", "-i", str(self.intervalo_ms)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                assert self._proc.stdout
                for linha in self._proc.stdout:
                    try:
                        self.ultimo = json.loads(linha)
                    except json.JSONDecodeError:
                        continue
            except Exception as e:  # macmon sumiu, falhou etc.
                self.erro = str(e)
            time.sleep(5)  # tenta de novo

    def parar(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


class ColetorHardware:
    def __init__(self, usar_macmon: bool = True, disco: str = "/"):
        self.disco = disco
        self.macmon: LeitorMacmon | None = None
        if usar_macmon and LeitorMacmon.disponivel():
            self.macmon = LeitorMacmon()
            self.macmon.start()
        self._rede_ant = psutil.net_io_counters()
        self._t_ant = time.monotonic()
        self._hist_cpu: deque[float] = deque(maxlen=HISTORICO)
        self._hist_gpu: deque[float] = deque(maxlen=HISTORICO)
        psutil.cpu_percent(None)  # a primeira leitura sempre volta 0

    def ler(self) -> Hardware:
        h = Hardware()
        h.cpu_pct = psutil.cpu_percent(None)

        vm = psutil.virtual_memory()
        h.ram_total = vm.total
        h.ram_usada = vm.total - vm.available
        h.swap_usada = psutil.swap_memory().used

        du = psutil.disk_usage(self.disco)
        h.disco_pct = du.percent
        h.disco_livre = du.free

        agora = time.monotonic()
        rede = psutil.net_io_counters()
        dt = max(agora - self._t_ant, 1e-3)
        h.rede_down = max(0, rede.bytes_recv - self._rede_ant.bytes_recv) / dt
        h.rede_up = max(0, rede.bytes_sent - self._rede_ant.bytes_sent) / dt
        self._rede_ant, self._t_ant = rede, agora

        m = self.macmon.ultimo if self.macmon else None
        if m:
            h.macmon = True
            h.cpu_pct = float(m.get("cpu_scaled_ratio", h.cpu_pct / 100)) * 100
            for tier in m.get("cpu_tiers") or []:
                h.nucleos.append((str(tier.get("label", "?")), float(tier.get("scaled_ratio", 0)) * 100))
            if not h.nucleos:  # macmon antigo, sem cpu_tiers
                h.nucleos = [
                    ("E", float(m.get("ecpu_scaled_ratio", 0)) * 100),
                    ("P", float(m.get("pcpu_scaled_ratio", 0)) * 100),
                ]
            h.gpu_pct = float(m.get("gpu_scaled_ratio", 0)) * 100
            temp = m.get("temp") or {}
            h.temp_cpu = temp.get("cpu_temp_avg")
            h.temp_gpu = temp.get("gpu_temp_avg")
            h.watts_soc = m.get("all_power")
            h.watts_sistema = m.get("sys_power") or None
            mem = m.get("memory") or {}
            if mem.get("ram_total"):
                h.ram_total = int(mem["ram_total"])
                h.ram_usada = int(mem.get("ram_usage", h.ram_usada))
                h.swap_usada = int(mem.get("swap_usage", h.swap_usada))

        self._hist_cpu.append(h.cpu_pct)
        if h.gpu_pct is not None:
            self._hist_gpu.append(h.gpu_pct)
        h.hist_cpu = list(self._hist_cpu)
        h.hist_gpu = list(self._hist_gpu)
        return h

    def parar(self) -> None:
        if self.macmon:
            self.macmon.parar()
