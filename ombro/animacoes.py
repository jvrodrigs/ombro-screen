"""O pacote de animações do mascote: o que você escolheu, o que está em uso, e quando um vira o outro.

Três coisas que não se confundem:

  desejado   a preferência `pacote_animacao` (config.json): o que você escolheu por último
  aplicado   o que o app e a telinha estão mostrando agora; gravado à parte (animacao.json, na pasta do Ombro), para
             um reinício não trocar o pacote no meio de uma atividade que continua
  pendente   desejado != aplicado: a troca espera um momento seguro (só a última escolha conta, nada enfileira)

A troca nunca corta uma atividade que o mascote está representando. Ela só acontece quando TUDO isto vale:

  1. o Ombro já leu os agentes depois de iniciar (antes disso não dá para saber se há atividade);
  2. nenhuma sessão de nenhum agente ligado está em andamento: trabalhando, esperando você (resposta ou aprovação)
     ou parada com erro. Vale para todos os agentes, seja qual for o layout ou o cartão em foco;
  3. está tudo quieto há pelo menos FIM_DA_CONCLUSAO s (a comemoração de quem acabou de terminar já passou);
  4. o mascote da telinha está num ponto seguro (mascote.Mascote.momento_seguro: ocioso ou alerta assentados, ou o
     concluído depois da comemoração; nunca trabalhando, esperando ou reset).

Quem decide é o ombrod (esta classe roda no laço da Aplicacao). O Ombro.app só mostra o `aplicado` que o ombrod
publica, e cada mascote animado do app troca no próximo ponto seguro dele (o app recebe os retratos com alguns segundos
de atraso; ver app/src/features/avatars/animacoes.ts).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple

from config import PACOTES_ANIMACAO, PACOTES_EM_AVALIACAO
from core.models import Activity, AgentSession, Attention
from logging_config import curto, logger, sem_casa

from .arquivos import ArquivoMudou, assinatura, gravar_atomico

log = logger("animacao")

PADRAO = PACOTES_ANIMACAO[0]
FORMATO = 1  # versão do animacao.json
FIM_DA_CONCLUSAO = 2.0  # o mesmo do mascote.py (um teste confere)
# o que a interface mostra dos pacotes que ainda não dá para escolher
EM_AVALIACAO = {
    "3d": "falta validar o volume na tela USB e o custo do 3D no app",
    "terminal": "falta validar a leitura das letras na tela USB e nos layouts menores",
}


def em_andamento(s: AgentSession) -> bool:
    """Uma sessão com atividade que o mascote está representando: trabalhando, esperando você ou parada com erro (as
    duas últimas aparecem como "esperando" no mascote)."""
    return s.activity is Activity.WORKING or s.attention in (Attention.WAITING_USER, Attention.WAITING_APPROVAL,
                                                            Attention.ERROR)


@dataclass(frozen=True)
class Atividade:
    """O que está em andamento agora, por agente: (id do agente, quantas sessões em andamento)."""
    coletado: bool  # o Ombro já leu os agentes ao menos uma vez
    agentes: Tuple[Tuple[str, int], ...] = ()

    @classmethod
    def das_sessoes(cls, coletado: bool, por_agente: Iterable[Tuple[str, Iterable[AgentSession]]]) -> "Atividade":
        contas = []
        for pid, sessoes in por_agente:
            n = sum(1 for s in sessoes if em_andamento(s))
            if n:
                contas.append((pid, n))
        return cls(coletado, tuple(contas))

    @property
    def ocupado(self) -> bool:
        return bool(self.agentes)


def ler_aplicado(arquivo: Optional[Path]) -> Optional[str]:
    """O pacote que estava em uso quando o Ombro parou (None: não há, ou o arquivo não serve: vale o desejado)."""
    if arquivo is None:
        return None
    try:
        dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("não consegui ler %s (%s); o pacote em uso volta a ser o escolhido", curto(arquivo), sem_casa(e))
        return None
    v = dados.get("aplicado") if isinstance(dados, dict) else None
    if v not in PACOTES_ANIMACAO:
        if v is not None:
            log.warning("%s: pacote em uso desconhecido (%r); vale o escolhido", curto(arquivo), str(v)[:40])
        return None
    return v


def gravar_aplicado(arquivo: Optional[Path], pacote: str) -> None:
    """Grava o pacote em uso (de uma vez). Uma falha só vai para o log: o pior caso é, ao reiniciar, voltar ao
    escolhido (que então espera um momento seguro como qualquer troca)."""
    if arquivo is None:
        return
    arquivo = Path(arquivo)
    try:
        arquivo.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        texto = json.dumps({"versao": FORMATO, "aplicado": pacote}, ensure_ascii=False) + "\n"
        gravar_atomico(arquivo, texto.encode("utf-8"), modo=0o600, antes=assinatura(arquivo))
    except (OSError, ValueError, ArquivoMudou) as e:
        log.warning("não consegui gravar o pacote em uso em %s (%s)", curto(arquivo), sem_casa(e))


class Animacoes:
    """O desejado, o aplicado e a regra de quando trocar. Não desenha nada: quem troca o pacote do mascote é a
    Aplicacao, quando `pode_trocar` diz que sim."""

    def __init__(self, desejado: str = PADRAO, aplicado: Optional[str] = None, arquivo: Optional[Path] = None):
        self.desejado = desejado if desejado in PACOTES_ANIMACAO else PADRAO
        # sem o que estava em uso (primeira vez, ou --demo): o padrão de antes desta preferência existir, o "vivo";
        # se o escolhido for outro, ele espera um momento seguro como qualquer troca
        self.aplicado = aplicado if aplicado in PACOTES_ANIMACAO else PADRAO
        self.arquivo = arquivo
        self.falhou: Optional[str] = None  # um pacote que não carregou (não é tentado de novo até mudar a escolha)
        self.motivo: Optional[str] = None  # por que a troca ainda espera (starting, activity, settling, mascot)
        self.agentes_ocupados: Tuple[str, ...] = ()
        self._quieto_desde: Optional[float] = None

    @property
    def pendente(self) -> bool:
        return self.desejado != self.aplicado and self.falhou != self.desejado

    def pedir(self, desejado: str) -> None:
        """Uma escolha nova. Só a última vale: a de antes, se ainda pendente, é esquecida."""
        if desejado not in PACOTES_ANIMACAO:
            raise ValueError(f"pacote de animações fora da escolha: {desejado!r}")
        self.desejado = desejado
        if self.falhou != desejado:
            self.falhou = None
        if not self.pendente:
            self.motivo = None

    def pode_trocar(self, agora: float, atividade: Atividade, mascote_seguro: Callable[[], bool]) -> bool:
        """A regra do módulo (os quatro pontos), avaliada agora. Guarda o motivo da espera para a IPC."""
        if atividade.ocupado:
            self._quieto_desde = None
        elif atividade.coletado and self._quieto_desde is None:
            self._quieto_desde = agora
        self.agentes_ocupados = tuple(pid for pid, _ in atividade.agentes)
        if not self.pendente:
            self.motivo = None
            return False
        if not atividade.coletado:
            self.motivo = "starting"
        elif atividade.ocupado:
            self.motivo = "activity"
        elif agora - self._quieto_desde < FIM_DA_CONCLUSAO:
            self.motivo = "settling"
        elif not mascote_seguro():
            self.motivo = "mascot"
        else:
            self.motivo = None
            return True
        return False

    def confirmar(self, pacote: str) -> None:
        """O pacote entrou em uso: vira o aplicado e é gravado."""
        mudou = pacote != self.aplicado
        self.aplicado, self.motivo = pacote, None
        if mudou:
            gravar_aplicado(self.arquivo, pacote)
            log.info("pacote de animações em uso: %s", pacote)

    def recusar(self, pacote: str, erro: BaseException) -> None:
        """O pacote não carregou: fica o que está em uso, e ele não é tentado de novo até a escolha mudar."""
        self.falhou, self.motivo = pacote, None
        log.warning("não consegui carregar o pacote de animações %s (%s); segue o %s", pacote, sem_casa(erro),
                    self.aplicado, exc_info=log.isEnabledFor(logging.DEBUG))

    def como_dict(self) -> Dict:
        """O que a IPC publica (config.get, config.update e providers.list): o app mostra o aplicado e explica a
        espera."""
        return {
            "desired": self.desejado,
            "applied": self.aplicado,
            "pending": self.pendente,
            "waiting_for": self.motivo if self.pendente else None,
            "busy_providers": list(self.agentes_ocupados) if self.pendente else [],
            "failed": self.falhou,
            "choices": list(PACOTES_ANIMACAO),
            "unavailable": [{"id": k, "reason": EM_AVALIACAO.get(k, "")} for k in PACOTES_EM_AVALIACAO],
        }
