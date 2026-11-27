"""Instalar, conferir e remover os hooks do Ombro no Claude Code, no Codex e no Cursor.

  python3 -m ombro integrations list
  python3 -m ombro integrations check codex
  python3 -m ombro integrations install claude --dry-run      mostra a mudança, sem gravar
  python3 -m ombro integrations install claude
  python3 -m ombro integrations uninstall cursor
  python3 -m ombro integrations rollback claude              volta o arquivo para o último backup

Onde (formatos das documentações oficiais; detalhes em docs/integrations.md):
  claude  $CLAUDE_CONFIG_DIR/settings.json (padrão ~/.claude)   {"hooks": {Evento: [{"hooks": [{type, command, async}]}]}}
  codex   $CODEX_HOME/hooks.json (padrão ~/.codex)              o mesmo formato; o Codex pede para confiar no hook (/hooks)
  cursor  ~/.cursor/hooks.json                                  {"version": 1, "hooks": {evento: [{"command": ...}]}}

Regras:
  - só mexe quando você pede (install/uninstall/rollback); `status`, `doctor` e o ombrod nunca instalam nada;
  - uma entrada é do Ombro só se o comando for exatamente `PYTHON .../ombro_hook.py PROVIDER [fallback]`, com o
    fallback que o próprio Ombro escreve. Outra coisa que cite ombro_hook.py é ambígua: o Ombro não mexe nela e
    não instala por cima (avisa);
  - o resto do arquivo fica como estava (outros hooks, preferências, a ordem das chaves); muda o mínimo;
  - instalar duas vezes não duplica nada; reinstalar só troca as entradas do Ombro (outro caminho, outro python);
  - antes de gravar: backup (0600, os 10 mais recentes por agente) em ~/.ombro-screen/backups/integrations/;
    grava de uma vez (ombro/arquivos.py) e só se ninguém mexeu no arquivo desde a leitura; depois relê e confere,
    e, se não bate, volta o backup sozinho;
  - JSON inválido, formato desconhecido, sem permissão ou pasta do agente inexistente: nada é gravado.
"""
from __future__ import annotations

import difflib
import fcntl
import json
import os
import re
import shlex
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from logging_config import curto, logger

from . import integracao_opencode
from .arquivos import ArquivoMudou, assinatura, destino_real, gravar_atomico
from .caminhos import garantir_pasta_privada, pasta_backups

log = logger("integrations")

RAIZ = Path(__file__).resolve().parent.parent
HOOK = RAIZ / "ombro_hook.py"
MANTER_BACKUPS = 10
FALLBACK = {  # o que vem depois de "PROVIDER" no comando: garante código 0 mesmo sem o ombro_hook.py
    "claude": ["||", "true"],
    "codex": ["||", "true"],
    "cursor": ["2>/dev/null", "||", "echo", "{}"],  # no Cursor, código 2 bloqueia e alguns hooks pedem JSON
}
FALLBACKS_ACEITOS = ([], ["||", "true"], ["2>/dev/null", "||", "echo", "{}"])  # formas que a documentação já usou


class ErroIntegracao(Exception):
    """Não dá para fazer a mudança com segurança; nada foi gravado. A mensagem diz por quê e o que fazer."""


# ---------- os agentes ----------
def _eventos(provider: str) -> Tuple[str, ...]:
    """Os eventos que o ombro_hook.py sabe guardar: a fonte da verdade é o próprio hook."""
    sys.path.insert(0, str(RAIZ)) if str(RAIZ) not in sys.path else None
    import ombro_hook

    return tuple(ombro_hook.EVENTOS[provider])


@dataclass(frozen=True)
class Alvo:
    provider: str
    nome: str
    executaveis: Tuple[str, ...]
    apps: Tuple[str, ...]
    nota: str = ""  # aviso depois de instalar

    @property
    def arquivo(self) -> Path:
        casa = Path.home()
        if self.provider == "claude":
            return Path(os.environ.get("CLAUDE_CONFIG_DIR") or casa / ".claude").expanduser() / "settings.json"
        if self.provider == "codex":
            return Path(os.environ.get("CODEX_HOME") or casa / ".codex").expanduser() / "hooks.json"
        return casa / ".cursor" / "hooks.json"

    def instalado(self) -> bool:
        return (any(shutil.which(e) for e in self.executaveis) or any(Path(a).exists() for a in self.apps)
                or (self.provider == "claude" and self.arquivo.parent.is_dir()))

    def handler(self, comando: str, evento: str) -> dict:
        """A entrada do Ombro para `evento`, no formato do agente."""
        if self.provider == "cursor":
            return {"command": comando}
        if self.provider == "codex" and evento == "SessionEnd":
            return {"type": "command", "command": comando, "timeout": 3}  # o SessionEnd do Codex não aceita async
        return {"type": "command", "command": comando, "async": True}

    def grupo(self, comando: str, evento: str) -> dict:
        h = self.handler(comando, evento)
        return h if self.provider == "cursor" else {"hooks": [h]}


