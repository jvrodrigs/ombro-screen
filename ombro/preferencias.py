"""As preferências que a IPC pode mudar, o esquema delas, e como vão para o config.json sem estragar o resto.

`ESQUEMA` é a fonte da verdade: a IPC (`config.schema`) entrega essa lista para o Ombro.app montar a tela de
ajustes sem saber nada de antemão. Cada opção diz o tipo, os valores permitidos, o grupo e se vale na hora
(`live`) ou só depois de reiniciar o ombrod. A validação é a mesma do config.json (`config.validar`), mais as
regras daqui.

Ficam de fora, de propósito:
  lib, claude_dir    caminhos de pastas para ler: a IPC não aceita caminhos vindos do cliente
  nao_abrir          só faz sentido no terminal (o ombrod nunca abre o navegador)
  demo, demo_ciclo, png, arquivo     ações de uma vez, só na linha de comando

O arquivo é lido, só as chaves pedidas mudam (as outras, a ordem e o "versao" ficam) e ele é gravado de uma
vez (ombro/arquivos.py). Um config.json que não é JSON válido não é sobrescrito: a mudança é recusada.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from config import LAYOUTS, PROVIDERS, REVISOES, TRANSPORTES, VERSAO, VISTAS, ConfigInvalida, validar

from .arquivos import assinatura, gravar_atomico

_PORTA = re.compile(r"/dev/(cu|tty)\.[A-Za-z0-9._-]{1,64}")


@dataclass(frozen=True)
class Opcao:
    chave: str  # o nome na IPC: "brilho", "providers.codex"
    grupo: str  # display, appearance, multi_agent, providers, hardware, diagnostics
    tipo: str  # boolean, integer, number, string, enum, color
    descricao: str
    ao_vivo: bool  # vale na hora; senão, depois de reiniciar o ombrod
    opcoes: Optional[Tuple] = None
    minimo: Optional[float] = None
    maximo: Optional[float] = None
    nulo: bool = False

    def como_dict(self) -> dict:
        d = {"key": self.chave, "group": self.grupo, "type": self.tipo, "description": self.descricao,
             "live": self.ao_vivo, "nullable": self.nulo}
        if self.opcoes is not None:
            d["choices"] = list(self.opcoes)
        if self.minimo is not None:
            d["min"] = self.minimo
        if self.maximo is not None:
            d["max"] = self.maximo
        return d


ESQUEMA: Tuple[Opcao, ...] = (
    # a tela
    Opcao("usb", "display", "boolean", "usar a tela USB (desligado: simulador no navegador)", False),
    Opcao("revisao", "display", "enum", "revisão do hardware da tela", False, REVISOES),
    Opcao("transporte", "display", "enum", "como falar com a tela: auto, USB direto ou porta serial", False, TRANSPORTES),
    Opcao("porta", "display", "string", "porta serial (AUTO, ou /dev/cu.<nome>)", False),
    Opcao("brilho", "display", "integer", "brilho em % (a revisão A esquenta acima de 50)", True, minimo=0, maximo=100),
    Opcao("paisagem", "display", "boolean", "tela deitada no layout clássico", False),
    Opcao("girar", "display", "boolean", "girar 180°", False),
    Opcao("porta_sim", "display", "integer", "porta do simulador em localhost", False, minimo=1024, maximo=65535),
    # o que aparece
    Opcao("layout", "appearance", "enum", "o modelo de tela (de ou para o clássico, só ao reiniciar)", True, LAYOUTS),
    Opcao("cor_mascote", "appearance", "color", "cor fixa do cloud (#RRGGBB; null sorteia a cada estado)", True, nulo=True),
    Opcao("intervalo", "appearance", "number", "segundos entre quadros (null: o padrão do layout)", False,
          minimo=0.05, maximo=10, nulo=True),
    # layout multiagente
    Opcao("vista", "multi_agent", "enum", "visão do layout multiagente", True, VISTAS),
    Opcao("foco", "multi_agent", "enum", "o agente da visão focus", True, PROVIDERS, nulo=True),
    Opcao("rotacao", "multi_agent", "boolean", "alternar sozinho entre a visão geral e cada agente", True),
    Opcao("intervalo_rotacao", "multi_agent", "number", "segundos em cada visão da rotação", True, minimo=5, maximo=3600),
    # agentes
    *(Opcao(f"providers.{p}", "providers", "boolean", f"acompanhar o {nome}", False)
      for p, nome in (("claude", "Claude Code"), ("codex", "Codex"), ("cursor", "Cursor"))),
    # coleta e diagnóstico
    Opcao("sem_macmon", "hardware", "boolean", "não usar o macmon (só psutil: sem GPU e temperatura)", False),
    Opcao("debug", "diagnostics", "boolean", "logs detalhados (nível DEBUG)", False),
)
POR_CHAVE: Dict[str, Opcao] = {o.chave: o for o in ESQUEMA}
EDITAVEIS = tuple(POR_CHAVE)
AO_VIVO = tuple(o.chave for o in ESQUEMA if o.ao_vivo)
NO_BLOCO_MULTI = {"vista": "view", "foco": "focus_provider", "rotacao": "auto_rotate", "intervalo_rotacao": "rotate_interval"}


def ao_vivo(chave: str) -> bool:
    return POR_CHAVE[chave].ao_vivo


def esquema() -> dict:
    return {"version": 1, "options": [o.como_dict() for o in ESQUEMA]}


def validar_mudancas(mudancas: Dict, ligados: Iterable[str] = PROVIDERS) -> Dict:
    """As mudanças conferidas e normalizadas. `ligados`: os agentes ligados agora (para não desligar todos).
    Levanta ConfigInvalida com uma mensagem que diz o porquê."""
    if not isinstance(mudancas, dict) or not mudancas:
        raise ConfigInvalida("changes deve ser um objeto com pelo menos uma preferência")
    fora = sorted(set(mudancas) - set(EDITAVEIS))
    if fora:
        raise ConfigInvalida(f"não dá para mudar pela IPC: {', '.join(fora)[:120]} (veja config.schema)")
    limpo = {}
    for chave, v in mudancas.items():
        o = POR_CHAVE[chave]
        if v is None and not o.nulo:
            raise ConfigInvalida(f"{chave} não pode ser null")
        if chave.startswith("providers."):
            if not isinstance(v, bool):
                raise ConfigInvalida(f"{chave} deve ser true ou false (veio {v!r})")
            limpo[chave] = v
            continue
        v = validar(chave, v)
        if chave == "porta" and v.upper() != "AUTO" and not _PORTA.fullmatch(v):
            raise ConfigInvalida("porta deve ser AUTO ou um dispositivo como /dev/cu.usbmodem1101")
        if v is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            if (o.minimo is not None and v < o.minimo) or (o.maximo is not None and v > o.maximo):
                raise ConfigInvalida(f"{chave} deve estar entre {o.minimo:g} e {o.maximo:g} (veio {v!r})")
        limpo[chave] = v
    restantes = {p for p in ligados if limpo.get(f"providers.{p}", True)} | {
        p for p in PROVIDERS if limpo.get(f"providers.{p}") is True}
    if not restantes:
        raise ConfigInvalida("pelo menos um agente precisa ficar ligado")
    return limpo


def gravar(arquivo: Path, mudancas: Dict) -> Tuple[Path, Dict]:
    """Aplica `mudancas` (já validadas) no arquivo. Devolve (arquivo gravado, o que ficou no arquivo)."""
    arquivo = Path(arquivo)
    antes = assinatura(arquivo)
    dados: Dict = {"versao": VERSAO}
    if antes is not None:
        texto = arquivo.read_text(encoding="utf-8")
        if texto.strip():
            try:
                dados = json.loads(texto)
            except ValueError as e:
                raise ConfigInvalida(f"{arquivo.name} não é um JSON válido ({e}); corrija-o antes de mudar preferências") from None
            if not isinstance(dados, dict):
                raise ConfigInvalida(f"{arquivo.name} deve ter um objeto JSON")

    def bloco(nome: str) -> dict:
        b = dados.get(nome)
        if b is None:
            b = dados[nome] = {}
        if not isinstance(b, dict):
            raise ConfigInvalida(f"{arquivo.name}: {nome} deve ser um objeto")
        return b

    gravado = {}
    for chave, valor in mudancas.items():
        if chave in NO_BLOCO_MULTI:
            bloco("multi_agent")[NO_BLOCO_MULTI[chave]] = valor
        elif chave.startswith("providers."):
            pid = chave.split(".", 1)[1]
            atual = bloco("providers").get(pid)
            if atual is None:
                atual = bloco("providers")[pid] = {}
            if not isinstance(atual, dict):
                raise ConfigInvalida(f"{arquivo.name}: providers.{pid} deve ser um objeto")
            atual["enabled"] = valor
        else:
            dados[chave] = valor
        gravado[chave] = valor
    arquivo.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    texto = json.dumps(dados, ensure_ascii=False, indent=2) + "\n"
    return gravar_atomico(arquivo, texto.encode("utf-8"), modo=0o600, antes=antes), gravado
