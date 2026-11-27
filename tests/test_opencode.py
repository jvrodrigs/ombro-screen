"""Provider do OpenCode: identidade por origem, dedup entre instâncias, ordem, recuperação sem inventar estado,
conflito de origem e reinícios do plugin, do servidor e do Ombro. Tudo em pastas temporárias, sem OpenCode."""
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.models import Activity, Attention
from core.monitor import Monitor
from core.resumo import resumir
from providers.opencode import PULSO_VALIDO, OpenCodeProvider

ORIGEM, OUTRA = "oc-aaaaaaaaaaaaaaaa", "oc-bbbbbbbbbbbbbbbb"


def ancora(ns):
    """Nos testes, cada banco (marca) tem a sua âncora; sem marca, nenhuma."""
    return hashlib.sha256(ns.encode()).hexdigest()[:32] if ns else ""


class Base(unittest.TestCase):
    REGISTRADAS = (ORIGEM, OUTRA)

    def registrar(self, *origens):
        (self.opencode / "registry.json").write_text(json.dumps(
            {"v": 2, "current": origens[0] if origens else None,
             "origins": {o: {"state": "pending", "registered_at": 1} for o in origens}}))

    def registro(self):
        return json.loads((self.opencode / "registry.json").read_text())["origins"]

    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="ombro-oc-"))
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.eventos, self.opencode = self.pasta / "eventos", self.pasta / "opencode"
        self.eventos.mkdir()
        (self.opencode / "live").mkdir(parents=True)
        self.agora = datetime.now().replace(microsecond=0)
        self.vivos = {100, 200}
        self.instalado = True
        self.n = 0
        self.monitor = None
        if self.REGISTRADAS:
            self.registrar(*self.REGISTRADAS)
        self.ligar()

    def ligar(self, banco=False):
        """Um Ombro novo sobre as mesmas pastas (o primeiro, ou um reinício)."""
        if self.monitor is not None:
            self.monitor.fechar()
        self.p = OpenCodeProvider(self.opencode, relogio=lambda: self.agora, pid_vivo=lambda pid: pid in self.vivos,
                                  instalado=lambda: self.instalado)
        self.monitor = Monitor.montar([self.p], banco=self.pasta / "state.db" if banco else None,
                                      relogio=lambda: self.agora, eventos=self.eventos)
        self.addCleanup(self.monitor.fechar)

    def ms(self, atras=0.0):
        return int((self.agora.timestamp() - atras) * 1000)

    def pulso(self, origem=ORIGEM, pid=100, boot="boot1", ns="ns1", estado="ready", versao="2.4.2", idade=0, **extra):
        d = {"v": 1, "origin": origem, "pid": pid, "boot": boot, "ns": ns, "anchor": ancora(ns), "state": estado,
             "version": versao, "ts": self.ms(idade), **extra}
        (self.opencode / "live" / f"{origem}.{pid}.json").write_text(json.dumps(d))

    def ev(self, nome, sid="ses_a", origem=ORIGEM, atras=10.0, id=None, boot="boot1", ns="ns1", seq=None, req=None,
           projeto="api", snap=None, n=None):
        self.n += 1
        x = {"origin": origem, "boot": boot, "ns": ns, "anc": ancora(ns), "n": self.n if n is None else n,
             "at": self.ms(atras), "id": id if id is not None else f"evt_{self.n}"}
        if seq is not None:
            x["seq"] = seq
        if req is not None:
            x["req"] = req
        if snap is not None:
            x["snap"] = snap
        d = {"v": 1, "provider": "opencode", "session": f"{origem}:{sid}" if sid else origem, "event": nome,
             "ts": self.ms(atras), "project": projeto, "detail": None, "x": x}
        (self.eventos / f"{self.n:020d}-1-abc.json").write_text(json.dumps(d))
        os.utime(self.eventos, (time.time() + self.n, time.time() + self.n))

    def coletar(self, avancar=0):
        self.agora += timedelta(seconds=avancar)
        self.monitor.atualizar_todos()
        return self.monitor.store.snapshot("opencode")

    def sessoes(self):
        return {s.session_id: (s.activity, s.attention) for s in self.coletar().sessions}

    def estado(self, sid="ses_a", origem=ORIGEM):
        return self.sessoes().get(f"{origem}:{sid}")


