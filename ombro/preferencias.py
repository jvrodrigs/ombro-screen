"""As preferências que a IPC pode mudar, o esquema delas, e como vão para o config.json sem estragar o resto.

`ESQUEMA` é a fonte da verdade: a IPC (`config.schema`) entrega essa lista para o Ombro.app montar a tela de
ajustes sem saber nada de antemão. Cada opção diz o tipo, os valores permitidos, o grupo e se vale na hora
(`live`) ou só depois de reiniciar o ombrod. Vale na hora tudo o que o laço consegue pôr em uso sem refazer o que
abriu ao iniciar (brilho, layout, girar, paisagem, cores, avatares, visão, pacote de animações); fica para o reinício o que escolhe a
saída e as fontes (usb, revisão, transporte, portas, agentes ligados, macmon, debug). A validação é a mesma do
config.json (`config.validar`), mais as regras daqui.

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import avatares
from config import (AVATAR_CHAVES, LAYOUTS, PACOTES_ANIMACAO, PROVIDERS, REVISOES, TRANSPORTES, VERSAO, VISTAS,
                    ConfigInvalida, validar, valor_cor_avatar)

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
    Opcao("paisagem", "display", "boolean", "layout clássico deitado (480x320) em vez de em pé (320x480); os outros "
          "layouts são sempre deitados", True),
    Opcao("girar", "display", "boolean", "girar a tela 180° (para o case que a deixa de cabeça para baixo)", True),
    Opcao("porta_sim", "display", "integer", "porta do simulador em localhost", False, minimo=1024, maximo=65535),
    # o que aparece
    Opcao("layout", "appearance", "enum", "o modelo de tela", True, LAYOUTS),
    Opcao("cor_mascote", "appearance", "color", "cor fixa do avatar (#RRGGBB; vale por cima do cor_modo; null: segue o cor_modo)",
          True, nulo=True),
    Opcao("cor_modo", "appearance", "enum", "cor do avatar: a do agente, ou sorteada a cada troca de estado", True,
          avatares.CORES_MODO),
    Opcao("intervalo", "appearance", "number", "segundos entre quadros (null: o padrão do layout)", True,
          minimo=0.05, maximo=10, nulo=True),
    # vale na hora, mas com atividade em andamento a troca espera um momento seguro (o estado dela vem em `animation`)
    Opcao("pacote_animacao", "appearance", "enum", "o pacote de animações do mascote, no app e na telinha; com um "
          "agente trabalhando ou esperando você, a troca espera a atividade terminar", True, PACOTES_ANIMACAO),
    # layout multiagente
    Opcao("vista", "multi_agent", "enum", "visão do layout multiagente", True, VISTAS),
    Opcao("foco", "multi_agent", "enum", "o agente da visão focus", True, PROVIDERS, nulo=True),
    Opcao("rotacao", "multi_agent", "boolean", "alternar sozinho entre a visão geral e cada agente", True),
    Opcao("intervalo_rotacao", "multi_agent", "number", "segundos em cada visão da rotação", True, minimo=5, maximo=3600),
    # agentes
    *(Opcao(f"providers.{p}", "providers", "boolean", f"acompanhar o {nome}", False)
      for p, nome in (("claude", "Claude Code"), ("codex", "Codex"), ("cursor", "Cursor"))),
    # avatares: o de cada agente e o do Ombro (sem agente em foco); "padrao", "aleatorio" ou o id de um avatar
    *(Opcao(f"avatares.{p}", "avatars", "enum", f"avatar {nome}", True,
            avatares.ESPECIAIS + avatares.catalogo().ids())
      for p, nome in (("claude", "do Claude Code"), ("codex", "do Codex"), ("cursor", "do Cursor"),
                      ("padrao", "do Ombro, quando nenhum agente está em foco"))),
    # a cor do corpo de cada avatar, por id: "default" (a original), "rgb" (o ciclo) ou "#RRGGBB"; null remove a entrada
    # e o avatar volta a herdar cor_mascote/cor_modo. Só os avatares do catálogo (como em avatares.*)
    *(Opcao(f"cores_avatares.{aid}", "avatars", "string",
            f"cor do avatar {aid}: \"default\", \"rgb\" (ciclo de cores) ou #RRGGBB; null herda as cores globais", True,
            nulo=True)
      for aid in avatares.catalogo().ids()),
    Opcao("avatar_sorteio", "avatars", "enum", "o aleatório: um por inicialização do Ombro, ou um por sessão", True,
          avatares.SORTEIOS),
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


def valor(cfg, chave: str):
    """O valor de uma preferência numa configuração (a efetiva, por exemplo), na forma em que a IPC a recebe."""
    if chave.startswith("providers."):
        return chave.split(".", 1)[1] in cfg.providers
    if chave.startswith("avatares."):
        return cfg.avatar(chave.split(".", 1)[1])
    if chave.startswith("cores_avatares."):
        return cfg.cor_avatar(chave.split(".", 1)[1])  # None: herda as cores globais
    return getattr(cfg, chave)


def aplicar_em(cfg, mudancas: Dict):
    """`cfg` com `mudancas` (já validadas). Só gera a configuração nova: quem a põe em uso é a Aplicacao."""
    simples, avs, provs, cores = {}, dict(cfg.avatares), set(cfg.providers), dict(cfg.cores_avatares)
    for chave, v in mudancas.items():
        if chave.startswith("cores_avatares."):
            aid = chave.split(".", 1)[1]
            if v is None:
                cores.pop(aid, None)  # herda de novo
            else:
                cores[aid] = v
        elif chave.startswith("avatares."):
            avs[chave.split(".", 1)[1]] = v
        elif chave.startswith("providers."):
            (provs.add if v else provs.discard)(chave.split(".", 1)[1])
        else:
            simples[chave] = v
    return replace(cfg, **simples, avatares=tuple((k, avs[k]) for k in AVATAR_CHAVES if k in avs),
                   cores_avatares=tuple(sorted(cores.items())), providers=tuple(p for p in PROVIDERS if p in provs))


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
        if chave.startswith("cores_avatares."):  # o id existe (o esquema tem uma opção por avatar do catálogo)
            limpo[chave] = None if v is None else valor_cor_avatar(chave, v)
            continue
        if chave.startswith("avatares."):  # pela IPC, só os avatares que existem (o arquivo aceita mais, com aviso)
            if v not in o.opcoes:
                raise ConfigInvalida(f"{chave} deve ser \"padrao\", \"aleatorio\" ou um destes avatares: "
                                     f"{', '.join(o.opcoes[2:])} (veio {v!r})")
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
        elif chave.startswith("cores_avatares."):
            aid = chave.split(".", 1)[1]
            if valor is None:  # null remove a entrada (e o bloco, se esvaziar): o avatar volta a herdar
                if isinstance(dados.get("cores_avatares"), dict):
                    dados["cores_avatares"].pop(aid, None)
                    if not dados["cores_avatares"]:
                        del dados["cores_avatares"]
            else:
                bloco("cores_avatares")[aid] = valor
        elif chave.startswith("avatares."):
            alvo = chave.split(".", 1)[1]
            bloco("avatars")["default" if alvo == "padrao" else alvo] = valor
        elif chave == "avatar_sorteio":
            bloco("avatars")["random"] = valor
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
