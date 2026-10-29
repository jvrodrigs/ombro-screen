"""Uso do Claude Code, só a partir de arquivos locais (sem API, sem credenciais).

Fontes do uso do plano (sessão de 5h e semana), vale a mais recente:
  1. ~/.ombro-screen/uso.json            gravado pelo statusline_tap.py (ao vivo, recomendado)
  2. ~/.habblaud/usage/<conta>.json     se você também usa o Habblaud
  3. ~/.claude.json → cachedUsageUtilization   o próprio Claude Code grava ao rodar /usage

Tokens de hoje: soma dos transcripts em <config>/projects/**/*.jsonl.
Sessões abertas: <config>/sessions/<pid>.json (só as com processo vivo).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DESATUALIZADO_MS = 30 * 60_000
PASTA_TELA = Path(os.environ.get("OMBRO_SCREEN_DIR", "~/.ombro-screen")).expanduser()


@dataclass
class Janela:
    pct: float
    reseta_em: datetime | None = None


@dataclass
class UsoClaude:
    cinco_horas: Janela | None = None
    semana: Janela | None = None
    fonte: str | None = None
    coletado_em: datetime | None = None
    desatualizado: bool = False
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


def _ler_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


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

    # ---------- uso do plano ----------
    def _fontes_uso(self):
        tap = _ler_json(PASTA_TELA / "uso.json")
        if isinstance(tap, dict):
            yield "statusline", _epoch_ms(tap.get("fetchedAt")), tap
        hab = _ler_json(Path("~/.habblaud/usage").expanduser() / f"{self.conta}.json")
        if isinstance(hab, dict):
            yield "habblaud", _epoch_ms(hab.get("fetchedAt")), hab
        cj = _ler_json(self.claude_json)
        cache = cj.get("cachedUsageUtilization") if isinstance(cj, dict) else None
        if isinstance(cache, dict):
            yield "/usage", _epoch_ms(cache.get("fetchedAtMs", cache.get("fetchedAt"))), cache.get("utilization")

    def _uso_plano(self, u: UsoClaude, agora_ms: int) -> None:
        melhor = None
        for fonte, quando, dados in self._fontes_uso():
            if quando is None or not isinstance(dados, dict):
                continue
            if melhor is None or quando > melhor[1]:
                melhor = (fonte, quando, dados)
        if not melhor:
            return
        fonte, quando, dados = melhor
        u.configurado = True
        u.fonte = fonte
        u.coletado_em = datetime.fromtimestamp(quando / 1000)
        u.desatualizado = agora_ms - quando > DESATUALIZADO_MS
        u.cinco_horas = _janela(dados.get("five_hour"), agora_ms)
        u.semana = _janela(dados.get("seven_day"), agora_ms)

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
            u.sessoes += 1
            st = j.get("status")
            u.status_sessoes.append(st if st in ("busy", "waiting") else "idle")
            if st == "busy":
                u.trabalhando += 1
            elif st == "waiting":
                u.esperando += 1
                desde = _epoch_ms(j.get("statusUpdatedAt"))
                if desde is not None:
                    quando = datetime.fromtimestamp(desde / 1000)
                    if u.espera_desde is None or quando < u.espera_desde:  # a que espera há mais tempo
                        u.espera_desde = quando
                        u.espera_projeto = Path(str(j.get("cwd") or "")).name or "sessão"

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
            except Exception as e:  # nunca derruba a tela por causa de um arquivo estranho
                u.erros.append(f"{nome}: {e}")
        return u
