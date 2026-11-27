"""Regressões da review da PR 1 (OC1 a OC4, OC7 a OC9): replay, tombstones, âncora da origem, revogação,
timestamp da fonte e diagnóstico. Pastas temporárias; nenhum OpenCode."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core.models import Activity, Attention
from core.monitor import Monitor
from providers.opencode import OpenCodeProvider

ORIGEM, ANCORA, COPIA = "oc-cccccccccccccccccccccccc", "a" * 32, "b" * 32
OUTRA = "oc-dddddddddddddddddddddddd"


class Base(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="ombro-oc-reg-"))
        self.addCleanup(shutil.rmtree, self.pasta, True)
        env = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta), "HOME": str(self.pasta / "casa")})
        env.start()
        self.addCleanup(env.stop)
        self.eventos, self.opencode = self.pasta / "eventos", self.pasta / "opencode"
        self.eventos.mkdir()
        (self.opencode / "live").mkdir(parents=True)
        self.agora = datetime.now().replace(microsecond=0)
        self.vivos, self.n, self.arq, self.monitor = {100}, 0, 0, None
        self.registrar()
        self.ligar()

    def registrar(self, origem=ORIGEM):
        """O que `integrations install opencode` deixa: a origem registrada, esperando a primeira conexão."""
        (self.opencode / "integration.json").write_text(json.dumps({"origin": origem}))
        (self.opencode / "registry.json").write_text(json.dumps(
            {"v": 2, "current": origem, "origins": {origem: {"state": "pending", "registered_at": 1}}}))

    def ligar(self):
        if self.monitor is not None:
            self.monitor.fechar()
        self.p = OpenCodeProvider(self.opencode, relogio=lambda: self.agora, pid_vivo=lambda pid: pid in self.vivos,
                                  instalado=lambda: True)
        self.monitor = Monitor.montar([self.p], banco=self.pasta / "state.db", relogio=lambda: self.agora,
                                      eventos=self.eventos)
        self.addCleanup(self.monitor.fechar)

    def ms(self, atras=0.0):
        return int((self.agora.timestamp() - atras) * 1000)

    def pulso(self, origem=ORIGEM, pid=100, boot="boot1", ns="marca1", anc=ANCORA, estado="ready", **extra):
        (self.opencode / "live" / f"{origem}.{pid}.json").write_text(json.dumps(
            {"v": 1, "origin": origem, "pid": pid, "boot": boot, "ns": ns, "anchor": anc, "state": estado,
             "version": "2.4.2", "ts": self.ms(), **extra}))

    def ev(self, nome, sid="ses_a", id=None, boot="boot1", ns="marca1", anc=ANCORA, seq=None, req=None, n=None,
           at="agora", snap=None, origem=ORIGEM, atras=1.0):
        """Um arquivo na caixa, como o plugin grava. `n` é a ordem do produtor; `at`, o horário da fonte."""
        self.arq += 1
        self.n = self.n + 1 if n is None else self.n
        x = {"origin": origem, "boot": boot, "ns": ns, "anc": anc, "n": self.n if n is None else n,
             "id": f"evt_{self.arq}" if id is None else id}
        if at is not None:
            x["at"] = self.ms(atras) if at == "agora" else at
        for chave, valor in (("seq", seq), ("req", req), ("snap", snap)):
            if valor is not None:
                x[chave] = valor
        d = {"v": 1, "provider": "opencode", "session": f"{origem}:{sid}" if sid else origem, "event": nome,
             "ts": self.ms(atras), "project": "api", "detail": None, "x": x}
        nome_arq = self.eventos / f"{self.arq:020d}-1-abc.json"
        nome_arq.write_text(json.dumps(d))
        os.utime(self.eventos, (time.time() + self.arq, time.time() + self.arq))
        return d

    def repor(self, evento):
        """O mesmo arquivo de novo na caixa (ficou de uma queda entre ler e apagar, ou foi regravado)."""
        self.arq += 1
        (self.eventos / f"{self.arq:020d}-1-abc.json").write_text(json.dumps(evento))
        os.utime(self.eventos, (time.time() + self.arq, time.time() + self.arq))

    def coletar(self):
        self.monitor.atualizar_todos()
        return self.monitor.store.snapshot("opencode")

    def estado(self, sid="ses_a"):
        s = {x.session_id: x for x in self.coletar().sessions}.get(f"{ORIGEM}:{sid}")
        return None if s is None else (s.activity, s.attention)


class Replay(Base):
    def test_oc7_pedido_antigo_nao_volta_depois_de_reiniciar(self):
        self.pulso()
        self.ev("session.execution.started", seq=2)
        pedido = self.ev("permission.asked", req="per_1")
        self.ev("permission.replied", req="per_1")
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.NONE))
        self.ligar()  # restaurado do banco
        self.ev("session.execution.started", seq=20)  # atualizado
        self.coletar()
        self.repor(pedido)
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.NONE))

    def test_oc7_pergunta_respondida_nao_reaparece_nem_com_id_novo(self):
        self.pulso()
        self.ev("session.execution.started", seq=2)
        self.ev("form.created", req="frm_1", n=5)
        self.ev("form.replied", req="frm_1", n=6)
        self.coletar()
        self.ligar()
        self.ev("session.execution.started", seq=9, n=7)
        self.ev("form.created", req="frm_1", n=5, id="evt_regravado")
        self.assertNotEqual(self.estado()[1], Attention.WAITING_USER)

    def test_oc7_execucao_antiga_nao_reabre_depois_de_reiniciar(self):
        self.pulso()
        self.ev("session.execution.started", seq=2)
        self.ev("session.execution.succeeded", seq=12)
        self.coletar()
        self.ligar()
        self.ev("session.execution.started", seq=2, id="evt_outro")
        estado = self.estado()
        self.assertNotEqual(estado[0], Activity.WORKING)

    def test_oc7_eventos_invalidos_nao_mudam_nada(self):
        self.pulso()
        self.ev("session.execution.started", seq=2)
        self.assertEqual(self.estado()[0], Activity.WORKING)
        for ruim in (dict(n=-1), dict(n="x"), dict(boot=""), dict(anc=None), dict(id=""), dict(seq="3"), dict(seq=-1),
                     dict(seq=True)):
            self.ev("session.execution.failed", **ruim)
        self.ev("permission.asked", req=None)
        self.ev("permission.asked", req="../x")
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.NONE))

    def test_oc7_ordem_pela_sequencia_duravel_e_nao_pela_chegada_nem_pelo_relogio(self):
        self.pulso()
        self.ev("session.execution.succeeded", seq=12, atras=30)
        self.coletar()
        self.ev("session.execution.started", seq=14, atras=60)  # relógio "mais velho", sequência mais nova: vale
        self.assertEqual(self.estado()[0], Activity.WORKING)
        self.ev("session.execution.succeeded", seq=13, atras=1)  # relógio novo, sequência velha: não vale
        self.assertEqual(self.estado()[0], Activity.WORKING)

    def test_oc7_fonte_morta_nao_confirma_estado(self):
        self.ev("session.execution.failed", seq=4)  # ficou na caixa; o processo já não existe
        self.ev("permission.asked", req="per_1")
        self.assertEqual(self.estado(), (Activity.UNKNOWN, Attention.UNKNOWN))


class Remocao(Base):
    def test_oc1_resposta_tardia_nao_ressuscita(self):
        self.pulso()
        self.ev("session.execution.started", seq=2)
        self.ev("permission.asked", req="per_1")
        self.ev("session.deleted", seq=3)
        self.assertIsNone(self.estado())
        self.ev("permission.replied", req="per_1")
        self.assertIsNone(self.estado())

    def test_oc1_nada_ressuscita_depois_de_reiniciar(self):
        self.pulso()
        velho = self.ev("session.execution.started", seq=2)
        self.ev("session.deleted", seq=3)
        self.coletar()
        self.ligar()
        self.repor(velho)
        self.ev("session.execution.succeeded", seq=2, id="evt_z")
        self.ev("permission.replied", req="per_9")
        self.ev("form.created", req="frm_9")
        self.assertIsNone(self.estado())

    def test_oc1_recriacao_oferecida_pelo_opencode_vale(self):
        self.pulso()
        self.ev("session.created", seq=0, n=1)
        self.ev("session.deleted", seq=1, n=2)
        self.coletar()
        self.ev("session.created", seq=0, n=3)  # evento novo, ordem posterior: outra vida do mesmo id
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.NONE))

    def test_oc7_remocao_perdida_so_com_prova(self):
        self.pulso()
        self.ev("session.execution.started", seq=2, n=1)
        self.coletar()
        self.ev("ombro.removed", id="", n=2)  # o plugin fez session.get e recebeu "não existe"
        self.assertIsNone(self.estado())


class Ancora(Base):
    def conectar(self):
        self.pulso()
        self.ev("session.execution.started", seq=2)
        self.assertEqual(self.estado()[0], Activity.WORKING)

    def test_oc8_copia_com_a_mesma_marca_e_configuracao_nao_entra(self):
        self.conectar()
        self.vivos = {200}
        (self.opencode / "live" / f"{ORIGEM}.100.json").unlink()
        self.ligar()  # em sequência, com o Ombro reiniciado
        self.pulso(pid=200, boot="boot2", anc=COPIA)
        self.ev("session.execution.started", sid="ses_copia", seq=2, boot="boot2", anc=COPIA)
        snap = self.coletar()
        self.assertNotIn(f"{ORIGEM}:ses_copia", [s.session_id for s in snap.sessions])
        self.assertEqual(snap.condition, "conflict")

    def test_oc8_copia_simultanea_suspende(self):
        self.conectar()
        self.vivos = {100, 200}
        self.pulso(pid=200, boot="boot2", anc=COPIA)
        self.ev("session.execution.started", sid="ses_copia", seq=2, boot="boot2", anc=COPIA)
        snap = self.coletar()
        self.assertEqual((snap.condition, [s.activity for s in snap.sessions]), ("conflict", [Activity.UNKNOWN]))

    def test_oc8_origem_sem_registro_nao_entra(self):
        outra = "oc-dddddddddddddddddddddddd"
        self.pulso(origem=outra)
        self.ev("session.execution.started", origem=outra, seq=2)
        snap = self.coletar()
        self.assertEqual((snap.sessions, snap.condition), ((), "unregistered"))
        self.assertNotIn(outra, json.loads((self.opencode / "registry.json").read_text())["origins"])  # nem é adotada

    def test_oc2_desinstalar_revoga_e_o_plugin_ativo_nao_e_readotado(self):
        from ombro import integracao_opencode

        self.conectar()
        integracao_opencode.remover()
        self.ev("session.execution.started", sid="ses_depois", seq=5)
        snap = self.coletar()
        self.assertEqual(snap.sessions, ())
        self.ligar()
        self.pulso()
        self.ev("session.execution.started", sid="ses_mais", seq=6)
        self.assertEqual(self.coletar().sessions, ())
        integracao_opencode.instalar()  # registro novo, explícito: outra origem
        self.assertNotEqual(integracao_opencode.origem_salva(), ORIGEM)
        self.ev("session.execution.started", sid="ses_velha", seq=7)
        self.assertEqual(self.coletar().sessions, ())


class Boots(Base):
    """Entre produtores não há ordem presumida: um boot encerrado não mexe no que um vivo sustenta (nomes de boot nas
    duas ordens alfabéticas, no mesmo lote e em lotes separados)."""

    def atrasados(self, velho):
        return (("session.deleted", dict(seq=40)), ("session.execution.failed", dict(seq=41)),
                ("permission.asked", dict(req="per_velho")), ("form.created", dict(req="frm_velho")),
                ("ombro.removed", dict(id="")), ("session.execution.succeeded", dict(seq=42)))

    def retrato(self, boot):
        self.ev("ombro.snapshot", id="", boot=boot, n=5, snap={"created": self.ms(100), "updated": self.ms(5),
                                                              "busy": self.ms(20), "idle": self.ms(5), "pending": []})

    def test_boot_encerrado_depois_do_retrato_do_novo(self):
        for velho, novo in (("zzz-velho", "aaa-novo"), ("aaa-velho", "zzz-novo")):
            for juntos in (True, False):
                for i, (nome, extra) in enumerate(self.atrasados(velho)):
                    sid = f"ses_{velho[:3]}_{int(juntos)}_{i}"
                    (self.opencode / "live" / f"{ORIGEM}.100.json").unlink(missing_ok=True)
                    self.pulso(boot=novo)
                    self.retrato_de(sid, novo)
                    if not juntos:
                        self.coletar()
                    self.ev(nome, sid=sid, boot=velho, n=900, **extra)
                    self.assertEqual(self.estado(sid), (Activity.IDLE, Attention.UNKNOWN), (velho, juntos, nome))

    def retrato_de(self, sid, boot):
        self.ev("ombro.snapshot", sid=sid, id="", boot=boot, n=5,
                snap={"created": self.ms(100), "updated": self.ms(5), "busy": self.ms(20), "idle": self.ms(5),
                      "pending": []})

    def test_dois_boots_vivos_nao_disputam_volateis(self):
        self.vivos = {100, 200}
        self.pulso(boot="boot-a")
        self.pulso(pid=200, boot="boot-b")
        self.ev("session.execution.started", seq=2, boot="boot-a", n=10)
        self.ev("permission.asked", req="per_a", boot="boot-a", n=11)
        self.coletar()
        self.ev("session.retry.scheduled", boot="boot-b", n=99)  # sem sequência durável: sem ordem entre os dois
        self.ev("form.created", req="frm_b", boot="boot-b", n=100)
        self.ev("ombro.removed", id="", boot="boot-b", n=101)
        self.retrato_de("ses_a", "boot-b")
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.WAITING_APPROVAL))
        self.ev("session.execution.succeeded", seq=9, boot="boot-b", n=102)  # a sequência durável ordena
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.NONE))


class Gravacao(Base):
    def test_sem_gravar_o_cursor_nada_avanca_e_o_evento_espera(self):
        from providers import opencode_registro

        self.pulso()
        self.ev("session.execution.started", seq=2)
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.NONE))
        original = opencode_registro.gravar

        def falhar(arq, dados):
            if arq.name == "state.json":
                raise OSError(28, "sem espaço")
            return original(arq, dados)

        with mock.patch.object(opencode_registro, "gravar", falhar):
            self.ev("permission.asked", req="per_1")
            self.ev("session.deleted", sid="ses_b", seq=7)
            snap = self.coletar()
            self.assertEqual(self.estado(), (Activity.WORKING, Attention.NONE))  # nada avançou, nada a notificar
            self.assertEqual(snap.condition, "partial")
            self.assertEqual(len(list(self.eventos.iterdir())), 2)  # esperam na caixa
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.WAITING_APPROVAL))  # gravou: aplica uma vez
        self.assertEqual(list(self.eventos.iterdir()), [])
        self.ligar()
        self.pulso()
        self.ev("session.execution.started", sid="ses_b", seq=3)  # a remoção ficou lembrada no reinício
        self.assertIsNone(self.estado("ses_b"))

    def test_falha_com_reinicio_antes_de_gravar_nao_perde_a_protecao(self):
        from providers import opencode_registro

        self.pulso()
        original = opencode_registro.gravar

        def falhar(arq, dados):
            if arq.name == "state.json":
                raise OSError(13, "negado")
            return original(arq, dados)

        with mock.patch.object(opencode_registro, "gravar", falhar):
            self.ev("session.deleted", seq=7)
            self.coletar()
            self.ligar()  # caiu com a gravação falhando
        self.pulso()
        self.ev("permission.asked", req="per_1", n=999)
        self.coletar()
        self.assertIsNone(self.estado())  # a remoção, que esperava na caixa, é aplicada e vale


class SemFome(Base):
    """Eventos do OpenCode esperando a gravação não tomam a vez dos outros agentes na caixa."""

    def test_outros_agentes_avancam_e_a_ordem_do_opencode_se_mantem(self):
        from core import caixa as modulo
        from providers import opencode_registro
        from providers.codex import CodexProvider

        self.monitor.fechar()
        codex = CodexProvider(relogio=lambda: self.agora)
        self.p = OpenCodeProvider(self.opencode, relogio=lambda: self.agora, pid_vivo=lambda pid: pid in self.vivos,
                                  instalado=lambda: True)
        self.monitor = Monitor.montar([codex, self.p], relogio=lambda: self.agora, eventos=self.eventos)
        self.addCleanup(self.monitor.fechar)
        original = opencode_registro.gravar

        def falhar(arq, dados):
            if arq.name == "state.json":
                raise OSError(28, "sem espaço")
            return original(arq, dados)

        def do_codex(sessao, nome):
            self.arq += 1
            d = {"v": 1, "provider": "codex", "session": sessao, "event": nome, "ts": self.ms(), "project": None,
                 "detail": None}
            (self.eventos / f"{self.arq:020d}-2-def.json").write_text(json.dumps(d))
            os.utime(self.eventos, (time.time() + self.arq, time.time() + self.arq))

        self.pulso()
        with mock.patch.object(modulo, "MAX_POR_LEITURA", 3), mock.patch.object(opencode_registro, "gravar", falhar):
            for i in range(7):  # bem mais que uma janela de leitura
                self.ev("session.execution.started", sid=f"ses_{i}", seq=2)
            self.coletar()
            do_codex("c1", "UserPromptSubmit")
            for _ in range(4):
                self.coletar()
            self.assertEqual([s.session_id for s in self.monitor.store.sessions("codex")], ["c1"])
            do_codex("c2", "UserPromptSubmit")
            self.ev("session.execution.succeeded", sid="ses_0", seq=9)  # o fim chega enquanto o começo ainda espera
            for _ in range(4):
                self.coletar()
            self.assertEqual({s.session_id for s in self.monitor.store.sessions("codex")}, {"c1", "c2"})
            self.assertEqual(self.monitor.store.sessions("opencode"), [])
            self.assertEqual(len(list(self.eventos.iterdir())), 8)
        with mock.patch.object(modulo, "MAX_POR_LEITURA", 3):
            for _ in range(5):
                self.coletar()
        estados = {s.session_id.split(":")[1]: s.activity for s in self.monitor.store.sessions("opencode")}
        self.assertEqual(estados, {"ses_0": Activity.IDLE, **{f"ses_{i}": Activity.WORKING for i in range(1, 7)}})
        self.assertEqual(list(self.eventos.iterdir()), [])


class Horario(Base):
    def test_oc9_sem_horario_valido_nao_afirma_estado(self):
        self.pulso()
        for at in (None, 0, -5, "ontem", self.ms(-3600)):
            self.ev("session.execution.started", sid=f"ses_{self.arq}", seq=2, at=at)
        self.ev("session.execution.failed", sid="ses_falha", seq=3, at=None)
        snap = self.coletar()
        self.assertEqual({(s.activity, s.attention) for s in snap.sessions}, {(Activity.UNKNOWN, Attention.UNKNOWN)})
        self.assertEqual({(s.status_since, s.updated_at, s.started_at) for s in snap.sessions}, {(None, None, None)})

    def test_oc9_atrasado_vale_pela_ordem_e_guarda_o_horario_da_fonte(self):
        self.pulso()
        self.ev("session.execution.started", seq=2, atras=600)
        s = self.coletar().sessions[0]
        self.assertEqual((s.activity, int(s.updated_at.timestamp())), (Activity.WORKING, int(self.agora.timestamp()) - 600))


class Diagnostico(Base):
    def doutor(self, **extra):
        from ombro import doctor

        snap = self.coletar()
        p = {"id": "opencode", "enabled": True, "condition": snap.condition, "condition_detail": snap.condition_detail,
             "version": snap.version, "integration": snap.integration, "collection": snap.status.value,
             "unknown_sessions": sum(s.activity is Activity.UNKNOWN for s in snap.sessions), "capabilities": [], **extra}

        class Amb:
            def executar(self, _argv):
                return None

            def ipc(self, *_a):
                return {"providers": [p]}

        return next(v for v in doctor.agentes(Amb(), {}) if v.id == "provider.opencode")

    def test_oc4_estados_distintos(self):
        from ombro import doctor

        nunca = self.doutor()
        self.assertEqual((nunca.status, "nunca" in nunca.mensagem), (doctor.WARN, True))
        self.pulso()
        self.ev("session.execution.succeeded", seq=2)
        self.assertEqual(self.doutor().status, doctor.PASS)
        self.vivos = set()
        fechado = self.doutor()
        self.assertEqual(fechado.status, doctor.WARN)
        self.assertIn("desconectad", fechado.mensagem)
        desligado = self.doutor(enabled=False)
        self.assertEqual((desligado.status, "desligado" in desligado.mensagem), (doctor.SKIP, True))

    def test_oc4_recuperado_desconhecido(self):
        from ombro import doctor

        self.pulso()
        self.ev("session.execution.started", seq=2)
        self.coletar()
        self.ligar()
        v = self.doutor()
        self.assertEqual(v.status, doctor.WARN)
        self.assertIn("desconhecid", v.mensagem)


class Desconectado(Base):
    """Com o OpenCode fechado, o que se sabia é histórico: o rótulo diz desconectado, nunca "ocioso" confirmado."""

    def resumo(self):
        from core.resumo import resumir

        self.coletar()
        return resumir(self.monitor.store, [("opencode", "OpenCode")])

    def textos(self, vista="overview", foco=None):
        from coletores.hardware import Hardware
        from layout_multiagente import Texto, plano

        h = Hardware("27.0", 16 * 2 ** 30, 8 * 2 ** 30, 50.0, 1.0)
        return [t.s for t in plano(self.resumo(), vista, foco, h, self.agora) if isinstance(t, Texto)]

    def encerrar(self, evento="session.execution.succeeded"):
        self.pulso()
        self.ev(evento, seq=2)
        self.coletar()

    def test_ocioso_confirmado_so_enquanto_conectado(self):
        self.encerrar()
        self.assertEqual(self.resumo().providers[0].estado, "ocioso")
        self.vivos = set()
        r = self.resumo()
        p = r.providers[0]  # o histórico fica, mas não conta como sessão de agora
        self.assertEqual((p.estado, p.sessoes, p.desconectadas, r.total_sessoes, r.providers_ativos),
                         ("desconectado", 0, 1, 0, 0))
        self.assertEqual([(s.activity, s.disconnected) for s in p.snapshot.sessions], [(Activity.IDLE, True)])
        t = self.textos()
        self.assertIn("desconectado", t)
        self.assertIn("último conhecido: ocioso", t)
        self.assertIn("último: ocioso", t)
        self.assertNotIn("ocioso", t)
        self.assertIn("NENHUM ATIVO", t)
        foco = self.textos("focus", "opencode")
        self.assertIn("DESCONECTADO", foco)
        self.assertIn("último: ocioso", foco)
        self.assertNotIn("ocioso", foco)

    def cair(self):
        self.vivos = set()
        return self.resumo()

    def voltar(self, boot="boot2", estado="collecting"):
        self.vivos = {100}
        self.pulso(boot=boot, estado=estado)
        return self.resumo()

    def marca(self):
        s = self.coletar().sessions[0]
        return s.activity, s.attention, s.disconnected

    def test_pulso_novo_poe_a_origem_no_ar_mas_nao_confirma_a_sessao(self):
        self.encerrar()
        self.cair()
        r = self.voltar()
        self.assertEqual(self.marca(), (Activity.UNKNOWN, Attention.UNKNOWN, False))
        self.assertEqual(r.providers[0].estado, "coletando")
        self.assertEqual(self.voltar(estado="ready").providers[0].estado, "desconhecido")
        self.assertIn("sem notícias", self.textos())
        self.assertNotIn("ocioso", self.textos())

    def test_o_historico_volta_enquanto_offline_e_o_erro_antigo_nao_e_promovido(self):
        self.encerrar("session.execution.failed")
        self.cair()
        r = self.voltar()
        self.assertEqual(self.marca(), (Activity.UNKNOWN, Attention.UNKNOWN, False))
        self.assertEqual((r.erros, r.mais_urgente), (0, None))
        r = self.cair()  # sem nada confirmado no meio: o último conhecido é o mesmo
        self.assertEqual(self.marca(), (Activity.IDLE, Attention.ERROR, True))
        self.assertEqual((r.erros, r.mais_urgente), (0, None))
        self.voltar(estado="ready")
        self.ev("ombro.snapshot", boot="boot2", id="", snap={"busy": self.ms(9), "idle": self.ms(8), "pending": []})
        r = self.resumo()  # a foto prova que está parada, não como terminou
        self.assertEqual(self.marca(), (Activity.IDLE, Attention.UNKNOWN, False))
        self.assertEqual((r.providers[0].estado, r.erros, r.mais_urgente), ("ocioso", 0, None))

    def test_um_evento_do_boot_vivo_confirma(self):
        self.encerrar()
        self.cair()
        self.voltar(estado="ready")
        self.ev("session.execution.failed", boot="boot2", seq=3)
        self.assertEqual((self.resumo().erros, self.marca()), (1, (Activity.IDLE, Attention.ERROR, False)))

    def test_um_pedido_do_boot_novo_nao_traz_o_estado_antigo(self):
        for fonte in ("evento", "foto"):
            with self.subTest(fonte):
                self.setUp()
                self.encerrar("session.execution.failed")
                self.cair()
                self.voltar(estado="ready")
                if fonte == "evento":
                    self.ev("permission.asked", boot="boot2", req="per_9")
                else:
                    self.ev("ombro.snapshot", boot="boot2", id="", snap={"busy": self.ms(5), "pending": ["per_9"]})
                self.assertEqual(self.marca(), (Activity.UNKNOWN, Attention.WAITING_APPROVAL, False))
                self.assertEqual(self.resumo().erros, 0)
                self.ev("permission.replied", boot="boot2", req="per_9")
                self.assertEqual(self.marca(), (Activity.UNKNOWN, Attention.UNKNOWN, False))

    def test_foto_que_nao_prova_nada_nao_confirma(self):
        self.encerrar()
        self.cair()
        self.voltar(estado="ready")
        self.ev("ombro.snapshot", boot="boot2", id="", snap={"busy": self.ms(0.5), "pending": []})
        self.assertEqual(self.marca()[0], Activity.UNKNOWN)

    def test_o_mesmo_boot_que_volta_de_um_silencio_tambem_precisa_confirmar(self):
        self.encerrar()
        self.cair()
        self.voltar(boot="boot1", estado="ready")
        self.assertEqual(self.marca(), (Activity.UNKNOWN, Attention.UNKNOWN, False))
        self.ev("ombro.snapshot", id="", snap={"busy": self.ms(9), "idle": self.ms(8), "pending": []})
        self.assertEqual(self.marca(), (Activity.IDLE, Attention.UNKNOWN, False))

    def test_boot_trocado_sem_o_ombro_ver_a_queda_tambem_nao_confirma(self):
        self.encerrar("session.execution.failed")
        self.pulso(boot="boot2", estado="ready")  # entre duas coletas
        self.assertEqual(self.marca(), (Activity.UNKNOWN, Attention.UNKNOWN, False))

    def test_erro_de_antes_vira_historico(self):
        self.encerrar("session.execution.failed")
        self.assertEqual((self.resumo().erros, self.resumo().mais_urgente is not None), (1, True))
        self.vivos = set()
        r = self.resumo()
        self.assertEqual((r.providers[0].estado, r.erros, r.mais_urgente), ("desconectado", 0, None))
        self.assertEqual([s.attention for s in r.providers[0].snapshot.sessions], [Attention.ERROR])  # preservado
        t = self.textos()
        self.assertIn("último conhecido: 1 com erro", t)
        self.assertIn("último: erro", t)
        self.assertNotIn("parou com erro", t)
        self.assertNotIn("1 PRECISA DE VOCÊ", t)

    def test_distinto_de_desconhecido_sem_sessoes_nunca_e_desligado(self):
        from core.resumo import resumir

        self.assertEqual(self.resumo().providers[0].estado, "sem_plugin")  # nunca conectou
        self.pulso()
        self.assertEqual(self.resumo().providers[0].estado, "sem_sessoes")
        self.ev("session.execution.started", seq=2)
        self.coletar()
        self.ligar()  # o Ombro reinicia: recuperado, ainda sem confirmação, mas conectado
        self.assertEqual(self.resumo().providers[0].estado, "desconhecido")
        self.vivos = set()
        self.assertEqual(self.resumo().providers[0].estado, "desconectado")
        self.assertIn("sem notícias", self.textos())
        self.assertNotIn("opencode", [p.provider_id for p in resumir(self.monitor.store, []).providers])  # desligado

    def test_desconectado_sem_sessoes(self):
        self.pulso()
        self.assertEqual(self.resumo().providers[0].estado, "sem_sessoes")
        self.vivos = set()
        self.assertEqual(self.resumo().providers[0].estado, "desconectado")
        self.assertIn("OpenCode fechado ou sem o plugin", self.textos())

    def test_so_um_plugin_sem_registro_no_ar_tambem_e_desconectado(self):
        self.encerrar()
        self.vivos = {200}
        self.pulso(origem="oc-zzzzzzzzzzzzzzzzzzzzzzzz", pid=200)
        self.assertEqual(self.coletar().condition, "unregistered")
        self.assertEqual(self.resumo().providers[0].estado, "desconectado")

    def duas_origens(self, evento_de_a="session.execution.succeeded"):
        registro = json.loads((self.opencode / "registry.json").read_text())
        registro["origins"][OUTRA] = {"state": "pending", "registered_at": 2}
        (self.opencode / "registry.json").write_text(json.dumps(registro))
        self.vivos = {100, 200}
        self.pulso()
        self.pulso(OUTRA, pid=200, boot="boot2", ns="marca2", anc=COPIA)
        self.ev(evento_de_a, "ses_a", seq=2)
        self.ev("session.execution.started", "ses_b", origem=OUTRA, boot="boot2", ns="marca2", anc=COPIA, seq=2)
        self.coletar()

    def marcas(self):
        return {s.session_id.split(":")[1]: (s.activity, s.attention, s.disconnected) for s in self.coletar().sessions}

    def test_uma_origem_cai_e_a_outra_segue(self):
        self.duas_origens()
        self.assertEqual(self.marcas()["ses_a"], (Activity.IDLE, Attention.NONE, False))
        self.vivos = {200}
        r = self.resumo()
        self.assertIsNone(r.providers[0].snapshot.condition)  # o conjunto está saudável
        self.assertEqual(self.marcas(), {"ses_a": (Activity.IDLE, Attention.NONE, True),
                                         "ses_b": (Activity.WORKING, Attention.NONE, False)})
        p = r.providers[0]
        self.assertEqual((p.estado, p.sessoes, p.desconectadas, p.trabalhando), ("trabalhando", 1, 1, 1))
        t = self.textos()
        self.assertIn("último: ocioso", t)
        self.assertIn("trabalhando", t)
        self.assertNotIn("ocioso", t)
        self.ev("session.execution.succeeded", "ses_b", origem=OUTRA, boot="boot2", ns="marca2", anc=COPIA, seq=3)
        self.assertEqual(self.resumo().providers[0].estado, "ocioso")  # de B, confirmado agora

    def test_origem_com_erro_cai_sem_conclusao_nem_aviso_novo(self):
        self.duas_origens("session.execution.failed")
        self.ev("session.execution.succeeded", "ses_b", origem=OUTRA, boot="boot2", ns="marca2", anc=COPIA, seq=3)
        antes = self.coletar()
        self.vivos = {200}
        depois = self.coletar()
        self.assertEqual(depois.unconfirmed, antes.unconfirmed)  # nada trabalhava: nada ficou por confirmar
        self.assertEqual(self.marcas()["ses_a"], (Activity.IDLE, Attention.ERROR, True))
        r = self.resumo()
        self.assertEqual((r.providers[0].estado, r.erros, r.mais_urgente), ("ocioso", 0, None))
        self.vivos = {100, 200}  # a mesma origem volta ao ar: até um evento ou uma foto, não se sabe
        self.assertEqual(self.marcas()["ses_a"], (Activity.UNKNOWN, Attention.UNKNOWN, False))
        self.assertEqual((self.resumo().erros, self.resumo().mais_urgente), (0, None))

    def test_so_a_origem_offline_tinha_sessoes(self):
        self.duas_origens()
        self.ev("session.deleted", "ses_b", origem=OUTRA, boot="boot2", ns="marca2", anc=COPIA, seq=3)
        self.coletar()
        self.vivos = {200}
        p = self.resumo().providers[0]
        self.assertEqual((p.estado, p.sessoes, p.desconectadas), ("sem_sessoes", 0, 1))
        self.assertIn("1 de origem desconectada", self.textos())
        self.assertIn("1 conhecida", self.textos())

    def test_a_ipc_publica_a_proveniencia_e_conta_so_o_que_e_de_agora(self):
        from types import SimpleNamespace

        from config import OmbroConfig
        from ombro.daemon import Daemon

        self.duas_origens("session.execution.failed")
        self.vivos = {200}
        self.coletar()
        d = Daemon(OmbroConfig(demo=True, arquivo=self.pasta / "config.json"))
        d.app = SimpleNamespace(monitor=self.monitor, coletado_em=self.agora, avatares_resolvidos=lambda: {},
                                cfg=d._confirmado.cfg, ultimo_hw=None)
        d._reavaliar = lambda: None
        d._publicar()
        sessoes = {s["session_id"].split(":")[1]: s for s in d.m_sessions({})["sessions"]}
        self.assertEqual((sessoes["ses_a"]["status"], sessoes["ses_a"].get("disconnected")), ("error", True))
        self.assertNotIn("disconnected", sessoes["ses_b"])
        oc = next(p for p in d.m_providers({})["providers"] if p["id"] == "opencode")
        self.assertEqual((oc["sessions"], oc["disconnected_sessions"], oc["errors"], oc["working"], oc["unknown_sessions"]),
                         (1, 1, 0, 1, 0))

    def test_os_outros_agentes_nao_mudam(self):
        from core.models import AgentSession, ProviderSnapshot
        from core.resumo import resumir_provider

        s = AgentSession("codex", "x", Activity.IDLE, Attention.NONE)
        snap = ProviderSnapshot("codex", self.agora, sessions=(s,))
        self.assertEqual(resumir_provider("codex", "Codex", snap, [s]).estado, "ocioso")


if __name__ == "__main__":
    unittest.main()
