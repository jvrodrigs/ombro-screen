"""Mudanças de preferência com o Ombro rodando: layouts, girar 180°, paisagem no clássico, e o que a IPC responde.

O caminho é um só (Daemon._mudar_preferencias -> Aplicacao.aplicar_cfg): validar, pôr em uso no laço, gravar,
responder com o que vale agora. Nada aqui precisa de tela USB: a saída é falsa e os dados são os do --demo.
"""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import saida
from config import LAYOUTS, OmbroConfig
from ombro import preferencias
from ombro.app import SEM_TELA_S, Aplicacao
from ombro.daemon import Daemon
from ombro.ipc import ErroIPC

TAMANHO = {"auto": (480, 320), "ombro": (480, 320), "minimal-v2": (480, 320), "multiagente": (480, 320)}


class TelaFalsa:
    """Uma tela USB de mentira: guarda o que recebeu; pode ficar fora (desconectada) e voltar."""

    def __init__(self, conectada=True):
        self.conectada = conectada
        self.imagens, self.brilho, self.orientacao, self.fechada = [], None, None, False
        self.chamadas = []  # ("orientacao", paisagem, girar) e ("brilho", v), na ordem
        self.aberturas = 1  # quantas vezes a conexão foi aberta: mudar preferência não pode reabrir

    def pronta(self):
        return self.conectada

    def mostrar(self, img):
        self.imagens.append(img)

    def definir_brilho(self, v):
        self.brilho = v
        self.chamadas.append(("brilho", v))
        return self.conectada

    def definir_orientacao(self, paisagem, girar):
        self.orientacao = (paisagem, girar)
        self.chamadas.append(("orientacao", paisagem, girar))
        return self.conectada

    def estado(self):
        return {"kind": "usb", "connection": "connected" if self.conectada else "disconnected", "brightness": self.brilho}

    def fechar(self):
        self.fechada = True


class Rodando(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp(prefix="om", dir="/tmp" if os.path.isdir("/tmp") else None))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.pasta, ignore_errors=True))
        amb = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": str(self.pasta)})
        amb.start()
        self.addCleanup(amb.stop)
        self.arquivo = self.pasta / "config.json"

    def app(self, tela=None, **cfg):
        cfg.setdefault("intervalo", 0.03)
        app = Aplicacao(OmbroConfig(demo=True, arquivo=self.arquivo, **cfg), saida=tela or TelaFalsa())
        parar = threading.Event()
        t = threading.Thread(target=app.laco, args=(parar.is_set,), daemon=True)
        t.start()
        self.esperar(lambda: app.quadros > 0)

        def fim():
            parar.set()
            app.acordar()
            t.join(5)
            app.fechar()

        self.addCleanup(fim)
        return app

    def daemon(self, tela=None, **cfg):
        app = self.app(tela, **cfg)
        d = Daemon(app.cfg)
        d.app, d.estado = app, "running"
        return d, app

    def esperar(self, condicao, prazo=3.0):
        fim = time.monotonic() + prazo
        while time.monotonic() < fim:
            if condicao():
                return
            time.sleep(0.01)
        self.fail("a condição não chegou a tempo")

    def quadro_novo(self, app):
        """O próximo quadro desenhado depois de agora."""
        n = app.quadros
        self.esperar(lambda: app.quadros > n + 1)
        return app.ultimo_quadro

    def no_arquivo(self):
        return json.loads(self.arquivo.read_text())


