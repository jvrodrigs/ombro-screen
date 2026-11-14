"""Junta as peças do core para quem roda o Ombro: providers, estado, eventos e banco.

  monitor = Monitor.montar([ClaudeProvider.da_pasta()], banco=pasta_ombro() / "state.db")
  snap = monitor.atualizar("claude")     # coleta, aplica no estado, grava as mudanças; devolve o snapshot efetivo
  monitor.fechar()

O laço do tela.py chama `atualizar` no ritmo da coleta (5 s), não a cada quadro.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from logging_config import logger

from .events import EventBus, registrar_no_log
from .models import ProviderSnapshot
from .persistence import StateDB
from .providers import ProviderAdapter, ProviderRegistry
from .state import StateStore

log = logger("estado")


class Monitor:
    def __init__(self, registry: ProviderRegistry, store: StateStore, bus: EventBus, banco: Optional[StateDB] = None,
                 relogio: Callable[[], datetime] = datetime.now):
        self.registry, self.store, self.bus, self.banco = registry, store, bus, banco
        self._relogio = relogio

    @classmethod
    def montar(cls, providers: Iterable[ProviderAdapter], banco: Optional[Path] = None,
               relogio: Callable[[], datetime] = datetime.now) -> "Monitor":
        """Registra os providers e, com `banco`, recupera o estado salvo e passa a gravar as mudanças.

        Sem `banco` (no --demo e no --png), tudo fica em memória."""
        registry, bus = ProviderRegistry(relogio), EventBus()
        for p in providers:
            registry.register(p)
        store = StateStore(bus)
        bus.subscribe(registrar_no_log)
        db = None
        if banco is not None:
            db = StateDB(banco, relogio)
            if db.abrir():
                db.limpar(forcar=True)
                n = store.restore(s for s in db.sessoes_abertas() if s.provider_id in registry)
                if n:
                    log.info("%d sessão(ões) recuperada(s) do banco, com estado desconhecido até a próxima coleta", n)
                bus.subscribe(db.ao_evento)
        return cls(registry, store, bus, db, relogio)

    def atualizar(self, provider_id: str) -> Optional[ProviderSnapshot]:
        self.store.apply_snapshot(self.registry.collect(provider_id))
        if self.banco is not None and self.banco.ativo:
            self.banco.limpar()  # no máximo uma vez por hora
        return self.store.snapshot(provider_id)

    def fechar(self) -> None:
        self.registry.close()
        if self.banco is not None:
            self.banco.fechar()
