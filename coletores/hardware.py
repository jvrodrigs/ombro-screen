"""Coleta de hardware do Mac.

- psutil: CPU total, RAM, disco, rede (funciona em qualquer sistema).
- macmon (opcional, recomendado no Apple Silicon): uso por tipo de núcleo, GPU,
  temperaturas e consumo em watts, sem sudo. Instale com `brew install macmon`.
  Sem o macmon, esses campos ficam vazios e a tela mostra "—".
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import psutil

from logging_config import SoQuandoMuda, logger

log = logger("hardware")

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

    ESPERA_REINICIO = 5.0  # segundos antes de rodar o macmon de novo quando ele sai ou falha

    def __init__(self, intervalo_ms: int = 1000):
        super().__init__(daemon=True)
        self.intervalo_ms = intervalo_ms
        self.ultimo: dict | None = None
        self.erro: str | None = None
        self._proc: subprocess.Popen | None = None
        self._parar = threading.Event()
        self._avisos = SoQuandoMuda(log)

    @staticmethod
    def disponivel() -> bool:
        return shutil.which("macmon") is not None

    def run(self) -> None:
        while not self._parar.is_set():
            self._rodar_uma_vez()
            self._parar.wait(self.ESPERA_REINICIO)

    def _rodar_uma_vez(self) -> None:
        """Roda o macmon até ele sair; guarda a última amostra válida."""
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
                    amostra = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if isinstance(amostra, dict):
                    self.ultimo, self.erro = amostra, None
                    self._avisos.resolver("macmon", "macmon voltou")
            if not self._parar.is_set():
                try:
                    codigo = self._proc.wait(timeout=2)  # a saída fechou: o processo está terminando
                except subprocess.TimeoutExpired:
                    codigo = None
                self._avisos.registrar("macmon", logging.WARNING, "macmon parou (código %s); tentando de novo a cada %.0f s",
                                       codigo, self.ESPERA_REINICIO)
        except Exception as e:  # noqa: BLE001 - a thread não pode morrer: o macmon é opcional
            self.erro = str(e) or type(e).__name__
            if self._avisos.registrar("macmon", logging.WARNING, "macmon falhou (%s); tentando de novo a cada %.0f s",
                                      self.erro, self.ESPERA_REINICIO):
                log.debug("detalhes da falha do macmon", exc_info=True)
        # O macmon saiu: a última amostra vai envelhecer, então a tela volta ao psutil até ele voltar.
        self.ultimo = None

    def parar(self) -> None:
        self._parar.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


def _float(v) -> float | None:
    return None if v is None else float(v)


def campos_macmon(m: dict, cpu_psutil: float, ram: tuple[int, int, int]) -> dict:
    """Campos do Hardware a partir de uma amostra do `macmon pipe`.

    Levanta TypeError, ValueError ou AttributeError se a amostra vier estranha (um campo nulo
    ou de outro tipo); quem chama fica então com os números do psutil.
    """
    nucleos = [(str(t.get("label", "?")), float(t.get("scaled_ratio", 0)) * 100) for t in m.get("cpu_tiers") or []]
    if not nucleos:  # macmon antigo, sem cpu_tiers
        nucleos = [("E", float(m.get("ecpu_scaled_ratio", 0)) * 100), ("P", float(m.get("pcpu_scaled_ratio", 0)) * 100)]
    temp = m.get("temp") or {}
    mem = m.get("memory") or {}
    ram_total, ram_usada, swap_usada = ram
    if mem.get("ram_total"):
        ram_total = int(mem["ram_total"])
        ram_usada = int(mem.get("ram_usage", ram_usada))
        swap_usada = int(mem.get("swap_usage", swap_usada))
    return dict(
        macmon=True,
        cpu_pct=float(m.get("cpu_scaled_ratio", cpu_psutil / 100)) * 100,
        nucleos=nucleos,
        gpu_pct=float(m.get("gpu_scaled_ratio", 0)) * 100,
        temp_cpu=_float(temp.get("cpu_temp_avg")),
        temp_gpu=_float(temp.get("gpu_temp_avg")),
        watts_soc=_float(m.get("all_power")),
        watts_sistema=_float(m.get("sys_power")) or None,
        ram_total=ram_total,
        ram_usada=ram_usada,
        swap_usada=swap_usada,
    )


class ColetorHardware:
    def __init__(self, usar_macmon: bool = True, disco: str = "/"):
        self.disco = disco
        self.macmon: LeitorMacmon | None = None
        self._avisos = SoQuandoMuda(log)
        if usar_macmon and LeitorMacmon.disponivel():
            self.macmon = LeitorMacmon()
            self.macmon.start()
            log.info("macmon encontrado: uso por núcleo, GPU, temperatura e consumo vêm dele")
        elif usar_macmon:
            log.info("macmon não instalado: CPU e RAM vêm do psutil (GPU e temperatura ficam em branco)")
        else:
            log.debug("macmon desligado (--sem-macmon): só psutil")
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
            try:
                campos = campos_macmon(m, h.cpu_pct, (h.ram_total, h.ram_usada, h.swap_usada))
            except (TypeError, ValueError, AttributeError) as e:
                campos = {}  # amostra estranha: fica com o psutil nesta leitura
                self._avisos.registrar("amostra", logging.WARNING, "amostra do macmon ignorada (%s: %s); usando o psutil",
                                       type(e).__name__, e)
            else:
                self._avisos.resolver("amostra", "amostras do macmon voltaram ao normal")
            for nome, valor in campos.items():
                setattr(h, nome, valor)

        self._hist_cpu.append(h.cpu_pct)
        if h.gpu_pct is not None:
            self._hist_gpu.append(h.gpu_pct)
        h.hist_cpu = list(self._hist_cpu)
        h.hist_gpu = list(self._hist_gpu)
        return h

    def parar(self) -> None:
        if self.macmon:
            self.macmon.parar()
