"""Avatares: o catálogo (avatars/), o adaptador do formato do open-bot-avatar, a escolha por agente e o mascote.

Os vetores do hash (FNV-1a) e das escolhas aleatórias são os mesmos de app/src/features/avatars/catalog.test.ts:
o app e a telinha precisam escolher o mesmo avatar para a mesma sessão.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

import avatares
import mascote
from config import ConfigInvalida, ler_arquivo, resolver
from ombro import preferencias

IDS = ("ombro-cloud", "sereno", "orbita", "cubo", "scout", "pixel", "chevron", "maestro", "nova")


class Catalogo(unittest.TestCase):
    def test_carrega_todos_e_os_padroes(self):
        c = avatares.catalogo()
        self.assertEqual(c.ids(), IDS)
        self.assertEqual(c.por_agente, {"claude": "ombro-cloud", "codex": "orbita", "cursor": "cubo"})
        self.assertEqual(c.padrao, "ombro-cloud")
        self.assertEqual(c.sorteaveis(), tuple(sorted(IDS)))
        for p, cor in c.cor_agente.items():
            self.assertRegex(cor, r"^#[0-9A-F]{6}$", p)

    def test_cada_definicao_segue_o_contrato_e_tem_rosto(self):
        pasta = avatares.PASTA / "defs"
        self.assertEqual(sorted(p.stem for p in pasta.glob("*.json")), sorted(IDS))  # nada solto fora do catálogo
        for aid in IDS:
            dados = json.loads((pasta / f"{aid}.json").read_text(encoding="utf-8"))
            self.assertEqual(set(dados), {"version", "id", "name", "body", "eyes"}, aid)  # o formato v1, sem extras
            av = avatares.catalogo().avatares[aid]
            x0, y0, x1, y1 = av.limites
            self.assertTrue(5 <= x0 and x1 <= 95 and 5 <= y0 and y1 <= 95, f"{aid}: fora da área recomendada")
            self.assertEqual(av.aumento_olhos(), avatares.EYE_BOOST, f"{aid}: o rosto não tem espaço para os olhos grandes")

    def test_nenhum_contorno_repetido(self):
        """Contornos e rostos não se repetem. (Se dois avatares *parecem* iguais é julgamento visual, feito nas
        pranchas: medidas de área, como a IoU, não separam formas compactas que diferem nos cantos.)"""
        caminhos = [av.path for av in avatares.catalogo().avatares.values()]
        self.assertEqual(len(set(caminhos)), len(caminhos))
        nomes = [av.nome for av in avatares.catalogo().avatares.values()]
        self.assertEqual(len(set(nomes)), len(nomes))

    def test_catalogo_com_avatar_inexistente_e_recusado(self):
        with tempfile.TemporaryDirectory() as d:
            pasta = Path(d)
            (pasta / "defs").mkdir()
            (pasta / "catalog.json").write_text(json.dumps({"default": "x", "providers": {}, "avatars": []}))
            with self.assertRaises(avatares.AvatarInvalido):
                avatares.carregar(pasta)


class Path_(unittest.TestCase):
    def test_comandos_absolutos_e_relativos(self):
        self.assertEqual(avatares.poligonos("M10 10H90V90H10Z"), [[(10, 10), (90, 10), (90, 90), (10, 90)]])
        rel = avatares.poligonos("m10 10h80v80h-80z")
        self.assertEqual(rel, [[(10, 10), (90, 10), (90, 90), (10, 90)]])

    def test_curvas_e_reflexoes_terminam_no_ponto_certo(self):
        p = avatares.poligonos("M10 50C10 20 90 20 90 50S10 80 10 50ZM20 20Q30 10 40 20T60 20Z")
        self.assertEqual(len(p), 2)
        self.assertAlmostEqual(p[0][-1][0], 10)
        self.assertAlmostEqual(p[1][-1][0], 60)

    def test_arco_vira_circulo(self):
        p = avatares.poligonos("M20 50A30 30 0 1 1 80 50A30 30 0 1 1 20 50Z")[0]
        for x, y in p:
            self.assertAlmostEqual(((x - 50) ** 2 + (y - 50) ** 2) ** .5, 30, delta=0.01)

    def test_nonzero_furo_e_uniao(self):
        anel = avatares.poligonos("M10 10H90V90H10ZM30 30V70H70V30Z")  # o de dentro no sentido contrário: furo
        self.assertTrue(avatares.dentro(anel, 20, 20))
        self.assertFalse(avatares.dentro(anel, 50, 50))
        uniao = avatares.poligonos("M10 10H60V60H10ZM40 40H90V90H40Z")  # mesmo sentido: somam
        self.assertTrue(avatares.dentro(uniao, 50, 50))

    def test_path_invalido(self):
        for ruim in ("", "L10 10", "M10 10 L", "M10 10 X 20 20", "M10 10 L20 20 <script>"):
            with self.assertRaises(avatares.AvatarInvalido, msg=ruim):
                avatares.poligonos(ruim)


class Definicao(unittest.TestCase):
    BOA = {"version": 1, "id": "caixa", "name": "Caixa", "body": {"path": "M20 20H80V80H20Z"},
           "eyes": {"x": 50, "y": 50, "spacing": 15, "scale": 1}}

    def test_boa(self):
        self.assertEqual(avatares.ler_definicao(self.BOA)[0], "caixa")

    def test_recusas(self):
        casos = {
            "campo a mais": {**self.BOA, "color": "#fff"},
            "versão": {**self.BOA, "version": 2},
            "id": {**self.BOA, "id": "Caixa!"},
            "olhos fora": {**self.BOA, "eyes": {"x": 5, "y": 5, "spacing": 4, "scale": 1}},
            "olhos sem número": {**self.BOA, "eyes": {"x": "50", "y": 50, "spacing": 15, "scale": 1}},
            "body com mais": {**self.BOA, "body": {"path": "M20 20H80V80H20Z", "fill": "red"}},
            "fora da caixa": {**self.BOA, "body": {"path": "M-50 20H80V80H20Z"}},
        }
        for nome, d in casos.items():
            with self.assertRaises(avatares.AvatarInvalido, msg=nome):
                avatares.ler_definicao(d)
        with self.assertRaises(avatares.AvatarInvalido):
            avatares.ler_definicao(self.BOA, esperado="outro")


class Escolha(unittest.TestCase):
    def test_fnv1a_igual_ao_do_app(self):
        vetores = {"": 2166136261, "a": 3826002220, "ombro:0": 4140119464, "codex:abc-123": 3675777911,
                   "claude:sessão-ç": 3870169681, "cursor:4294967295": 3203207324}
        for texto, h in vetores.items():
            self.assertEqual(avatares.fnv1a(texto), h, texto)

    def test_padrao_especifico_e_inexistente(self):
        e = avatares.escolher
        self.assertEqual(e("padrao", "codex").id, "orbita")
        self.assertEqual(e(None, "cursor").id, "cubo")  # agente sem preferência explícita
        self.assertEqual(e("sereno", "claude").id, "sereno")
        self.assertEqual(e("nao-existe", "cursor").id, "cubo")  # avatar que sumiu: o padrão do agente
        self.assertEqual(e("padrao", None).id, "ombro-cloud")
        self.assertEqual(e("nova", None).id, "nova")
        self.assertEqual(e("padrao", "agente-novo").id, "ombro-cloud")  # agente sem padrão: o do Ombro

    def test_aleatorio_iguais_aos_do_app(self):
        e = avatares.escolher
        self.assertEqual(e("aleatorio", "codex", "sessao", 0, "s-1").id, "scout")
        self.assertEqual(e("aleatorio", "codex", "sessao", 0, "s-2").id, "cubo")
        self.assertEqual(e("aleatorio", "claude", "inicio", 12345).id, "sereno")
        self.assertEqual(e("aleatorio", None, "inicio", 7).id, "chevron")

    def test_aleatorio_estavel_e_variado(self):
        e = avatares.escolher
        self.assertEqual(e("aleatorio", "codex", "inicio", 99).id, e("aleatorio", "codex", "inicio", 99).id)
        por_sessao = {e("aleatorio", "codex", "sessao", 0, f"s{i}").id for i in range(60)}
        self.assertGreaterEqual(len(por_sessao), 6)  # sessões diferentes usam a coleção toda
        self.assertEqual(e("aleatorio", "codex", "sessao", 0, None).id, e("aleatorio", "codex", "inicio", 0).id)  # sem sessão: o de inicio

    def test_so_sorteia_os_marcados(self):
        c = avatares.catalogo()
        so_um = avatares.Catalogo({k: v for k, v in c.avatares.items()}, c.padrao, c.por_agente, c.cor_agente)
        object.__setattr__(so_um, "avatares", {k: (v if k == "pixel" else _sem_sorteio(v)) for k, v in c.avatares.items()})
        for i in range(20):
            self.assertEqual(avatares.escolher("aleatorio", "codex", "inicio", i, cat=so_um).id, "pixel")


def _sem_sorteio(av):
    from dataclasses import replace

    return replace(av, aleatorio=False)


class Mascote_(unittest.TestCase):
    def test_forma_cabe_na_area_com_a_base_no_lugar(self):
        for av in avatares.catalogo().avatares.values():
            f = mascote.forma_de(av)
            xs = [x for p in f.poligonos for x, _ in p]
            ys = [y for p in f.poligonos for _, y in p]
            self.assertGreaterEqual(min(xs), mascote.BX0 - 1, av.id)
            self.assertLessEqual(max(xs), mascote.BX1 + 1, av.id)
            self.assertAlmostEqual(max(ys), mascote.BY1, delta=0.5)
            self.assertGreaterEqual(min(ys), mascote.BY0 - 1, av.id)
            self.assertEqual(len(f.olhos), 2)

    def test_todos_os_avatares_em_todos_os_estados(self):
        for av in avatares.catalogo().avatares.values():
            f = mascote.forma_de(av)
            for est in mascote.ESTADOS:
                img = mascote.desenhar_cloud(mascote.parametros(est, 1.0), est, 1.0, (200, 100, 80), forma=f)
                self.assertGreater(img.getbbox()[2] - img.getbbox()[0], 50, f"{av.id}/{est}")

    def test_furo_do_anel_aparece_na_tela(self):
        orb = mascote.forma_de(avatares.catalogo().avatares["orbita"])
        img = mascote.desenhar_cloud(mascote.parametros("ocioso", 0.0) | {"pisca": 0.0}, "ocioso", 0.0, (0, 200, 0), forma=orb)
        # um ponto no furo do anel, fora da esfera (coordenadas do avatar), levado para a camada
        x0, y0, x1, y1 = avatares.catalogo().avatares["orbita"].limites
        k = min(mascote.W / (x1 - x0), mascote.H / (y1 - y0))
        px, py = mascote._pt(mascote.CX + (19.5 - (x0 + x1) / 2) * k, mascote.BY1 - (y1 - 58.7) * k)
        self.assertEqual(img.getpixel((int(px), int(py)))[3], 0)  # transparente: é furo

    def test_troca_de_avatar_numa_transicao_curta(self):
        c = avatares.catalogo()
        m = mascote.Mascote(cor_modo="agente")
        self.assertEqual(m.forma.avatar, "ombro-cloud")
        m.ir_para("trabalhando", 9.0)
        m.usar(c.avatares["pixel"], (1, 2, 3), agora=10.0)
        m.camada(10.0 + mascote.TRANSICAO * .3)
        self.assertEqual(m.forma.avatar, "ombro-cloud")  # primeira metade: encolhendo, de olhos fechando
        m.camada(10.0 + mascote.TRANSICAO * .6)
        self.assertEqual((m.forma.avatar, m.cor), ("pixel", (1, 2, 3)))  # no meio: forma e cor do agente novas
        m.ir_para("trabalhando", 10.0 + mascote.TRANSICAO + .01)  # sem precisar o estado mudar
        self.assertIsNone(m._troca)
        # pedido de novo antes do meio: muda só o destino, sem recomeçar
        m.usar(c.avatares["nova"], (4, 5, 6), agora=20.0)
        m.usar(c.avatares["cubo"], (7, 8, 9), agora=20.1)
        m.ir_para("trabalhando", 20.0 + mascote.TRANSICAO * .6)
        self.assertEqual(m.forma.avatar, "cubo")
        m.usar(c.avatares["scout"], None, agora=30.0, ja=True)  # mudança de preferência: na hora
        self.assertEqual((m.forma.avatar, m.cor), ("scout", avatares.cor_hex(c.avatares["scout"].cor)))
        self.assertIsNone(m._troca)

    def test_transicao_encolhe_e_fecha_os_olhos(self):
        c = avatares.catalogo()
        t = mascote.TRANSICAO * .45  # perto do meio, ainda com a forma antiga
        alturas, olhos = [], []
        for trocando in (False, True):
            m = mascote.Mascote(cor_modo="agente", cor_fixa=(200, 100, 80), estado="esperando")  # de olhos abertos
            m.inicio = 0.0
            if trocando:
                m.usar(c.avatares["cubo"], None, agora=0.0)
            img = m.camada(t)
            linhas = {i // img.width for i, px in enumerate(img.getdata()) if px == (200, 100, 80, 255)}
            alturas.append(max(linhas) - min(linhas))
            olhos.append(sum(1 for px in img.getdata() if px[3] and px[:3] == (26, 27, 29)))
        self.assertLess(alturas[1], alturas[0] * .95)  # encolhido
        self.assertLess(olhos[1], olhos[0] * .5)  # olhos quase fechados

    def test_realce_da_cor_na_troca_de_estado(self):
        m = mascote.Mascote(cor_modo="agente")
        base = m.cor
        m.ir_para("trabalhando", 5.0)
        self.assertEqual(m.cor, base)  # a cor do agente não muda...
        clara = m.cor_atual(5.05)
        self.assertTrue(all(a > b for a, b in zip(clara, base)))  # ...mas clareia um instante
        self.assertEqual(m.cor_atual(5.0 + mascote.REALCE), base)
        sorteada = mascote.Mascote(cor_modo="sorteada")
        sorteada.ir_para("trabalhando", 5.0)
        self.assertEqual(sorteada.cor_atual(5.05), sorteada.cor)  # lá a cor nova já marca a troca

    def test_olhar_acompanha_a_inclinacao_como_o_cloud_antigo(self):
        f = mascote.forma_de(avatares.catalogo().avatares["ombro-cloud"])
        e = f.olhos[0]
        p = dict(mascote.parametros("ocioso", 0.0), rot=-6.0)  # uma inclinação qualquer: a geometria é a do palco
        # deslocamento lateral dos olhos pela inclinação = o que o cloud antigo teria (altura ALTURA_OLHAR)
        com = mascote._corpo(p, e["cx"] + mascote._olhar_x(p, e), e["cy"])[0] - mascote._corpo(dict(p, rot=0.0), e["cx"], e["cy"])[0]
        import math
        esperado = mascote.ALTURA_OLHAR * math.sin(math.radians(p["rot"]))
        self.assertAlmostEqual(com, esperado, delta=abs(esperado) * .05)
        reto = dict(p, rot=0.0)
        self.assertEqual(mascote._olhar_x(reto, e), reto["olhaX"])  # sem inclinação, o olhar é o de sempre

    def test_modos_de_cor(self):
        c = avatares.catalogo()
        m = mascote.Mascote(cor_modo="sorteada")
        m.usar(c.avatares["cubo"], (10, 20, 30), 0.0, ja=True)
        cores = set()
        for i, est in enumerate(("trabalhando", "esperando", "ocioso", "alerta")):
            m.ir_para(est, float(i))
            cores.add(m.cor)
        self.assertGreater(len(cores), 1)  # sorteada a cada estado, como antes
        m.definir_cor_modo("agente")
        self.assertEqual(m.cor, (10, 20, 30))
        fixa = mascote.Mascote(cor_fixa=(9, 9, 9), cor_modo="agente")
        fixa.usar(c.avatares["cubo"], (10, 20, 30), 0.0, ja=True)
        fixa.ir_para("trabalhando", 1.0)
        self.assertEqual(fixa.cor, (9, 9, 9))  # a fixa vale por cima de tudo

    def test_custo_do_desenho(self):
        """Regressão de desempenho: desenhar o mascote não pode ficar muito mais caro que o cloud de antes (~1 ms)."""
        for aid in ("ombro-cloud", "orbita"):  # um contorno simples e um com furo (regra nonzero)
            f = mascote.forma_de(avatares.catalogo().avatares[aid])
            p = mascote.parametros("trabalhando", 0.3)
            t = time.perf_counter()
            for _ in range(20):
                mascote.desenhar_cloud(p, "trabalhando", 0.3, (200, 100, 80), forma=f)
            ms = (time.perf_counter() - t) / 20 * 1000
            self.assertLess(ms, 25, f"{aid}: {ms:.1f} ms por quadro")


class Configuracao(unittest.TestCase):
    def setUp(self):
        self.pasta = Path(tempfile.mkdtemp())
        self.arq = self.pasta / "config.json"

    def test_bloco_avatars_no_arquivo(self):
        self.arq.write_text(json.dumps({"avatars": {"claude": "sereno", "codex": "aleatorio", "default": "nova", "random": "sessao"},
                                        "cor_modo": "sorteada"}))
        cfg, avisos = resolver({}, self.arq)
        self.assertEqual((cfg.avatar("claude"), cfg.avatar("codex"), cfg.avatar("cursor"), cfg.avatar("padrao")),
                         ("sereno", "aleatorio", "padrao", "nova"))
        self.assertEqual((cfg.avatar_sorteio, cfg.cor_modo), ("sessao", "sorteada"))
        self.assertEqual(avisos, [])

    def test_sem_nada_e_o_padrao(self):
        cfg, _ = resolver({}, self.arq if self.arq.exists() else None)
        self.assertEqual((cfg.avatar("claude"), cfg.avatar_sorteio, cfg.cor_modo), ("padrao", "inicio", "agente"))

    def test_avatar_que_sumiu_avisa_e_nao_trava(self):
        self.arq.write_text(json.dumps({"avatars": {"cursor": "apagado"}}))
        valores, avisos = ler_arquivo(self.arq)
        self.assertEqual(dict(valores["avatares"]), {"cursor": "apagado"})
        self.assertIn("não existe", avisos[0])
        self.assertEqual(avatares.escolher("apagado", "cursor").id, "cubo")

    def test_recusas_no_arquivo(self):
        for ruim in ({"avatars": {"cluade": "nova"}}, {"avatars": {"claude": "Nova!"}}, {"avatars": {"random": "sempre"}},
                     {"avatars": "nova"}, {"cor_modo": "arco-iris"}):
            self.arq.write_text(json.dumps(ruim))
            with self.assertRaises(ConfigInvalida, msg=ruim):
                ler_arquivo(self.arq)

    def test_ipc_valida_e_grava_no_bloco(self):
        ok = preferencias.validar_mudancas({"avatares.codex": "nova", "avatar_sorteio": "sessao", "cor_modo": "sorteada"})
        self.assertEqual(ok["avatares.codex"], "nova")
        for ruim in ({"avatares.codex": "apagado"}, {"avatares.codex": None}, {"avatar_sorteio": "sempre"}):
            with self.assertRaises(ConfigInvalida, msg=ruim):
                preferencias.validar_mudancas(ruim)
        self.arq.write_text(json.dumps({"versao": 1, "brilho": 40}))
        preferencias.gravar(self.arq, {"avatares.codex": "nova", "avatares.padrao": "sereno", "avatar_sorteio": "sessao"})
        dados = json.loads(self.arq.read_text())
        self.assertEqual(dados["avatars"], {"codex": "nova", "default": "sereno", "random": "sessao"})
        self.assertEqual(dados["brilho"], 40)
        cfg, _ = resolver({}, self.arq)  # o que a IPC grava, o arquivo lê igual
        self.assertEqual((cfg.avatar("codex"), cfg.avatar("padrao"), cfg.avatar_sorteio), ("nova", "sereno", "sessao"))

    def test_esquema_lista_os_avatares(self):
        op = {o["key"]: o for o in preferencias.esquema()["options"]}
        for k in ("avatares.claude", "avatares.codex", "avatares.cursor", "avatares.padrao"):
            self.assertEqual(op[k]["choices"], ["padrao", "aleatorio", *IDS])
            self.assertTrue(op[k]["live"])
        self.assertEqual(op["avatar_sorteio"]["choices"], ["inicio", "sessao"])
        self.assertEqual(op["cor_modo"]["choices"], ["agente", "sorteada"])


class QuemOMascoteRepresenta(unittest.TestCase):
    """O avatar segue o agente da vez: Claude nos layouts de um agente; no multiagente, o mais urgente."""

    def _app(self, **opcoes):
        import os
        from unittest import mock

        ambiente = mock.patch.dict(os.environ, {"OMBRO_SCREEN_DIR": tempfile.mkdtemp()})
        ambiente.start()
        self.addCleanup(ambiente.stop)
        from ombro.app import Aplicacao

        cfg, _ = resolver({"demo": True, "sem_macmon": True, "nao_abrir": True, **opcoes})
        app = Aplicacao(cfg)
        self.addCleanup(app.fechar)
        app.ler_claude()
        return app

    def test_layout_de_um_agente_e_o_claude(self):
        app = self._app(layout="minimal-v2")
        self.assertEqual(app.sujeito()[0], "claude")
        app.atualizar_avatar(0.0, ja=True)
        self.assertEqual(app.mascote.forma.avatar, "ombro-cloud")

    def test_multiagente_segue_o_mais_urgente(self):
        app = self._app(layout="multiagente")
        urg = app.resumo.mais_urgente
        self.assertIsNotNone(urg)
        self.assertEqual(app.sujeito()[0], urg.provider_id)
        app.atualizar_avatar(0.0, ja=True)
        esperado = avatares.catalogo().por_agente[urg.provider_id]
        self.assertEqual(app.mascote.forma.avatar, esperado)
        self.assertEqual(app.mascote.cor, avatares.cor_hex(avatares.catalogo().cor_agente[urg.provider_id]))

    def test_alerta_e_reset_sao_do_claude(self):
        app = self._app(layout="multiagente")
        self.assertNotEqual(app.sujeito()[0], "claude")  # o demo começa com outro agente mais urgente
        for estado in ("alerta", "reset"):
            app.mascote.estado = estado
            self.assertEqual(app.sujeito()[0], "claude")
            app.atualizar_avatar(0.0, ja=True)
            self.assertEqual(app.mascote.forma.avatar, avatares.catalogo().por_agente["claude"])

    def test_preferencia_ao_vivo_e_resolvidos(self):
        app = self._app(layout="minimal-v2")
        app.definir_avatar("avatares.claude", "pixel")
        self.assertEqual(app.mascote.forma.avatar, "pixel")  # na hora, sem esperar o estado mudar
        r = app.avatares_resolvidos()
        self.assertEqual(r["claude"], {"avatar": "pixel", "color": avatares.catalogo().cor_agente["claude"], "choice": "pixel"})
        self.assertEqual(r["padrao"]["avatar"], "ombro-cloud")
        app.definir_avatar("avatares.codex", "aleatorio")
        self.assertIn(app.avatares_resolvidos()["codex"]["avatar"], IDS)
        app.definir_avatar("cor_modo", "sorteada")
        self.assertEqual(app.mascote.cor_modo, "sorteada")


if __name__ == "__main__":
    unittest.main()
