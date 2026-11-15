"""As preferências que a IPC pode mudar, e como elas vão para o config.json sem estragar o resto dele.

Só estas chaves (allowlist), cada uma validada pelo mesmo `config.validar` da linha de comando:

  brilho, layout, cor_mascote                        no topo do arquivo
  vista, foco, rotacao, intervalo_rotacao            no bloco "multi_agent" (view, focus_provider, ...)

O arquivo é lido, só essas chaves mudam (as outras, a ordem e o "versao" ficam) e ele é gravado de uma
vez (ombro/arquivos.py). Um config.json que não é JSON válido não é sobrescrito: a mudança é recusada.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

from config import VERSAO, ConfigInvalida, validar

from .arquivos import assinatura, gravar_atomico

EDITAVEIS = ("brilho", "layout", "cor_mascote", "vista", "foco", "rotacao", "intervalo_rotacao")
AO_VIVO = EDITAVEIS  # todas valem na hora (layout: só entre os layouts com o cloud; ver Aplicacao.definir_layout)
NO_BLOCO_MULTI = {"vista": "view", "foco": "focus_provider", "rotacao": "auto_rotate", "intervalo_rotacao": "rotate_interval"}


def validar_mudancas(mudancas: Dict) -> Dict:
    """As mudanças conferidas e normalizadas. Levanta ConfigInvalida com uma mensagem que diz o porquê."""
    if not isinstance(mudancas, dict) or not mudancas:
        raise ConfigInvalida("changes deve ser um objeto com pelo menos uma preferência")
    fora = sorted(set(mudancas) - set(EDITAVEIS))
    if fora:
        raise ConfigInvalida(f"não dá para mudar pela IPC: {', '.join(fora)} (permitidas: {', '.join(EDITAVEIS)})")
    return {k: validar(k, v) for k, v in mudancas.items()}


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
    gravado = {}
    for chave, valor in mudancas.items():
        if chave in NO_BLOCO_MULTI:
            bloco = dados.get("multi_agent")
            if bloco is None:
                bloco = dados["multi_agent"] = {}
            if not isinstance(bloco, dict):
                raise ConfigInvalida(f"{arquivo.name}: multi_agent deve ser um objeto")
            bloco[NO_BLOCO_MULTI[chave]] = valor
        else:
            dados[chave] = valor
        gravado[chave] = valor
    arquivo.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    texto = json.dumps(dados, ensure_ascii=False, indent=2) + "\n"
    return gravar_atomico(arquivo, texto.encode("utf-8"), modo=0o600, antes=antes), gravado
