"""Coletor de hardware com psutil e macmon falsos: nada aqui depende da máquina que roda o teste."""
import unittest
from types import SimpleNamespace
from unittest import mock

from coletores import hardware
from coletores.hardware import HISTORICO, ColetorHardware, LeitorMacmon, campos_macmon

GB = 1024**3


class PsutilFalso:
    def __init__(self):
        self.cpu = 25.0
        self.recebidos, self.enviados = 1_000_000, 500_000

    def cpu_percent(self, intervalo=None):
        return self.cpu

    def virtual_memory(self):
        return SimpleNamespace(total=16 * GB, available=6 * GB)

    def swap_memory(self):
        return SimpleNamespace(used=GB // 2)

    def disk_usage(self, caminho):
        return SimpleNamespace(percent=63.0, free=300 * GB)

    def net_io_counters(self):
        return SimpleNamespace(bytes_recv=self.recebidos, bytes_sent=self.enviados)


AMOSTRA = {
    "cpu_scaled_ratio": 0.41, "gpu_scaled_ratio": 0.12,
    "cpu_tiers": [{"label": "E", "scaled_ratio": 0.6}, {"label": "P", "scaled_ratio": 0.3}],
    "temp": {"cpu_temp_avg": 48.5, "gpu_temp_avg": 41}, "all_power": 6.2, "sys_power": 0,
    "memory": {"ram_total": 24 * GB, "ram_usage": 10 * GB, "swap_usage": GB},
}


class Coletor(unittest.TestCase):
    def setUp(self):
        self.ps = PsutilFalso()
        self.relogio = 100.0
        p1 = mock.patch.object(hardware, "psutil", self.ps)
        p2 = mock.patch.object(hardware.time, "monotonic", lambda: self.relogio)
        p3 = mock.patch.object(LeitorMacmon, "disponivel", staticmethod(lambda: False))
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        self.c = ColetorHardware()

    def test_sem_macmon_usa_so_o_psutil(self):
        self.assertIsNone(self.c.macmon)
        h = self.c.ler()
        self.assertEqual(h.cpu_pct, 25.0)
        self.assertEqual((h.ram_total, h.ram_usada, h.swap_usada), (16 * GB, 10 * GB, GB // 2))
        self.assertEqual((h.disco_pct, h.disco_livre), (63.0, 300 * GB))
        self.assertFalse(h.macmon)
        self.assertIsNone(h.gpu_pct)
        self.assertEqual(h.nucleos, [])

    def test_rede_em_bytes_por_segundo(self):
        self.ps.recebidos += 2_000_000
        self.ps.enviados += 100_000
        self.relogio += 2.0
        h = self.c.ler()
        self.assertEqual((h.rede_down, h.rede_up), (1_000_000.0, 50_000.0))

    def test_contador_de_rede_que_volta_nao_fica_negativo(self):
        self.ps.recebidos = 0  # a interface reiniciou
        self.relogio += 1
        self.assertEqual(self.c.ler().rede_down, 0)

    def test_historico_guarda_as_ultimas_amostras(self):
        for i in range(HISTORICO + 5):
            self.ps.cpu = float(i)
            h = self.c.ler()
        self.assertEqual(len(h.hist_cpu), HISTORICO)
        self.assertEqual(h.hist_cpu[-1], HISTORICO + 4)
        self.assertEqual(h.hist_gpu, [])

    def test_com_amostra_do_macmon(self):
        self.c.macmon = SimpleNamespace(ultimo=dict(AMOSTRA))
        h = self.c.ler()
        self.assertTrue(h.macmon)
        self.assertAlmostEqual(h.cpu_pct, 41.0)
        self.assertEqual(h.nucleos, [("E", 60.0), ("P", 30.0)])
        self.assertAlmostEqual(h.gpu_pct, 12.0)
        self.assertEqual((h.temp_cpu, h.temp_gpu, h.watts_soc), (48.5, 41.0, 6.2))
        self.assertIsNone(h.watts_sistema)  # 0 = o macmon não sabe
        self.assertEqual((h.ram_total, h.ram_usada, h.swap_usada), (24 * GB, 10 * GB, GB))
        self.assertEqual(len(h.hist_gpu), 1)

    def test_macmon_antigo_sem_cpu_tiers(self):
        amostra = {"cpu_scaled_ratio": .2, "ecpu_scaled_ratio": .5, "pcpu_scaled_ratio": .1}
        self.c.macmon = SimpleNamespace(ultimo=amostra)
        self.assertEqual(self.c.ler().nucleos, [("E", 50.0), ("P", 10.0)])

    def test_amostra_estranha_do_macmon_nao_derruba(self):
        for estranha in ({"cpu_scaled_ratio": None}, {"temp": [1, 2]}, {"cpu_tiers": [{"scaled_ratio": "x"}]},
                         {"memory": {"ram_total": "muito"}}):
            self.c.macmon = SimpleNamespace(ultimo=estranha)
            h = self.c.ler()  # antes, um campo nulo levantava TypeError e parava o laço da tela
            self.assertFalse(h.macmon, estranha)
            self.assertEqual(h.cpu_pct, 25.0)
            self.assertEqual(h.ram_total, 16 * GB)

    def test_campos_macmon_converte_numeros_em_texto(self):
        campos = campos_macmon({"temp": {"cpu_temp_avg": "47"}, "all_power": "3.5"}, 10.0, (1, 2, 3))
        self.assertEqual((campos["temp_cpu"], campos["watts_soc"]), (47.0, 3.5))
        self.assertEqual((campos["ram_total"], campos["ram_usada"], campos["swap_usada"]), (1, 2, 3))
        self.assertAlmostEqual(campos["cpu_pct"], 10.0)  # sem cpu_scaled_ratio, fica o do psutil


class ProcessoFalso:
    def __init__(self, linhas, leitor=None, vistos=None):
        self.stdout = self._linhas(linhas, leitor, vistos)
        self.terminado = False

    @staticmethod
    def _linhas(linhas, leitor, vistos):
        for linha in linhas:
            yield linha
            if vistos is not None:  # o que o leitor guardou depois de cada linha
                vistos.append(leitor.ultimo)

    def poll(self):
        return 0 if self.terminado else None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.terminado = True


class Leitor(unittest.TestCase):
    def test_guarda_a_ultima_amostra_valida_e_ignora_lixo(self):
        leitor, vistos = LeitorMacmon(), []
        linhas = ['{"cpu_scaled_ratio": 0.1}\n', "isto não é json\n", "[1, 2]\n", '{"cpu_scaled_ratio": 0.3}\n']
        with mock.patch.object(hardware.subprocess, "Popen", lambda *a, **kw: ProcessoFalso(linhas, leitor, vistos)):
            leitor._rodar_uma_vez()
        a, b = {"cpu_scaled_ratio": 0.1}, {"cpu_scaled_ratio": 0.3}
        self.assertEqual(vistos, [a, a, a, b])  # o lixo e a lista [1, 2] não substituem a amostra
        self.assertIsNone(leitor.ultimo)  # o macmon saiu: a amostra não vale mais

    def test_macmon_que_nao_abre_vira_erro(self):
        leitor = LeitorMacmon()
        with mock.patch.object(hardware.subprocess, "Popen", side_effect=FileNotFoundError("macmon")):
            leitor._rodar_uma_vez()
        self.assertEqual(leitor.erro, "macmon")
        self.assertIsNone(leitor.ultimo)

    def test_parar_encerra_o_processo_e_a_thread(self):
        leitor = LeitorMacmon()
        leitor._proc = ProcessoFalso([])
        leitor.parar()
        self.assertTrue(leitor._proc.terminado)
        with mock.patch.object(LeitorMacmon, "_rodar_uma_vez") as rodar:
            leitor.run()  # já parado: não roda o macmon de novo
        rodar.assert_not_called()

    def test_coletor_so_usa_macmon_se_estiver_instalado(self):
        with mock.patch.object(hardware, "psutil", PsutilFalso()), \
                mock.patch.object(LeitorMacmon, "disponivel", staticmethod(lambda: True)), \
                mock.patch.object(LeitorMacmon, "start") as start:
            c = ColetorHardware(usar_macmon=True)
            self.assertIsNotNone(c.macmon)
            start.assert_called_once()
            self.assertIsNone(ColetorHardware(usar_macmon=False).macmon)


if __name__ == "__main__":
    unittest.main()
