"""Uso do Claude Code, só a partir de arquivos locais (sem API, sem credenciais).

Fontes do uso do plano (sessão de 5h e semana):
  1. ~/.ombro-screen/uso.json            gravado pelo statusline_tap.py (a interface documentada do Claude Code)
  2. ~/.claude.json → cachedUsageUtilization   o próprio Claude Code grava ao rodar /usage
  3. ~/.habblaud/usage/<conta>.json     se você também usa o Habblaud

O que estas fontes são, e o que não são:
  - Os percentuais são da assinatura inteira (os limites que a Anthropic devolve ao Claude Code), então incluem o
    que foi gasto no app Claude e no claude.ai. Mas só há número novo quando o Claude Code no terminal recebe
    uma resposta (statusline) ou roda /usage. O app Claude Desktop, incluindo a aba Code, não escreve em nenhuma.
  - Não existe interface suportada para um app de fora consultar o uso da assinatura sem o CLI, e a Anthropic
    não permite que terceiros usem as credenciais da assinatura. Por isso o Ombro não consulta nada pela rede:
    mostra o último valor observado, com a idade dele sempre à vista.

Qual vale (a regra inteira está em `_escolher`): a observação mais recente, entre as que são da conta em uso;
no empate, a mais confiável (statusline, /usage, habblaud). Uma leitura nunca completa a outra: as janelas vêm
todas da mesma observação.

Tokens de hoje: soma dos transcripts em <config>/projects/**/*.jsonl.
Sessões abertas: <config>/sessions/<pid>.json (só as com processo vivo).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config import pasta_ombro
from logging_config import SoQuandoMuda, curto, logger, sem_casa

log = logger("claude")

DESATUALIZADO_MS = 15 * 60_000  # depois disso o número é "o último conhecido", não "atualizado"
ESCOPO_CONTA, ESCOPO_DESCONHECIDO = "account", "unknown"
# fonte -> (prioridade no empate, o que os percentuais cobrem)
FONTES = {"statusline": (0, ESCOPO_CONTA), "/usage": (1, ESCOPO_CONTA), "habblaud": (2, ESCOPO_DESCONHECIDO)}
PASTA_TELA = pasta_ombro()  # ~/.ombro-screen ou OMBRO_SCREEN_DIR; o statusline_tap.py grava o uso.json aqui


@dataclass
class Janela:
    pct: float
    reseta_em: datetime | None = None


@dataclass
class SessaoClaude:
    """Uma sessão viva do Claude Code, como está em <config>/sessions/<pid>.json (só o que o Ombro usa)."""
    id: str  # o sessionId; sem ele, "pid-<pid>". Estável enquanto o processo vive.
    status: str  # "busy", "waiting" ou "idle" (qualquer outro valor conta como "idle", como sempre)
    status_bruto: str | None = None  # o valor original, para o core distinguir um status desconhecido
    desde: datetime | None = None  # statusUpdatedAt: desde quando está nesse status
    projeto: str | None = None  # só o nome da pasta (cwd)
    iniciada: datetime | None = None  # startedAt
    atualizada: datetime | None = None  # updatedAt


def resumo_sessoes(sessoes) -> dict:
    """Contagens e quem espera você há mais tempo, a partir de itens com .status, .desde e .projeto.

    Uma função só para o coletor e para a camada de apresentação, para as duas contarem igual.
    """
    r = dict(sessoes=0, trabalhando=0, esperando=0, status_sessoes=[], espera_projeto="", espera_desde=None)
    for s in sessoes:
        r["sessoes"] += 1
        r["status_sessoes"].append(s.status)
        if s.status == "busy":
            r["trabalhando"] += 1
        elif s.status == "waiting":
            r["esperando"] += 1
            if s.desde is not None and (r["espera_desde"] is None or s.desde < r["espera_desde"]):  # a que espera há mais tempo
                r["espera_desde"], r["espera_projeto"] = s.desde, s.projeto or "sessão"
    return r


@dataclass
class UsoClaude:
    cinco_horas: Janela | None = None
    semana: Janela | None = None
    fonte: str | None = None
    coletado_em: datetime | None = None
    desatualizado: bool = False
    escopo: str = ESCOPO_DESCONHECIDO  # "account": a assinatura inteira no instante da observação; "unknown"
    # janelas que reiniciaram depois da última observação (campo -> quando): o número de antes não vale e o novo
    # ainda não chegou. Nunca viram 0%: ficam sem número até a próxima observação
    expiradas: dict = field(default_factory=dict)
    tokens_entrada: int = 0  # inclui cache (leitura + escrita)
    tokens_saida: int = 0
    tokens_cache: int = 0
    mensagens_hoje: int = 0
    sessoes: int = 0
    trabalhando: int = 0
    esperando: int = 0
    status_sessoes: list[str] = field(default_factory=list)  # "busy", "waiting" ou "idle", uma por sessão viva
    espera_projeto: str = ""  # pasta da sessão que espera você há mais tempo
    espera_desde: datetime | None = None
    tokens_por_hora: list[int | None] = field(default_factory=lambda: [None] * 24)  # hoje, 0h..23h; None = hora que não chegou
    configurado: bool = False  # existe alguma fonte de uso do plano?
    erros: list[str] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)  # etapas que falharam: "uso do plano", "tokens", "sessões"
    lista_sessoes: list[SessaoClaude] = field(default_factory=list)  # uma por sessão viva, na ordem dos arquivos


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip():
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _epoch_ms(v) -> int | None:
    n = _num(v)
    if n is not None:
        return int(n * 1000) if n < 1e12 else int(n)
    if isinstance(v, str) and v.strip():
        try:
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _data(v) -> datetime | None:
    """Horário local de um epoch (s ou ms) ou texto ISO; None se não der para ler."""
    ms = _epoch_ms(v)
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError):
        return None


def _janela(raw, agora_ms: int) -> Janela | None:
    if not isinstance(raw, dict):
        return None
    pct = _num(raw.get("utilization", raw.get("used_percentage")))
    if pct is None:
        return None
    reset = _epoch_ms(raw.get("resets_at", raw.get("resetsAt")))
    if reset is not None and reset <= agora_ms:
        return None  # a janela já reiniciou: o número antigo não vale mais
    return Janela(max(0.0, min(100.0, pct)), datetime.fromtimestamp(reset / 1000) if reset else None)


def _reiniciou(raw, agora_ms: int) -> datetime | None:
    """Quando a janela reiniciou, se ela tinha um número e o reset já passou (o número não vale mais)."""
    if not isinstance(raw, dict) or _num(raw.get("utilization", raw.get("used_percentage"))) is None:
        return None
    reset = _epoch_ms(raw.get("resets_at", raw.get("resetsAt")))
    if reset is None or reset > agora_ms:
        return None
    try:
        return datetime.fromtimestamp(reset / 1000)
    except (OverflowError, OSError, ValueError):
        return None


_ignorados = SoQuandoMuda(log)  # arquivos estranhos: um aviso de depuração por arquivo, não um a cada leitura


def _ler_json(p: Path):
    """O JSON do arquivo, ou None se ele não existe, não abre ou não é JSON (o Claude Code pode estar escrevendo)."""
    try:
        dados = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as e:  # ValueError inclui JSON e UTF-8 inválidos
        _ignorados.registrar(str(p), logging.DEBUG, "%s ignorado (%s)", curto(p), type(e).__name__)
        return None
    _ignorados.resolver(str(p))
    return dados


class ColetorClaude:
    def __init__(self, config_dir: str | None = None):
        cfg = config_dir or os.environ.get("CLAUDE_CONFIG_DIR", "").split(",")[0].strip() or "~/.claude"
        self.config = Path(cfg).expanduser()
        padrao = self.config.resolve() == Path("~/.claude").expanduser().resolve()
        # A conta padrão guarda o .claude.json na home; contas extras, dentro da própria pasta.
        self.claude_json = Path("~/.claude.json").expanduser() if padrao else self.config / ".claude.json"
        self.conta = self.config.name
        self._offsets: dict[Path, int] = {}
        self._por_msg: dict[str, tuple[int, int, int, int]] = {}  # entrada, saída, cache, hora do dia
        self._dia = None
        self._avisos = SoQuandoMuda(log)
        self._fonte_ant: str | None | bool = False  # False = ainda não leu
        self._conta: str | None = None  # a conta em uso (só para comparar; nunca vai para o log nem para a IPC)
        self._conta_mudou_ms = 0  # observações de antes disso são de outra conta
        log.debug("pasta do Claude Code: %s (conta %s)", curto(self.config), self.conta)

    # ---------- uso do plano ----------
    def _fontes_uso(self, agora_ms: int):
        """As leituras que existem agora: (fonte, quando foi observada em ms, dados). Só as da conta em uso."""
        cj = _ler_json(self.claude_json)
        cj = cj if isinstance(cj, dict) else {}
        conta = (cj.get("oauthAccount") or {}).get("accountUuid") if isinstance(cj.get("oauthAccount"), dict) else None
        if isinstance(conta, str) and conta:
            if self._conta is not None and conta != self._conta:  # entrou com outra conta: o que foi visto antes é dela
                self._conta_mudou_ms = agora_ms
                log.info("a conta do Claude Code mudou: os limites vistos antes não são desta conta")
            self._conta = conta
        tap = _ler_json(PASTA_TELA / "uso.json")
        if isinstance(tap, dict):
            yield "statusline", _epoch_ms(tap.get("fetchedAt")), tap
        cache = cj.get("cachedUsageUtilization")
        if isinstance(cache, dict):
            de_outra = isinstance(cache.get("accountUuid"), str) and self._conta is not None and cache["accountUuid"] != self._conta
            if not de_outra:  # o cache diz de que conta é: se é de outra, não entra
                yield "/usage", _epoch_ms(cache.get("fetchedAtMs", cache.get("fetchedAt"))), cache.get("utilization")
        hab = _ler_json(Path("~/.habblaud/usage").expanduser() / f"{self.conta}.json")
        if isinstance(hab, dict):
            yield "habblaud", _epoch_ms(hab.get("fetchedAt")), hab

    def _escolher(self, agora_ms: int):
        """A leitura que vale: a observação mais recente; no empate, a fonte mais confiável. Ficam de fora as sem
        horário, as do futuro (relógio errado) e as de antes de uma troca de conta. As fontes não se misturam."""
        melhor = None
        for fonte, quando, dados in self._fontes_uso(agora_ms):
            if quando is None or not isinstance(dados, dict) or quando < self._conta_mudou_ms:
                continue
            quando = min(quando, agora_ms)  # um horário no futuro não deixa a leitura "mais nova que agora"
            chave = (quando, -FONTES[fonte][0])
            if melhor is None or chave > melhor[0]:
                melhor = (chave, fonte, quando, dados)
        return melhor[1:] if melhor else None

    def _uso_plano(self, u: UsoClaude, agora_ms: int) -> None:
        melhor = self._escolher(agora_ms)
        if not melhor:
            return
        fonte, quando, dados = melhor
        u.configurado = True
        u.fonte = fonte
        u.escopo = FONTES[fonte][1]
        u.coletado_em = datetime.fromtimestamp(quando / 1000)
        u.desatualizado = agora_ms - quando > DESATUALIZADO_MS
        for campo, chave in (("cinco_horas", "five_hour"), ("semana", "seven_day")):
            setattr(u, campo, _janela(dados.get(chave), agora_ms))
            reiniciou = _reiniciou(dados.get(chave), agora_ms)
            if reiniciou is not None:
                u.expiradas[campo] = reiniciou

    # ---------- tokens de hoje ----------
    def _tokens(self, u: UsoClaude) -> None:
        hoje = datetime.now().date()
        if hoje != self._dia:  # virou o dia: recomeça a contagem
            self._dia, self._offsets, self._por_msg = hoje, {}, {}
        inicio_dia = datetime.combine(hoje, datetime.min.time()).timestamp()
        projetos = self.config / "projects"
        if projetos.is_dir():
            for arq in projetos.rglob("*.jsonl"):
                try:
                    st = arq.stat()
                except OSError:
                    continue
                if st.st_mtime < inicio_dia:
                    continue
                pos = self._offsets.get(arq, 0)
                if st.st_size < pos:
                    pos = 0  # arquivo foi reescrito
                if st.st_size == pos:
                    continue
                try:
                    with arq.open("rb") as f:
                        f.seek(pos)
                        bloco = f.read()
                except OSError:
                    continue
                fim = bloco.rfind(b"\n") + 1  # só linhas completas
                self._offsets[arq] = pos + fim
                for linha in bloco[:fim].splitlines():
                    self._linha(linha, hoje)
        agora_h = datetime.now().hour
        por_hora = [0 if h <= agora_h else None for h in range(24)]
        for ent, sai, cache, hora in self._por_msg.values():
            u.tokens_entrada += ent
            u.tokens_saida += sai
            u.tokens_cache += cache
            if por_hora[hora] is not None:
                por_hora[hora] += ent + sai
        u.tokens_por_hora = por_hora
        u.mensagens_hoje = len(self._por_msg)

    def _linha(self, linha: bytes, hoje) -> None:
        if b'"usage"' not in linha:
            return
        try:
            j = json.loads(linha)
        except json.JSONDecodeError:
            return
        m = j.get("message")
        if j.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
            return
        ts = j.get("timestamp")
        try:
            quando = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()
        except ValueError:
            return
        if quando.date() != hoje:
            return
        us = m["usage"]
        n = lambda k: int(us.get(k) or 0)
        cache = n("cache_read_input_tokens") + n("cache_creation_input_tokens")
        ent = n("input_tokens") + cache
        sai = n("output_tokens")
        mid = str(m.get("id") or j.get("requestId") or j.get("uuid"))
        # A mesma mensagem aparece em várias linhas, repetindo o uso: fica o maior valor.
        a = self._por_msg.get(mid, (0, 0, 0, quando.hour))
        self._por_msg[mid] = (max(a[0], ent), max(a[1], sai), max(a[2], cache), a[3])

    # ---------- sessões abertas ----------
    def _sessoes(self, u: UsoClaude) -> None:
        pasta = self.config / "sessions"
        if not pasta.is_dir():
            return
        vistos: set[str] = set()
        for arq in pasta.glob("*.json"):
            j = _ler_json(arq)
            if not isinstance(j, dict) or not isinstance(j.get("pid"), int):
                continue
            try:
                os.kill(j["pid"], 0)
            except ProcessLookupError:
                continue  # sobrou de um processo que já morreu
            except PermissionError:
                pass
            st = j.get("status")
            sid = j.get("sessionId") if isinstance(j.get("sessionId"), str) and j.get("sessionId") else f"pid-{j['pid']}"
            if sid in vistos:  # a mesma conversa aberta em dois processos: cada processo é uma sessão
                sid = f"{sid}#{j['pid']}"
            vistos.add(sid)
            u.lista_sessoes.append(SessaoClaude(
                id=sid,
                status=st if st in ("busy", "waiting") else "idle",
                status_bruto=st if isinstance(st, str) else None,
                desde=_data(j.get("statusUpdatedAt")),
                projeto=Path(str(j.get("cwd") or "")).name or None,
                iniciada=_data(j.get("startedAt")),
                atualizada=_data(j.get("updatedAt")),
            ))
        for nome, valor in resumo_sessoes(u.lista_sessoes).items():
            setattr(u, nome, valor)

    def ler(self) -> UsoClaude:
        u = UsoClaude()
        agora_ms = int(datetime.now().timestamp() * 1000)
        etapas = (
            ("uso do plano", lambda: self._uso_plano(u, agora_ms)),
            ("tokens", lambda: self._tokens(u)),
            ("sessões", lambda: self._sessoes(u)),
        )
        for nome, etapa in etapas:
            try:
                etapa()
            except Exception as e:  # noqa: BLE001 - nunca derruba a tela por causa de um arquivo estranho
                u.erros.append(f"{nome}: {e}")
                u.falhas.append(nome)
                if self._avisos.registrar(f"etapa:{nome}", logging.WARNING, "não consegui ler %s do Claude Code (%s: %s)",
                                          nome, type(e).__name__, sem_casa(e)):
                    log.debug("detalhes da falha em %s", nome, exc_info=True)
            else:
                self._avisos.resolver(f"etapa:{nome}", "leitura de %s do Claude Code voltou ao normal", nome)
        self._registrar_estado(u)
        return u

    def _registrar_estado(self, u: UsoClaude) -> None:
        """Só as mudanças: fonte do uso do plano e números velhos (nunca nomes de projeto ou conteúdo)."""
        fonte = u.fonte if u.configurado else None
        if fonte != self._fonte_ant:
            if fonte:
                log.info("uso do plano (5 h e semana): fonte %s", fonte)
            else:
                log.info("sem dados do uso do plano (5 h e semana); veja a seção 4 do README")
            self._fonte_ant = fonte
        if u.desatualizado:
            self._avisos.registrar("desatualizado", logging.WARNING, "o uso do plano (%s) tem mais de %d min: só há número "
                                   "novo quando o Claude Code no terminal conversa ou roda /usage", u.fonte, DESATUALIZADO_MS // 60_000)
        else:
            self._avisos.resolver("desatualizado", "o uso do plano voltou a ser atual (%s)", u.fonte)
