"""O contrato de um provider (uma ferramenta de IA monitorada) e o registro dos providers ativos.

Um provider só precisa saber coletar: `collect()` devolve um ProviderSnapshot com o que ele sabe
agora. O que ele consegue informar fica em `capabilities`, para quem consome não supor que todo
agente tem sessões, quotas ou tokens.

Hoje só o Claude Code está registrado (providers/claude.py). Providers não dependem uns dos
outros, nem da tela, do USB ou do Pillow.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Iterator, List

from logging_config import SoQuandoMuda, logger, sem_casa

from .models import ProviderSnapshot, SnapshotStatus

log = logger("providers")
_ID = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class EventosNaoGravados(Exception):
    """Um provider não conseguiu guardar o que precisa lembrar: os eventos dele ficam na caixa para outra vez."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """O que um provider consegue informar. Falso = não informa (e não é zero nem vazio)."""
    sessions: bool = False  # lista as sessões abertas, com status de atividade
    attention: bool = False  # sabe quando uma sessão espera você
    token_usage: bool = False  # tokens usados (ver UsageMetrics.scope)
    quota_windows: bool = False  # janelas de limite de uso
    context_usage: bool = False  # uso da janela de contexto
    costs: bool = False  # custo em dinheiro
    events: bool = False  # avisa mudanças na hora (hooks); sem isso, só por coleta


class ProviderAdapter(ABC):
    """Base de um provider. Subclasses definem `provider_id`, `display_name`, `capabilities` e `collect`."""

    provider_id: str = ""  # estável e em minúsculas: "claude"; vira parte da chave das sessões e do banco
    display_name: str = ""
    capabilities: ProviderCapabilities = ProviderCapabilities()

    @abstractmethod
    def collect(self) -> ProviderSnapshot:
        """O que o provider sabe agora. Pode levantar exceção: o registro transforma em snapshot ERROR."""

    def close(self) -> None:
        """Solta recursos (threads, processos). A maioria não precisa."""


class ProviderRegistry:
    """Providers ativos, na ordem em que foram registrados. Sem descoberta dinâmica nem plugins."""

    def __init__(self, relogio: Callable[[], datetime] = datetime.now):
        self._providers: Dict[str, ProviderAdapter] = {}
        self._relogio = relogio
        self._avisos = SoQuandoMuda(log)

    def register(self, provider: ProviderAdapter) -> None:
        if not isinstance(provider, ProviderAdapter):
            raise TypeError(f"{type(provider).__name__} não é um ProviderAdapter")
        pid = provider.provider_id
        if not isinstance(pid, str) or not _ID.fullmatch(pid):
            raise ValueError(f"provider_id inválido: {pid!r} (minúsculas, números, - e _; começa com letra)")
        if not isinstance(provider.capabilities, ProviderCapabilities):
            raise TypeError(f"{pid}: capabilities deve ser um ProviderCapabilities")
        if pid in self._providers:
            raise ValueError(f"provider já registrado: {pid}")
        self._providers[pid] = provider
        log.debug("provider registrado: %s", pid)

    def get(self, provider_id: str) -> ProviderAdapter:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise KeyError(f"provider desconhecido: {provider_id!r} (registrados: {', '.join(self._providers) or 'nenhum'})") from None

    def __contains__(self, provider_id: str) -> bool:
        return provider_id in self._providers

    def __iter__(self) -> Iterator[ProviderAdapter]:
        return iter(list(self._providers.values()))

    def ids(self) -> List[str]:
        return list(self._providers)

    def collect(self, provider_id: str) -> ProviderSnapshot:
        """Coleta de um provider. Uma falha vira snapshot ERROR (registrada uma vez), não exceção:
        um provider com problema não pode derrubar o monitoramento dos outros."""
        provider = self.get(provider_id)
        try:
            snap = provider.collect()
            if not isinstance(snap, ProviderSnapshot) or snap.provider_id != provider_id:
                raise TypeError(f"collect() de {provider_id} devolveu {type(snap).__name__} de outro provider ou tipo")
        except Exception as e:  # noqa: BLE001 - isola o provider; o traceback vai para o log de depuração
            if self._avisos.registrar(provider_id, logging.ERROR, "a coleta de %s falhou (%s: %s)",
                                      provider_id, type(e).__name__, sem_casa(e)):
                log.debug("detalhes da falha de %s", provider_id, exc_info=True)
            return ProviderSnapshot(provider_id, self._relogio(), status=SnapshotStatus.ERROR,
                                    errors=(f"{type(e).__name__}: {sem_casa(e)}",))
        self._avisos.resolver(provider_id, "a coleta de %s voltou ao normal", provider_id)
        return snap

    def collect_all(self) -> List[ProviderSnapshot]:
        return [self.collect(pid) for pid in self._providers]

    def close(self) -> None:
        for provider in self:
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - fechar é arrumação; não impede os outros de fechar
                log.debug("erro ao fechar %s (ignorado)", provider.provider_id, exc_info=True)
