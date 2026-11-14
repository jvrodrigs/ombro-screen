"""Junta as peças do core para quem roda o Ombro: providers, estado, eventos e banco.

  monitor = Monitor.montar([ClaudeProvider.da_pasta()], banco=pasta_ombro() / "state.db")
  snap = monitor.atualizar("claude")     # coleta, aplica no estado, grava as mudanças; devolve o snapshot efetivo
  monitor.atualizar_todos()             # os eventos dos hooks e depois todos os providers
  monitor.fechar()

O laço do tela.py chama `atualizar_todos` no ritmo da coleta (5 s), não a cada quadro. Os eventos
dos hooks (core/caixa.py) são entregues a cada provider que sabe recebê-los (`receber`) antes da
coleta; um provider com defeito não impede os outros.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

import logging

from logging_config import SoQuandoMuda, logger

from .caixa import CaixaDeEventos
from .events import EventBus, registrar_no_log
from .models import ProviderSnapshot
from .persistence import StateDB
from .providers import ProviderAdapter, ProviderRegistry
from .state import StateStore

log = logger("estado")


class Monitor:
    def __init__(self, registry: ProviderRegistry, store: StateStore, bus: EventBus, banco: Optional[StateDB] = None,
                 relogio: Callable[[], datetime] = datetime.now, caixa: Optional[CaixaDeEventos] = None):
        self.registry, self.store, self.bus, self.banco, self.caixa = registry, store, bus, banco, caixa
        self._relogio = relogio
        self._avisos = SoQuandoMuda(log)
        self._com_eventos: set = set()  # providers dos quais já chegou algum evento (para o log)

    @classmethod
    def montar(cls, providers: Iterable[ProviderAdapter], banco: Optional[Path] = None,
               relogio: Callable[[], datetime] = datetime.now, eventos: Optional[Path] = None) -> "Monitor":
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
                for p in registry:  # quem só sabe das sessões por eventos continua de onde parou
                    if hasattr(p, "restaurar"):
                        p.restaurar(store.sessions(p.provider_id))
                bus.subscribe(db.ao_evento)
        caixa = CaixaDeEventos(eventos, relogio) if eventos is not None else None
        return cls(registry, store, bus, db, relogio, caixa)

    def entregar_eventos(self) -> int:
        """Lê a caixa dos hooks e entrega a cada provider o que é dele. Devolve quantos foram lidos."""
        if self.caixa is None:
            return 0
        eventos = self.caixa.ler()
        if not eventos:
            return 0
        por_provider: dict = {}
        for ev in eventos:
            por_provider.setdefault(ev.provider, []).append(ev)
        for pid, evs in por_provider.items():
            p = self.registry.get(pid) if pid in self.registry else None
            if p is None or not hasattr(p, "receber"):
                continue  # provider desligado ou sem eventos: descarta
            if pid not in self._com_eventos:
                self._com_eventos.add(pid)
                log.info("eventos dos hooks do %s chegando", p.display_name or pid)
            try:
                p.receber(evs)
            except Exception as e:  # noqa: BLE001 - um provider com defeito não pode travar os outros
                if self._avisos.registrar(f"receber:{pid}", logging.ERROR, "%s não conseguiu aplicar eventos (%s: %s)",
                                          pid, type(e).__name__, e):
                    log.debug("detalhes", exc_info=True)
        self.caixa.confirmar(eventos)
        return len(eventos)

    def atualizar_todos(self) -> None:
        self.entregar_eventos()
        for snap in self.registry.collect_all():
            self.store.apply_snapshot(snap)
        if self.banco is not None and self.banco.ativo:
            self.banco.limpar()

    def atualizar(self, provider_id: str) -> Optional[ProviderSnapshot]:
        self.store.apply_snapshot(self.registry.collect(provider_id))
        if self.banco is not None and self.banco.ativo:
            self.banco.limpar()  # no máximo uma vez por hora
        return self.store.snapshot(provider_id)

    def fechar(self) -> None:
        self.registry.close()
        if self.banco is not None:
            self.banco.fechar()