ALVOS: Dict[str, Alvo] = {
    "claude": Alvo("claude", "Claude Code", ("claude",), ()),
    "codex": Alvo("codex", "Codex", ("codex",), ("/Applications/Codex.app",),
                  nota="O Codex só roda hooks novos depois que você confia neles: abra o Codex e rode /hooks "
                       "(no app: Settings > Hooks)."),
    "cursor": Alvo("cursor", "Cursor", ("cursor-agent",), ("/Applications/Cursor.app",),
                   nota="O Cursor recarrega o hooks.json sozinho ao salvar."),
}


def comando_do_hook(provider: str, python: Optional[str] = None) -> str:
    py = python or sys.executable
    return " ".join([shlex.quote(py), shlex.quote(str(HOOK)), provider] + [
        "'{}'" if t == "{}" else t for t in FALLBACK[provider]])


def e_do_ombro(comando, provider: Optional[str] = None) -> Optional[bool]:
    """True: entrada do Ombro (do `provider`, se dado). False: não cita o Ombro. None: cita, mas é ambígua."""
    if not isinstance(comando, str) or "ombro_hook.py" not in comando:
        return False
    try:
        t = shlex.split(comando)
    except ValueError:
        return None
    if (len(t) >= 3 and os.path.basename(t[1]) == "ombro_hook.py" and re.fullmatch(r"python[0-9.]*", os.path.basename(t[0]))
            and t[2] in ALVOS and t[3:] in FALLBACKS_ACEITOS):
        return provider is None or t[2] == provider or None
    return None


