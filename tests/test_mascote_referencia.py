"""A referência histórica: o motor antigo do Ombro (o mascote.py desenhado à mão, em 5ceafaf, antes do mascotinhos).

Não é mais o que o mascote padrão deve ser, e sim o que ele era. Serve para provar duas coisas:
  - o pacote "classico" (a escolha "Clássico") é exatamente aquele motor, nos seis estados: ele nasceu dali;
  - no "vivo", o padrão, os seis estados mudaram de propósito (o que eles devem ser está em
    test_mascote_mascotinhos.py, contra a própria biblioteca).

As poses (dx, dy, sx...) valem em qualquer máquina (tolerância 1e-6). Os pixels (um sha256 dos quadros de cada avatar
em cada estado) dependem do sistema e das versões de Python, Pillow e FreeType: noutro ambiente, o teste é pulado. O
arquivo não é refeito: o motor que o gerou não existe mais (ele está no histórico do git, em 5ceafaf).
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import mascotinhos as oa  # noqa: E402
import PIL  # noqa: E402
from PIL import features  # noqa: E402

import avatares  # noqa: E402
import mascote  # noqa: E402

ARQUIVO = Path(__file__).resolve().parent / "referencias_mascote.json"
TEMPOS_QUADRO = (0.0, 0.07, 0.3, 0.6, 1.1, 1.37, 2.0, 2.71, 4.25, 6.05)  # os do arquivo
# os avatares que o motor antigo desenhou (os do arquivo); Bolota, Broto e Fantasma chegaram depois e não têm "antes"
ANTIGOS = ("ombro-cloud", "sereno", "orbita", "cubo", "scout", "pixel", "chevron", "maestro", "nova")


def ambiente() -> dict:
    return {"plataforma": sys.platform, "python": "%d.%d" % sys.version_info[:2], "pillow": PIL.__version__,
            "freetype": features.version("freetype2") or "?"}


def _resumo(imagens) -> str:
    h = hashlib.sha256()
    for img in imagens:
        h.update(f"{img.mode}{img.size}".encode())
        h.update(img.tobytes())
    return h.hexdigest()[:24]


def quadros(pacote: str) -> dict:
    """Um resumo por avatar e estado, como o arquivo: os instantes de TEMPOS_QUADRO e os extras a meio caminho, com as
    animações do `pacote` no estilo da telinha (o mesmo desenho do mascote.desenhar_cloud)."""
    b, out = mascote.biblioteca(pacote), {}
    for aid in ANTIGOS:
        av = avatares.catalogo().avatares[aid]
        f, cor = mascote.forma_de(av), avatares.cor_hex(av.cor)
        for e in mascote.ESTADOS:
            imgs = [oa.desenhar(f, b.parametros(e, t), b.get(e), t, cor, 1.0, mascote.ESTILO) for t in TEMPOS_QUADRO]
            imgs.append(oa.desenhar(f, b.parametros(e, 1.3), b.get(e), 1.3, cor, 0.37, mascote.ESTILO))
            out[f"{aid}/{e}"] = _resumo(imgs)
    return out


class Historico(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
        cls.tempos = cls.dados["tempos_pose"]
        assert tuple(cls.dados["campos"]) == mascote.CAMPOS

    def _pose(self, e, t, pacote="vivo"):
        p = mascote.biblioteca(pacote).parametros(e, t)
        return [round(p[k], 6) for k in mascote.CAMPOS]

    def _pixels(self) -> dict:
        amb = ambiente()
        ref = next((a for a in self.dados["pixels"] if {k: a.get(k) for k in amb} == amb), None)
        if ref is None:
            self.skipTest(f"sem referência de pixels para {amb}")
        return ref["quadros"]

    def test_o_padrao_e_o_vivo(self):
        self.assertIs(mascote.BIBLIOTECA, mascote.biblioteca("vivo"))

    def test_o_classico_tem_a_pose_de_antes_nos_seis_estados(self):
        for e in mascote.ESTADOS:
            for t, antes in zip(self.tempos, self.dados["poses"][e]):
                for k, a, b in zip(mascote.CAMPOS, antes, self._pose(e, t, "classico")):
                    self.assertAlmostEqual(a, b, delta=1e-6, msg=f"{e} em t={t}: {k}")

    def test_o_vivo_mudou_de_proposito_nos_seis_estados(self):
        """A pose do padrão não é mais a do motor antigo: é a do pacote vivo (e cada estado muda na maior parte da
        grade), alerta e reset inclusive."""
        for e in mascote.ESTADOS:
            diferentes = sum(antes != self._pose(e, t) for t, antes in zip(self.tempos, self.dados["poses"][e]))
            self.assertGreater(diferentes, len(self.tempos) // 2, f"{e}: ainda parece o movimento antigo")

    def test_a_referencia_e_dos_nove_avatares_de_antes(self):
        ref = self._pixels()
        self.assertEqual(sorted({k.split("/")[0] for k in ref}), sorted(ANTIGOS))

    def test_os_quadros_do_classico_sao_os_de_antes(self):
        ref = self._pixels()
        atual = quadros("classico")
        self.assertEqual(sorted(k for k in atual if atual[k] != ref[k]), [])

    def test_os_quadros_do_vivo_mudaram(self):
        ref = self._pixels()
        atual = quadros("vivo")
        self.assertEqual(sorted(k for k in atual if atual[k] == ref[k]), [])


if __name__ == "__main__":
    unittest.main()