class Layouts(Rodando):
    def test_todos_os_layouts_registrados_entram_na_hora(self):
        d, app = self.daemon()
        vistos = {}
        for layout in LAYOUTS:
            r = d.m_layout({"layout": layout, "persist": True})
            self.assertEqual((r["applied"], r["restart_required"]), (["layout"], []), layout)
            self.assertEqual(r["values"], {"layout": layout})
            img = self.quadro_novo(app)
            self.assertEqual(app.cfg.layout, layout)
            self.assertEqual(img.size, TAMANHO.get(layout, (320, 480)), layout)  # o clássico, em pé por padrão
            vistos[layout] = img.tobytes()
            self.assertEqual(self.no_arquivo()["layout"], layout)
        # cada layout desenha um quadro diferente (auto é o do Ombro)
        for a in ("ombro", "minimal-v2", "multiagente", "classico"):
            for b in ("ombro", "minimal-v2", "multiagente", "classico"):
                if a < b:
                    self.assertNotEqual(vistos[a], vistos[b], (a, b))

    def test_minimal_v2_e_o_renderer_do_minimal_v2(self):
        import ombro.app as modulo

        d, app = self.daemon()
        minimal = mock.patch.object(modulo, "desenhar_minimal", wraps=modulo.desenhar_minimal).start()
        ombro = mock.patch.object(modulo, "desenhar_ombro", wraps=modulo.desenhar_ombro).start()
        self.addCleanup(mock.patch.stopall)
        d.m_layout({"layout": "minimal-v2"})
        self.quadro_novo(app)
        self.assertTrue(minimal.called)
        ombro.reset_mock()
        self.quadro_novo(app)
        self.assertFalse(ombro.called)  # nenhum quadro cai no layout do Ombro por engano

    def test_classico_minimal_classico_sem_reiniciar_nem_reabrir_a_tela(self):
        tela = TelaFalsa()
        d, app = self.daemon(tela, layout="classico", paisagem=True)
        monitor, store = app.monitor, app.monitor.store
        self.assertEqual(self.quadro_novo(app).size, (480, 320))
        for layout, tamanho in (("minimal-v2", (480, 320)), ("classico", (480, 320)), ("minimal-v2", (480, 320))):
            r = d.m_layout({"layout": layout, "persist": True})
            self.assertEqual(r["restart_required"], [])
            self.assertEqual(self.quadro_novo(app).size, tamanho)
        self.assertIs(app.monitor, monitor)  # os agentes, as sessões e o banco são os mesmos
        self.assertIs(app.monitor.store, store)
        self.assertEqual(tela.aberturas, 1)
        self.assertFalse(tela.fechada)
        self.assertEqual(tela.chamadas, [])  # deitado dos dois lados: nem a orientação precisou mudar

    def test_alternar_muitas_vezes_termina_no_ultimo(self):
        d, app = self.daemon()
        for _ in range(4):
            for layout in ("classico", "minimal-v2", "multiagente", "ombro"):
                d.m_layout({"layout": layout, "persist": True})
        self.assertEqual((app.cfg.layout, self.no_arquivo()["layout"]), ("ombro", "ombro"))
        self.assertEqual(self.quadro_novo(app).size, (480, 320))
        self.assertEqual(d.m_config_get({})["revision"], 16)

    def test_o_quadro_novo_sai_logo_sem_esperar_o_intervalo(self):
        d, app = self.daemon(layout="classico", intervalo=5.0)  # um quadro a cada 5 s
        n = app.quadros
        d.m_layout({"layout": "minimal-v2"})
        self.esperar(lambda: app.quadros > n, prazo=1.0)  # muito antes dos 5 s
        self.assertEqual(app.ultimo_quadro.size, (480, 320))


