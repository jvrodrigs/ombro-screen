"""Estado em disco (SQLite): para o Ombro lembrar das sessões depois de reiniciar.

O banco não substitui as fontes do Claude Code: elas continuam valendo. Ele guarda o mínimo para
um provider que avisa por eventos (sem arquivo para reler) não perder o que sabia:

  providers  último status da coleta de cada provider
  sessions   uma linha por (provider_id, session_id): atividade, atenção, horários, quando acabou
  events     as mudanças recentes (tipo, sessão, horário, atividade, atenção)

Não guarda: nome de projeto ou pasta, conteúdo de conversas, prompts, respostas, tokens ou
credenciais. A versão do formato fica no `PRAGMA user_version`; cada migração roda numa transação.

Nada aqui pode derrubar o Ombro: banco ilegível, travado, corrompido, sem permissão ou de uma versão
mais nova faz o Ombro seguir só em memória, com um aviso no log. Um banco com problema nunca é apagado.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from logging_config import SoQuandoMuda, curto, logger

from .events import AgentEvent, EventType
from .models import Activity, AgentSession, Attention

log = logger("persistencia")

VERSAO = 1
MIGRACOES = {
    1: [
        """CREATE TABLE providers (
            provider_id TEXT PRIMARY KEY,
            status      TEXT NOT NULL,      -- ok, partial ou error (a última coleta)
            updated_at  TEXT NOT NULL
        )""",
        """CREATE TABLE sessions (
            provider_id  TEXT NOT NULL,
            session_id   TEXT NOT NULL,
            activity     TEXT NOT NULL,     -- unknown, idle ou working
            attention    TEXT NOT NULL,     -- unknown, none ou waiting_user
            started_at   TEXT,
            status_since TEXT,
            last_seen_at TEXT NOT NULL,     -- a última mudança vista
            ended_at     TEXT,              -- NULL enquanto aberta
            PRIMARY KEY (provider_id, session_id)
        )""",
        """CREATE TABLE events (
            seq         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT NOT NULL UNIQUE,
            provider_id TEXT NOT NULL,
            session_id  TEXT,
            event_type  TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            activity    TEXT,
            attention   TEXT
        )""",
        "CREATE INDEX events_occurred_at ON events (occurred_at)",
        "CREATE INDEX sessions_ended_at ON sessions (ended_at)",
    ],
}

RETENCAO_EVENTOS = timedelta(days=7)
MAX_EVENTOS = 5000
RETENCAO_SESSOES = timedelta(days=7)  # sessões encerradas, ou abertas sem notícia há tanto tempo
INTERVALO_LIMPEZA = timedelta(hours=1)


class BancoMaisNovo(Exception):
    """O banco é de uma versão mais nova do Ombro: não mexer nele."""


def _txt(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d is not None else None


def _data(s: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


class StateDB:
    def __init__(self, caminho: Path, relogio: Callable[[], datetime] = datetime.now):
        self.caminho = Path(caminho)
        self._relogio = relogio
        self._conn: Optional[sqlite3.Connection] = None
        self._avisos = SoQuandoMuda(log)
        self._ultima_limpeza: Optional[datetime] = None

    @property
    def ativo(self) -> bool:
        return self._conn is not None

    # ---------- abrir, migrar, fechar ----------
    def abrir(self) -> bool:
        """Abre (ou cria) o banco e põe o formato em dia. Falso = seguir só em memória."""
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            conn = sqlite3.connect(str(self.caminho), timeout=1.0, isolation_level=None)  # transações explícitas
            try:
                conn.execute("PRAGMA busy_timeout = 1000")
                self._migrar(conn)
            except BaseException:
                conn.close()
                raise
        except BancoMaisNovo as e:
            self._desligar(f"o banco {curto(self.caminho)} é de uma versão mais nova do Ombro ({e}); "
                           "seguindo sem salvar estado")
            return False
        except (sqlite3.Error, OSError) as e:
            self._desligar(f"não consegui abrir o banco de estado {curto(self.caminho)} ({type(e).__name__}: {e}); "
                           "seguindo sem salvar estado (o arquivo não foi apagado)")
            return False
        self._conn = conn
        try:
            self.caminho.chmod(0o600)
        except OSError:
            pass
        log.debug("banco de estado aberto: %s (versão %d)", curto(self.caminho), VERSAO)
        return True

    def _migrar(self, conn: sqlite3.Connection) -> None:
        versao = conn.execute("PRAGMA user_version").fetchone()[0]
        if versao > VERSAO:
            raise BancoMaisNovo(f"versão {versao}; este Ombro conhece até a {VERSAO}")
        for alvo in range(versao + 1, VERSAO + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for sql in MIGRACOES[alvo]:
                    conn.execute(sql)
                conn.execute(f"PRAGMA user_version = {alvo}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")  # nada pela metade: o banco fica na versão de antes
                raise
            log.info("banco de estado: formato atualizado para a versão %d", alvo)

    def fechar(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def _desligar(self, msg: str) -> None:
        self._avisos.registrar("banco", logging.WARNING, msg)
        self.fechar()

    @contextmanager
    def _transacao(self):
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def _escrever(self, o_que: str, fn) -> bool:
        """Roda `fn(conn)` numa transação. Erro passageiro (travado, disco cheio): pula e avisa uma vez.
        Banco corrompido: desliga a persistência (sem apagar nada)."""
        if self._conn is None:
            return False
        try:
            with self._transacao() as conn:
                fn(conn)
        except sqlite3.OperationalError as e:  # travado, somente leitura, disco cheio
            self._avisos.registrar("escrita", logging.WARNING, "não consegui gravar %s no banco de estado (%s); "
                                   "tento de novo na próxima mudança", o_que, e)
            return False
        except sqlite3.DatabaseError as e:  # corrompido
            self._desligar(f"o banco de estado {curto(self.caminho)} parece corrompido ({e}); seguindo sem salvar estado "
                           "(o arquivo não foi apagado; mova-o para outro lugar para começar um novo)")
            return False
        self._avisos.resolver("escrita", "o banco de estado voltou a gravar")
        return True

    # ---------- escrita: um consumidor do EventBus ----------
    def ao_evento(self, ev: AgentEvent) -> None:
        """Grava a mudança. Só é chamado quando algo muda, nunca a cada quadro."""
        def gravar(conn: sqlite3.Connection) -> None:
            s = ev.session
            conn.execute("INSERT OR IGNORE INTO events (event_id, provider_id, session_id, event_type, occurred_at, activity, "
                         "attention) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (ev.event_id, ev.provider_id, ev.session_id, ev.type.value, _txt(ev.occurred_at),
                          s.activity.value if s else None, s.attention.value if s else None))
            if ev.type is EventType.PROVIDER_CHANGED:
                conn.execute("INSERT INTO providers (provider_id, status, updated_at) VALUES (?, ?, ?) "
                             "ON CONFLICT (provider_id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                             (ev.provider_id, ev.detail or "", _txt(ev.occurred_at)))
            elif ev.type is EventType.SESSION_ENDED:
                conn.execute("UPDATE sessions SET ended_at = ?, last_seen_at = ? WHERE provider_id = ? AND session_id = ? "
                             "AND ended_at IS NULL", (_txt(ev.occurred_at), _txt(ev.occurred_at), ev.provider_id, ev.session_id))
            else:  # começou ou mudou: a linha fica com o último estado (o projeto não é guardado)
                conn.execute("INSERT INTO sessions (provider_id, session_id, activity, attention, started_at, status_since, "
                             "last_seen_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
                             "ON CONFLICT (provider_id, session_id) DO UPDATE SET activity = excluded.activity, "
                             "attention = excluded.attention, started_at = COALESCE(excluded.started_at, sessions.started_at), "
                             "status_since = excluded.status_since, last_seen_at = excluded.last_seen_at, ended_at = NULL",
                             (s.provider_id, s.session_id, s.activity.value, s.attention.value, _txt(s.started_at),
                              _txt(s.status_since), _txt(ev.occurred_at)))
        self._escrever("uma mudança de estado", gravar)

    # ---------- leitura: recuperação ----------
    def sessoes_abertas(self, agora: Optional[datetime] = None) -> List[AgentSession]:
        """Sessões que estavam abertas quando o Ombro fechou e ainda podem existir (vistas há menos de RETENCAO_SESSOES).

        Voltam com o estado salvo; o StateStore marca como desconhecido até a coleta confirmar.
        """
        if self._conn is None:
            return []
        agora = agora or self._relogio()
        try:
            linhas = self._conn.execute(
                "SELECT provider_id, session_id, activity, attention, started_at, status_since, last_seen_at FROM sessions "
                "WHERE ended_at IS NULL AND last_seen_at >= ? ORDER BY last_seen_at", (_txt(agora - RETENCAO_SESSOES),)).fetchall()
        except sqlite3.DatabaseError as e:
            self._desligar(f"não consegui ler o banco de estado ({e}); seguindo sem salvar estado")
            return []
        sessoes = []
        for pid, sid, atividade, atencao, inicio, desde, visto in linhas:
            try:
                sessoes.append(AgentSession(pid, sid, Activity(atividade), Attention(atencao), started_at=_data(inicio),
                                            status_since=_data(desde), updated_at=_data(visto)))
            except (ValueError, TypeError):
                log.debug("linha estranha no banco de estado ignorada (%s)", pid)
        return sessoes

    # ---------- retenção ----------
    def limpar(self, agora: Optional[datetime] = None, forcar: bool = False) -> bool:
        """Apaga o que não serve mais: eventos com mais de 7 dias (e além dos 5000 mais novos), sessões
        encerradas há mais de 7 dias, e sessões abertas sem notícia há 7 dias. Roda no máximo uma vez por hora."""
        agora = agora or self._relogio()
        if not forcar and self._ultima_limpeza is not None and agora - self._ultima_limpeza < INTERVALO_LIMPEZA:
            return False
        self._ultima_limpeza = agora
        corte_ev, corte_s = _txt(agora - RETENCAO_EVENTOS), _txt(agora - RETENCAO_SESSOES)

        def apagar(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM events WHERE occurred_at < ?", (corte_ev,))
            conn.execute("DELETE FROM events WHERE seq NOT IN (SELECT seq FROM events ORDER BY seq DESC LIMIT ?)", (MAX_EVENTOS,))
            conn.execute("DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (corte_s,))
            conn.execute("DELETE FROM sessions WHERE ended_at IS NULL AND last_seen_at < ?", (corte_s,))
        return self._escrever("a limpeza", apagar)