def _python_e_hook(comando: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        t = shlex.split(comando)
    except ValueError:
        return None, None
    return (t[0], t[1]) if len(t) >= 2 else (None, None)


def python_em_uso(alvo: Alvo, leitura: "Leitura") -> Optional[str]:
    """O python da primeira entrada do Ombro que ainda existe (reinstalar sem --python mantém o mesmo)."""
    for _ev, grupos in leitura.dados.get("hooks", {}).items():
        for _g, _l, h in _handlers(alvo, grupos):
            if e_do_ombro(h.get("command"), alvo.provider):
                py, _ = _python_e_hook(h["command"])
                if py and os.path.isfile(py) and os.access(py, os.X_OK):
                    return py
    return None


# ---------- leitura e análise ----------
@dataclass
class Leitura:
    arquivo: Path  # o arquivo de verdade (o alvo, se for link)
    existe: bool
    texto: str
    dados: dict
    antes: object  # assinatura, para notar mudanças de outros programas
    indent: int = 2
    nova_linha: bool = True


def _indentacao(texto: str) -> int:
    for linha in texto.splitlines()[1:]:
        m = re.match(r"( +)\S", linha)
        if m:
            return len(m.group(1))
    return 2


def ler(alvo: Alvo) -> Leitura:
    arq = destino_real(alvo.arquivo)
    antes = assinatura(arq) if arq.exists() else None
    if not arq.exists():
        if not arq.parent.is_dir():
            raise ErroIntegracao(f"a pasta {curto(arq.parent)} não existe: o {alvo.nome} parece não estar instalado "
                                 f"(abra o {alvo.nome} uma vez e tente de novo)")
        base = {"version": 1, "hooks": {}} if alvo.provider == "cursor" else {}
        return Leitura(arq, False, "", base, None)
    try:
        texto = arq.read_text(encoding="utf-8")
    except PermissionError:
        raise ErroIntegracao(f"sem permissão para ler {curto(arq)}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ErroIntegracao(f"não consegui ler {curto(arq)} ({type(e).__name__})") from None
    if not texto.strip():
        dados = {"version": 1, "hooks": {}} if alvo.provider == "cursor" else {}
    else:
        try:
            dados = json.loads(texto)
        except ValueError as e:
            raise ErroIntegracao(f"{curto(arq)} não é um JSON válido ({e}); corrija o arquivo antes, o Ombro não mexe nele") from None
    if not isinstance(dados, dict):
        raise ErroIntegracao(f"{curto(arq)} deve ter um objeto JSON no topo; o Ombro não mexe nele")
    _conferir_formato(alvo, dados, arq)
    return Leitura(arq, True, texto, dados, antes, _indentacao(texto), texto.endswith("\n"))


def _conferir_formato(alvo: Alvo, dados: dict, arq: Path) -> None:
    hooks = dados.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ErroIntegracao(f"{curto(arq)}: \"hooks\" não é um objeto; formato desconhecido, o Ombro não mexe nele")
    if alvo.provider == "cursor" and dados.get("version", 1) != 1:
        raise ErroIntegracao(f"{curto(arq)}: versão {dados.get('version')!r} do formato do Cursor; o Ombro conhece a 1")
    for evento, grupos in hooks.items():
        if not isinstance(grupos, list) or not all(isinstance(g, dict) for g in grupos):
            raise ErroIntegracao(f"{curto(arq)}: hooks.{evento} não é uma lista de objetos; formato desconhecido")
        if alvo.provider != "cursor":
            for g in grupos:
                if "hooks" in g and not (isinstance(g["hooks"], list) and all(isinstance(h, dict) for h in g["hooks"])):
                    raise ErroIntegracao(f"{curto(arq)}: hooks.{evento}[].hooks não é uma lista de objetos; formato desconhecido")


def _handlers(alvo: Alvo, grupos: list):
    """(grupo, lista que contém o handler, handler) de cada handler de um evento."""
    for g in grupos:
        if alvo.provider == "cursor":
            yield g, grupos, g
        else:
            for h in g.get("hooks", []):
                yield g, g["hooks"], h


@dataclass
class Analise:
    provider: str
    arquivo: str  # com ~
    existe: bool
    instalado: bool
    eventos: List[str]  # os que o Ombro usa
    presentes: List[str] = field(default_factory=list)  # com a entrada do Ombro exatamente como deveria
    desatualizados: List[str] = field(default_factory=list)  # com uma entrada do Ombro diferente (outro caminho/python)
    ambiguos: List[str] = field(default_factory=list)  # citam ombro_hook.py de um jeito que o Ombro não reconhece
    sobrando: List[str] = field(default_factory=list)  # eventos que o Ombro não usa mais, com entrada do Ombro
    hook_existe: bool = True
    erro: Optional[str] = None

    @property
    def estado(self) -> str:
        if self.erro:
            return "invalid"
        if self.ambiguos:
            return "conflict"
        if set(self.presentes) == set(self.eventos) and not self.desatualizados and not self.sobrando:
            return "configured"
        if self.presentes or self.desatualizados:
            return "partial"
        return "not_configured"

    def como_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("provider", "arquivo", "existe", "instalado", "eventos", "presentes",
                                           "desatualizados", "ambiguos", "sobrando", "hook_existe", "erro")}
        d["file"] = d.pop("arquivo")
        d["state"] = self.estado
        return d


def analisar(alvo: Alvo, python: Optional[str] = None, leitura: Optional[Leitura] = None) -> Analise:
    eventos = list(_eventos(alvo.provider))
    a = Analise(alvo.provider, curto(alvo.arquivo), alvo.arquivo.exists(), alvo.instalado(), eventos,
                hook_existe=HOOK.exists())
    try:
        leitura = leitura or ler(alvo)
    except ErroIntegracao as e:
        if not alvo.arquivo.exists() and not alvo.arquivo.parent.is_dir():
            return a  # agente sem pasta: simplesmente não configurado
        a.erro = str(e)
        return a
    for evento, grupos in leitura.dados.get("hooks", {}).items():
        for _g, _lista, h in _handlers(alvo, grupos):
            dono = e_do_ombro(h.get("command"), alvo.provider)
            if dono is None:
                a.ambiguos.append(evento)
            elif dono:
                # certa = este ombro_hook.py, um python que existe (o pedido, se houver) e os campos de sempre
                py, script = _python_e_hook(h["command"])
                py_ok = (py == python) if python else bool(py and os.path.isfile(py))
                certa = py_ok and script == str(HOOK) and h == alvo.handler(comando_do_hook(alvo.provider, py), evento)
                if evento not in eventos:
                    a.sobrando.append(evento)
                elif certa and evento not in a.presentes:
                    a.presentes.append(evento)
                else:
                    a.desatualizados.append(evento)
    return a


# ---------- a mudança ----------
def _sem_entradas_do_ombro(alvo: Alvo, dados: dict, manter: Optional[Dict[str, dict]] = None) -> dict:
    """Cópia de `dados` sem as entradas do Ombro deste provider. `manter`: evento -> a entrada certa; a primeira
    entrada do Ombro desse evento vira ela, no mesmo lugar (reinstalar não mexe na ordem dos seus hooks)."""
    novo = json.loads(json.dumps(dados))  # cópia funda, só tipos JSON
    hooks = novo.get("hooks")
    if not isinstance(hooks, dict):
        return novo
    for evento in list(hooks):
        grupos = hooks[evento]
        mantido = False
        antes = len(grupos)
        for g in list(grupos):
            if alvo.provider == "cursor":
                if e_do_ombro(g.get("command"), alvo.provider):
                    if manter and not mantido and evento in manter:
                        grupos[grupos.index(g)] = manter[evento]  # a primeira fica, já corrigida, no mesmo lugar
                        mantido = True
                        continue
                    grupos.remove(g)
                continue
            lista = g.get("hooks", [])
            for h in list(lista):
                if e_do_ombro(h.get("command"), alvo.provider):
                    if manter and not mantido and evento in manter:
                        lista[lista.index(h)] = manter[evento]
                        mantido = True
                        continue
                    lista.remove(h)
            if "hooks" in g and not lista and set(g) <= {"hooks", "matcher"}:
                grupos.remove(g)  # o grupo era só do Ombro
        if antes and not grupos:
            del hooks[evento]  # a lista era só do Ombro
    return novo


def planejar_instalacao(alvo: Alvo, leitura: Leitura, python: Optional[str] = None) -> dict:
    python = python or python_em_uso(alvo, leitura)
    cmd = comando_do_hook(alvo.provider, python)
    analise = analisar(alvo, python, leitura)
    if analise.ambiguos:
        raise ErroIntegracao(
            f"{curto(leitura.arquivo)} já tem entradas que chamam o ombro_hook.py de um jeito que o Ombro não reconhece "
            f"({', '.join(sorted(set(analise.ambiguos)))}). Para não duplicar nem apagar algo seu, nada foi feito: "
            "remova ou ajuste essas entradas à mão e rode de novo.")
    certas = {ev: alvo.handler(cmd, ev) for ev in analise.eventos}
    novo = _sem_entradas_do_ombro(alvo, leitura.dados, manter=certas)
    hooks = novo.setdefault("hooks", {})
    if alvo.provider == "cursor":
        novo.setdefault("version", 1)
    for ev in analise.eventos:
        grupos = hooks.setdefault(ev, [])
        ja = any(h == certas[ev] for _g, _l, h in _handlers(alvo, grupos))
        if not ja:
            grupos.append(alvo.grupo(cmd, ev))
    return novo


def planejar_remocao(alvo: Alvo, leitura: Leitura) -> dict:
    return _sem_entradas_do_ombro(alvo, leitura.dados)


def serializar(dados: dict, leitura: Leitura) -> str:
    texto = json.dumps(dados, ensure_ascii=False, indent=leitura.indent)
    return texto + ("\n" if leitura.nova_linha or not leitura.existe else "")


def diferenca(leitura: Leitura, novo_texto: str) -> str:
    nome = curto(leitura.arquivo)
    return "".join(difflib.unified_diff(leitura.texto.splitlines(True), novo_texto.splitlines(True),
                                        fromfile=f"{nome} (agora)", tofile=f"{nome} (depois)"))


# ---------- backups ----------
def pasta_de_backups(provider: str) -> Path:
    base = pasta_backups()
    garantir_pasta_privada(base)
    garantir_pasta_privada(base / "integrations")
    return garantir_pasta_privada(base / "integrations" / provider)


def fazer_backup(provider: str, arquivo: Path, dados: bytes) -> Path:
    pasta = pasta_de_backups(provider)
    carimbo = time.strftime("%Y%m%d-%H%M%S") + f"-{(time.time_ns() // 1000) % 1_000_000:06d}"
    destino = pasta / f"{carimbo}.bak"
    fd = os.open(str(destino), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(dados)
        f.flush()
        os.fsync(f.fileno())
    meta = pasta / f"{carimbo}.json"
    fd = os.open(str(meta), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"provider": provider, "file": str(arquivo), "created_at": int(time.time()), "size": len(dados)}, f)
    _podar(pasta)
    return destino


def backups(provider: str) -> List[Path]:
    pasta = pasta_backups() / "integrations" / provider
    if not pasta.is_dir():
        return []
    return sorted(pasta.glob("*.bak"))


def _podar(pasta: Path) -> None:
    for velho in sorted(pasta.glob("*.bak"))[:-MANTER_BACKUPS]:
        for p in (velho, velho.with_suffix(".json")):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


class _TravaDeIntegracoes:
    """Uma mudança de integração por vez (duas CLIs ao mesmo tempo não se atropelam)."""

    def __enter__(self):
        pasta = pasta_backups()
        garantir_pasta_privada(pasta)
        self.fd = os.open(str(pasta / ".integrations.lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise ErroIntegracao("outra mudança de integração está em andamento; espere ela terminar") from None
        return self

    def __exit__(self, *_):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


# ---------- operações ----------
@dataclass
class Resultado:
    provider: str
    acao: str
    arquivo: str
    mudou: bool
    dry_run: bool
    diff: str = ""
    backup: Optional[str] = None
    analise: Optional[dict] = None
    nota: str = ""

    def como_dict(self) -> dict:
        return {"provider": self.provider, "action": self.acao, "file": self.arquivo, "changed": self.mudou,
                "dry_run": self.dry_run, "diff": self.diff, "backup": self.backup, "check": self.analise, "note": self.nota}


def _aplicar(alvo: Alvo, leitura: Leitura, novo: dict, acao: str, dry_run: bool, conferir) -> Resultado:
    texto = serializar(novo, leitura)
    mudou = (novo != leitura.dados) or (not leitura.existe and acao == "install")
    r = Resultado(alvo.provider, acao, curto(leitura.arquivo), mudou, dry_run, diferenca(leitura, texto) if mudou else "")
    if not mudou or dry_run:
        return r
    if not os.access(leitura.arquivo if leitura.existe else leitura.arquivo.parent, os.W_OK):
        raise ErroIntegracao(f"sem permissão para gravar {curto(leitura.arquivo)} (somente leitura?); nada foi feito")
    if leitura.existe:
        try:
            r.backup = curto(fazer_backup(alvo.provider, leitura.arquivo, leitura.arquivo.read_bytes()))
        except OSError as e:
            raise ErroIntegracao(f"não consegui fazer o backup ({type(e).__name__}); sem backup, nada é gravado") from None
    try:
        gravar_atomico(leitura.arquivo, texto.encode("utf-8"), modo=0o600, antes=leitura.antes)
    except ArquivoMudou as e:
        raise ErroIntegracao(str(e)) from None
    except PermissionError:
        raise ErroIntegracao(f"sem permissão para gravar {curto(leitura.arquivo)}; o arquivo ficou como estava") from None
    except OSError as e:
        raise ErroIntegracao(f"não consegui gravar {curto(leitura.arquivo)} ({type(e).__name__}); o arquivo ficou como estava") from None
    # confere o que ficou no disco; se não bate, volta o que estava
    try:
        depois = ler(alvo)
        ok = conferir(depois)
    except ErroIntegracao:
        ok = False
    if not ok:
        if leitura.existe:
            gravar_atomico(leitura.arquivo, leitura.texto.encode("utf-8"))
        else:
            leitura.arquivo.unlink()
        raise ErroIntegracao(f"{curto(leitura.arquivo)} não ficou como esperado depois de gravar; o arquivo original foi restaurado")
    log.info("integração %s: %s em %s", alvo.provider, acao, curto(leitura.arquivo))
    return r


OPENCODE = "opencode"  # não usa hooks nem tem arquivo editado pelo Ombro: ver ombro/integracao_opencode.py
TODOS = ("claude", "codex", "cursor", OPENCODE)


def analise_de(provider: str, python: Optional[str] = None) -> dict:
    if provider == OPENCODE:
        return integracao_opencode.analisar()
    return analisar(ALVOS[provider], python).como_dict()


def instalar(provider: str, dry_run: bool = False, python: Optional[str] = None, nova: bool = False) -> Resultado:
    if provider == OPENCODE:
        return integracao_opencode.instalar(dry_run, nova)
    if nova:
        raise ErroIntegracao("--new-origin vale só para o opencode")
    alvo = ALVOS[provider]
    if not HOOK.exists():
        raise ErroIntegracao(f"{curto(HOOK)} não existe; rode a partir da pasta do Ombro")
    with _TravaDeIntegracoes():
        leitura = ler(alvo)
        python = python or python_em_uso(alvo, leitura) or sys.executable
        novo = planejar_instalacao(alvo, leitura, python)
        r = _aplicar(alvo, leitura, novo, "install", dry_run,
                     lambda d: analisar(alvo, python, d).estado == "configured")
    r.analise = analisar(alvo, python).como_dict() if not dry_run else None
    r.nota = alvo.nota if r.mudou and not dry_run else ""
    return r


def remover(provider: str, dry_run: bool = False) -> Resultado:
    if provider == OPENCODE:
        return integracao_opencode.remover(dry_run)
    alvo = ALVOS[provider]
    with _TravaDeIntegracoes():
        try:
            leitura = ler(alvo)
        except ErroIntegracao:
            if not alvo.arquivo.exists():
                return Resultado(provider, "uninstall", curto(alvo.arquivo), False, dry_run)
            raise
        a = analisar(alvo, leitura=leitura)
        novo = planejar_remocao(alvo, leitura)
        r = _aplicar(alvo, leitura, novo, "uninstall", dry_run,
                     lambda d: not (set(analisar(alvo, leitura=d).presentes) | set(analisar(alvo, leitura=d).desatualizados)))
    if a.ambiguos:
        r.nota = ("Ficaram entradas que citam o ombro_hook.py de um jeito que o Ombro não reconhece "
                  f"({', '.join(sorted(set(a.ambiguos)))}); remova-as à mão se forem do Ombro.")
    return r


def restaurar(provider: str, dry_run: bool = False) -> Resultado:
    """Volta o arquivo do agente para o último backup. O estado atual vira um backup antes (dá para desfazer)."""
    if provider == OPENCODE:
        return integracao_opencode.restaurar(dry_run)
    alvo = ALVOS[provider]
    lista = backups(provider)
    if not lista:
        raise ErroIntegracao(f"não há backup do {alvo.nome} em {curto(pasta_backups() / 'integrations' / provider)}")
    ultimo = lista[-1]
    try:
        meta = json.loads(ultimo.with_suffix(".json").read_text(encoding="utf-8"))
        destino = Path(meta["file"])
    except (OSError, ValueError, KeyError):
        raise ErroIntegracao(f"o backup {ultimo.name} não diz de qual arquivo é; nada foi feito") from None
    if destino_real(alvo.arquivo) != destino:
        raise ErroIntegracao(f"o backup {ultimo.name} é de {curto(destino)}, não de {curto(alvo.arquivo)}; nada foi feito")
    dados = ultimo.read_bytes()
    try:
        json.loads(dados.decode("utf-8") or "{}")
    except ValueError:
        raise ErroIntegracao(f"o backup {ultimo.name} não é um JSON válido; nada foi feito") from None
    with _TravaDeIntegracoes():
        antes = assinatura(destino)
        atual = destino.read_bytes() if destino.exists() else None
        r = Resultado(provider, "rollback", curto(destino), atual != dados, dry_run, backup=curto(ultimo))
        if atual is not None:
            r.diff = "".join(difflib.unified_diff(atual.decode("utf-8", "replace").splitlines(True),
                                                  dados.decode("utf-8", "replace").splitlines(True),
                                                  fromfile=f"{curto(destino)} (agora)", tofile=f"{ultimo.name}"))
        if dry_run or not r.mudou:
            return r
        if atual is not None:
            fazer_backup(provider, destino, atual)
        try:
            gravar_atomico(destino, dados, modo=0o600, antes=antes)
        except ArquivoMudou as e:
            raise ErroIntegracao(str(e)) from None
        ultimo.unlink()  # usado: o backup do estado anterior fica no lugar dele
        ultimo.with_suffix(".json").unlink()
    log.info("integração %s: arquivo restaurado do backup", provider)
    return r


# ---------- a CLI ----------
def _imprimir_analise(a: dict) -> None:
    nomes = {"configured": "configurada", "partial": "parcial", "not_configured": "não configurada",
             "conflict": "conflito", "invalid": "arquivo inválido"}
    if a["provider"] == OPENCODE:
        print(f"OpenCode: {nomes[a['state']]}" + (" (esperando o plugin aparecer)" if a["state"] == "partial" else ""))
        print(f"  arquivo:      {a['file']}{'' if a['existe'] else ' (não existe)'}")
        print(f"  instalado:    {'sim' if a['instalado'] else 'não encontrado'}")
        if a["trecho"]:
            print("  trecho para a configuração do OpenCode:\n" + a["trecho"])
        return
    print(f"{ALVOS[a['provider']].nome}: {nomes[a['state']]}")
    print(f"  arquivo:      {a['file']}{'' if a['existe'] else ' (não existe)'}")
    print(f"  instalado:    {'sim' if a['instalado'] else 'não encontrado'}")
    faltam = [e for e in a["eventos"] if e not in a["presentes"]]
    print(f"  eventos:      {len(a['presentes'])}/{len(a['eventos'])}" + (f" (faltam: {', '.join(faltam)})" if faltam else ""))
    if a["desatualizados"]:
        print(f"  desatualizados: {', '.join(sorted(set(a['desatualizados'])))} (outro caminho ou python; reinstale)")
    if a["ambiguos"]:
        print(f"  ambíguos:     {', '.join(sorted(set(a['ambiguos'])))} (citam ombro_hook.py; o Ombro não mexe neles)")
    if a["sobrando"]:
        print(f"  sobrando:     {', '.join(sorted(set(a['sobrando'])))} (eventos que o Ombro não usa mais; reinstale)")
    if not a["hook_existe"]:
        print("  aviso:        o ombro_hook.py não existe nesta pasta")
    if a["erro"]:
        print(f"  erro:         {a['erro']}")


def comando(args) -> int:
    acao, provider = args.acao, args.provider
    if acao in ("check", "install", "uninstall", "rollback") and provider is None:
        print(f"integrations {acao} precisa do agente: claude, codex, cursor ou opencode", file=sys.stderr)
        return 2
    try:
        if acao in ("list", "check"):
            alvos = [provider] if provider else list(TODOS)
            analises = [analise_de(p, args.python) for p in alvos]
            if args.json:
                print(json.dumps({"integrations": analises}, ensure_ascii=False, indent=2))
            else:
                for i, a in enumerate(analises):
                    if i:
                        print()
                    _imprimir_analise(a)
            return 0 if all(a["state"] in ("configured", "not_configured") for a in analises) or acao == "list" else 1
        fn = {"install": lambda: instalar(provider, args.dry_run, args.python, getattr(args, "new_origin", False)),
              "uninstall": lambda: remover(provider, args.dry_run),
              "rollback": lambda: restaurar(provider, args.dry_run)}[acao]
        r = fn()
    except ErroIntegracao as e:
        if args.json:
            print(json.dumps({"ok": False, "provider": provider, "action": acao, "error": str(e)}, ensure_ascii=False))
        else:
            print(f"erro: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, **r.como_dict()}, ensure_ascii=False, indent=2))
        return 0
    if provider == OPENCODE:
        if not r.mudou:
            print(f"OpenCode: nada a mudar em {r.arquivo}")
        else:
            print(f"OpenCode: {'o que mudaria (nada foi gravado)' if r.dry_run else 'feito'} em {r.arquivo}")
        if r.diff:
            print("\ntrecho para a configuração do OpenCode:\n" + r.diff)
        if r.nota:
            print(f"  {r.nota}")
        return 0
    nome = ALVOS[provider].nome
    verbo = {"install": "instalada", "uninstall": "removida", "rollback": "restaurada do backup"}[acao]
    if not r.mudou:
        print(f"{nome}: nada a mudar em {r.arquivo}" + (" (integração já está assim)" if acao != "rollback" else ""))
    elif r.dry_run:
        print(f"{nome}: o que mudaria em {r.arquivo} (nada foi gravado):\n")
        print(r.diff or "(arquivo novo)")
    else:
        print(f"{nome}: integração {verbo} em {r.arquivo}")
        if r.backup:
            print(f"  backup: {r.backup}" + ("  (desfazer: python3 -m ombro integrations rollback " + provider + ")"
                                             if acao != "rollback" else ""))
    if r.nota:
        print(f"  {r.nota}")
    return 0