class Orientacao(Rodando):
    def test_girar_180_liga_e_desliga_na_hora(self):
        tela = TelaFalsa()
        d, app = self.daemon(tela, layout="minimal-v2")
        antes = self.quadro_novo(app).size
        r = d.m_config_update({"changes": {"girar": True}})
        self.assertEqual((r["applied"], r["restart_required"], r["values"]), (["girar"], [], {"girar": True}))
        self.assertEqual(tela.chamadas[-1], ("orientacao", True, True))
        self.assertEqual(self.quadro_novo(app).size, antes)  # o quadro é o mesmo: quem gira é a tela
        self.assertTrue(d.m_config_get({})["values"]["girar"])
        r = d.m_config_update({"changes": {"girar": False}})
        self.assertEqual(r["values"], {"girar": False})
        self.assertEqual(tela.chamadas[-1], ("orientacao", True, False))
        cfg = d.m_config_get({})
        self.assertEqual((cfg["values"]["girar"], cfg["pending"], self.no_arquivo()["girar"]), (False, {}, False))
        self.assertEqual(tela.aberturas, 1)

    def test_paisagem_so_muda_o_layout_classico(self):
        tela = TelaFalsa()
        d, app = self.daemon(tela, layout="classico")
        self.assertEqual(self.quadro_novo(app).size, (320, 480))  # em pé
        r = d.m_config_update({"changes": {"paisagem": True}})
        self.assertEqual(r["applied"], ["paisagem"])
        self.assertEqual(self.quadro_novo(app).size, (480, 320))  # deitado
        self.assertEqual(tela.chamadas[-1], ("orientacao", True, False))
        d.m_config_update({"changes": {"paisagem": False}})
        self.assertEqual(self.quadro_novo(app).size, (320, 480))
        # nos outros layouts a preferência fica guardada e nada muda na tela: eles são sempre deitados
        d.m_layout({"layout": "minimal-v2"})
        n = len(tela.chamadas)
        r = d.m_config_update({"changes": {"paisagem": True}})
        self.assertEqual((r["applied"], r["deferred"]), (["paisagem"], []))
        self.assertEqual(len(tela.chamadas), n)
        self.assertEqual(self.quadro_novo(app).size, (480, 320))
        d.m_layout({"layout": "classico"})  # e vale quando o clássico voltar
        self.assertEqual(self.quadro_novo(app).size, (480, 320))

    def test_girar_e_trocar_de_layout_em_sequencia(self):
        tela = TelaFalsa()
        d, app = self.daemon(tela, layout="minimal-v2")
        d.m_config_update({"changes": {"girar": True}})
        d.m_layout({"layout": "classico"})
        self.assertEqual(tela.orientacao, (False, True))  # em pé e girada
        d.m_config_update({"changes": {"girar": False, "layout": "multiagente"}})  # as duas juntas: uma operação só
        self.assertEqual(tela.orientacao, (True, False))
        self.assertEqual(self.quadro_novo(app).size, (480, 320))

    def test_simulador_acompanha_a_orientacao(self):
        sim = saida.Simulador(0, paisagem=True)
        self.addCleanup(sim.fechar)
        self.assertEqual(sim.estado()["orientation"], "landscape")
        self.assertTrue(sim.definir_orientacao(False, True))
        self.assertEqual((sim.estado()["width"], sim.estado()["height"], sim.estado()["orientation"]), (320, 480, "portrait"))


class TelaFora(Rodando):
    def test_com_a_tela_desconectada_a_mudanca_vale_e_a_previa_muda(self):
        tela = TelaFalsa(conectada=False)
        d, app = self.daemon(tela, layout="minimal-v2")
        r = d.m_config_update({"changes": {"layout": "classico", "girar": True, "brilho": 40}})
        self.assertEqual(sorted(r["applied"]), ["brilho", "girar", "layout"])
        self.assertEqual(sorted(r["deferred"]), ["brilho", "girar", "layout"])  # a tela recebe quando voltar
        self.assertEqual(r["restart_required"], [])
        self.assertEqual(self.quadro_novo(app).size, (320, 480))  # a prévia já é o clássico
        self.assertEqual(tela.imagens, [])  # nada foi mandado para uma tela que não está lá
        self.assertEqual((tela.orientacao, tela.brilho), ((False, True), 40))  # guardado para a reconexão
        self.assertEqual((self.no_arquivo()["layout"], self.no_arquivo()["girar"]), ("classico", True))

    def test_previa_continua_viva_com_a_tela_fora(self):
        tela = TelaFalsa(conectada=False)
        app = self.app(tela)
        n = app.quadros
        time.sleep(SEM_TELA_S * 1.6)
        self.assertGreater(app.quadros, n)  # um por segundo, só para a prévia
        self.assertLessEqual(app.quadros - n, 3)
        self.assertEqual(tela.imagens, [])

    def test_reconectou_depois_da_mudanca(self):
        tela = TelaFalsa(conectada=False)
        d, app = self.daemon(tela, layout="minimal-v2")
        d.m_config_update({"changes": {"girar": True}})
        tela.conectada = True
        self.esperar(lambda: len(tela.imagens) > 0)
        self.assertEqual(tela.orientacao, (True, True))
        self.assertEqual(d.m_config_update({"changes": {"girar": False}})["deferred"], [])


