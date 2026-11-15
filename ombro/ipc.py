"""A interface local do Ombro: JSON Lines por um Unix Domain Socket. Contrato completo em docs/ipc.md.

Pedido (uma linha, no máximo MAX_MENSAGEM bytes):
  {"version": 1, "request_id": "req-1", "method": "status", "params": {}}
Resposta (uma linha):
  {"version": 1, "request_id": "req-1", "ok": true, "result": {...}}
  {"version": 1, "request_id": "req-1", "ok": false, "error": {"code": "UNKNOWN_METHOD", "message": "..."}}

Segurança, em camadas:
  - só socket local (AF_UNIX); nada de porta de rede;
  - a pasta run/ é 0700 e do usuário; o socket nasce 0600 (umask) e é conferido depois do bind;
  - quem conecta precisa ser o mesmo usuário (LOCAL_PEERCRED no macOS, SO_PEERCRED no Linux); onde o
    sistema não diz, valem só as permissões dos arquivos;
  - cada método tem o seu esquema de parâmetros; nada de comando genérico, shell, eval ou caminho
    vindo do cliente;
  - limites de tamanho, de clientes ao mesmo tempo, de pedidos por conexão e de tempo parado;
  - erros sem traceback nem dado privado: o detalhe vai para o log local.

O servidor roda em threads próprias e nunca toca o laço de desenho: os métodos de leitura usam o que o
laço publicou; os administrativos pedem ao laço (ombro.app.Aplicacao.executar) e esperam pouco.
"""
from __future__ import annotations

import errno
import json
import os
import re
import socket
import stat
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from logging_config import logger

from .caminhos import CaminhoInseguro, arquivo_socket, garantir_pasta_privada, socket_cabe

log = logger("ipc")

PROTOCOLO = 1
MAX_MENSAGEM = 64 * 1024  # bytes de um pedido, com o \n
MAX_RESPOSTA = 4 * 1024 * 1024  # o cliente recusa respostas maiores (a prévia do quadro tem ~100 KiB)
MAX_CLIENTES = 8
MAX_PEDIDOS_POR_CONEXAO = 1000  # depois disso o servidor fecha; o ClienteIPC reconecta sozinho
TEMPO_PARADO = 5.0  # segundos sem receber nada: a conexão fecha
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")

# códigos de erro (docs/ipc.md)
INVALID_REQUEST = "INVALID_REQUEST"
UNKNOWN_METHOD = "UNKNOWN_METHOD"
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
VALIDATION_ERROR = "VALIDATION_ERROR"
SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
PERMISSION_DENIED = "PERMISSION_DENIED"
CODIGOS = (INVALID_REQUEST, UNKNOWN_METHOD, UNSUPPORTED_VERSION, VALIDATION_ERROR, SERVICE_UNAVAILABLE,
           INTERNAL_ERROR, PERMISSION_DENIED)


