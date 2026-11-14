"""Base dos providers que só sabem das sessões pelos hooks (Codex e Cursor).

Cada um diz: como saber se a ferramenta está instalada, onde procurar a configuração do hook do
Ombro, e como traduzir os seus eventos. O resto (sessões, tempos, snapshot) é igual para todos.
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from core.caixa import EventoLocal
from core.models import AgentSession, ProviderSnapshot
from core.providers import ProviderAdapter
from core.rastreador import RastreadorDeSessoes, Transicao

MARCA = "ombro_hook.py"  # o que procurar nos arquivos de configuração para saber se o hook está lá
CACHE_DETECCAO_S = 60.0  # instalação e configuração mudam pouco: confere no máximo uma vez por minuto


class ProviderPorHooks(ProviderAdapter):
    executaveis: Sequence[str] = ()  # nomes no PATH que indicam a ferramenta instalada
    apps: Sequence[str] = ()  # caminhos (.app no macOS) que também indicam
    configs: Sequence[str] = ()  # arquivos onde o hook do Ombro fica configurado (com ~)

    def __init__(self, relogio: Callable[[], datetime] = datetime.now, monotonic: Callable[[], float] = time.monotonic):
        self._relogio, self._monotonic = relogio, monotonic
        self.rastreador = RastreadorDeSessoes(self.provider_id, self.mapear)
        self._deteccao: Optional[tuple] = None  # (quando, instalado, configurado)

    # ---------- cada provider define ----------
    def mapear(self, ev: EventoLocal, atual: Optional[AgentSession]) -> Optional[Transicao]:
        raise NotImplementedError

    # ---------- igual para todos ----------
    def receber(self, eventos: Iterable[EventoLocal]) -> int:
        return self.rastreador.aplicar(eventos)

    def restaurar(self, sessoes: Iterable[AgentSession]) -> None:
        self.rastreador.restaurar(sessoes)

    def instalado(self) -> bool:
        return any(shutil.which(e) for e in self.executaveis) or any(Path(a).exists() for a in self.apps)

    def configurado(self) -> bool:
        """O hook do Ombro aparece na configuração global? (Hooks por projeto só se descobrem quando chegam.)"""
        for caminho in self.configs:
            try:
                if MARCA in Path(caminho).expanduser().read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                continue
        return False

    def integracao(self) -> str:
        if self.rastreador.ultimo_evento is not None:
            return "ok"  # eventos chegando: está instalado e configurado, onde quer que seja
        agora = self._monotonic()
        if self._deteccao is None or agora - self._deteccao[0] > CACHE_DETECCAO_S:
            self._deteccao = (agora, self.instalado(), self.configurado())
        _, instalado, configurado = self._deteccao
        if not instalado and not configurado:
            return "not_installed"
        return "ok" if configurado else "not_configured"

    def collect(self) -> ProviderSnapshot:
        agora = self._relogio()
        return ProviderSnapshot(self.provider_id, agora, sessions=self.rastreador.sessoes(agora), integration=self.integracao())
