"""Configuração do Ombro, resolvida num lugar só.

Precedência, da mais forte para a mais fraca:
  1. opção dada na linha de comando (`--brilho 40`, `--no-usb`)
  2. arquivo de configuração (opcional): ~/.ombro-screen/config.json, ou o de `--config`
  3. valores padrão (os mesmos de antes do arquivo existir)

As chaves do arquivo têm o nome das opções, com `_` no lugar de `-`:

  {"versao": 1, "usb": true, "brilho": 40, "cor_mascote": "#3D7A4F"}

A pasta segue a variável OMBRO_SCREEN_DIR, a mesma do uso.json do statusline_tap.py.
O arquivo não é criado sozinho e não deve guardar credenciais (o Ombro não usa nenhuma).

Este módulo não importa nada da tela, do USB nem dos coletores: dá para testar sozinho.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path

import avatares as _av

RAIZ = Path(__file__).resolve().parent
VERSAO = 1  # versão do formato do arquivo

REVISOES = ("A", "B", "C", "D", "WEACT_A")
TRANSPORTES = ("auto", "usb", "serial")
LAYOUTS = ("auto", "ombro", "classico", "minimal-v2", "multiagente")
NOMES_LAYOUT = {"auto": "do Ombro", "ombro": "do Ombro", "classico": "clássico", "minimal-v2": "Minimal v2",
                "multiagente": "multiagente"}
PROVIDERS = ("claude", "codex", "cursor")  # na ordem em que aparecem na tela
VISTAS = ("overview", "focus", "attention")  # visões do layout multiagente
MIN_ROTACAO = 5.0  # segundos: trocar de visão mais rápido que isso deixa a tela instável
SO_NA_LINHA_DE_COMANDO = ("demo", "demo_ciclo", "png", "arquivo")  # ações de uma vez, não preferências
AVATAR_CHAVES = PROVIDERS + ("padrao",)  # um avatar por agente, e o do Ombro (sem agente em foco)
# Os pacotes de animações do mascotinhos que dá para escolher (o primeiro é o padrão), e os que ela tem mas ainda estão
# em avaliação no Ombro (num config.json, valem o padrão, com um aviso). Ver mascote.py e docs/avatars.md.
PACOTES_ANIMACAO = ("vivo", "classico")
PACOTES_EM_AVALIACAO = ("3d", "terminal")
COR_AVATAR_ESPECIAIS = ("default", "rgb")
_COR = re.compile(r"#?[0-9a-fA-F]{6}")


class ConfigInvalida(ValueError):
    """Um valor fora do permitido, com uma mensagem que diz qual e por quê."""


def pasta_ombro() -> Path:
    """~/.ombro-screen, ou a pasta de OMBRO_SCREEN_DIR."""
    return Path(os.environ.get("OMBRO_SCREEN_DIR", "~/.ombro-screen")).expanduser()


def arquivo_padrao() -> Path:
    return pasta_ombro() / "config.json"


@dataclass(frozen=True)
class OmbroConfig:
    # para onde vão os quadros
    usb: bool = False
    revisao: str = "A"
    porta: str = "AUTO"
    transporte: str = "auto"
    brilho: int = 30
    lib: Path = RAIZ / "turing-smart-screen-python"
    paisagem: bool = False
    girar: bool = False
    # coleta
    sem_macmon: bool = False
    claude_dir: str | None = None
    # o que aparece
    layout: str = "auto"
    intervalo: float | None = None  # None: 0,2 s no layout do Ombro, 1 s no clássico
    cor_mascote: str | None = None  # "#RRGGBB": cor fixa, vale por cima do cor_modo
    cor_modo: str = "agente"  # "agente": a cor de cada agente; "sorteada": uma cor nova a cada troca de estado
    # avatares (avatars/ e avatares.py); no arquivo: {"avatars": {"claude": "sereno", "default": ..., "random": "sessao"}}
    avatares: tuple = ()  # pares (agente ou "padrao", "padrao" | "aleatorio" | id); o que falta é "padrao"
    avatar_sorteio: str = "inicio"  # o "aleatorio": um por inicialização do Ombro, ou um por sessão
    # a cor do corpo de cada avatar, por id: {"sereno": "#3D7A4F", "cubo": "rgb", "pixel": "default"}. Sem entrada, valem
    # o cor_mascote e o cor_modo de sempre; uma entrada explícita vale por cima deles. Pares (id, valor) em ordem de id.
    cores_avatares: tuple = ()
    # o pacote de animações do mascote, no app e na telinha: o que você escolheu. O que está em uso pode ser outro por
    # um tempo (a troca espera a atividade em andamento terminar; ver ombro/animacoes.py)
    pacote_animacao: str = PACOTES_ANIMACAO[0]
    # simulador
    porta_sim: int = 5680
    nao_abrir: bool = False
    # diagnóstico
    debug: bool = False
    # agentes monitorados e o layout multiagente
    providers: tuple = PROVIDERS  # os ligados; no arquivo: {"providers": {"codex": {"enabled": false}}}
    vista: str = "overview"  # no arquivo: {"multi_agent": {"view": ...}}
    foco: str | None = None  # o provider da visão "focus" ({"multi_agent": {"focus_provider": ...}})
    rotacao: bool = False  # alternar sozinho entre a visão geral e cada agente ({"auto_rotate": true})
    intervalo_rotacao: float = 15.0  # segundos em cada visão ({"rotate_interval": 15})
    # só na linha de comando
    demo: bool = False
    demo_ciclo: float = 0.0
    png: Path | None = None
    arquivo: Path | None = None  # o arquivo de configuração lido, se algum

    @property
    def ombro(self) -> bool:
        return self.layout in ("auto", "ombro")

    @property
    def animado(self) -> bool:
        """Layouts com o cloud: sempre 480x320, 5 quadros por segundo."""
        return self.layout in ("auto", "ombro", "minimal-v2", "multiagente")

    @property
    def paisagem_efetiva(self) -> bool:
        return True if self.animado else self.paisagem  # os layouts com o cloud são sempre 480x320

    @property
    def intervalo_efetivo(self) -> float:
        if self.intervalo is not None:
            return self.intervalo
        return 0.2 if self.animado else 1.0

    def avatar(self, chave: str) -> str:
        """A escolha de avatar de um agente (ou "padrao", a do Ombro): "padrao", "aleatorio" ou um id."""
        return dict(self.avatares).get(chave, "padrao")

    def cor_avatar(self, avatar_id: str) -> str | None:
        """A entrada de cor deste avatar ("default", "rgb" ou "#RRGGBB"), ou None se ele herda as preferências globais."""
        return dict(self.cores_avatares).get(avatar_id)

    @property
    def cor_mascote_rgb(self) -> tuple[int, int, int] | None:
        if not self.cor_mascote:
            return None
        h = self.cor_mascote.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def resumo(self) -> dict:
        """O que foi resolvido, para o log de depuração (não há nada sensível aqui)."""
        valores = {f.name: getattr(self, f.name) for f in fields(self)}
        return {k: str(v) if isinstance(v, Path) else v for k, v in valores.items()}


CAMPOS = {f.name for f in fields(OmbroConfig)}
PERSISTENTES = CAMPOS - set(SO_NA_LINHA_DE_COMANDO)


# ---------- validação ----------
def _bool(nome, v):
    if not isinstance(v, bool):
        raise ConfigInvalida(f"{nome} deve ser true ou false (veio {v!r})")
    return v


def _numero(nome, v, minimo=None, maximo=None, inteiro=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (inteiro and not float(v).is_integer()):
        raise ConfigInvalida(f"{nome} deve ser um número{' inteiro' if inteiro else ''} (veio {v!r})")
    if (minimo is not None and v < minimo) or (maximo is not None and v > maximo):
        faixa = f"de {minimo} a {maximo}" if maximo is not None else f"maior ou igual a {minimo}"
        raise ConfigInvalida(f"{nome} deve ser {faixa} (veio {v!r})")
    return int(v) if inteiro else float(v)


def _escolha(nome, v, opcoes, maiusculas=False):
    if not isinstance(v, str):
        raise ConfigInvalida(f"{nome} deve ser um texto (veio {v!r})")
    v = v.upper() if maiusculas else v
    if v not in opcoes:
        raise ConfigInvalida(f"{nome} deve ser um de: {', '.join(opcoes)} (veio {v!r})")
    return v


def _texto(nome, v, vazio=False):
    if not isinstance(v, str) or (not vazio and not v.strip()):
        raise ConfigInvalida(f"{nome} deve ser um texto{' não vazio' if not vazio else ''} (veio {v!r})")
    return v


def _caminho(nome, v, base: Path | None):
    if isinstance(v, Path):
        return v
    p = Path(_texto(nome, v)).expanduser()
    return p if p.is_absolute() or base is None else base / p


def validar(nome: str, v, base: Path | None = None):
    """Valor de `nome` conferido e normalizado. `base`: pasta para caminhos relativos do arquivo."""
    if nome == "providers":
        if isinstance(v, (list, tuple)):
            return tuple(_escolha("providers", p, PROVIDERS) for p in v)
        raise ConfigInvalida(f"providers deve ser uma lista (veio {v!r})")
    if nome == "vista":
        return _escolha(nome, v, VISTAS)
    if nome == "foco":
        return None if v is None else _escolha(nome, v, PROVIDERS)
    if nome == "intervalo_rotacao":
        return _numero(nome, v, MIN_ROTACAO)
    if nome in ("usb", "paisagem", "girar", "sem_macmon", "nao_abrir", "debug", "demo", "rotacao"):
        return _bool(nome, v)
    if nome == "revisao":
        return _escolha(nome, v, REVISOES, maiusculas=True)
    if nome == "transporte":
        return _escolha(nome, v, TRANSPORTES)
    if nome == "layout":
        return _escolha(nome, v, LAYOUTS)
    if nome == "porta":
        return _texto(nome, v)
    if nome == "brilho":
        return _numero(nome, v, 0, 100, inteiro=True)  # a revisão A esquenta acima de 50
    if nome == "porta_sim":
        return _numero(nome, v, 1, 65535, inteiro=True)
    if nome in ("intervalo", "demo_ciclo"):
        return None if v is None and nome == "intervalo" else _numero(nome, v, 0)
    if nome == "cor_mascote":
        if v is None:
            return None
        if not isinstance(v, str) or not _COR.fullmatch(v):
            raise ConfigInvalida(f"cor_mascote deve ser uma cor como \"#3D7A4F\" (veio {v!r})")
        return "#" + v.lstrip("#").upper()
    if nome == "cor_modo":
        return _escolha(nome, v, _av.CORES_MODO)
    if nome == "avatar_sorteio":
        return _escolha(nome, v, _av.SORTEIOS)
    if nome == "pacote_animacao":
        return _escolha(nome, v, PACOTES_ANIMACAO)
    if nome == "avatares":
        pares = v.items() if isinstance(v, dict) else v
        if not isinstance(pares, (list, tuple)) and not isinstance(v, dict):
            raise ConfigInvalida(f"avatares deve ser um objeto, como {{\"codex\": \"orbita\"}} (veio {v!r})")
        limpo = {}
        for par in pares:
            if not (isinstance(par, (list, tuple)) and len(par) == 2):
                raise ConfigInvalida(f"avatares: entrada inválida {par!r}")
            limpo[_escolha("avatares", par[0], AVATAR_CHAVES)] = _valor_avatar(f"avatares.{par[0]}", par[1])
        return tuple((k, limpo[k]) for k in AVATAR_CHAVES if k in limpo)
    if nome == "cores_avatares":
        return _cores_avatares(v)
    if nome == "claude_dir":
        return None if v is None else _texto(nome, v)
    if nome in ("lib", "png", "arquivo"):
        return None if v is None and nome != "lib" else _caminho(nome, v, base)
    raise ConfigInvalida(f"opção desconhecida: {nome}")


def _valor_avatar(nome: str, v) -> str:
    """"padrao", "aleatorio" ou o id de um avatar. Um id que não está no catálogo passa (vira o padrão na hora
    de desenhar, com um aviso ao ler o arquivo): apagar um avatar não pode impedir o Ombro de abrir."""
    if not isinstance(v, str) or not (v in _av.ESPECIAIS or _av._ID.fullmatch(v)):
        raise ConfigInvalida(f"{nome} deve ser \"padrao\", \"aleatorio\" ou o id de um avatar (veio {v!r})")
    return v


def valor_cor_avatar(nome: str, v) -> str:
    """O valor de cor de um avatar: "default" (a cor original do avatar/agente), "rgb" (o ciclo de cores) ou uma cor
    "#RRGGBB" (com ou sem #, em qualquer caixa), normalizada para "#RRGGBB". Sem coerção: só texto."""
    if isinstance(v, str):
        t = v.strip()
        if t.lower() in COR_AVATAR_ESPECIAIS:
            return t.lower()
        if _COR.fullmatch(t):
            return "#" + t.lstrip("#").upper()
    raise ConfigInvalida(f"{nome} deve ser \"default\", \"rgb\" ou uma cor como \"#3D7A4F\" (veio {v!r})")


def _cores_avatares(v) -> tuple:
    """{"sereno": "#3D7A4F", "cubo": "rgb"} -> pares em ordem de id. Um valor null remove a entrada (herda). Um id que
    não está no catálogo passa (como em avatares: apagar um avatar não impede o Ombro de abrir)."""
    pares = v.items() if isinstance(v, dict) else v
    if not isinstance(pares, (list, tuple)) and not isinstance(v, dict):
        raise ConfigInvalida(f"cores_avatares deve ser um objeto, como {{\"sereno\": \"#3D7A4F\"}} (veio {v!r})")
    limpo = {}
    for par in pares:
        if not (isinstance(par, (list, tuple)) and len(par) == 2):
            raise ConfigInvalida(f"cores_avatares: entrada inválida {par!r}")
        aid, valor = par
        if not isinstance(aid, str) or not _av._ID.fullmatch(aid):
            raise ConfigInvalida(f"cores_avatares: o id de avatar {aid!r} não é válido")
        if valor is None:
            limpo.pop(aid, None)
        else:
            limpo[aid] = valor_cor_avatar(f"cores_avatares.{aid}", valor)
    return tuple(sorted(limpo.items()))


def _avatars(v) -> dict:
    """{"claude": "sereno", "codex": "aleatorio", "default": "ombro-cloud", "random": "sessao"}."""
    if not isinstance(v, dict):
        raise ConfigInvalida("avatars deve ser um objeto, como {\"codex\": \"orbita\"}")
    nomes = {**{p: p for p in PROVIDERS}, "default": "padrao"}
    desconhecidas = set(v) - set(nomes) - {"random"}
    if desconhecidas:
        raise ConfigInvalida(f"avatars: opção desconhecida {', '.join(sorted(desconhecidas))} "
                             f"(use {', '.join([*nomes, 'random'])})")
    valores = {"avatares": validar("avatares", [(nomes[k], x) for k, x in v.items() if k != "random"])}
    if "random" in v:
        valores["avatar_sorteio"] = validar("avatar_sorteio", v["random"])
    return valores


def _providers(v) -> dict:
    """{"claude": {"enabled": true}, "codex": {"enabled": false}} -> providers ligados, na ordem da tela.
    Um provider que não aparece fica ligado (o padrão)."""
    if not isinstance(v, dict):
        raise ConfigInvalida("providers deve ser um objeto, como {\"codex\": {\"enabled\": false}}")
    desligados = set()
    for pid, opcoes in v.items():
        _escolha("providers", pid, PROVIDERS)
        if not isinstance(opcoes, dict) or set(opcoes) - {"enabled"}:
            raise ConfigInvalida(f"providers.{pid} deve ser um objeto com \"enabled\" (veio {opcoes!r})")
        if not _bool(f"providers.{pid}.enabled", opcoes.get("enabled", True)):
            desligados.add(pid)
    return {"providers": tuple(p for p in PROVIDERS if p not in desligados)}


def _multi_agent(v) -> dict:
    if not isinstance(v, dict):
        raise ConfigInvalida("multi_agent deve ser um objeto, como {\"view\": \"overview\"}")
    nomes = {"view": "vista", "focus_provider": "foco", "auto_rotate": "rotacao", "rotate_interval": "intervalo_rotacao"}
    desconhecidas = set(v) - set(nomes)
    if desconhecidas:
        raise ConfigInvalida(f"multi_agent: opção desconhecida {', '.join(sorted(desconhecidas))} "
                             f"(use {', '.join(nomes)})")
    return {nomes[k]: validar(nomes[k], x) for k, x in v.items()}


# ---------- arquivo ----------
def ler_arquivo(arq: Path, obrigatorio: bool = False) -> tuple[dict, list[str]]:
    """(valores validados, avisos). Sem o arquivo: ({}, []), a não ser que seja obrigatório."""
    try:
        texto = arq.read_text(encoding="utf-8")
    except FileNotFoundError:
        if obrigatorio:
            raise ConfigInvalida(f"arquivo de configuração não encontrado: {arq}") from None
        return {}, []
    except OSError as e:
        raise ConfigInvalida(f"não consegui ler {arq}: {e.strerror or e}") from None
    if not texto.strip():
        return {}, []
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as e:
        raise ConfigInvalida(f"{arq} não é um JSON válido (linha {e.lineno}, coluna {e.colno}: {e.msg})") from None
    if not isinstance(dados, dict):
        raise ConfigInvalida(f"{arq} deve ter um objeto JSON, como {{\"brilho\": 40}}")

    avisos = []
    versao = dados.pop("versao", VERSAO)
    if isinstance(versao, bool) or not isinstance(versao, int):
        raise ConfigInvalida(f"{arq}: versao deve ser um número inteiro (veio {versao!r})")
    if versao > VERSAO:
        avisos.append(f"{arq.name} é da versão {versao} do formato e este Ombro conhece até a {VERSAO}: "
                      "o que ele não conhece fica de fora")
    valores = {}
    for nome, v in dados.items():
        if nome in ("providers", "multi_agent", "avatars"):  # os blocos aninhados, no formato do README
            try:
                valores.update({"providers": _providers, "multi_agent": _multi_agent, "avatars": _avatars}[nome](v))
            except ConfigInvalida as e:
                raise ConfigInvalida(f"{arq}: {e}") from None
            for chave, escolha in valores.get("avatares", ()) if nome == "avatars" else ():
                if escolha not in _av.ESPECIAIS and escolha not in _av.catalogo().avatares:
                    avisos.append(f"{arq.name}: o avatar \"{escolha}\" ({chave}) não existe; vale o padrão")
        elif nome == "cores_avatares":
            try:
                valores[nome] = validar(nome, v)
            except ConfigInvalida as e:
                raise ConfigInvalida(f"{arq}: {e}") from None
            for aid, _ in valores[nome]:
                if aid not in _av.catalogo().avatares:
                    avisos.append(f"{arq.name}: cores_avatares: o avatar \"{aid}\" não existe; a cor é ignorada")
        elif nome == "pacote_animacao" and isinstance(v, str) and v not in PACOTES_ANIMACAO:
            # um pacote que este Ombro não oferece (em avaliação, de outra versão, ou digitado errado) não impede o
            # Ombro de abrir: vale o padrão
            motivo = "ainda está em avaliação" if v in PACOTES_EM_AVALIACAO else "não existe"
            avisos.append(f"{arq.name}: o pacote de animações \"{v[:40]}\" {motivo}; vale o "
                          f"\"{PACOTES_ANIMACAO[0]}\"")
        elif nome in SO_NA_LINHA_DE_COMANDO:
            avisos.append(f"{arq.name}: \"{nome}\" só vale na linha de comando; ignorado")
        elif nome not in PERSISTENTES:
            avisos.append(f"{arq.name}: opção desconhecida \"{nome}\"; ignorada")
        else:
            try:
                valores[nome] = validar(nome, v, base=arq.parent)
            except ConfigInvalida as e:
                raise ConfigInvalida(f"{arq}: {e}") from None
    return valores, avisos


def resolver(linha_de_comando: dict, arquivo: Path | None = None) -> tuple[OmbroConfig, list[str]]:
    """Junta padrões, arquivo e linha de comando. Na linha de comando, None = opção não dada.

    `arquivo`: o de --config (obrigatório); sem ele, o arquivo padrão, se existir.
    """
    obrigatorio = arquivo is not None
    arq = arquivo if arquivo is not None else arquivo_padrao()
    do_arquivo, avisos = ler_arquivo(arq, obrigatorio=obrigatorio)
    cfg = replace(OmbroConfig(), **do_arquivo)
    if do_arquivo or obrigatorio or arq.exists():
        cfg = replace(cfg, arquivo=arq)
    dados = {k: v for k, v in linha_de_comando.items() if v is not None}
    desconhecidos = set(dados) - CAMPOS
    if desconhecidos:
        raise ConfigInvalida(f"opção desconhecida: {', '.join(sorted(desconhecidos))}")
    cfg = replace(cfg, **{k: validar(k, v) for k, v in dados.items()})
    return cfg, avisos
