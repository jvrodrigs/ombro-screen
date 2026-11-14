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

RAIZ = Path(__file__).resolve().parent
VERSAO = 1  # versão do formato do arquivo

REVISOES = ("A", "B", "C", "D", "WEACT_A")
TRANSPORTES = ("auto", "usb", "serial")
LAYOUTS = ("auto", "ombro", "classico")
SO_NA_LINHA_DE_COMANDO = ("demo", "demo_ciclo", "png", "arquivo")  # ações de uma vez, não preferências
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
    cor_mascote: str | None = None  # "#RRGGBB"; None sorteia uma cor a cada estado
    # simulador
    porta_sim: int = 5680
    nao_abrir: bool = False
    # diagnóstico
    debug: bool = False
    # só na linha de comando
    demo: bool = False
    demo_ciclo: float = 0.0
    png: Path | None = None
    arquivo: Path | None = None  # o arquivo de configuração lido, se algum

    @property
    def ombro(self) -> bool:
        return self.layout in ("auto", "ombro")

    @property
    def paisagem_efetiva(self) -> bool:
        return True if self.ombro else self.paisagem  # o layout do Ombro é sempre 480x320

    @property
    def intervalo_efetivo(self) -> float:
        if self.intervalo is not None:
            return self.intervalo
        return 0.2 if self.ombro else 1.0

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
    if nome in ("usb", "paisagem", "girar", "sem_macmon", "nao_abrir", "debug", "demo"):
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
    if nome == "claude_dir":
        return None if v is None else _texto(nome, v)
    if nome in ("lib", "png", "arquivo"):
        return None if v is None and nome != "lib" else _caminho(nome, v, base)
    raise ConfigInvalida(f"opção desconhecida: {nome}")


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
        if nome in SO_NA_LINHA_DE_COMANDO:
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
