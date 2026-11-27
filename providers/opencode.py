"""Provider do OpenCode, pelo plugin de servidor do Ombro (integrations/opencode/), que você liga no OpenCode.

O plugin deixa duas coisas na pasta do Ombro, e só metadados (ids, horários, tipo do evento, nome curto da
pasta): os eventos na caixa de sempre (eventos/) e um pulso por processo em opencode/live/. O Ombro não fala
com o servidor do OpenCode nem lê a configuração, o banco, as conversas ou as credenciais dele.

IDENTIDADE. Uma sessão é "<origem>:<ses_…>". A origem é um registro opaco do Ombro (opencode_registro.py), ligado
na primeira conexão a duas provas da instância local: a ÂNCORA (identidade física do arquivo do banco que aquele
processo tem aberto: inode e instante de criação) e a MARCA gravada no banco. A marca viaja numa cópia; a âncora
não. Só entra o que vem com as duas. Processo, porta, projeto e "boot" não fazem parte da identidade. O mesmo
evento chega por uma instância do plugin por projeto: conta uma vez por origem.

ORDEM. Eventos de sessão têm sequência durável do OpenCode (`seq`): vale a maior, não a ordem de chegada nem o
relógio. Os voláteis (permissão, pergunta) e as fotos usam a ordem do produtor (`boot`, `n`), que só compara
dentro do mesmo boot. Entre boots só há ordem quando o anterior já não vive e o outro vive; um boot encerrado não
mexe no que um vivo sustenta, e dois vivos não disputam eventos voláteis. O que já foi visto, respondido ou
apagado fica lembrado em disco, e uma reprise não muda nada. Nada é publicado antes de essa lembrança estar
gravada: sem conseguir gravar, os eventos esperam na caixa.

CONFIRMAÇÃO. Estado atual só vem de um produtor vivo (pulso recente, processo existente, vínculo conferido) com
horário válido da fonte. Fora disso a sessão fica desconhecida; nada é preenchido com o relógio do Ombro e nada
desconhecido vira conclusão, erro ou aprovação. O OpenCode não informa, por este caminho, tokens, custo nem
limites que o Ombro possa provar.

LIMITES. Máquinas ou discos clonados inteiros preservam inode e criação, e regravar o conteúdo por cima do mesmo
arquivo mantém a âncora: são indistinguíveis daqui.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from config import pasta_ombro
from core.caixa import EventoLocal
from core.models import Activity, AgentSession, Attention, ProviderSnapshot
from core.providers import EventosNaoGravados, ProviderAdapter, ProviderCapabilities
from core.rastreador import TTL_SESSAO
from logging_config import SoQuandoMuda, logger

from . import opencode_registro as registro
from .opencode_registro import ANCORA, ORIGEM

log = logger("providers")

PROVIDER_ID = "opencode"
VERSAO_MINIMA = "2.4.2"
PULSO_VALIDO = 75  # segundos: o plugin escreve o pulso a cada 20 s (no máximo 30)
PULSO_ESQUECIDO = 3600  # segundos: um pulso parado há mais que isso é de um processo que não volta
FOLGA_FUTURO = 120_000  # ms: um horário da fonte além disso no futuro não é válido
MEMORIA_VISTOS = 8192
MAX_CURSORES = 2000
CURSOR_VALE = 7 * 24 * 3_600_000  # ms: bem além das 24 h que um evento espera na caixa
MAX_PULSO = 4096  # bytes
MAX_PULSOS = 64
MAX_PENDENTES = 64  # pedidos abertos (e respondidos) lembrados por sessão
EXECUTAVEIS = ("opencode",)
APPS = ("/Applications/OpenCode.app",)

_ID = re.compile(r"[A-Za-z0-9._-]{1,80}")

_TERMINAIS = {  # evento -> atenção depois dele (a execução acabou, com confirmação)
    "session.execution.succeeded": Attention.NONE,
    "session.execution.failed": Attention.ERROR,
    "session.execution.interrupted": Attention.NONE,
}
_COMECOS = ("session.execution.started", "session.retry.scheduled")
_ATIVIDADE = ("session.created", *_COMECOS, *_TERMINAIS)
_PEDIDOS = {"permission.asked": "p", "form.created": "f"}
_RESPOSTAS = ("permission.replied", "form.replied", "form.cancelled")
_CONHECIDOS = frozenset((*_ATIVIDADE, *_PEDIDOS, *_RESPOSTAS, "session.deleted", "ombro.removed", "ombro.snapshot",
                         "ombro.gap"))
_VIVOS = ("ready", "collecting", "error")  # estados do plugin que estão coletando


def pasta_opencode() -> Path:
    return pasta_ombro() / "opencode"


def processo_vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # sinal 0: só pergunta se existe
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return False
    return True


def ferramenta_instalada() -> bool:
    return any(shutil.which(e) for e in EXECUTAVEIS) or any(Path(a).exists() for a in APPS)


@dataclass(frozen=True)
class Pulso:
    origem: str
    pid: int
    boot: str
    ns: str
    ancora: str
    estado: str  # ready, collecting, incompatible, unverified, error, no_origin, stopped
    versao: Optional[str]
    parcial: bool
    vivo: bool  # recente, o processo existe e o plugin não disse que parou

    @property
    def prova(self) -> Optional[tuple]:
        """(âncora, marca) quando as duas vieram bem formadas."""
        return (self.ancora, self.ns) if ANCORA.fullmatch(self.ancora) and _ID.fullmatch(self.ns) else None


@dataclass
class _Evento:
    """Um evento da caixa já conferido campo a campo."""
    origem: str
    sid: Optional[str]
    nome: str
    prova: tuple
    boot: str
    n: int
    chave: str  # para não aplicar duas vezes
    seq: Optional[int]
    pedido: Optional[str]
    quando: Optional[datetime]  # o horário da FONTE, quando válido
    recebido: datetime  # quando o plugin observou (idade na caixa)
    projeto: Optional[str]
    foto: Optional[dict]


@dataclass
class _Sessao:
    origem: str
    activity: Activity = Activity.UNKNOWN
    base: Attention = Attention.UNKNOWN  # a atenção quando não há pedido aberto
    pedidos: Dict[str, str] = field(default_factory=dict)  # id do pedido -> "p" (permissão) ou "f" (pergunta)
    boot: Optional[str] = None  # o produtor vivo que sustenta o estado; None = nada o sustenta
    projeto: Optional[str] = None
    inicio: Optional[datetime] = None
    desde: Optional[datetime] = None
    visto: Optional[datetime] = None  # o horário mais novo vindo da fonte
    observado: Optional[datetime] = None  # a última notícia (só para saber quando esquecer)
    recuperada: bool = False  # a atenção veio de uma foto, não de um pedido visto acontecer
    por_evento: bool = False  # o produtor em `boot` mostrou um evento (e não só uma releitura)

    @property
    def atencao(self) -> Attention:
        tipos = set(self.pedidos.values())
        if "p" in tipos:
            return Attention.WAITING_APPROVAL
        if "f" in tipos:
            return Attention.WAITING_USER
        return self.base


def _ms_valido(ms, agora_ms: float) -> Optional[datetime]:
    if type(ms) is not int or ms <= 0 or ms > agora_ms + FOLGA_FUTURO:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError):
        return None


def _inteiro(v) -> Optional[int]:
    return v if type(v) is int and v >= 0 else None


class OpenCodeProvider(ProviderAdapter):
    provider_id = PROVIDER_ID
    display_name = "OpenCode"
    capabilities = ProviderCapabilities(sessions=True, attention=True, events=True)

    def __init__(self, pasta: Optional[Path] = None, relogio: Callable[[], datetime] = datetime.now,
                 pid_vivo: Callable[[int], bool] = processo_vivo, instalado: Callable[[], bool] = ferramenta_instalada,
                 monotonic: Callable[[], float] = time.monotonic):
        self.pasta = Path(pasta) if pasta is not None else pasta_opencode()
        self._relogio, self._pid_vivo, self._instalado, self._monotonic = relogio, pid_vivo, instalado, monotonic
        self._sessoes: "OrderedDict[str, _Sessao]" = OrderedDict()
        self._cursores: Dict[str, dict] = {}  # sessão -> o que se lembra dela mesmo depois de sair (reprises)
        self._vistos: "OrderedDict[str, None]" = OrderedDict()
        self._carregado = False
        self._ilegivel = False  # o registro ou o estado não se leem: nada é aplicado
        self._sem_gravar = False  # o estado não pôde ser gravado: os eventos esperam na caixa
        self._produtores: set = set()  # (origem, boot) vivos e vinculados, na aplicação em curso
        self._sem_confirmacao = 0
        self._deteccao: Optional[tuple] = None
        self._avisos = SoQuandoMuda(log)
        self.ultimo_evento: Optional[datetime] = None

    # ---------- o que o plugin deixou ----------
    def pulsos(self) -> list:
        agora = self._relogio().timestamp() * 1000
        achados = []
        try:
            todos = [(a.stat().st_mtime, a) for a in (self.pasta / "live").glob("*.json")]
        except OSError:
            return []
        for _, arq in sorted(todos, reverse=True)[:MAX_PULSOS]:  # os mais novos primeiro
            try:
                if arq.stat().st_size > MAX_PULSO:
                    continue
                d = json.loads(arq.read_text(encoding="utf-8"))
                origem, pid, ts = d["origin"], d["pid"], d["ts"]
                if d.get("v") != 1 or not isinstance(origem, str) or type(pid) is not int or pid <= 0 or type(ts) is not int:
                    continue
                estado = str(d.get("state"))
                vivo = (estado != "stopped" and -FOLGA_FUTURO <= agora - ts <= PULSO_VALIDO * 1000
                        and self._pid_vivo(pid))
                versao = d.get("version")
                achados.append(Pulso(origem, pid, str(d.get("boot"))[:64], str(d.get("ns") or "")[:64],
                                     str(d.get("anchor") or "")[:64], estado,
                                     versao if isinstance(versao, str) and _ID.fullmatch(versao) else None,
                                     d.get("partial") is True, vivo))
            except (OSError, ValueError, KeyError, TypeError):
                continue  # um pulso ilegível não derruba a coleta
        return achados

    def _limpar_pulsos(self) -> None:
        limite = self._relogio().timestamp() - PULSO_ESQUECIDO
        try:
            for arq in (self.pasta / "live").glob("*.json"):
                if arq.stat().st_mtime < limite:
                    arq.unlink(missing_ok=True)
        except OSError:
            log.debug("não consegui limpar pulsos antigos do OpenCode")

    # ---------- o que fica em disco ----------
    def _carregar(self) -> None:
        if self._carregado:
            return
        estado = registro.ler_estado(self.pasta)  # levanta Ilegivel
        self._cursores = {k: v for k, v in estado["sessions"].items() if isinstance(v, dict)}
        self._vistos = OrderedDict((k, None) for k in estado["seen"] if isinstance(k, str))
        self._carregado = True

    def _salvar(self) -> bool:
        agora = self._relogio().timestamp() * 1000
        recentes = sorted(((c.get("touched", 0), k) for k, c in self._cursores.items()), reverse=True)
        manter = {k for t, k in recentes[:MAX_CURSORES] if agora - t <= CURSOR_VALE} | set(self._sessoes)
        self._cursores = {k: v for k, v in self._cursores.items() if k in manter}
        try:  # o ponto de confirmação: sem isto gravado, nada avança
            registro.gravar(self.pasta / "state.json", {"v": registro.VERSAO_ESTADO, "sessions": self._cursores,
                                                        "seen": list(self._vistos)})
        except OSError as e:
            self._avisos.registrar("estado", 30, "não consegui gravar o estado do OpenCode (%s); os eventos esperam",
                                   type(e).__name__)
            return False
        self._avisos.resolver("estado")
        try:  # a lista de sessões conhecidas só ajuda o plugin a notar remoções perdidas
            por_origem: Dict[str, list] = {}
            for sid, s in self._sessoes.items():
                por_origem.setdefault(s.origem, []).append(sid[len(s.origem) + 1:])
            for origem in {c.split(":", 1)[0] for c in self._cursores}:
                registro.gravar(self.pasta / "known" / f"{origem}.json", {"sessions": por_origem.get(origem, [])})
        except OSError:
            log.debug("não consegui gravar a lista de sessões conhecidas do OpenCode")
        return True

    # ---------- o vínculo ----------
    @staticmethod
    def _em_conflito(pulsos: Iterable[Pulso]) -> set:
        """Origens com produtores vivos que não provam ser a mesma instância."""
        provas: Dict[str, set] = {}
        for p in pulsos:
            if p.vivo and p.estado in _VIVOS:
                provas.setdefault(p.origem, set()).add(p.prova)
        return {origem for origem, vistas in provas.items() if len(vistas) > 1 or None in vistas}

    def _vincular(self, reg: dict, eventos: list, pulsos: list) -> dict:
        """{origem: (âncora, marca)} do que pode entrar agora. Liga as origens pendentes (e grava) antes disso."""
        provas: Dict[str, set] = {}
        for e in eventos:
            provas.setdefault(e.origem, set()).add(e.prova)
        for p in pulsos:
            if p.vivo and p.estado in _VIVOS:
                provas.setdefault(p.origem, set()).add(p.prova)
        mudou = False
        for origem, vistas in provas.items():
            o = reg["origins"].get(origem)
            if o is None or o["state"] != "pending" or None in vistas:
                continue
            if len(vistas) == 1:
                ancora, marca = next(iter(vistas))
                o.update(state="active", anchor=ancora, mark=marca,
                         connected_at=int(self._relogio().timestamp()))
            else:
                o["state"] = "ambiguous"
            mudou = True
        if mudou:
            try:
                registro.gravar_registro(self.pasta, reg)
            except OSError as e:
                self._avisos.registrar("registro", 30, "não consegui gravar o registro do OpenCode (%s); nada é "
                                       "aplicado", type(e).__name__)
                return {}
        suspensas = self._em_conflito(pulsos)
        return {origem: (o["anchor"], o["mark"]) for origem, o in reg["origins"].items()
                if o["state"] == "active" and origem not in suspensas}

    @staticmethod
    def _vivos(pulsos: list, vinculos: dict) -> set:
        return {(p.origem, p.boot) for p in pulsos
                if p.vivo and p.estado in _VIVOS and vinculos.get(p.origem) == p.prova}

    # ---------- eventos ----------
    def _conferir(self, ev: EventoLocal) -> Optional[_Evento]:
        x = ev.extra or {}
        origem, boot, nome = x.get("origin"), x.get("boot"), ev.event
        n, prova = _inteiro(x.get("n")), (x.get("anc"), x.get("ns"))
        if (nome not in _CONHECIDOS or not isinstance(origem, str) or not ORIGEM.fullmatch(origem) or n is None
                or not isinstance(boot, str) or not _ID.fullmatch(boot)
                or not (isinstance(prova[0], str) and ANCORA.fullmatch(prova[0]))
                or not (isinstance(prova[1], str) and _ID.fullmatch(prova[1]))):
            return None
        sid = None
        if nome != "ombro.gap":
            sid = ev.session
            if not sid.startswith(origem + ":") or not _ID.fullmatch(sid[len(origem) + 1:]):
                return None
        eid = x.get("id")
        if nome.startswith(("session.", "permission.", "form.")):
            if not isinstance(eid, str) or not _ID.fullmatch(eid):
                return None
            chave = f"{origem} {eid}"
        else:
            chave = f"{origem} {boot} {n} {nome} {sid or ''}"
        seq = x.get("seq")
        if seq is not None and _inteiro(seq) is None:
            return None
        pedido = x.get("req")
        if nome in _PEDIDOS or nome in _RESPOSTAS:
            if not isinstance(pedido, str) or not _ID.fullmatch(pedido):
                return None
        foto = x.get("snap")
        if nome == "ombro.snapshot" and not isinstance(foto, dict):
            return None
        quando = _ms_valido(x.get("at"), self._relogio().timestamp() * 1000)
        return _Evento(origem, sid, nome, prova, boot, n, chave, seq, pedido, quando, ev.quando, ev.project, foto)

    def receber(self, eventos: Iterable[EventoLocal]) -> int:
        eventos = [ev for ev in eventos if ev.provider == PROVIDER_ID]
        if not eventos or not self.pasta.is_dir():
            return 0
        with registro.trava(self.pasta):  # revogar não acontece no meio de uma aplicação
            try:
                self._carregar()
                reg = registro.ler_registro(self.pasta)
            except registro.Ilegivel:
                return 0
            bons = [e for e in (self._conferir(ev) for ev in eventos) if e is not None]
            pulsos = self.pulsos()
            vinculos = self._vincular(reg, bons, pulsos)
            self._produtores = vivos = self._vivos(pulsos, vinculos)
            antes = copy.deepcopy((self._sessoes, self._cursores, self._vistos, self._sem_confirmacao,
                                   self.ultimo_evento))
            n = 0
            # primeiro os produtores vivos (o que eles sustentam não é desfeito por um encerrado); dentro de cada boot,
            # a ordem do produtor. Os nomes dos boots não dizem nada sobre o tempo
            for e in sorted(bons, key=lambda e: ((e.origem, e.boot) not in vivos, e.boot, e.n,
                                                 e.seq if e.seq is not None else -1)):
                if vinculos.get(e.origem) != e.prova or e.chave in self._vistos:
                    continue  # de outra instância, de uma origem sem registro ou revogada; ou uma reprise
                self._vistos[e.chave] = None
                while len(self._vistos) > MEMORIA_VISTOS:
                    self._vistos.popitem(last=False)
                n += self._aplicar(e, (e.origem, e.boot) in vivos)
            if self._vistos != antes[2] and not self._salvar():
                (self._sessoes, self._cursores, self._vistos, self._sem_confirmacao, self.ultimo_evento) = antes
                self._sem_gravar = True
                raise EventosNaoGravados("o estado do OpenCode não pôde ser gravado")
            self._sem_gravar = False
            return n

    def _cursor(self, sid: str) -> dict:
        c = self._cursores.setdefault(sid, {})
        c["touched"] = int(self._relogio().timestamp() * 1000)
        return c

    def _depois(self, c: dict, chave: str, e: _Evento) -> bool:
        """`e` é comprovadamente posterior à marca `chave` do cursor? No mesmo boot, pela ordem do produtor. Entre
        boots, só quando o da marca já não vive e o de `e` vive."""
        marca = c.get(chave)
        if not marca:
            return True
        if marca[0] == e.boot:
            return e.n > marca[1]
        return (e.origem, marca[0]) not in self._produtores and (e.origem, e.boot) in self._produtores

    def _disputa(self, e: _Evento, vivo: bool) -> bool:
        """`e` tentaria mudar o que outro produtor vivo sustenta, sem ordem que o permita?"""
        s = self._sessoes.get(e.sid)
        if s is None or s.boot in (None, e.boot) or (e.origem, s.boot) not in self._produtores:
            return False
        if not vivo:
            return True  # um boot encerrado não apaga nem sobrescreve o que um vivo sustenta
        if e.seq is not None and e.nome.startswith("session."):
            return False  # a sequência durável ordena os dois
        return s.por_evento  # voláteis de dois vivos: sem ordem comprovada, vale o que já se viu acontecer

    def _confirmada(self, s: _Sessao) -> bool:
        """Um produtor que está no ar sustenta o estado desta sessão (com um evento ou uma leitura dele)?"""
        return s.boot is not None and (s.origem, s.boot) in self._produtores

    def _sessao(self, e: _Evento) -> _Sessao:
        s = self._sessoes.get(e.sid)
        if s is None:
            s = self._sessoes[e.sid] = _Sessao(e.origem)
        s.projeto = e.projeto or s.projeto
        s.observado = max(s.observado or e.recebido, e.recebido)
        return s

    def _aplicar(self, e: _Evento, vivo: bool) -> int:
        if e.nome == "ombro.gap":  # o plugin perdeu o fluxo: o que se sabia ao vivo daquela origem deixa de valer
            for s in self._sessoes.values():
                if s.origem == e.origem and s.boot == e.boot:
                    self._desconfirmar(s)
            return 1
        c = self._cursor(e.sid)
        if "deleted" in c and not self._recriada(c, e, vivo):
            return 0
        if self._disputa(e, vivo):
            if e.nome in _RESPOSTAS:  # que o pedido foi respondido continua sendo um fato: ele não volta
                self._lembrar_resposta(c, e.pedido)
            return 0
        if e.nome in ("session.deleted", "ombro.removed"):
            return self._apagar(c, e, vivo)
        if e.nome == "ombro.snapshot":
            return self._foto(c, e) if vivo else 0
        if e.nome in _PEDIDOS or e.nome in _RESPOSTAS:
            return self._pedido(c, e, vivo)
        return self._atividade(c, e, vivo)

    def _recriada(self, c: dict, e: _Evento, vivo: bool) -> bool:
        """O OpenCode ofereceu de novo uma sessão apagada? Só um "criada" ou uma foto, de um produtor vivo, posterior
        à remoção. Se sim, o id começa outra vida (sequência e pedidos do zero)."""
        morte = c["deleted"]
        if morte[0] == e.boot:
            prova = e.nome in ("session.created", "ombro.snapshot") and e.n > morte[1]
        else:
            prova = e.nome == "session.created"  # entre boots, só a criação (uma releitura pode ser anterior)
        if not prova or not vivo:
            return False
        tocado = c["touched"]
        c.clear()
        c["touched"] = tocado
        return True

    def _apagar(self, c: dict, e: _Evento, vivo: bool) -> int:
        if e.nome == "ombro.removed" and (not vivo or not self._depois(c, "act", e)):
            return 0  # a prova de ausência precisa ser atual
        s = self._sessoes.pop(e.sid, None)
        if s is not None and s.activity is Activity.WORKING:
            self._sem_confirmacao += 1
        tocado = c["touched"]
        c.clear()
        c.update(touched=tocado, deleted=[e.boot, e.n])
        return 1

    def _atividade(self, c: dict, e: _Evento, vivo: bool) -> int:
        if e.seq is not None:
            if e.seq <= c.get("seq", -1):
                return 0  # a sequência durável já passou deste ponto
        elif not self._depois(c, "act", e):
            return 0
        if e.seq is not None:
            c["seq"] = e.seq
        c["act"] = [e.boot, e.n]
        terminal = e.nome in _TERMINAIS
        if terminal or e.nome == "session.execution.started":
            c["cut"] = [e.boot, e.n]  # pedidos anteriores a isto são de uma execução passada
        s = self._sessao(e)
        antes = (s.activity, s.atencao)
        if not vivo or e.quando is None:  # algo aconteceu, mas a fonte não sustenta dizer como está agora
            self._desconfirmar(s, forcar=True)
            return 1
        if e.nome == "session.created":
            s.activity, s.base, s.inicio = Activity.IDLE, Attention.NONE, s.inicio or e.quando
        elif terminal:
            s.activity, s.base = Activity.IDLE, _TERMINAIS[e.nome]
            s.pedidos.clear()
        else:
            if e.nome == "session.execution.started":
                s.pedidos.clear()
                s.base = Attention.NONE
            elif s.activity is not Activity.WORKING:
                s.base = Attention.UNKNOWN
            if e.nome != "session.execution.started" and not self._confirmada(s):
                s.pedidos.clear()  # os pedidos de antes não foram confirmados por quem está no ar
            s.activity = Activity.WORKING
        s.boot, s.recuperada, s.por_evento = e.boot, False, True
        self._marcar(s, e, antes)
        return 1

    @staticmethod
    def _lembrar_resposta(c: dict, pedido: str) -> None:
        respondidos = c.setdefault("done", [])
        if pedido not in respondidos:
            respondidos.append(pedido)
            del respondidos[:-MAX_PENDENTES]

    def _marcar(self, s: _Sessao, e: _Evento, antes: tuple) -> None:
        if e.quando is not None:
            s.visto = max(s.visto or e.quando, e.quando)
            self.ultimo_evento = max(self.ultimo_evento or e.quando, e.quando)
        if (s.activity, s.atencao) != antes:
            s.desde = e.quando

    def _pedido(self, c: dict, e: _Evento, vivo: bool) -> int:
        respondidos = c.setdefault("done", [])
        if e.nome in _RESPOSTAS:
            self._lembrar_resposta(c, e.pedido)  # respondido é um fato, venha de quem vier: o pedido não volta
            s = self._sessoes.get(e.sid)
            if s is None or not vivo:
                return 1
            antes = (s.activity, s.atencao)
            # sem outro pedido conhecido, volta a atenção de base: "nada" só se os eventos a confirmaram
            if s.pedidos.pop(e.pedido, None) is None and not s.pedidos:
                s.base = Attention.UNKNOWN  # resposta a um pedido que não vimos: pode haver outros
            s.observado = max(s.observado or e.recebido, e.recebido)
            self._marcar(s, e, antes)
            return 1
        if not vivo or e.pedido in respondidos or not self._depois(c, "cut", e):
            return 0
        s = self._sessao(e)
        antes = (s.activity, s.atencao)
        if e.quando is None:
            s.base = Attention.UNKNOWN
        elif len(s.pedidos) < MAX_PENDENTES:
            if not self._confirmada(s):  # um pedido de agora não traz de volta o estado de antes
                self._desconfirmar(s, forcar=True)
            s.pedidos[e.pedido] = _PEDIDOS[e.nome]
            s.boot, s.recuperada, s.por_evento = e.boot, False, True
        self._marcar(s, e, antes)
        return 1

    def _foto(self, c: dict, e: _Evento) -> int:
        """Uma sessão numa leitura do plugin (recuperação). `n` é de quando a leitura foi pedida: o que veio depois
        vale mais. Só acrescenta o que a leitura prova."""
        agora = self._relogio().timestamp() * 1000
        criada, atualizada = (_ms_valido(e.foto.get(k), agora) for k in ("created", "updated"))
        busy, idle = (_ms_valido(e.foto.get(k), agora) for k in ("busy", "idle"))
        pendentes = [p for p in (e.foto.get("pending") or []) if isinstance(p, str) and _ID.fullmatch(p)]
        s = self._sessao(e)
        s.inicio = s.inicio or criada
        if atualizada is not None:
            s.visto = max(s.visto or atualizada, atualizada)
        antes = (s.activity, s.atencao)
        parada = busy is not None and idle is not None and idle >= busy
        if parada and self._depois(c, "act", e):
            if not self._confirmada(s):  # prova que está parada agora, não como terminou o que se sabia antes
                self._desconfirmar(s, forcar=True)
            if s.activity is Activity.WORKING:
                self._sem_confirmacao += 1  # o fim se perdeu: não é um "concluiu agora"
            if s.activity is not Activity.IDLE:
                s.activity, s.base, s.desde = Activity.IDLE, Attention.UNKNOWN, idle
                s.pedidos.clear()
                s.recuperada = True
            if s.boot != e.boot:
                s.por_evento = False
            s.boot = e.boot
            c["act"] = c["cut"] = [e.boot, e.n]
        elif (not parada and s.activity is Activity.IDLE and busy is not None and s.desde is not None and busy > s.desde
              and self._depois(c, "act", e)):
            self._desconfirmar(s, forcar=True)  # começou algo que não vimos: não dá para dizer que segue parada
        respondidos = c.get("done", [])
        for pedido in pendentes[:MAX_PENDENTES]:  # um pedido aberto no momento da leitura é um fato
            if pedido not in respondidos and pedido not in s.pedidos and (parada or self._depois(c, "cut", e)):
                if not self._confirmada(s):
                    self._desconfirmar(s, forcar=True)
                s.pedidos[pedido] = "p"
                if s.boot != e.boot:
                    s.por_evento = False
                s.boot, s.recuperada = e.boot, True
        if (s.activity, s.atencao) != antes and s.activity is not Activity.IDLE:
            s.desde = None  # a recuperação não inventa horário
        return 1

    def _desconfirmar(self, s: _Sessao, forcar: bool = False) -> None:
        """O que dependia de uma fonte ao vivo vira desconhecido. Sem `forcar`, um fim e um erro já confirmados ficam."""
        if s.activity is Activity.WORKING:
            self._sem_confirmacao += 1
        if forcar or s.activity is Activity.WORKING or s.pedidos:
            s.activity, s.base, s.desde = Activity.UNKNOWN, Attention.UNKNOWN, None
            s.pedidos.clear()
        s.boot = None

    # ---------- recuperação e coleta ----------
    def restaurar(self, sessoes: Iterable[AgentSession]) -> None:
        """Sessões que o banco lembrava: desconhecidas até o plugin confirmar. Pede a ele uma leitura nova."""
        for a in sessoes:
            origem = a.session_id.split(":", 1)[0]
            if a.provider_id == PROVIDER_ID and ORIGEM.fullmatch(origem) and a.session_id not in self._sessoes:
                self._sessoes[a.session_id] = _Sessao(origem, inicio=a.started_at, visto=a.updated_at,
                                                      projeto=a.project, observado=self._relogio())
        self.pedir_foto()

    def pedir_foto(self) -> None:
        """Toca opencode/resync: o plugin vê no próximo pulso e lê as sessões de novo. Sem pasta, nada."""
        if not self.pasta.is_dir():
            return
        try:
            (self.pasta / "resync").touch(mode=0o600)
            os.utime(self.pasta / "resync")
        except OSError:
            log.debug("não consegui pedir uma leitura nova ao plugin do OpenCode")

    def _ferramenta(self) -> bool:
        agora = self._monotonic()
        if self._deteccao is None or agora - self._deteccao[0] > 60:
            self._deteccao = (agora, self._instalado())
        return self._deteccao[1]

    def collect(self) -> ProviderSnapshot:
        if not self.pasta.is_dir():
            return self._retrato(registro.registro_vazio(), [], {})
        with registro.trava(self.pasta):
            self._limpar_pulsos()
            pulsos = self.pulsos()
            try:
                self._carregar()
                reg = registro.ler_registro(self.pasta)
                self._ilegivel = False
            except registro.Ilegivel:
                self._ilegivel = True
                reg = registro.registro_vazio()
            vinculos = {} if self._ilegivel else self._vincular(reg, [], pulsos)
            self._produtores = self._vivos(pulsos, vinculos)
            return self._retrato(reg, pulsos, vinculos)

    def _retrato(self, reg: dict, pulsos: list, vinculos: dict) -> ProviderSnapshot:
        agora = self._relogio()
        vivos = self._vivos(pulsos, vinculos)
        ativas = {o for o, d in reg["origins"].items() if d["state"] == "active"}
        saiu = False
        for sid, s in list(self._sessoes.items()):
            if not self._ilegivel and s.origem not in ativas:  # revogada (ou sem registro): sai, sem "concluir"
                if s.activity is Activity.WORKING:
                    self._sem_confirmacao += 1
                del self._sessoes[sid]
                saiu = True
                continue
            if self._ilegivel or (s.boot is not None and (s.origem, s.boot) not in vivos):
                self._desconfirmar(s)
            em_curso = s.boot is not None and (s.activity is Activity.WORKING or s.pedidos)
            referencia = s.visto or s.observado
            if not em_curso and referencia is not None and agora - referencia > TTL_SESSAO:
                del self._sessoes[sid]  # abandonada: sai da lista (o OpenCode guarda o histórico dele)
        if saiu:
            self._salvar()
        integracao, condicao, detalhe, versao = self._situacao(reg, pulsos, vinculos)
        no_ar = {origem for origem, _ in vivos}  # por origem: uma pode cair com as outras no ar
        sessoes = tuple(self._publicada(sid, s, s.origem in no_ar) for sid, s in self._sessoes.items())
        return ProviderSnapshot(PROVIDER_ID, agora, sessions=sessoes, integration=integracao, condition=condicao,
                                condition_detail=detalhe, version=versao, unconfirmed=self._sem_confirmacao)

    @staticmethod
    def _publicada(sid: str, s: _Sessao, no_ar: bool) -> AgentSession:
        """Fora do ar: o último estado conhecido, marcado como tal. No ar, mas sem um evento ou uma leitura de um boot
        vivo (que já tiraria `boot` de None): desconhecida; o pulso confirma a origem, não cada sessão. O histórico
        fica guardado e volta a aparecer se a origem cair de novo sem ter confirmado nada."""
        sem_prova = no_ar and s.boot is None
        return AgentSession(
            PROVIDER_ID, sid, Activity.UNKNOWN if sem_prova else s.activity,
            Attention.UNKNOWN if sem_prova else s.atencao, project=s.projeto, started_at=s.inicio,
            status_since=None if sem_prova else s.desde, updated_at=s.visto,
            recovered=s.recuperada and bool(s.pedidos) and not sem_prova, disconnected=not no_ar)

    def _situacao(self, reg: dict, pulsos: list, vinculos: dict) -> tuple:
        """(integração, condição, detalhe, versão) para o snapshot."""
        vivos = [p for p in pulsos if p.vivo]
        versao = next((p.versao for p in vivos if p.versao), None)
        if self._ilegivel:
            return ("ok", "conflict", "o registro ou o estado do OpenCode no Ombro não se leem: nada é aplicado "
                    "(confira a pasta opencode/ do Ombro)", versao)
        origens = reg["origins"]
        validas = {o for o, d in origens.items() if d["state"] != "revoked"}
        if not validas:
            soltos = [p for p in vivos if p.estado != "no_origin"]
            if soltos or any(p.estado == "no_origin" for p in vivos):
                return ("not_configured", "unregistered", "há um plugin ativo com uma origem sem registro (ou revogada): "
                        "rode `ombro integrations install opencode` e troque o trecho", versao)
            return ("not_configured" if self._ferramenta() else "not_installed"), None, None, None
        meus = [p for p in vivos if p.origem in validas]
        antigos = [p for p in meus if p.estado == "incompatible"]
        if antigos and len(antigos) == len(meus):
            v = antigos[0].versao
            return ("ok", "incompatible", f"OpenCode {v or 'de versão desconhecida'} não tem o que o Ombro precisa: "
                    f"atualize à mão para a {VERSAO_MINIMA} ou mais nova", v)
        suspensas = self._em_conflito(pulsos)
        ruins = [o for o in sorted(validas) if origens[o]["state"] == "ambiguous" or o in suspensas
                 or any(p.origem == o and p.estado in _VIVOS and origens[o]["state"] == "active"
                        and p.prova != (origens[o]["anchor"], origens[o]["mark"]) for p in meus)]
        if ruins:
            return ("ok", "conflict", "a origem apareceu em mais de uma instância do OpenCode (uma cópia, outro banco ou "
                    "o banco trocado) e o Ombro não mistura: registre uma origem nova para essa instalação", versao)
        if any(p.estado == "unverified" for p in meus):
            return ("ok", "unverified", "o plugin não conseguiu provar qual banco este OpenCode usa: nada é coletado",
                    versao)
        if self._sem_gravar:
            return ("ok", "partial", "o Ombro não conseguiu gravar o estado do OpenCode: os eventos esperam e nada "
                    "avança (confira o espaço e a permissão da pasta do Ombro)", versao)
        coletando = [p for p in meus if p.estado in _VIVOS]
        if coletando:
            if any(p.estado == "collecting" for p in coletando):
                return "ok", "collecting", "lendo as sessões abertas", versao
            if any(p.estado == "error" or p.parcial for p in coletando):
                return ("ok", "partial", "a leitura das sessões ficou incompleta; o que não veio segue como estava",
                        versao)
            return "ok", None, None, versao
        if any(p.estado == "no_origin" or p.origem not in origens for p in vivos):
            return ("ok", "unregistered", "há um plugin ativo sem origem registrada: confira o trecho na configuração do "
                    "OpenCode", versao)
        if any(origens[o]["state"] == "active" for o in validas):
            return "ok", "offline", "desconectado: o OpenCode está fechado, ou sem o plugin", None
        return "ok", "never", "configurado, mas o plugin nunca conectou: cole o trecho e reinicie o OpenCode", None
