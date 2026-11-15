"""A IPC local (ombro/ipc.py): protocolo, validação, limites, segurança do socket e o cliente."""
import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ombro import ipc
from ombro.caminhos import CaminhoInseguro
from ombro.ipc import ClienteIPC, DaemonIndisponivel, ErroIPC, Metodo, Param, ServidorIPC


def pasta_curta():
    """O caminho de um socket tem limite (~104 bytes no macOS): uma pasta temporária curta."""
    return tempfile.mkdtemp(prefix="om", dir="/tmp" if os.path.isdir("/tmp") else None)


def bruto(caminho, dados: bytes, timeout=2.0) -> bytes:
    """Manda bytes crus e devolve a primeira linha da resposta."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(caminho))
        s.sendall(dados)
        buf = b""
        while b"\n" not in buf:
            parte = s.recv(65536)
            if not parte:
                break
            buf += parte
        return buf.split(b"\n", 1)[0]


class Base(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(pasta_curta())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.pasta, ignore_errors=True))
        self.sock = self.pasta / "run" / "o.sock"
        self.liberar = threading.Event()

        def lento(_p):
            self.liberar.wait(3)
            return {"ok": True}

        def quebra(_p):
            raise RuntimeError("segredo /Users/alguem/projeto")

        self.metodos = {m.nome: m for m in [
            Metodo("ping", lambda p: {"pong": True}, {}),
            Metodo("eco", lambda p: p, {"n": Param(int, obrigatorio=True, minimo=0, maximo=10),
                                        "nome": Param(str, opcoes=("a", "b")), "x": Param(bool)}),
            Metodo("lento", lento, {}),
            Metodo("quebra", quebra, {}),
            Metodo("mexe", lambda p: {"mexeu": True}, {}, admin=True),
        ]}

    def servidor(self, **kw):
        s = ServidorIPC(self.metodos, caminho=self.sock, **kw)
        s.iniciar()
        self.addCleanup(s.parar)
        self.addCleanup(self.liberar.set)
        return s


class Protocolo(Base):
    def test_chamada_valida_e_request_id_ecoado(self):
        self.servidor()
        with ClienteIPC(self.sock) as c:
            self.assertEqual(c.chamar("ping"), {"pong": True})
            self.assertEqual(c.chamar("eco", {"n": 3, "nome": "a"}), {"n": 3, "nome": "a"})  # na mesma conexão
        r = json.loads(bruto(self.sock, b'{"version":1,"request_id":"req-001","method":"ping"}\n'))
        self.assertEqual(r, {"version": 1, "request_id": "req-001", "ok": True, "result": {"pong": True}})

    def test_erros_padronizados(self):
        self.servidor()
        casos = [
            (b'{"version":1,"method":"shell.execute"}\n', "UNKNOWN_METHOD"),
            (b'nao e json\n', "INVALID_REQUEST"),
            (b'\xff\xfe\n', "INVALID_REQUEST"),
            (b'[1,2]\n', "INVALID_REQUEST"),
            (b'{"method":"ping"}\n', "INVALID_REQUEST"),
            (b'{"version":"1","method":"ping"}\n', "INVALID_REQUEST"),
            (b'{"version":2,"method":"ping"}\n', "UNSUPPORTED_VERSION"),
            (b'{"version":1,"method":"ping","extra":1}\n', "INVALID_REQUEST"),
            (b'{"version":1,"method":"ping","params":[]}\n', "INVALID_REQUEST"),
            (b'{"version":1,"request_id":"tem espaco","method":"ping"}\n', "INVALID_REQUEST"),
            (b'{"version":1,"method":"eco","params":{}}\n', "VALIDATION_ERROR"),
            (b'{"version":1,"method":"eco","params":{"n":"3"}}\n', "VALIDATION_ERROR"),
            (b'{"version":1,"method":"eco","params":{"n":true}}\n', "VALIDATION_ERROR"),
            (b'{"version":1,"method":"eco","params":{"n":11}}\n', "VALIDATION_ERROR"),
            (b'{"version":1,"method":"eco","params":{"n":1,"nome":"c"}}\n', "VALIDATION_ERROR"),
            (b'{"version":1,"method":"eco","params":{"n":1,"caminho":"/etc/passwd"}}\n', "VALIDATION_ERROR"),
        ]
        for pedido, codigo in casos:
            r = json.loads(bruto(self.sock, pedido))
            self.assertFalse(r["ok"], pedido)
            self.assertEqual(r["error"]["code"], codigo, pedido)
            self.assertIn(r["error"]["code"], ipc.CODIGOS)

    def test_versao_incompativel_mantem_o_request_id(self):
        self.servidor()
        r = json.loads(bruto(self.sock, b'{"version":9,"request_id":"r9","method":"ping"}\n'))
        self.assertEqual((r["request_id"], r["error"]["code"]), ("r9", "UNSUPPORTED_VERSION"))

    def test_erro_interno_nao_vaza_detalhes(self):
        self.servidor()
        with self.assertLogs("ombro.ipc", "ERROR"):
            r = json.loads(bruto(self.sock, b'{"version":1,"method":"quebra"}\n'))
        self.assertEqual(r["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("segredo", json.dumps(r))
        self.assertNotIn("Traceback", json.dumps(r))

    def test_mensagem_grande_demais_e_recusada(self):
        self.servidor(max_mensagem=1024)
        r = json.loads(bruto(self.sock, b'{"version":1,"method":"ping","params":{"x":"' + b"a" * 5000 + b'"}}\n'))
        self.assertEqual(r["error"]["code"], "INVALID_REQUEST")
        # sem \n nenhum também: não acumula para sempre
        r = json.loads(bruto(self.sock, b"a" * 5000))
        self.assertEqual(r["error"]["code"], "INVALID_REQUEST")

    def test_admin_recusado_quando_fechando(self):
        s = self.servidor()
        self.assertEqual(ipc.chamar("mexe", caminho=self.sock), {"mexeu": True})
        s.recusar_admin()
        with self.assertRaises(ErroIPC) as e:
            ipc.chamar("mexe", caminho=self.sock)
        self.assertEqual(e.exception.codigo, "SERVICE_UNAVAILABLE")
        self.assertEqual(ipc.chamar("ping", caminho=self.sock), {"pong": True})  # leitura segue

    def test_validar_params(self):
        m = Metodo("t", None, {"v": Param(int, minimo=0, maximo=100), "f": Param(float), "s": Param(str, nulo=True)})
        self.assertEqual(ipc.validar_params(m, None), {})
        self.assertEqual(ipc.validar_params(m, {"v": 0, "f": 1, "s": None}), {"v": 0, "f": 1, "s": None})
        with self.assertRaises(ErroIPC):
            ipc.validar_params(m, {"s": "x" * 65})


class Conexoes(Base):
    def test_cliente_parado_e_desconectado(self):
        self.servidor(tempo_parado=0.3)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(self.sock))
            t0 = time.monotonic()
            self.assertEqual(s.recv(10), b"")  # o servidor fecha a conexão parada
            self.assertLess(time.monotonic() - t0, 1.5)

    def test_cliente_que_some_no_meio_nao_atrapalha_os_outros(self):
        self.servidor()
        for _ in range(5):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(str(self.sock))
            s.sendall(b'{"version":1,"meth')
            s.close()
        self.assertEqual(ipc.chamar("ping", caminho=self.sock), {"pong": True})

    def test_conexoes_concorrentes(self):
        self.servidor()
        resultados, erros = [], []

        def cliente(i):
            try:
                with ClienteIPC(self.sock) as c:
                    for _ in range(20):
                        resultados.append(c.chamar("eco", {"n": i % 10})["n"])
            except Exception as e:  # noqa: BLE001
                erros.append(e)

        ts = [threading.Thread(target=cliente, args=(i,)) for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(erros, [])
        self.assertEqual(len(resultados), 120)

    def test_limite_de_clientes(self):
        self.servidor(max_clientes=1)
        ocupa = ClienteIPC(self.sock).conectar()
        self.addCleanup(ocupa.fechar)
        ocupa.chamar("ping")
        with self.assertRaises(ErroIPC) as e:
            ipc.chamar("ping", caminho=self.sock)
        self.assertEqual(e.exception.codigo, "SERVICE_UNAVAILABLE")
        ocupa.fechar()
        time.sleep(0.2)
        self.assertEqual(ipc.chamar("ping", caminho=self.sock), {"pong": True})

    def test_cliente_reconecta_quando_o_servidor_fecha_a_conexao(self):
        self.servidor(tempo_parado=0.2)
        with ClienteIPC(self.sock) as c:
            self.assertEqual(c.chamar("ping"), {"pong": True})
            time.sleep(0.5)  # o servidor fecha a conexão parada
            self.assertEqual(c.chamar("ping"), {"pong": True})  # conexão nova, sem erro
        with mock.patch.object(ipc, "MAX_PEDIDOS_POR_CONEXAO", 3):
            with ClienteIPC(self.sock) as c:
                for _ in range(10):
                    self.assertEqual(c.chamar("ping"), {"pong": True})

    def test_timeout_do_cliente(self):
        self.servidor()
        with self.assertRaises(DaemonIndisponivel):
            ipc.chamar("lento", caminho=self.sock, timeout=0.3)

    def test_encerrar_durante_um_pedido(self):
        s = ServidorIPC(self.metodos, caminho=self.sock)
        s.iniciar()
        erro = []

        def pede():
            try:
                ipc.chamar("lento", caminho=self.sock, timeout=5)
            except (DaemonIndisponivel, ErroIPC) as e:
                erro.append(e)

        t = threading.Thread(target=pede)
        t.start()
        time.sleep(0.2)
        t0 = time.monotonic()
        s.parar()
        self.liberar.set()
        t.join(5)
        self.assertLess(time.monotonic() - t0, 3)
        self.assertFalse(self.sock.exists())  # o socket sai junto
        self.assertFalse(t.is_alive())

    def test_socket_indisponivel(self):
        with self.assertRaises(DaemonIndisponivel):
            ipc.chamar("ping", caminho=self.sock)
        # socket que sobrou sem ninguém ouvindo
        self.sock.parent.mkdir(mode=0o700)
        morto = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        morto.bind(str(self.sock))
        morto.close()
        with self.assertRaises(DaemonIndisponivel):
            ipc.chamar("ping", caminho=self.sock)


class Seguranca(Base):
    def test_permissoes(self):
        self.servidor()
        self.assertEqual(stat.S_IMODE(os.stat(self.sock).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.sock.parent).st_mode), 0o700)

    def test_uid_do_cliente_e_o_nosso(self):
        self.servidor()
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with a, b:
            uid = ipc.uid_do_cliente(a)
        self.assertIn(uid, (os.getuid(), None))  # None só onde o sistema não informa

    def test_socket_que_sobrou_e_substituido(self):
        self.sock.parent.mkdir(mode=0o700)
        velho = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        velho.bind(str(self.sock))
        velho.close()
        self.servidor()
        self.assertEqual(ipc.chamar("ping", caminho=self.sock), {"pong": True})

    def test_arquivo_que_nao_e_socket_nunca_e_apagado(self):
        self.sock.parent.mkdir(mode=0o700)
        self.sock.write_text("meu arquivo")
        with self.assertRaises(CaminhoInseguro):
            ServidorIPC(self.metodos, caminho=self.sock).iniciar()
        self.assertEqual(self.sock.read_text(), "meu arquivo")
        with self.assertRaises(DaemonIndisponivel):  # o cliente também não fala com o que não é socket
            ipc.chamar("ping", caminho=self.sock)

    def test_pasta_link_simbolico_e_recusada(self):
        alvo = self.pasta / "outra"
        alvo.mkdir(mode=0o700)
        (self.pasta / "run").symlink_to(alvo)
        with self.assertRaises(CaminhoInseguro):
            ServidorIPC(self.metodos, caminho=self.sock).iniciar()
        self.assertEqual(list(alvo.iterdir()), [])

    def test_pasta_aberta_e_fechada(self):
        (self.pasta / "run").mkdir(mode=0o755)
        os.chmod(self.pasta / "run", 0o755)
        self.servidor()
        self.assertEqual(stat.S_IMODE(os.stat(self.sock.parent).st_mode), 0o700)

    def test_caminho_longo_demais(self):
        longo = self.pasta / ("x" * 120) / "o.sock"
        with self.assertRaises(CaminhoInseguro):
            ServidorIPC(self.metodos, caminho=longo).iniciar()

    def test_parar_nao_apaga_socket_de_outro(self):
        s = ServidorIPC(self.metodos, caminho=self.sock)
        s.iniciar()
        os.unlink(self.sock)  # alguém trocou o arquivo enquanto rodava
        self.sock.write_text("de outro")
        s.parar()
        self.assertEqual(self.sock.read_text(), "de outro")


if __name__ == "__main__":
    unittest.main()