class ErroIPC(Exception):
    """Um erro do protocolo, com um código de CODIGOS e uma mensagem curta e segura para o cliente."""

    def __init__(self, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo, self.mensagem = codigo, mensagem


class DaemonIndisponivel(ConnectionError):
    """Não há ombrod respondendo neste socket."""


# ---------- esquema dos parâmetros ----------
@dataclass(frozen=True)
class Param:
    tipo: type  # bool, int, str, dict
    obrigatorio: bool = False
    opcoes: Optional[tuple] = None  # valores permitidos (para str)
    minimo: Optional[float] = None
    maximo: Optional[float] = None
    nulo: bool = False  # aceita null
    max_tamanho: int = 64  # para str


@dataclass(frozen=True)
class Metodo:
    nome: str
    fn: Callable[[dict], Any]
    params: Dict[str, Param]
    admin: bool = False  # muda algo; recusado enquanto o ombrod fecha


def validar_params(metodo: Metodo, params) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ErroIPC(INVALID_REQUEST, "params deve ser um objeto")
    desconhecidos = sorted(set(params) - set(metodo.params))
    if desconhecidos:
        raise ErroIPC(VALIDATION_ERROR, f"parâmetro desconhecido em {metodo.nome}: {', '.join(desconhecidos)[:80]}")
    limpo = {}
    for nome, regra in metodo.params.items():
        if nome not in params:
            if regra.obrigatorio:
                raise ErroIPC(VALIDATION_ERROR, f"{metodo.nome} precisa de {nome}")
            continue
        v = params[nome]
        if v is None and regra.nulo:
            limpo[nome] = None
            continue
        if regra.tipo is int:
            ok = isinstance(v, int) and not isinstance(v, bool)
        elif regra.tipo is float:
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        else:
            ok = isinstance(v, regra.tipo)
        if not ok:
            raise ErroIPC(VALIDATION_ERROR, f"{nome} deve ser do tipo {regra.tipo.__name__}")
        if isinstance(v, str):
            if len(v) > regra.max_tamanho:
                raise ErroIPC(VALIDATION_ERROR, f"{nome} é longo demais")
            if regra.opcoes is not None and v not in regra.opcoes:
                raise ErroIPC(VALIDATION_ERROR, f"{nome} deve ser um de: {', '.join(regra.opcoes)}")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if (regra.minimo is not None and v < regra.minimo) or (regra.maximo is not None and v > regra.maximo):
                raise ErroIPC(VALIDATION_ERROR, f"{nome} deve estar entre {regra.minimo:g} e {regra.maximo:g}")
        limpo[nome] = v
    return limpo


# ---------- mensagens ----------
def _linha(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8") + b"\n"


def resposta_ok(request_id, resultado) -> bytes:
    return _linha({"version": PROTOCOLO, "request_id": request_id, "ok": True, "result": resultado})


def resposta_erro(request_id, codigo: str, mensagem: str) -> bytes:
    return _linha({"version": PROTOCOLO, "request_id": request_id, "ok": False,
                   "error": {"code": codigo, "message": mensagem}})


def interpretar(linha: bytes, metodos: Dict[str, Metodo]):
    """(request_id, Metodo, params validados), ou ErroIPC (com o request_id, se deu para ler)."""
    try:
        pedido = json.loads(linha.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ErroIPC(INVALID_REQUEST, "a mensagem não é um JSON válido") from None
    if not isinstance(pedido, dict):
        raise ErroIPC(INVALID_REQUEST, "a mensagem deve ser um objeto JSON")
    rid = pedido.get("request_id")
    if rid is not None and (not isinstance(rid, str) or not _REQUEST_ID.fullmatch(rid)):
        raise ErroIPC(INVALID_REQUEST, "request_id deve ter de 1 a 64 caracteres: letras, números, . _ : -")
    try:
        versao = pedido.get("version")
        if isinstance(versao, bool) or not isinstance(versao, int):
            raise ErroIPC(INVALID_REQUEST, "version é obrigatório e deve ser um inteiro")
        if versao != PROTOCOLO:
            raise ErroIPC(UNSUPPORTED_VERSION, f"este ombrod fala a versão {PROTOCOLO} do protocolo (veio {versao})")
        extras = set(pedido) - {"version", "request_id", "method", "params"}
        if extras:
            raise ErroIPC(INVALID_REQUEST, f"campo desconhecido: {', '.join(sorted(extras))[:80]}")
        nome = pedido.get("method")
        if not isinstance(nome, str) or not nome:
            raise ErroIPC(INVALID_REQUEST, "method é obrigatório")
        metodo = metodos.get(nome)
        if metodo is None:
            raise ErroIPC(UNKNOWN_METHOD, f"método desconhecido: {nome[:64]}")
        return rid, metodo, validar_params(metodo, pedido.get("params"))
    except ErroIPC as e:
        e.request_id = rid  # type: ignore[attr-defined]
        raise


# ---------- quem está do outro lado ----------
def uid_do_cliente(conn: socket.socket) -> Optional[int]:
    """O uid do processo conectado, ou None se o sistema não informa."""
    try:
        if sys.platform == "darwin":
            # getsockopt(SOL_LOCAL=0, LOCAL_PEERCRED=1) -> struct xucred {u_int cr_version; uid_t cr_uid; ...}
            dados = conn.getsockopt(0, 1, 76)
            versao, uid = struct.unpack_from("=II", dados, 0)
            return uid if versao == 0 else None
        if hasattr(socket, "SO_PEERCRED"):
            dados = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", dados)
            return uid
    except (OSError, struct.error):
        return None
    return None


def conferir_socket(caminho: Path) -> None:
    """O arquivo é um socket do nosso usuário? Senão, levanta CaminhoInseguro (o cliente não conecta)."""
    st = os.lstat(caminho)
    if not stat.S_ISSOCK(st.st_mode):
        raise CaminhoInseguro(f"{caminho.name} existe mas não é um socket")
    if st.st_uid != os.getuid():
        raise CaminhoInseguro(f"{caminho.name} é de outro usuário")


# ---------- servidor ----------
class ServidorIPC:
    def __init__(self, metodos: Dict[str, Metodo], caminho: Optional[Path] = None, max_clientes: int = MAX_CLIENTES,
                 tempo_parado: float = TEMPO_PARADO, max_mensagem: int = MAX_MENSAGEM):
        self.metodos = metodos
        self.caminho = Path(caminho) if caminho is not None else arquivo_socket()
        self.max_mensagem, self.tempo_parado = max_mensagem, tempo_parado
        self._vagas = threading.BoundedSemaphore(max_clientes)
        self._sock: Optional[socket.socket] = None
        self._inode: Optional[tuple] = None  # (dev, ino) do socket que criamos: só esse é apagado no fim
        self._parando = threading.Event()
        self._recusar_admin = threading.Event()
        self._conexoes: set = set()
        self._trava = threading.Lock()
        self._threads: list = []
        self.atendidos = 0  # pedidos respondidos (para o status e o benchmark)

    # ---------- abrir ----------
    def iniciar(self) -> None:
        """Cria o socket. Só chame com o lock de instância na mão: um socket que sobrou é então de uma
        instância que já morreu e pode ser trocado (se for mesmo um socket nosso)."""
        if not socket_cabe(self.caminho):
            raise CaminhoInseguro("o caminho do socket é longo demais para o sistema; use um OMBRO_SCREEN_DIR mais curto")
        garantir_pasta_privada(self.caminho.parent)
        try:
            conferir_socket(self.caminho)
        except FileNotFoundError:
            pass
        else:
            os.unlink(self.caminho)  # socket nosso que sobrou de uma instância que caiu
            log.info("socket antigo de uma instância que já não roda foi substituído")
        # (um arquivo que não é socket, ou de outro usuário, levanta CaminhoInseguro acima e não é apagado)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        antiga = os.umask(0o177)  # o socket já nasce 0600: não há janela com ele aberto
        try:
            sock.bind(str(self.caminho))
        except BaseException:
            sock.close()
            raise
        finally:
            os.umask(antiga)
        os.chmod(self.caminho, 0o600)
        st = os.lstat(self.caminho)
        self._inode = (st.st_dev, st.st_ino)
        sock.listen(16)
        sock.settimeout(0.2)  # para o laço de accept notar o pedido de parar
        self._sock = sock
        t = threading.Thread(target=self._aceitar, name="ombro-ipc", daemon=True)
        t.start()
        self._threads.append(t)
        log.debug("IPC ouvindo em run/%s (protocolo %d)", self.caminho.name, PROTOCOLO)

    def recusar_admin(self) -> None:
        """Fechando: métodos de leitura seguem respondendo; os que mudam algo recebem SERVICE_UNAVAILABLE."""
        self._recusar_admin.set()

    # ---------- atender ----------
    def _aceitar(self) -> None:
        while not self._parando.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._parando.is_set():
                    return
                time.sleep(0.1)  # EMFILE e afins: não gira em falso
                continue
            uid = uid_do_cliente(conn)
            if uid is not None and uid != os.getuid():
                log.warning("conexão de outro usuário recusada no socket do Ombro")
                self._enviar_e_fechar(conn, resposta_erro(None, PERMISSION_DENIED, "acesso negado"))
                continue
            if not self._vagas.acquire(blocking=False):
                self._enviar_e_fechar(conn, resposta_erro(None, SERVICE_UNAVAILABLE, "clientes demais ao mesmo tempo"))
                continue
            with self._trava:
                self._conexoes.add(conn)
            t = threading.Thread(target=self._atender, args=(conn,), name="ombro-ipc-cliente", daemon=True)
            t.start()

    @staticmethod
    def _enviar_e_fechar(conn: socket.socket, dados: bytes) -> None:
        try:
            conn.settimeout(0.5)
            conn.sendall(dados)
        except OSError:
            pass
        finally:
            conn.close()

    def _atender(self, conn: socket.socket) -> None:
        buf = b""
        pedidos = 0
        try:
            conn.settimeout(self.tempo_parado)
            while not self._parando.is_set():
                fim = buf.find(b"\n")
                if fim < 0:
                    if len(buf) >= self.max_mensagem:
                        conn.sendall(resposta_erro(None, INVALID_REQUEST, f"mensagem maior que {self.max_mensagem} bytes"))
                        return
                    try:
                        parte = conn.recv(min(65536, self.max_mensagem - len(buf) + 1))
                    except socket.timeout:
                        return  # cliente parado: libera a vaga
                    if not parte:
                        return  # o cliente fechou
                    buf += parte
                    continue
                linha, buf = buf[:fim], buf[fim + 1:]
                if len(linha) + 1 > self.max_mensagem:
                    conn.sendall(resposta_erro(None, INVALID_REQUEST, f"mensagem maior que {self.max_mensagem} bytes"))
                    return
                if not linha.strip():
                    continue
                conn.sendall(self._responder(linha))
                pedidos += 1
                if pedidos >= MAX_PEDIDOS_POR_CONEXAO:
                    return
        except OSError:
            pass  # o cliente sumiu no meio: nada a fazer
        finally:
            with self._trava:
                self._conexoes.discard(conn)
            try:
                conn.close()
            finally:
                self._vagas.release()

    def _responder(self, linha: bytes) -> bytes:
        rid = None
        try:
            rid, metodo, params = interpretar(linha, self.metodos)
            if metodo.admin and self._recusar_admin.is_set():
                raise ErroIPC(SERVICE_UNAVAILABLE, "o ombrod está fechando")
            resultado = metodo.fn(params)
            self.atendidos += 1
            return resposta_ok(rid, resultado)
        except ErroIPC as e:
            return resposta_erro(getattr(e, "request_id", rid), e.codigo, e.mensagem)
        except Exception:  # noqa: BLE001 - o detalhe fica no log local, nunca na resposta
            log.error("erro interno ao atender um pedido da IPC", exc_info=True)
            return resposta_erro(rid, INTERNAL_ERROR, "erro interno; veja o log do ombrod")

    # ---------- fechar ----------
    def parar(self) -> None:
        """Fecha o socket de escuta, derruba as conexões abertas e apaga o socket, se ainda for o nosso."""
        self._parando.set()
        self._recusar_admin.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._trava:
            abertas = list(self._conexoes)
        for conn in abertas:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=2.0)
        if self._inode is not None:
            try:
                st = os.lstat(self.caminho)
                if (st.st_dev, st.st_ino) == self._inode:
                    os.unlink(self.caminho)
            except FileNotFoundError:
                pass
            self._inode = None
        self._sock = None


# ---------- cliente ----------
class ClienteIPC:
    """Uma conexão com o ombrod. `chamar` levanta DaemonIndisponivel ou ErroIPC."""

    def __init__(self, caminho: Optional[Path] = None, timeout: float = 3.0):
        self.caminho = Path(caminho) if caminho is not None else arquivo_socket()
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._n = 0

    def conectar(self) -> "ClienteIPC":
        if self._sock is not None:
            return self
        try:
            conferir_socket(self.caminho)
        except FileNotFoundError:
            raise DaemonIndisponivel("o ombrod não está rodando") from None
        except CaminhoInseguro as e:
            raise DaemonIndisponivel(str(e)) from None
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.caminho))
        except OSError as e:
            sock.close()
            if e.errno in (errno.ECONNREFUSED, errno.ENOENT):
                raise DaemonIndisponivel("o ombrod não está rodando (socket sem ninguém ouvindo)") from None
            if isinstance(e, socket.timeout):
                raise DaemonIndisponivel("o ombrod não respondeu a tempo") from None
            raise DaemonIndisponivel(f"não consegui falar com o ombrod ({e.strerror or e})") from None
        self._sock = sock
        return self

    def chamar(self, metodo: str, params: Optional[dict] = None):
        reaproveitada = self._sock is not None
        self.conectar()
        self._n += 1
        rid = f"cli-{os.getpid()}-{self._n}"
        pedido = {"version": PROTOCOLO, "request_id": rid, "method": metodo, "params": params or {}}
        try:
            self._sock.sendall(_linha(pedido))
            linha = self._ler_linha()
        except socket.timeout:
            self.fechar()
            raise DaemonIndisponivel("o ombrod não respondeu a tempo") from None
        except OSError as e:
            self.fechar()
            if reaproveitada and isinstance(e, (BrokenPipeError, ConnectionResetError)):
                # o servidor fechou uma conexão antiga (parada demais, ou pedidos demais): uma conexão nova,
                # uma vez só. Todos os métodos são idempotentes, então repetir não muda nada duas vezes.
                return self.chamar(metodo, params)
            raise DaemonIndisponivel(f"a conexão com o ombrod caiu ({e.strerror or e})") from None
        try:
            resposta = json.loads(linha.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ErroIPC(INVALID_REQUEST, "resposta inválida do ombrod") from None
        if not isinstance(resposta, dict) or resposta.get("version") != PROTOCOLO:
            raise ErroIPC(UNSUPPORTED_VERSION, "o ombrod fala outra versão do protocolo")
        if resposta.get("ok"):
            return resposta.get("result")
        erro = resposta.get("error") or {}
        raise ErroIPC(str(erro.get("code", INTERNAL_ERROR)), str(erro.get("message", "erro do ombrod")))

    def _ler_linha(self) -> bytes:
        while b"\n" not in self._buf:
            if len(self._buf) > MAX_RESPOSTA:
                raise ErroIPC(INVALID_REQUEST, "resposta grande demais")
            parte = self._sock.recv(65536)
            if not parte:
                raise ConnectionResetError(errno.ECONNRESET, "o ombrod fechou a conexão")
            self._buf += parte
        linha, self._buf = self._buf.split(b"\n", 1)
        return linha

    def fechar(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buf = b""

    def __enter__(self) -> "ClienteIPC":
        return self.conectar()

    def __exit__(self, *_exc) -> None:
        self.fechar()


def chamar(metodo: str, params: Optional[dict] = None, caminho: Optional[Path] = None, timeout: float = 3.0):
    """Atalho: uma chamada numa conexão nova."""
    with ClienteIPC(caminho, timeout) as c:
        return c.chamar(metodo, params)