class GravadoVersusEmUso(Rodando):
    def test_o_que_so_vale_ao_reiniciar_fica_pendente(self):
        d, app = self.daemon()
        r = d.m_config_update({"changes": {"usb": True, "girar": True}})
        self.assertEqual((r["applied"], r["restart_required"]), (["girar"], ["usb"]))
        self.assertEqual(r["values"], {"usb": False, "girar": True})  # em uso: o usb ainda é o de antes
        self.assertEqual(r["pending"], {"usb": True})
        cfg = d.m_config_get({})
        self.assertEqual((cfg["values"]["usb"], cfg["pending"]), (False, {"usb": True}))
        self.assertTrue(self.no_arquivo()["usb"])
        # voltou atrás antes de reiniciar: não há mais nada pendente
        r = d.m_config_update({"changes": {"usb": False}})
        self.assertEqual((r["restart_required"], r["pending"]), ([], {}))
        self.assertEqual(d.m_config_get({})["pending"], {})

    def test_agente_desligado_fica_pendente(self):
        d, _ = self.daemon()
        r = d.m_config_update({"changes": {"providers.codex": False}})
        self.assertEqual((r["restart_required"], r["pending"]), (["providers.codex"], {"providers.codex": False}))
        self.assertTrue(d.m_config_get({})["providers_enabled"]["codex"])  # em uso até reiniciar

    def test_sem_persist_vale_so_ate_reiniciar(self):
        d, app = self.daemon()
        r = d.m_config_update({"changes": {"girar": True}, "persist": False})
        self.assertEqual((r["applied"], r["persisted"], r["file"]), (["girar"], False, None))
        self.assertFalse(self.arquivo.exists())
        self.assertTrue(app.cfg.girar)

    def test_falha_ao_gravar_desfaz_o_que_entrou_em_uso(self):
        tela = TelaFalsa()
        d, app = self.daemon(tela, layout="minimal-v2")
        with mock.patch.object(preferencias, "gravar", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(ErroIPC) as e:
                d.m_config_update({"changes": {"layout": "classico", "girar": True}})
        self.assertEqual(e.exception.codigo, "SERVICE_UNAVAILABLE")
        self.assertIn("Nada mudou", str(e.exception))
        self.assertEqual((app.cfg.layout, app.cfg.girar), ("minimal-v2", False))
        self.assertEqual(tela.orientacao, (True, False))  # a tela voltou
        self.assertEqual(d.m_config_get({})["revision"], 0)  # não houve mudança
        self.assertFalse(self.arquivo.exists())
        self.assertEqual(self.quadro_novo(app).size, (480, 320))

    def test_erro_ao_por_em_uso_nao_grava_nem_deixa_pela_metade(self):
        d, app = self.daemon(layout="minimal-v2")
        real = app.aplicar_cfg
        chamadas = []

        def quebra(cfg):
            chamadas.append(cfg.layout)
            if len(chamadas) == 1:
                real(cfg)  # entra em uso...
                raise RuntimeError("defeito no meio")  # ...e algo dá errado depois
            return real(cfg)

        with mock.patch.object(app, "aplicar_cfg", side_effect=quebra):
            with self.assertRaises(ErroIPC) as e:
                d.m_config_update({"changes": {"layout": "classico"}})
        self.assertEqual(e.exception.codigo, "INTERNAL_ERROR")
        self.assertEqual(chamadas, ["classico", "minimal-v2"])  # desfeito dentro do laço
        self.assertEqual(app.cfg.layout, "minimal-v2")
        self.assertFalse(self.arquivo.exists())

    def test_laco_que_nao_atende_e_erro_e_nada_muda(self):
        app = Aplicacao(OmbroConfig(demo=True, arquivo=self.arquivo), saida=TelaFalsa())  # o laço não está rodando
        self.addCleanup(app.fechar)
        d = Daemon(app.cfg)
        d.app = app
        with self.assertRaises(ErroIPC) as e:
            d.m_config_update({"changes": {"girar": True}})
        self.assertEqual(e.exception.codigo, "SERVICE_UNAVAILABLE")
        self.assertFalse(self.arquivo.exists())
        self.assertFalse(app.cfg.girar)

    def test_valor_invalido_e_recusado_antes_de_tudo(self):
        d, app = self.daemon()
        for mudancas in ({"layout": "inexistente"}, {"girar": "sim"}, {"paisagem": None}):
            with self.assertRaises(ErroIPC) as e:
                d.m_config_update({"changes": mudancas})
            self.assertEqual(e.exception.codigo, "VALIDATION_ERROR", mudancas)
        self.assertEqual(d.m_config_get({})["revision"], 0)
        self.assertFalse(self.arquivo.exists())


class Concorrencia(Rodando):
    def test_pedidos_simultaneos_saem_em_ordem_e_terminam_coerentes(self):
        tela = TelaFalsa()
        d, app = self.daemon(tela)
        respostas, erros = [], []

        def pedir(i):
            try:
                mud = {"layout": ("classico", "minimal-v2", "multiagente", "ombro")[i % 4], "girar": i % 2 == 0}
                respostas.append((mud, d.m_config_update({"changes": mud})))
            except Exception as e:  # noqa: BLE001
                erros.append(e)

        fios = [threading.Thread(target=pedir, args=(i,)) for i in range(12)]
        for f in fios:
            f.start()
        for f in fios:
            f.join(10)
        self.assertEqual(erros, [])
        self.assertEqual(sorted(r["revision"] for _, r in respostas), list(range(1, 13)))  # uma de cada vez
        ultima = max(respostas, key=lambda par: par[1]["revision"])[0]
        cfg = d.m_config_get({})
        # o que está em uso, o que foi gravado e o que a tela recebeu são da mesma (última) operação
        self.assertEqual((cfg["values"]["layout"], cfg["values"]["girar"]), (ultima["layout"], ultima["girar"]))
        self.assertEqual((self.no_arquivo()["layout"], self.no_arquivo()["girar"]), (ultima["layout"], ultima["girar"]))
        self.assertEqual(tela.orientacao, (ultima["layout"] != "classico", ultima["girar"]))
        self.assertEqual(self.quadro_novo(app).size, (480, 320) if ultima["layout"] != "classico" else (320, 480))

    def test_pedido_que_ja_comecou_nao_vira_tempo_esgotado(self):
        app = self.app()
        comecou, liberar = threading.Event(), threading.Event()

        def demorado():
            comecou.set()
            liberar.wait(5)
            return "feito"

        saida_ = []
        t = threading.Thread(target=lambda: saida_.append(app.executar(demorado, timeout=0.05)))
        t.start()
        comecou.wait(2)
        time.sleep(0.15)  # passou do prazo, mas o laço já está atendendo
        liberar.set()
        t.join(5)
        self.assertEqual(saida_, ["feito"])  # a resposta é a de verdade, não um "nada mudou" errado


class TelaLenta(TelaFalsa):
    """Como a tela USB de verdade: um quadro inteiro leva 8 faixas de 0,2 s (1,6 s), e o envio para entre duas
    faixas se o laço disser que há um pedido esperando."""

    def __init__(self):
        super().__init__()
        self.interromper = None
        self.faixas, self.interrompidos = 0, 0

    def mostrar(self, img):
        for i in range(8):
            time.sleep(0.2)
            self.faixas += 1
            if i < 7 and self.interromper is not None and self.interromper():
                self.interrompidos += 1
                return
        self.imagens.append(img)


class TelaLentaDeVerdade(Rodando):
    def test_pedido_durante_o_envio_e_atendido_sem_esperar_o_quadro(self):
        tela = TelaLenta()
        d, app = self.daemon(tela, layout="minimal-v2")
        self.assertIsNotNone(tela.interromper)  # o laço ligou o aviso de "há pedido"
        tempos = []
        for layout in ("classico", "multiagente", "ombro", "minimal-v2", "classico", "minimal-v2"):
            t0 = time.monotonic()
            r = d.m_layout({"layout": layout})  # antes, o 2º pedido esperava o quadro inteiro e estourava o prazo
            tempos.append(time.monotonic() - t0)
            self.assertEqual(r["applied"], ["layout"], layout)
        self.assertLess(max(tempos), 1.0, tempos)  # uma faixa (0,2 s) e pouco, não o quadro (1,6 s)
        self.assertGreater(tela.interrompidos, 0)
        self.assertEqual(app.cfg.layout, "minimal-v2")
        self.esperar(lambda: tela.imagens and tela.imagens[-1].size == (480, 320), prazo=6.0)  # e o último chega inteiro


class Reinicio(Rodando):
    def test_ao_reiniciar_o_que_foi_gravado_e_o_que_entra_em_uso(self):
        d, app = self.daemon(layout="minimal-v2")
        d.m_config_update({"changes": {"layout": "classico", "paisagem": True, "girar": True, "usb": True}})
        from config import resolver

        cfg, _ = resolver({"demo": True}, self.arquivo)  # como o ombrod lê ao iniciar
        self.assertEqual((cfg.layout, cfg.paisagem, cfg.girar, cfg.usb), ("classico", True, True, True))
        novo = Daemon(cfg)
        self.assertEqual(novo.m_config_get({})["pending"], {})  # depois de reiniciar, nada pendente
        self.assertEqual(novo.m_config_get({})["values"]["girar"], True)


if __name__ == "__main__":
    unittest.main()
