"""Dados inventados, para ver a interface sem macmon nem Claude Code (`--demo`)."""
from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta

from .claude import Janela, UsoClaude
from .hardware import Hardware

GB = 1024**3


class Demo:
    def __init__(self):
        self.t0 = time.time()
        self.hist_cpu: list[float] = []
        self.hist_gpu: list[float] = []

    def hardware(self) -> Hardware:
        t = time.time() - self.t0
        cpu = 28 + 22 * math.sin(t / 7) + random.uniform(-6, 6)
        gpu = 12 + 10 * math.sin(t / 11 + 1) + random.uniform(-3, 3)
        cpu, gpu = max(2, min(99, cpu)), max(0, min(99, gpu))
        self.hist_cpu = (self.hist_cpu + [cpu])[-60:]
        self.hist_gpu = (self.hist_gpu + [gpu])[-60:]
        return Hardware(
            cpu_pct=cpu,
            nucleos=[("E", min(99, cpu * 1.4)), ("S", cpu * 0.7)],
            gpu_pct=gpu,
            ram_usada=int(14.2 * GB),
            ram_total=24 * GB,
            swap_usada=int(0.4 * GB),
            disco_pct=63,
            disco_livre=int(342 * GB),
            rede_down=random.uniform(0.2, 4.5) * 1024**2,
            rede_up=random.uniform(20, 400) * 1024,
            temp_cpu=46 + cpu / 6,
            temp_gpu=41 + gpu / 5,
            watts_soc=3 + cpu / 6 + gpu / 8,
            watts_sistema=9 + cpu / 5,
            hist_cpu=self.hist_cpu,
            hist_gpu=self.hist_gpu,
            macmon=True,
        )

    # milhões de tokens por hora do dia (0h..23h); só as horas até agora entram no quadro
    _CURVA = [0, 0, 0, 0, 0, 0, 0, .1, .6, 1.4, 2.2, 1.8, .9, 1.2, 2.6, 3.1, 2.4, 1.6, .8, 1.1, 1.9, 1.3, .4, .2]

    # cenários do ciclo de demonstração: (nome, % de 5h, reseta em, status das sessões)
    _CENARIOS = [
        ("ocioso", 42, timedelta(hours=2, minutes=14), ["idle"]),
        ("trabalhando", 42, timedelta(hours=2, minutes=14), ["busy", "idle", "busy"]),
        ("esperando", 42, timedelta(hours=2, minutes=14), ["busy", "waiting", "idle"]),
        ("alerta", 93, timedelta(minutes=30), ["busy", "idle"]),
        ("reset", 4, timedelta(hours=5), ["idle"]),  # o horário de reset avança: o cloud comemora
    ]

    def claude(self, ciclo: float = 0) -> UsoClaude:
        """`ciclo` > 0: troca de cenário a cada tantos segundos (para ver todos os estados do cloud)."""
        agora = datetime.now()
        nome, pct5, reset5, status = (
            self._CENARIOS[int((time.time() - self.t0) // ciclo) % len(self._CENARIOS)] if ciclo > 0 else self._CENARIOS[2]
        )
        por_hora = [int(v * 1e6) if h <= agora.hour else None for h, v in enumerate(self._CURVA)]
        return UsoClaude(
            cinco_horas=Janela(pct5, agora + reset5),
            semana=Janela(67, agora + timedelta(days=2, hours=5)),
            fonte="statusline",
            coletado_em=agora - timedelta(minutes=1),
            tokens_entrada=18_400_000,
            tokens_saida=212_000,
            tokens_cache=17_900_000,
            mensagens_hoje=486,
            sessoes=len(status),
            trabalhando=status.count("busy"),
            esperando=status.count("waiting"),
            status_sessoes=status,
            espera_projeto="ombro-screen" if "waiting" in status else "",
            espera_desde=agora - timedelta(minutes=12) if "waiting" in status else None,
            tokens_por_hora=por_hora,
            configurado=True,
        )