class Eventos(Base):
    def test_ciclo_de_uma_sessao(self):
        self.pulso()
        self.ev("session.created", atras=50)
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.NONE))  # criar não é atividade
        self.ev("session.execution.started", atras=40)
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.NONE))
        self.ev("permission.asked", atras=30, req="per_1")
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.WAITING_APPROVAL))
        self.ev("form.created", atras=29, req="frm_1")
        self.ev("permission.replied", atras=28, req="per_1")
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.WAITING_USER))  # a pergunta segue aberta
        self.ev("form.cancelled", atras=27, req="frm_1")
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.NONE))
        self.ev("session.execution.succeeded", atras=20)
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.NONE))
        self.ev("session.execution.started", atras=15)
        self.ev("session.execution.failed", atras=14)
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.ERROR))
        self.ev("session.deleted", atras=10)
        self.assertIsNone(self.estado())

    def test_resposta_de_pedido_desconhecido_nao_afirma_que_nada_espera(self):
        self.pulso()
        self.ev("session.execution.started", atras=20)
        self.ev("permission.replied", atras=10, req="per_antigo")
        self.assertEqual(self.estado(), (Activity.WORKING, Attention.UNKNOWN))

    def test_mesmo_evento_por_varias_instancias_conta_uma_vez(self):
        self.pulso()
        for _ in range(3):  # três instâncias do plugin (três projetos) gravam o mesmo evt_
            self.ev("session.execution.started", id="evt_x", atras=20)
        self.assertEqual(len(self.sessoes()), 1)
        self.ev("session.execution.succeeded", id="evt_y", atras=10)
        self.ev("session.execution.started", id="evt_x", atras=20)  # a cópia atrasada não reabre a execução
        self.assertEqual(self.estado()[0], Activity.IDLE)

    def test_mesmo_id_em_origens_diferentes_nao_colide(self):
        self.pulso()
        self.pulso(OUTRA, pid=200, boot="boot2", ns="ns2")
        self.ev("session.execution.started", id="evt_x", atras=20)
        self.ev("session.execution.started", id="evt_x", atras=20, origem=OUTRA, boot="boot2", ns="ns2")
        self.ev("session.execution.succeeded", atras=10)
        self.assertEqual(self.sessoes(), {f"{ORIGEM}:ses_a": (Activity.IDLE, Attention.NONE),
                                          f"{OUTRA}:ses_a": (Activity.WORKING, Attention.NONE)})

    def test_ordem_trocada_no_mesmo_lote_e_entre_lotes(self):
        self.pulso()
        self.ev("session.execution.succeeded", atras=10, seq=5, n=50)
        self.ev("session.execution.started", atras=20, seq=2, n=20)
        self.assertEqual(self.estado()[0], Activity.IDLE)
        self.ev("session.execution.started", atras=25, seq=1, n=10)  # chegou atrasado, noutro ciclo
        self.ev("permission.asked", atras=15, req="per_velho", n=30)  # pedido de uma execução que já acabou
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.NONE))

    def test_pergunta_resposta_fora_de_ordem(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.ev("form.replied", atras=10, req="frm_1")
        self.coletar()
        self.ev("form.created", atras=20, req="frm_1")  # o pedido já respondido não volta a esperar
        self.assertNotEqual(self.estado()[1], Attention.WAITING_USER)

    def test_duas_sessoes_e_dois_projetos(self):
        self.pulso()
        self.ev("session.execution.started", sid="ses_a", projeto="api", atras=20)
        self.ev("permission.asked", sid="ses_b", projeto="site", atras=15, req="per_1")
        snap = self.coletar()
        self.assertEqual({s.project for s in snap.sessions}, {"api", "site"})
        r = resumir(self.monitor.store, [("opencode", "OpenCode")]).providers[0]
        self.assertEqual((r.sessoes, r.trabalhando, r.aguardando), (2, 1, 1))

    def test_evento_invalido_ou_sem_origem_e_ignorado(self):
        self.pulso()
        self.ev("session.execution.started", origem="x")  # origem curta demais
        d = {"v": 1, "provider": "opencode", "session": "ses_solta", "event": "session.execution.started",
             "ts": self.ms(1), "project": None, "detail": None}
        (self.eventos / "99999999999999999999-1-a.json").write_text(json.dumps(d))
        self.assertEqual(self.sessoes(), {})


class Recuperacao(Base):
    def snap(self, updated=10.0, busy=None, idle=None, pending=(), created=100.0):
        d = {"created": self.ms(created), "updated": self.ms(updated), "pending": list(pending)}
        if busy is not None:
            d["busy"] = self.ms(busy)
        if idle is not None:
            d["idle"] = self.ms(idle)
        return d

    def foto(self, sid="ses_a", atras=10.0, **kw):
        self.ev("ombro.snapshot", sid=sid, atras=atras, id="", snap=self.snap(updated=atras, **kw))

    def test_preexistentes_so_afirmam_o_provavel(self):
        self.pulso()
        self.foto("ses_parada", busy=20, idle=15)
        self.foto("ses_ambigua", busy=20)  # sem idle: em andamento ou caiu, não dá para saber
        self.foto("ses_velha_idle", busy=12, idle=40)  # busy depois do idle: idem
        self.foto("ses_pendente", busy=20, pending=["per_1"])
        s = self.sessoes()
        self.assertEqual(s[f"{ORIGEM}:ses_parada"], (Activity.IDLE, Attention.UNKNOWN))
        self.assertEqual(s[f"{ORIGEM}:ses_ambigua"], (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertEqual(s[f"{ORIGEM}:ses_velha_idle"], (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertEqual(s[f"{ORIGEM}:ses_pendente"], (Activity.UNKNOWN, Attention.WAITING_APPROVAL))
        sessao = next(x for x in self.coletar().sessions if x.session_id.endswith("ses_ambigua"))
        self.assertIsNone(sessao.status_since)  # a recuperação não inventa horário

    def test_resposta_numa_sessao_recuperada_nao_afirma_que_nada_espera(self):
        self.pulso()
        self.foto("ses_a", busy=20, pending=["per_1"])  # recuperada: pode haver uma pergunta que a foto não mostra
        self.assertEqual(self.estado()[1], Attention.WAITING_APPROVAL)
        self.ev("permission.replied", atras=1, req="per_1")  # inclusive a resposta automática de uma recarga
        self.assertEqual(self.estado(), (Activity.UNKNOWN, Attention.UNKNOWN))

    def test_permissao_aberta_vale_mesmo_com_a_sessao_parada(self):
        self.pulso()
        self.foto("ses_a", busy=20, idle=15, pending=["per_1"])  # o pedido está aberto agora: é um fato
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.WAITING_APPROVAL))
        self.ev("permission.replied", atras=1, req="per_1")
        self.assertNotEqual(self.estado()[1], Attention.WAITING_APPROVAL)

    def test_pulsos_antigos_nao_escondem_o_vivo_e_sao_apagados(self):
        for pid in range(1000, 1080):  # servidores mortos de dias anteriores
            self.pulso(pid=pid, idade=7200)
            os.utime(self.opencode / "live" / f"{ORIGEM}.{pid}.json", (1_000_000, 1_000_000))
        self.pulso()
        self.ev("session.execution.started", atras=1)
        self.assertEqual(self.estado()[0], Activity.WORKING)
        self.assertEqual([p.name for p in (self.opencode / "live").glob("*.json")], [f"{ORIGEM}.100.json"])

    def test_foto_nao_rebaixa_o_que_os_eventos_confirmaram(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.coletar()
        self.foto(atras=20, busy=30)  # foto sem fim: não apaga o "trabalhando" ao vivo
        self.assertEqual(self.estado()[0], Activity.WORKING)

    def test_ausencia_na_foto_nao_encerra(self):
        self.pulso()
        self.ev("session.execution.started", sid="ses_b", atras=30)
        self.foto("ses_a", busy=20, idle=15)
        self.assertEqual(len(self.sessoes()), 2)

    def test_fim_perdido_vira_ocioso_sem_confirmar_conclusao(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        antes = self.coletar().unconfirmed
        self.foto(atras=5, busy=30, idle=5)
        snap = self.coletar()
        self.assertEqual(snap.sessions[0].activity, Activity.IDLE)
        self.assertGreater(snap.unconfirmed, antes)  # não é um "concluiu agora" para o mascote

    def test_perda_do_fluxo(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.ev("permission.asked", atras=29, req="per_1")
        self.ev("session.created", sid="ses_b", atras=28)
        antes = self.coletar().unconfirmed
        self.ev("ombro.gap", sid=None, atras=5)
        snap = self.coletar()
        s = {x.session_id.split(":")[1]: (x.activity, x.attention) for x in snap.sessions}
        self.assertEqual(s["ses_a"], (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertGreater(snap.unconfirmed, antes)
        self.ev("session.execution.succeeded", atras=1)  # evento novo confiável corrige
        self.assertEqual(self.estado(), (Activity.IDLE, Attention.NONE))

    def test_servidor_cai(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.ev("permission.asked", atras=29, req="per_1")
        self.assertEqual(self.estado()[1], Attention.WAITING_APPROVAL)
        self.vivos.discard(100)
        snap = self.coletar()
        self.assertEqual((snap.sessions[0].activity, snap.sessions[0].attention), (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertEqual(snap.condition, "offline")
        self.assertGreater(snap.unconfirmed, 0)

    def test_pulso_velho_ou_parado_vale_como_queda(self):
        for kw in ({"idade": PULSO_VALIDO + 5}, {"estado": "stopped"}):
            self.ligar()
            self.pulso()
            self.ev("session.execution.started", atras=30)
            self.assertEqual(self.estado()[0], Activity.WORKING)
            self.pulso(**kw)
            self.assertEqual(self.estado()[0], Activity.UNKNOWN, kw)

    def test_resposta_automatica_no_encerramento_nao_vira_decisao(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.ev("permission.asked", atras=20, req="per_1")
        self.coletar()
        self.ev("permission.replied", atras=2, req="per_1")
        self.pulso(estado="stopped")
        self.assertEqual(self.estado(), (Activity.UNKNOWN, Attention.UNKNOWN))

    def test_plugin_recarregado_boot_novo(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.coletar()
        self.pulso(boot="boot9")  # mesmo processo, plugin carregado de novo
        self.assertEqual(self.estado()[0], Activity.UNKNOWN)
        self.ev("session.execution.started", atras=1, boot="boot9")
        self.assertEqual(self.estado()[0], Activity.WORKING)
        self.assertEqual(len(self.sessoes()), 1)  # a mesma sessão: o boot não entra na identidade

    def test_reinicio_do_servidor_mantem_a_identidade(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.coletar()
        self.vivos = {300}
        (self.opencode / "live" / f"{ORIGEM}.100.json").unlink()
        self.pulso(pid=300, boot="boot2")
        self.ev("session.execution.succeeded", atras=1, boot="boot2")
        self.assertEqual(self.sessoes(), {f"{ORIGEM}:ses_a": (Activity.IDLE, Attention.NONE)})

    def test_reinicio_do_ombro_recupera_como_desconhecido_e_pede_foto(self):
        self.ligar(banco=True)
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.coletar()
        pedido = self.opencode / "resync"
        os.utime(pedido, (1, 1))
        self.ligar(banco=True)
        self.assertGreater(pedido.stat().st_mtime, 1)  # o plugin vê o pedido no próximo pulso
        self.assertEqual(self.estado(), (Activity.UNKNOWN, Attention.UNKNOWN))
        self.assertEqual(list(self.eventos.iterdir()), [])  # nada antigo para reprocessar
        self.ev("session.execution.succeeded", atras=0)
        self.assertEqual(self.estado()[0], Activity.IDLE)

    def test_sessao_abandonada_sai(self):
        self.pulso()
        self.ev("session.execution.succeeded", atras=10)
        self.coletar()
        self.agora += timedelta(hours=5)
        self.pulso()
        self.assertEqual(self.sessoes(), {})


class Origem(Base):
    def test_conflito_de_origem_nao_aplica_nada(self):
        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.coletar()
        self.pulso(pid=200, boot="boot2", ns="OUTRO-BANCO")  # a mesma origem, ao mesmo tempo, em outro banco
        self.ev("session.execution.started", sid="ses_intrusa", atras=1, boot="boot2", ns="OUTRO-BANCO")
        snap = self.coletar()
        self.assertEqual(snap.condition, "conflict")
        self.assertEqual({s.session_id: s.activity for s in snap.sessions}, {f"{ORIGEM}:ses_a": Activity.UNKNOWN})
        self.vivos.discard(200)  # sem o segundo processo, o banco associado volta a ser aceito
        self.ev("session.execution.succeeded", atras=0)
        self.assertEqual(self.estado()[0], Activity.IDLE)

    def test_dois_processos_no_mesmo_banco_nao_sao_conflito(self):
        self.pulso()
        self.pulso(pid=200, boot="boot2")
        self.ev("session.execution.started", atras=2, boot="boot2")
        snap = self.coletar()
        self.assertIsNone(snap.condition)
        self.assertEqual(snap.sessions[0].activity, Activity.WORKING)


class Associacao(Base):
    """A origem fica ligada, no disco do Ombro, ao primeiro banco que a usou: outro banco com a mesma origem não
    entra, nem depois, nem com o primeiro parado, nem depois de reiniciar o Ombro."""

    def do_banco_a(self):
        self.pulso()
        self.ev("session.execution.started", sid="ses_a", atras=30)
        self.ev("session.execution.succeeded", sid="ses_a", atras=25)
        self.coletar()

    def trocar_para_b(self):
        self.vivos = {200}
        (self.opencode / "live" / f"{ORIGEM}.100.json").unlink()
        self.pulso(pid=200, boot="boot2", ns="banco-b")

    def test_bancos_em_sequencia_nao_se_misturam(self):
        self.do_banco_a()
        self.trocar_para_b()
        self.ev("session.execution.started", sid="ses_a", atras=2, boot="boot2", ns="banco-b")  # até o mesmo id de sessão
        self.ev("session.execution.started", sid="ses_so_b", atras=1, boot="boot2", ns="banco-b")
        snap = self.coletar()
        self.assertEqual(snap.condition, "conflict")
        self.assertIn("registre uma origem nova", snap.condition_detail)
        self.assertEqual({s.session_id: s.activity for s in snap.sessions}, {f"{ORIGEM}:ses_a": Activity.IDLE})

    def test_continua_recusado_depois_de_reiniciar_o_ombro(self):
        self.ligar(banco=True)
        self.do_banco_a()
        self.trocar_para_b()
        self.ligar(banco=True)
        self.ev("session.execution.started", sid="ses_so_b", atras=1, boot="boot2", ns="banco-b")
        self.ev("session.deleted", sid="ses_a", atras=1, boot="boot2", ns="banco-b")  # nem apaga o que é do outro
        snap = self.coletar()
        self.assertEqual(snap.condition, "conflict")
        self.assertEqual([s.session_id for s in snap.sessions], [f"{ORIGEM}:ses_a"])
        linhas = self.monitor.banco._conn.execute("SELECT session_id FROM sessions").fetchall()
        self.assertEqual(linhas, [(f"{ORIGEM}:ses_a",)])

    def test_caixa_velha_depois_da_troca(self):
        self.do_banco_a()
        self.ligar()
        self.trocar_para_b()
        self.ev("session.execution.started", sid="ses_velha", atras=40)  # ficou na caixa: é do banco A, o associado
        self.ev("session.execution.started", sid="ses_so_b", atras=1, boot="boot2", ns="banco-b")
        s = self.sessoes()
        self.assertNotIn(f"{ORIGEM}:ses_so_b", s)
        self.assertEqual(s[f"{ORIGEM}:ses_velha"][0], Activity.UNKNOWN)  # a fonte dela já não está viva

    def test_dois_bancos_antes_de_qualquer_associacao_nada_e_guardado(self):
        self.pulso(pid=200, boot="boot2", ns="banco-b")  # Ombro desligado: A usou e parou, B está no ar
        self.ev("session.execution.started", sid="ses_a", atras=30)
        self.ev("session.execution.started", sid="ses_b", atras=1, boot="boot2", ns="banco-b")
        snap = self.coletar()
        self.assertEqual((snap.sessions, snap.condition), ((), "conflict"))
        self.assertEqual(self.registro()[ORIGEM]["state"], "ambiguous")
        self.ligar()  # reiniciado, com só o B no ar: a origem continua ambígua, não é "adotada" pelo que sobrou
        self.ev("session.execution.started", sid="ses_b", atras=0, boot="boot2", ns="banco-b")
        snap = self.coletar()
        self.assertEqual((snap.sessions, snap.condition), ((), "conflict"))

    def test_sem_marca_ou_sem_poder_gravar_a_associacao_nao_aplica(self):
        self.pulso(ns="")
        self.ev("session.execution.started", ns="")
        self.assertEqual(self.sessoes(), {})
        for arquivo in ("registry.json", "state.json"):
            self.registrar(ORIGEM)
            (self.opencode / arquivo).write_text("{quebrado")
            self.ligar()
            self.pulso()
            self.ev("session.execution.started", atras=1)
            snap = self.coletar()
            self.assertEqual((snap.sessions, snap.condition), ((), "conflict"), arquivo)
            self.assertEqual((self.opencode / arquivo).read_text(), "{quebrado")  # não sobrescreve o que não entende
            (self.opencode / arquivo).unlink()

    def test_origens_diferentes_cada_uma_com_o_seu_banco(self):
        self.do_banco_a()
        self.pulso(OUTRA, pid=200, boot="boot2", ns="banco-b")
        self.ev("session.execution.started", origem=OUTRA, boot="boot2", ns="banco-b", atras=1)
        self.assertEqual(len(self.sessoes()), 2)
        gravado = self.registro()
        self.assertEqual({o: (d["state"], d["mark"], d["anchor"]) for o, d in gravado.items()},
                         {ORIGEM: ("active", "ns1", ancora("ns1")), OUTRA: ("active", "banco-b", ancora("banco-b"))})
        for arquivo in ("registry.json", "state.json"):
            self.assertEqual((self.opencode / arquivo).stat().st_mode & 0o777, 0o600, arquivo)


class Condicoes(Base):
    def resumo(self):
        self.coletar()
        return resumir(self.monitor.store, [("opencode", "OpenCode")]).providers[0].estado

    def test_ausente_nao_configurado_sem_plugin_e_sem_sessoes(self):
        (self.opencode / "registry.json").unlink()
        self.instalado = False
        self.assertEqual(self.resumo(), "nao_instalado")
        self.instalado = True
        self.ligar()
        self.assertEqual(self.resumo(), "nao_configurado")
        self.registrar(ORIGEM)
        self.assertEqual(self.resumo(), "sem_plugin")  # nunca conectou (o "desconectado" é de quem já conectou)
        self.assertEqual(self.monitor.store.snapshot("opencode").condition, "never")
        self.pulso()
        self.assertEqual(self.resumo(), "sem_sessoes")

    def test_coletando_incompativel_parcial_e_sem_origem(self):
        self.pulso(estado="collecting")
        self.assertEqual(self.resumo(), "coletando")
        self.pulso(estado="incompatible", versao="2.0.17")
        self.assertEqual(self.resumo(), "incompativel")
        self.assertIn("2.4.2", self.monitor.store.snapshot("opencode").condition_detail)
        self.assertIn("2.0.17", self.monitor.store.snapshot("opencode").condition_detail)
        self.pulso(partial=True)
        self.coletar()
        self.assertEqual(self.monitor.store.snapshot("opencode").condition, "partial")
        (self.opencode / "live" / f"{ORIGEM}.100.json").unlink()
        self.pulso(origem="unconfigured", estado="no_origin")
        self.assertEqual(self.resumo(), "nao_configurado")

    def test_pulso_ilegivel_nao_derruba(self):
        (self.opencode / "live" / "lixo.1.json").write_text("{nao é json")
        self.pulso()
        self.assertEqual(self.resumo(), "sem_sessoes")

    def test_falha_do_opencode_nao_afeta_outro_provider(self):
        from providers.codex import CodexProvider

        self.monitor.fechar()

        class Quebrado(OpenCodeProvider):
            def collect(self):
                raise OSError("falha sintética")

        self.monitor = Monitor.montar([Quebrado(self.opencode), CodexProvider()], eventos=self.eventos,
                                      relogio=lambda: self.agora)
        self.monitor.atualizar_todos()
        self.assertEqual(self.monitor.store.snapshot("opencode").status.value, "error")
        self.assertEqual(self.monitor.store.snapshot("codex").status.value, "ok")

    def test_sem_tokens_custo_nem_limites(self):
        self.pulso()
        snap = self.coletar()
        self.assertIsNone(snap.usage)
        self.assertIsNone(snap.quotas)
        self.assertEqual((self.p.capabilities.token_usage, self.p.capabilities.quota_windows, self.p.capabilities.costs),
                         (False, False, False))


class Integracao(unittest.TestCase):
    """CLI, diagnóstico, preferências, avatar e cor: o Ombro nunca lê nem grava a configuração do OpenCode."""

    def setUp(self):
        from unittest import mock

        self.casa = Path(tempfile.mkdtemp(prefix="ombro-oc-int-"))
        self.addCleanup(shutil.rmtree, self.casa, True)
        self.ombro = self.casa / ".ombro-screen"
        self.config = self.casa / ".config" / "opencode" / "opencode.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"plugins": ["meu-plugin"]} // meu comentário')
        env = mock.patch.dict(os.environ, {"HOME": str(self.casa), "OMBRO_SCREEN_DIR": str(self.ombro)})
        env.start()
        self.addCleanup(env.stop)

    def rodar(self, *args):
        import contextlib
        import io

        from ombro.cli import main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            codigo = main(["integrations", *args, "--json"])
        return codigo, json.loads(out.getvalue())

    def test_instalar_conferir_remover_sem_tocar_no_opencode(self):
        antes = (self.config.read_bytes(), self.config.stat().st_mtime_ns)
        _, r = self.rodar("install", "opencode", "--dry-run")
        self.assertTrue(r["changed"])
        self.assertIn("oc-gerada-ao-confirmar", r["diff"])
        self.assertFalse((self.ombro / "opencode").exists())  # dry-run não grava nada
        _, r = self.rodar("install", "opencode")
        trecho = json.loads(r["diff"])["plugins"][0]
        origem = trecho["options"]["origin"]
        self.assertRegex(origem, r"^oc-[0-9a-f]{24}$")
        self.assertTrue(trecho["package"].endswith("integrations/opencode"))
        self.assertEqual(trecho["options"].get("dir"), None)  # a pasta padrão não precisa ir no trecho
        self.assertEqual(r["check"]["state"], "partial")
        arq = self.ombro / "opencode" / "registry.json"
        self.assertEqual(arq.stat().st_mode & 0o777, 0o600)
        _, r = self.rodar("install", "opencode")  # de novo: a mesma origem
        self.assertFalse(r["changed"])
        self.assertIn(origem, r["diff"])
        live = self.ombro / "opencode" / "live"
        live.mkdir()
        (live / f"{origem}.{os.getpid()}.json").write_text(json.dumps(
            {"v": 1, "origin": origem, "pid": os.getpid(), "boot": "b", "ns": "n", "anchor": ancora("n"),
             "state": "ready", "version": "2.4.2", "ts": int(time.time() * 1000)}))
        _, r = self.rodar("check", "opencode")
        self.assertEqual(r["integrations"][0]["state"], "partial")  # quem vincula é o ombrod, não o check
        OpenCodeProvider(self.ombro / "opencode").collect()
        _, r = self.rodar("check", "opencode")
        self.assertEqual(r["integrations"][0]["state"], "configured")
        _, r = self.rodar("install", "opencode", "--new-origin")
        segunda = json.loads(r["diff"])["plugins"][0]["options"]["origin"]
        self.assertNotEqual(segunda, origem)
        estados = {o: d["state"] for o, d in json.loads(arq.read_text())["origins"].items()}
        self.assertEqual(estados, {origem: "active", segunda: "pending"})  # outra instalação: a primeira continua
        self.rodar("uninstall", "opencode")
        self.assertEqual({d["state"] for d in json.loads(arq.read_text())["origins"].values()}, {"revoked"})
        _, r = self.rodar("check", "opencode")
        self.assertEqual((r["integrations"][0]["state"], r["integrations"][0]["trecho"]), ("not_configured", None))
        codigo, r = self.rodar("rollback", "opencode")
        self.assertEqual((codigo, r["ok"]), (1, False))
        self.assertEqual((self.config.read_bytes(), self.config.stat().st_mtime_ns), antes)

    def test_trecho_leva_a_pasta_quando_nao_e_a_padrao(self):
        from unittest import mock

        with mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.casa / "outra")}):
            _, r = self.rodar("install", "opencode")
        self.assertEqual(json.loads(r["diff"])["plugins"][0]["options"]["dir"], str(self.casa / "outra"))

    def test_doctor_nao_executa_o_opencode(self):
        from ombro import doctor

        chamadas = []

        class Amb:
            def executar(self, argv):
                chamadas.append(argv)
                return None

            def ipc(self, *_a):
                return {"providers": [{"id": "opencode", "enabled": True, "version": "2.0.17", "condition": "incompatible",
                                       "condition_detail": "atualize à mão para a 2.4.2", "capabilities": ["sessions"]}]}

        v = {x.id: x for x in doctor.agentes(Amb(), {})}
        self.assertEqual(v["provider.opencode"].status, doctor.FAIL)
        self.assertIn("2.4.2", v["provider.opencode"].mensagem)
        self.assertFalse([c for c in chamadas if "opencode" in str(c[0])])

    def test_preferencias_avatar_e_cor_do_quarto_agente(self):
        from config import OmbroConfig, ler_arquivo
        from ombro import preferencias
        from ombro.app import Aplicacao

        m = preferencias.validar_mudancas({"providers.opencode": False, "avatares.opencode": "cubo", "foco": "opencode",
                                           "cores_avatares.nova": "#112233"})
        arq = self.casa / "config.json"
        preferencias.gravar(arq, m)
        cfg = ler_arquivo(arq)[0]
        self.assertNotIn("opencode", cfg["providers"])
        self.assertEqual(cfg["foco"], "opencode")
        app = Aplicacao(OmbroConfig(layout="multiagente", demo=True, vista="focus", foco="opencode", sem_macmon=True,
                                    providers=("opencode",),
                                    cores_avatares=(("nova", "#112233"),)))
        self.addCleanup(app.monitor.fechar)
        app.salvar_png(self.casa / "q.png")
        self.assertEqual(app.sujeito()[0], "opencode")  # o foco no quarto agente é quem o mascote representa
        self.assertEqual(app.mascote.forma.avatar, "nova")  # um dos 12 de sempre: nenhum avatar novo
        self.assertEqual(app.mascote.cor_atual(0.0), (0x11, 0x22, 0x33))  # a cor do corpo segue o id do avatar
        from avatares import catalogo

        self.assertEqual(len(catalogo().ids()), 12)


class VisaoGeral(unittest.TestCase):
    def test_quatro_agentes_cabem_e_tres_ficam_como_antes(self):
        import layout_multiagente as lm
        from coletores.hardware import Hardware
        from core.models import AgentSession, ProviderSnapshot
        from core.state import StateStore

        agora = datetime(2026, 1, 2, 12, 0)
        nomes = [("claude", "Claude Code"), ("codex", "Codex"), ("cursor", "Cursor"), ("opencode", "OpenCode")]
        for n in (1, 2, 3, 4):
            store = StateStore()
            for pid, _ in nomes[:n]:
                store.apply_snapshot(ProviderSnapshot(pid, agora, sessions=(
                    AgentSession(pid, f"{pid}-1", Activity.WORKING, Attention.NONE, project="projeto"),)))
            itens = lm.plano(resumir(store, nomes[:n]), "overview", None, Hardware(disponivel=False), agora)
            linhas = [t for t in itens if isinstance(t, lm.Texto) and t.s in {p.upper() for p, _ in nomes} and t.px == 13]
            self.assertEqual([t.s for t in linhas], [p.upper() for p, _ in nomes[:n]])
            if n < 4:
                self.assertEqual([t.y for t in linhas], [47 + 42 * i for i in range(n)])  # as medidas de sempre
            else:
                topo = [t for t in itens if isinstance(t, lm.Texto) and 28 < t.y < 166]
                self.assertEqual(len({t.y for t in topo}), 8)
                self.assertLessEqual(max(t.y for t in topo) + 5, 166 - 5)  # acima do resumo
                ys = sorted({t.y for t in topo})
                self.assertGreaterEqual(min(b - a for a, b in zip(ys, ys[1:])), 11)  # sem sobreposição


class MascoteNoLaco(Base):
    """De ponta a ponta no laço: trabalhando -> desconhecido não comemora; um fim confirmado comemora."""

    def rodar(self, interromper):
        from unittest import mock

        from config import OmbroConfig
        from ombro.app import Aplicacao

        with mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta)}):
            app = Aplicacao(OmbroConfig(layout="multiagente", providers=("opencode",), png=self.pasta / "q.png",
                                        sem_macmon=True))
        self.addCleanup(app.monitor.fechar)
        app.monitor.fechar()
        app.monitor = Monitor.montar([self.p], relogio=lambda: self.agora, eventos=self.eventos)
        app.mascote.inicio = 0.0
        hw = app.ler_hw()

        def passo(t):
            app.ler_claude()
            app.quadro(hw, app.uso_multi, t)
            app.quadro(hw, app.uso_multi, t + 4)
            return app.mascote.estado

        self.pulso()
        self.ev("session.execution.started", atras=30)
        self.assertEqual(passo(100.0), "trabalhando")
        interromper()
        passo(110.0)
        return passo(120.0)

    def test_fim_confirmado_comemora(self):
        self.assertEqual(self.rodar(lambda: self.ev("session.execution.succeeded", atras=1)), "concluido")

    def test_servidor_caiu(self):
        self.assertEqual(self.rodar(lambda: self.vivos.discard(100)), "ocioso")

    def test_fluxo_perdido(self):
        self.assertEqual(self.rodar(lambda: self.ev("ombro.gap", sid=None, atras=1)), "ocioso")

    def test_sessao_apagada_em_andamento(self):
        self.assertEqual(self.rodar(lambda: self.ev("session.deleted", atras=1)), "ocioso")

    def test_coleta_com_erro(self):
        def falhar():
            raise OSError("falha sintética")

        def quebrar():
            self.p.collect = falhar
            self.agora += timedelta(seconds=60)

        self.assertEqual(self.rodar(quebrar), "ocioso")


class Mascote(unittest.TestCase):
    def test_perda_de_confirmacao_nao_vira_concluido(self):
        from coletores.claude import UsoClaude
        from mascote import Mascote

        for sem_confirmacao, esperado in ((False, "concluido"), (True, "ocioso")):
            m = Mascote()
            u = UsoClaude()
            u.trabalhando = 1
            m.ir_para(m.decidir(u, 0.0), 0.0)
            parado = UsoClaude()
            parado.sem_confirmacao = sem_confirmacao
            m.ir_para(m.decidir(parado, 10.0), 10.0)
            self.assertEqual(m.decidir(parado, 20.0), esperado)


if __name__ == "__main__":
    unittest.main()
