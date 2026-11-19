"""ombrod: o Ombro como serviço local. O mesmo laço do tela.py, mais lock, ciclo de vida, saúde e a IPC.

  python3 ombrod.py                       com o config.json (e as mesmas opções do tela.py, menos --png)
  python3 ombrod.py --usb --debug
  python3 ombrod.py --demo                dados inventados (para testar a CLI sem agentes nem tela)

Ciclo de vida (`status.daemon`):
  starting  ─► running ◄─► degraded ─► stopping ─► stopped
      └──────────────────────┴────────────┘
  - starting: lock, IPC e montagem; também enquanto um Ombro antigo (sem lock) segura a tela;
  - running: o laço roda e todos os componentes estão bem (tela desconectada ou agente não instalado
    não contam: não são falhas);
  - degraded: o laço roda, mas algo falhou (banco só em memória, um provider com erro, tela com erro,
    simulador sem porta, IPC fora) ou um Ombro antigo está com a tela;
  - stopping: chegou SIGTERM/SIGINT (ou um erro fatal); stopped: tudo solto.

Encerramento (nesta ordem): recusa métodos administrativos; libera os pedidos pendentes; fecha a tela,
o banco (as gravações são transações curtas no próprio laço: um sinal nunca corta uma gravação no meio) e o
leitor de hardware; fecha a IPC e apaga o socket (só se ainda for o nosso); solta o lock. Os agentes
(Claude Code, Codex, Cursor) nunca recebem sinal nenhum.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import platform
import signal
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import AVATAR_CHAVES, LAYOUTS, PERSISTENTES, PROVIDERS, VISTAS, ConfigInvalida, OmbroConfig, arquivo_padrao, pasta_ombro
from core.models import Activity, Attention
from core.resumo import resumir_provider
from logging_config import SoQuandoMuda, curto, logger, sem_casa

from . import VERSAO
from . import preferencias
from .app import Aplicacao, Indisponivel
from .caminhos import CaminhoInseguro
from .instancia import OutraInstancia, Trava
from .ipc import (
    INTERNAL_ERROR, PROTOCOLO, SERVICE_UNAVAILABLE, VALIDATION_ERROR, ErroIPC, Metodo, Param, ServidorIPC,
)
from .processos import conflitos

log = logger("daemon")
ESTADOS = ("starting", "running", "degraded", "stopping", "stopped")
SAUDES = ("healthy", "degraded", "unhealthy", "unavailable")  # unavailable: não se aplica agora (não conta)
ESPERA_CONFLITO = 10.0  # segundos entre conferências enquanto um Ombro antigo está com a tela
EX_TEMPFAIL = 75


def _iso(d) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, (int, float)):
        d = datetime.fromtimestamp(d)
    return d.isoformat(timespec="seconds")


def quotas_do_snapshot(snap) -> Optional[dict]:
    """As janelas de limite como o provider as entregou (sem recalcular nada); None = o provider não tem fonte."""
    q = getattr(snap, "quotas", None)
    if q is None:
        return None
    com_numero = [w for w in q.windows if w.used_percent is not None]
    # fresh: observado há pouco; stale: o último valor conhecido, velho; awaiting_update: as janelas reiniciaram
    # depois da última observação e ainda não há número novo (nunca se assume 0%)
    estado = "awaiting_update" if not com_numero and any(w.expired for w in q.windows) else "stale" if q.stale else "fresh"
    idade = max(0, int((datetime.now() - q.fetched_at).total_seconds()))
    return {"source": q.source, "fetched_at": _iso(q.fetched_at), "stale": q.stale,
            # `fetched_at`/`observed_at`: quando o dado foi observado (não quando o Ombro leu o arquivo de novo)
            "observed_at": _iso(q.fetched_at), "age_seconds": idade, "state": estado,
            "scope": q.scope, "auto_update": q.auto_update,
            "windows": [{"id": w.window_id, "label": w.label, "used_percent": w.used_percent,
                         "resets_at": _iso(w.resets_at), "expired": w.expired} for w in q.windows]}


def uso_do_snapshot(snap) -> Optional[dict]:
    """Os totais de hoje (só números; nada de conteúdo). None = o provider não informa uso."""
    u = getattr(snap, "usage", None)
    if u is None:
        return None
    return {"scope": u.scope, "total_tokens": u.total_tokens, "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens, "messages": u.messages}


def sistema_do_hardware(h) -> Optional[dict]:
    """CPU e RAM do Mac, da mesma leitura que vai para a tela. None = ainda sem leitura."""
    if h is None or not getattr(h, "disponivel", False):
        return None
    return {"cpu_percent": round(h.cpu_pct, 1), "ram_used_bytes": h.ram_usada, "ram_total_bytes": h.ram_total,
            "memory_pressure": {"normal": "normal", "atencao": "warning", "critico": "critical"}.get(h.pressao_memoria)}


def status_da_sessao(s) -> str:
    """Um nome só para a CLI e o app: error > approval > waiting > working > idle > unknown."""
    if s.attention is Attention.ERROR:
        return "error"
    if s.attention is Attention.WAITING_APPROVAL:
        return "approval"
    if s.attention is Attention.WAITING_USER:
        return "waiting"
    if s.activity is Activity.WORKING:
        return "working"
    if s.activity is Activity.IDLE:
        return "idle"
    return "unknown"


class SaidaIndisponivel:
    """No lugar do simulador quando a porta dele está ocupada: o daemon segue (coleta, estado, IPC) sem tela."""

    def __init__(self, motivo: str):
        self.motivo = motivo

    def pronta(self) -> bool:
        return True

    def mostrar(self, _img) -> None:
        pass

    def estado(self) -> dict:
        return {"kind": "simulator", "connection": "error", "transport": "http", "last_error": self.motivo}

    def fechar(self) -> None:
        pass


class Daemon:
    def __init__(self, cfg: OmbroConfig, trava: Optional[Trava] = None, socket_caminho: Optional[Path] = None):
        self.cfg = cfg
        self.estado = "starting"
        self.iniciado_em = time.time()
        self._mono0 = time.monotonic()
        self.trava = trava or Trava("ombrod")
        self.socket_caminho = socket_caminho
        self.app: Optional[Aplicacao] = None
        self.ipc: Optional[ServidorIPC] = None
        self.erro_ipc: Optional[str] = None
        self.motivo: Optional[str] = None  # o sinal (ou o erro) que mandou parar
        self.conflito: List[dict] = []  # Ombros antigos com a tela
        self._acordar = threading.Event()
        self._trava_config = threading.Lock()  # um config.update por vez
        self._avisos = SoQuandoMuda(log)
        self.publicado: Dict = {"providers": [], "sessions": [], "collected_at": None}
        self._preview_cache: Optional[tuple] = None  # (quadro, PNG em base64) do último display.preview
        self._pendentes: Dict = {}  # preferências gravadas que só entram em uso ao reiniciar: {chave: valor gravado}
        self._revisao = 0  # sobe a cada mudança de preferência: quem lê descarta uma resposta mais velha
        self._instancia = f"{os.getpid()}-{int(self.iniciado_em * 1000)}"  # a revisão só se compara no mesmo ombrod

    # ---------- ciclo de vida ----------
    def _mudar(self, novo: str) -> None:
        if novo != self.estado:
            nivel = logging.WARNING if novo == "degraded" else logging.INFO
            log.log(nivel, "ombrod: %s -> %s", self.estado, novo)
            self.estado = novo

    def pedir_parada(self, motivo: str) -> None:
        if self.motivo is None:
            self.motivo = motivo
        self._acordar.set()
        if self.app is not None:
            self.app.acordar()

    def _ao_sinal(self, sinal, _quadro) -> None:
        self.pedir_parada(signal.Signals(sinal).name)

    def rodar(self) -> int:
        try:
            self.trava.adquirir()
        except OutraInstancia as e:
            log.error("%s; o ombrod não vai iniciar outro (python3 -m ombro status)", e)
            return EX_TEMPFAIL
        except (OSError, CaminhoInseguro) as e:
            log.error("não consegui criar o lock de instância (%s)", sem_casa(e))
            return 1
        codigo = 0
        try:
            log.info("ombrod %s iniciando (PID %d, Python %s, protocolo IPC %d)", VERSAO, os.getpid(),
                     platform.python_version(), PROTOCOLO)
            signal.signal(signal.SIGTERM, self._ao_sinal)
            signal.signal(signal.SIGINT, self._ao_sinal)
            self._iniciar_ipc()
            if not self.cfg.demo or self.cfg.usb:
                self._esperar_conflito()
            if self.motivo is not None:
                return 0
            self.app = Aplicacao(self.cfg)
            self.app.ao_coletar = self._publicar
            try:
                self.app.abrir_saida(abrir_navegador=False)
            except OSError as e:  # o simulador sem a porta: segue sem tela, avisando
                log.error("não consegui abrir o simulador na porta %d (%s); seguindo sem tela",
                          self.cfg.porta_sim, e.strerror or e)
                self.app.saida = SaidaIndisponivel(f"porta {self.cfg.porta_sim} indisponível")
            self._reavaliar()
            self.app.laco(parar=lambda: self.motivo is not None)
        except SystemExit as e:  # erro de desenho (bug) ou de configuração da tela: o launchd reinicia
            codigo = e.code if isinstance(e.code, int) else 1
            if self.motivo is None:
                self.motivo = f"saída {codigo}"
        except Exception:
            log.critical("erro inesperado; o ombrod vai fechar", exc_info=True)
            codigo, self.motivo = 1, self.motivo or "erro fatal"
        finally:
            self._encerrar()
        return codigo

    def _iniciar_ipc(self) -> None:
        self.ipc = ServidorIPC(self.metodos(), caminho=self.socket_caminho)
        try:
            self.ipc.iniciar()
        except (OSError, CaminhoInseguro) as e:
            self.erro_ipc = sem_casa(e)
            log.error("IPC indisponível (%s); o Ombro segue mostrando a tela, mas a CLI não vai alcançá-lo", self.erro_ipc)
            self.ipc = None

    def _esperar_conflito(self) -> None:
        """Um tela.py de antes da Fase 4 (o LaunchAgent antigo, por exemplo) não segura o lock: espera ele sair."""
        while self.motivo is None:
            self.conflito = conflitos()
            if not self.conflito:
                if self._avisos.resolver("conflito"):
                    log.info("o outro Ombro saiu; iniciando")
                return
            pids = ", ".join(str(p["pid"]) for p in self.conflito)
            self._avisos.registrar("conflito", logging.WARNING,
                                   "outro Ombro está rodando sem o lock (PID %s; o LaunchAgent antigo?): esperando ele sair "
                                   "para não disputar a tela. Para migrar: python3 -m ombro service migrate", pids)
            self._mudar("degraded")
            self._acordar.wait(ESPERA_CONFLITO)
            self._acordar.clear()

    def _encerrar(self) -> None:
        self._mudar("stopping")
        log.info("encerrando%s", f" ({self.motivo})" if self.motivo else "")
        if self.ipc is not None:
            self.ipc.recusar_admin()
        if self.app is not None:
            self.app.fechar()
        if self.ipc is not None:
            self.ipc.parar()
        self.trava.soltar()
        self._mudar("stopped")

    # ---------- o que o laço publica (roda no laço, a cada coleta) ----------
    def _publicar(self) -> None:
        app = self.app
        store, registry = app.monitor.store, app.monitor.registry
        provs, sessoes = [], []
        ligados = {p.provider_id: p for p in registry}
        avs = app.avatares_resolvidos()
        for pid in PROVIDERS:
            p = ligados.get(pid)
            if p is None:
                provs.append({"id": pid, "enabled": False})
                continue
            st = store.provider(pid)
            snap = st.snapshot if st else None
            lista = store.sessions(pid)
            r = resumir_provider(pid, p.display_name, snap, lista)
            ultimo_evento = getattr(p, "ultimo_evento", None) or getattr(getattr(p, "rastreador", None), "ultimo_evento", None)
            provs.append({
                "id": pid, "name": p.display_name, "enabled": True,
                "capabilities": sorted(k for k, v in asdict(p.capabilities).items() if v),
                "installed": None if snap is None else snap.integration != "not_installed",
                "integration": snap.integration if snap else None,
                "collection": st.status.value if st and st.status else None,
                "state": r.estado, "sessions": r.sessoes, "working": r.trabalhando, "waiting": r.aguardando,
                "errors": r.erros, "quota_max_percent": r.quota_max,
                "stale_parts": sorted(st.partes_velhas) if st else [],
                "recovered": bool(st and st.recuperado),
                "last_collected_at": _iso(snap.collected_at) if snap else None,
                "last_event_at": _iso(ultimo_evento),
                "quotas": quotas_do_snapshot(snap), "usage": uso_do_snapshot(snap),
                "avatar": avs.get(pid),
            })
            for s in lista:
                sessoes.append({
                    "provider": pid, "session_id": s.session_id, "project": s.project,
                    "status": status_da_sessao(s), "activity": s.activity.value, "attention": s.attention.value,
                    "started_at": _iso(s.started_at), "status_since": _iso(s.status_since), "updated_at": _iso(s.updated_at),
                })
        self.publicado = {"providers": provs, "sessions": sessoes, "collected_at": _iso(app.coletado_em),
                          "avatar_ombro": avs.get("padrao"), "avatar_sorteio": app.cfg.avatar_sorteio}
        self._reavaliar()

    def _reavaliar(self) -> None:
        if self.estado in ("stopping", "stopped") or self.app is None:
            return
        self._mudar("degraded" if self.saude()["status"] != "healthy" else "running")

    # ---------- saúde ----------
    def _display(self) -> dict:
        app = self.app
        saida = app.saida if app is not None else None
        d = saida.estado() if saida is not None and hasattr(saida, "estado") else {"kind": "none", "connection": "none"}
        if self.conflito and app is None:
            d = {"kind": "usb" if self.cfg.usb else "simulator", "connection": "conflict",
                 "last_error": "outro Ombro (sem lock) está com a tela"}
        d = dict(d)
        d.setdefault("brightness", self.cfg.brilho if d.get("kind") == "usb" else None)
        d["layout"] = (app.cfg if app else self.cfg).layout
        d["last_frame_at"] = _iso(app.ultimo_quadro_em) if app else None
        sujeito = getattr(app, "sujeito_atual", None)
        if isinstance(sujeito, tuple):  # quem o mascote representa agora, e com qual avatar (v1: campo novo)
            m = app.mascote
            d["mascot"] = {"avatar": m.forma.avatar, "provider": sujeito[0], "state": m.estado,
                           "color": "#%02X%02X%02X" % m.cor, "package": m.pacote}
        d["frames"] = app.quadros if app else 0
        if d.get("connected_since"):
            d["connected_since"] = _iso(d["connected_since"])
        return d

    def saude(self) -> dict:
        comp: Dict[str, dict] = {}
        comp["daemon"] = {"status": "healthy" if self.estado not in ("stopping", "stopped") else "unhealthy",
                          "state": self.estado}
        comp["ipc"] = ({"status": "healthy", "state": "listening"} if self.ipc is not None
                       else {"status": "degraded", "state": "unavailable", "detail": self.erro_ipc})
        app = self.app
        banco = app.monitor.banco if app else None
        if app is None:
            comp["storage"] = {"status": "unavailable", "state": "not_open"}
        elif banco is None:
            comp["storage"] = {"status": "unavailable", "state": "disabled"}  # --demo
        else:
            comp["storage"] = ({"status": "healthy", "state": "persistent"} if banco.ativo
                               else {"status": "degraded", "state": "memory_only"})
        d = self._display()
        conexao = d.get("connection")
        comp["display"] = {"status": {"connected": "healthy", "disconnected": "unavailable"}.get(conexao, "degraded"),
                           "state": conexao}
        for p in self.publicado["providers"]:
            if not p.get("enabled"):
                comp[f"provider.{p['id']}"] = {"status": "unavailable", "state": "disabled"}
                continue
            if p.get("integration") == "not_installed":
                saude, estado = "unavailable", "not_installed"
            elif p.get("integration") == "not_configured":
                saude, estado = "unavailable", "not_configured"
            elif p.get("collection") == "error":
                saude, estado = "degraded", "error"
            elif p.get("collection") == "partial":
                saude, estado = "degraded", "partial"
            else:
                saude, estado = "healthy", p.get("state") or "ok"
            comp[f"provider.{p['id']}"] = {"status": saude, "state": estado}
        if self.conflito and app is None:
            comp["instance"] = {"status": "degraded", "state": "legacy_conflict",
                                "detail": "outro Ombro sem lock está com a tela"}
        estados = [c["status"] for c in comp.values()]
        if comp["daemon"]["status"] == "unhealthy":
            geral = "unhealthy"
        elif "unhealthy" in estados or "degraded" in estados:
            geral = "degraded"
        else:
            geral = "healthy"
        return {"status": geral, "components": comp}

    # ---------- métodos da IPC (rodam nas threads da IPC) ----------
    def metodos(self) -> Dict[str, Metodo]:
        lista = [
            Metodo("ping", self.m_ping, {}),
            Metodo("status", self.m_status, {}),
            Metodo("health", lambda _p: self.saude(), {}),
            Metodo("providers.list", self.m_providers, {}),
            Metodo("sessions.list", self.m_sessions, {"provider": Param(str, opcoes=PROVIDERS, nulo=True)}),
            Metodo("display.status", lambda _p: self._display(), {}),
            Metodo("display.preview", self.m_preview, {"format": Param(str, opcoes=("png",))}),
            Metodo("config.get", self.m_config_get, {}),
            Metodo("config.schema", self.m_config_schema, {}),
            Metodo("config.update", self.m_config_update,
                   {"changes": Param(dict, obrigatorio=True), "persist": Param(bool)}, admin=True),
            Metodo("display.set_brightness", self.m_brilho,
                   {"value": Param(int, obrigatorio=True, minimo=0, maximo=100), "persist": Param(bool)}, admin=True),
            Metodo("display.set_layout", self.m_layout,
                   {"layout": Param(str, obrigatorio=True, opcoes=LAYOUTS), "persist": Param(bool)}, admin=True),
        ]
        return {m.nome: m for m in lista}

    def m_ping(self, _p) -> dict:
        return {"pong": True, "protocol": PROTOCOLO, "daemon": self.estado}

    def m_status(self, _p) -> dict:
        provs = self.publicado["providers"]
        sessoes = self.publicado["sessions"]
        saude = self.saude()
        d = self._display()
        return {
            "daemon": self.estado, "version": VERSAO, "protocol": PROTOCOLO, "pid": os.getpid(),
            "started_at": _iso(self.iniciado_em), "uptime_s": round(time.monotonic() - self._mono0, 1),
            "health": saude["status"],
            "providers": {
                "configured": sum(1 for p in provs if p.get("enabled")),
                "installed": sum(1 for p in provs if p.get("installed")),
                "operational": sum(1 for p in provs if saude["components"].get(f"provider.{p['id']}", {}).get("status") == "healthy"),
            },
            "sessions": len(sessoes),
            "waiting": sum(1 for s in sessoes if s["status"] in ("waiting", "approval")),
            "errors": sum(1 for s in sessoes if s["status"] == "error"),
            "display": {"kind": d.get("kind"), "connection": d.get("connection"), "layout": d.get("layout")},
            "storage": saude["components"]["storage"]["state"],
            "demo": self.cfg.demo,
            "collected_at": self.publicado["collected_at"],
            "conflicts": [p["pid"] for p in self.conflito] if self.app is None else [],
            "system": sistema_do_hardware(getattr(self.app, "ultimo_hw", None)),
        }

    def _animacao(self) -> Optional[dict]:
        """O pacote de animações escolhido, o em uso e a espera (ombro/animacoes.py); None sem o laço rodando."""
        app = self.app
        return getattr(app, "animacao_publicada", None) if app is not None else None

    def m_providers(self, _p) -> dict:
        # `animation` vem aqui porque o app já lê providers.list a cada retrato: os mascotes dele seguem o pacote em uso
        return {"providers": self.publicado["providers"], "collected_at": self.publicado["collected_at"],
                "ombro_avatar": self.publicado.get("avatar_ombro"), "random_mode": self.publicado.get("avatar_sorteio"),
                "animation": self._animacao()}

    def m_sessions(self, p) -> dict:
        lista = self.publicado["sessions"]
        if p.get("provider"):
            lista = [s for s in lista if s["provider"] == p["provider"]]
        return {"sessions": lista, "collected_at": self.publicado["collected_at"]}

    def m_preview(self, _p) -> dict:
        """O último quadro mandado para a tela, em PNG (sem desenhar de novo). ~30-120 KiB."""
        app = self.app
        img = app.ultimo_quadro if app else None
        if img is None:
            return {"available": False}
        cache = self._preview_cache
        if cache is None or cache[0] is not img:  # o app pede a 5 fps: o mesmo quadro é codificado uma vez só
            buf = io.BytesIO()
            img.save(buf, "PNG")
            cache = self._preview_cache = (img, base64.b64encode(buf.getvalue()).decode("ascii"))
        return {"available": True, "format": "png", "width": img.width, "height": img.height,
                "captured_at": _iso(app.ultimo_quadro_em), "data_base64": cache[1]}

    def _cfg_atual(self) -> OmbroConfig:
        return self.app.cfg if self.app is not None else self.cfg

    def m_config_get(self, _p) -> dict:
        cfg = self._cfg_atual()
        valores = {}
        for k in sorted(PERSISTENTES):
            v = getattr(cfg, k)
            if isinstance(v, Path) or (k == "claude_dir" and v):
                v = curto(v)
            elif k == "avatares":
                v = {chave: cfg.avatar(chave) for chave in AVATAR_CHAVES}  # todas, com "padrao" no que falta
            elif isinstance(v, tuple):
                v = list(v)
            valores[k] = v
        arq = cfg.arquivo or arquivo_padrao()
        return {"file": curto(arq), "file_exists": arq.exists(), "values": valores,
                "providers_enabled": {pid: pid in cfg.providers for pid in PROVIDERS},
                "editable": list(preferencias.EDITAVEIS), "live": list(preferencias.AO_VIVO),
                "layouts_live": list(LAYOUTS), "views": list(VISTAS),
                # `values` é o que está em uso; `pending`, o que está gravado e só vale ao reiniciar o ombrod
                "pending": dict(self._pendentes), "revision": self._revisao, "instance": self._instancia,
                "animation": self._animacao()}

    def m_config_schema(self, _p) -> dict:
        """O que dá para mudar, com tipo, valores e se vale na hora: o Ombro.app monta os ajustes a partir disto."""
        return preferencias.esquema()

    def m_config_update(self, p) -> dict:
        return self._mudar_preferencias(p["changes"], p.get("persist", True))

    def m_brilho(self, p) -> dict:
        return self._mudar_preferencias({"brilho": p["value"]}, p.get("persist", False))

    def m_layout(self, p) -> dict:
        return self._mudar_preferencias({"layout": p["layout"]}, p.get("persist", False))

    def _mudar_preferencias(self, mudancas: dict, persistir: bool) -> dict:
        """O único caminho de uma mudança de preferência pela IPC, uma de cada vez (na ordem em que chegam):

            validar -> pôr em uso no laço (tudo junto) -> gravar no config.json -> responder com o que vale agora

        Se pôr em uso falha, nada muda e nada é gravado. Se gravar falha, o que entrou em uso volta atrás: a
        resposta de sucesso só sai com as duas coisas feitas. O que só vale ao reiniciar é gravado e fica em
        `pending` (no config.get também) até o ombrod reiniciar: gravado e em uso nunca se confundem."""
        try:
            mudancas = preferencias.validar_mudancas(mudancas, self._cfg_atual().providers)
        except ConfigInvalida as e:
            raise ErroIPC(VALIDATION_ERROR, str(e)) from None
        vivas = {k: v for k, v in mudancas.items() if preferencias.ao_vivo(k)}
        frias = {k: v for k, v in mudancas.items() if k not in vivas}
        if frias and not persistir:
            raise ErroIPC(VALIDATION_ERROR, f"{', '.join(frias)} só vale(m) depois de reiniciar o ombrod: "
                                            "use persist para gravar no config.json")
        with self._trava_config:
            app, antes, adiadas = self.app, None, []
            if app is None:
                if not persistir:
                    raise ErroIPC(SERVICE_UNAVAILABLE, "o ombrod ainda não está mostrando a tela (esperando outro Ombro sair?); "
                                                       "nada mudou. Com persist, a preferência é gravada para o próximo início")
                frias, vivas = dict(mudancas), {}
            elif vivas:
                antes, adiadas = self._no_laco(lambda: self._por_em_uso(vivas))
            arquivo = None
            if persistir:
                try:
                    arq, _ = preferencias.gravar(self._cfg_atual().arquivo or arquivo_padrao(), mudancas)
                    arquivo = curto(arq)
                except (ConfigInvalida, OSError) as e:
                    desfeito = self._desfazer(antes)
                    if isinstance(e, ConfigInvalida):
                        raise ErroIPC(VALIDATION_ERROR, sem_casa(e) + self._sobre_desfazer(desfeito)) from None
                    log.warning("não consegui gravar o config.json (%s)", sem_casa(e))
                    raise ErroIPC(SERVICE_UNAVAILABLE, "não consegui gravar o config.json; veja o log do ombrod"
                                  + self._sobre_desfazer(desfeito)) from None
                log.info("preferências gravadas: %s", ", ".join(sorted(mudancas)))
            cfg = self._cfg_atual()
            for k in vivas:
                self._pendentes.pop(k, None)
            for k, v in frias.items():  # gravado, mas só entra em uso ao reiniciar (a não ser que já seja o valor em uso)
                if preferencias.valor(cfg, k) == v:
                    self._pendentes.pop(k, None)
                else:
                    self._pendentes[k] = v
            self._revisao += 1
            return {"applied": list(vivas), "restart_required": [k for k in frias if k in self._pendentes],
                    "deferred": adiadas, "persisted": persistir, "file": arquivo,
                    "values": {k: preferencias.valor(cfg, k) for k in mudancas},
                    "pending": dict(self._pendentes), "revision": self._revisao, "instance": self._instancia,
                    "animation": self._animacao()}

    def _no_laco(self, fn):
        try:
            return self.app.executar(fn)
        except (Indisponivel, TimeoutError):
            raise ErroIPC(SERVICE_UNAVAILABLE, "o laço do Ombro não está atendendo agora; nada mudou. Tente de novo") from None

    def _por_em_uso(self, vivas: dict):
        """Roda dentro do laço (via Aplicacao.executar): tudo junto, ou nada."""
        app = self.app
        antes = app.cfg
        try:
            return antes, app.aplicar_cfg(preferencias.aplicar_em(antes, vivas))
        except Exception:
            log.error("não consegui pôr em uso %s; voltando ao que estava", ", ".join(sorted(vivas)), exc_info=True)
            app.aplicar_cfg(antes)
            raise ErroIPC(INTERNAL_ERROR, "não consegui pôr a mudança em uso; nada mudou. Veja o log do ombrod") from None

    def _desfazer(self, antes) -> Optional[bool]:
        """Volta ao que estava em uso antes (a gravação falhou). None: não havia nada a desfazer."""
        if antes is None:
            return None
        try:
            self.app.executar(lambda: self.app.aplicar_cfg(antes))
            return True
        except Exception:  # noqa: BLE001 - o laço parou no meio: o estado em uso fica o novo, e a resposta diz isso
            log.error("não consegui desfazer a mudança que entrou em uso e não foi gravada", exc_info=True)
            return False

    @staticmethod
    def _sobre_desfazer(desfeito: Optional[bool]) -> str:
        if desfeito is None:
            return ""
        return ". Nada mudou" if desfeito else ". A mudança ficou em uso, mas não foi gravada: vale até o ombrod reiniciar"


def argumentos(argv=None):
    import tela

    cfg, avisos = tela.argumentos(argv, prog="ombrod",
                                  descricao="O Ombro como serviço local, com a IPC para a CLI (python3 -m ombro).")
    if cfg.png:
        raise SystemExit("ombrod: --png não faz sentido no serviço; use python3 tela.py --png")
    from dataclasses import replace

    return replace(cfg, nao_abrir=True), avisos


def main(argv=None) -> int:
    from logging_config import configurar as configurar_logs

    cfg, avisos = argumentos(argv)
    configurar_logs(debug=cfg.debug)
    log_cfg = logger("config")
    for aviso in avisos:
        log_cfg.warning(aviso)
    log_cfg.info("configuração: %s", curto(cfg.arquivo) if cfg.arquivo else "padrões e linha de comando (sem arquivo)")
    log.debug("pasta do Ombro: %s", curto(pasta_ombro()))
    return Daemon(cfg).rodar()


if __name__ == "__main__":
    sys.exit(main())
