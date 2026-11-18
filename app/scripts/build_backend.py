#!/usr/bin/env python3
"""Monta o backend que vai dentro do Ombro.app: um CPython relocável, as dependências e o código do Ombro.

  .venv/bin/python -I app/scripts/build_backend.py            monta em app/src-tauri/bundle-resources/
  .venv/bin/python -I app/scripts/build_backend.py --check    só verifica o que já foi montado
  .venv/bin/python -I app/scripts/build_backend.py --sign "Developer ID Application: …"   monta e assina
  .venv/bin/python -I app/scripts/build_backend.py --sign -  assinatura ad-hoc (builds locais), também com hardened runtime

Resultado (o tauri.release.conf.json leva as duas pastas para Contents/Resources/):
  bundle-resources/runtime/   CPython 3.14 (python-build-standalone), com Pillow, psutil, pyserial, numpy, pyusb, o
                              mascotinhos (o desenho do mascote) e a libusb do libusb-package; sem pip, tkinter, testes
                              nem cabeçalhos
  bundle-resources/backend/   o código do Ombro (o mesmo do repositório), pré-compilado
  bundle-resources/licenses/  THIRD_PARTY_NOTICES.md e o texto da licença de tudo o que vai no app

Por que um CPython de verdade, e não um executável congelado: o hook dos agentes e o LaunchAgent gravam
`sys.executable` e o caminho dos scripts; com um interpretador normal, o gerenciador de integrações e o de serviço
da Fase 4 funcionam sem mudança.

Garantias que o script confere (e falha se não valem):
  - o runtime e cada wheel batem com o hash fixado (aqui e em app/backend/requirements.lock);
  - nenhum binário depende de /opt/homebrew, /usr/local ou de uma pasta pessoal;
  - todos os binários são arm64 e rodam no macOS 13 ou mais novo;
  - tudo vem pré-compilado (.pyc com hash "unchecked"): rodar o Ombro não grava nada dentro do app assinado;
  - o backend roda sem Homebrew no PATH: imports, a libusb embarcada, a CLI, o doctor e um ombrod --demo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TAURI = RAIZ / "app" / "src-tauri"
CACHE = TAURI / "target" / "backend-cache"
SAIDA = TAURI / "bundle-resources"
LOCK = RAIZ / "app" / "backend" / "requirements.lock"
# Entitlements do python3 embarcado: vazio de propósito (o codesign não aceita comentários no arquivo). O build
# roda o backend com o hardened runtime ligado e só passa se nada precisar de exceção; se um dia precisar (ex.: ctypes
# com callbacks pedir com.apple.security.cs.allow-unsigned-executable-memory), a exceção entra lá com o motivo aqui.
ENTITLEMENTS = RAIZ / "app" / "backend" / "runtime.entitlements"
LICENCAS = RAIZ / "app" / "backend" / "licenses"  # textos que nenhum pacote traz completos (LGPL-2.1, da gnu.org)

PY = "3.14"
PBS_TAG = "20261110"
PBS_ARQUIVO = f"cpython-3.14.10+{PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_URL = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/{PBS_ARQUIVO.replace('+', '%2B')}"
PBS_SHA256 = "f15ec3d15460dd66c2eb88639b152cd9040e7d1ece11e38bf89e8f53f0171e02"  # do SHA256SUMS do release
MACOS_MIN = (13, 0)

# O código do Ombro que vai no app (lista explícita: nada de testes, scripts, docs, .venv ou a biblioteca GPL).
PASTAS = ["ombro", "core", "providers", "coletores", "fontes", "avatars"]
ARQUIVOS = [
    "apresentacao.py", "avatares.py", "config.py", "layout_minimal_v2.py", "layout_multiagente.py", "layout_ombro.py",
    "logging_config.py", "mascote.py", "ombro_hook.py", "ombrod.py", "render.py", "saida.py", "statusline_tap.py",
    "tela.py", "usb_direto.py",
]

# O que sai do runtime: não é usado pelo Ombro e só aumenta o app (e o que precisa ser assinado).
PODAR = [
    "include", "share", "lib/pkgconfig", f"lib/python{PY}/test", f"lib/python{PY}/idlelib", f"lib/python{PY}/tkinter",
    f"lib/python{PY}/turtledemo", f"lib/python{PY}/turtle.py", f"lib/python{PY}/ensurepip", f"lib/python{PY}/pydoc_data",
    f"lib/python{PY}/config-{PY}-darwin", f"lib/python{PY}/site-packages/pip", f"lib/python{PY}/site-packages/bin",
]
PODAR_GLOB = ["lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*", "lib/libtcl*", "lib/libtk*",
              "lib/libpython*.dylib",  # o executável é estático: nada no runtime usa a dylib (o build confere)
              f"lib/python{PY}/site-packages/numpy/f2py", f"lib/python{PY}/site-packages/numpy/_pyinstaller",
              f"lib/python{PY}/site-packages/numpy/_core/include", f"lib/python{PY}/site-packages/numpy/_core/lib",
              f"lib/python{PY}/lib-dynload/_tkinter*", f"lib/python{PY}/site-packages/pip-*",
              "bin/idle*", "bin/pydoc*", "bin/pip*", "bin/2to3*", "bin/*-config"]
PERMITIDOS = ("/usr/lib/", "/System/Library/", "@rpath/", "@loader_path/", "@executable_path/")


def passo(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def falha(msg: str) -> None:
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar_runtime() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    destino = CACHE / PBS_ARQUIVO
    if not destino.exists() or sha256(destino) != PBS_SHA256:
        passo(f"baixando {PBS_ARQUIVO}")
        tmp = destino.with_suffix(".part")
        with urllib.request.urlopen(PBS_URL, timeout=120) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(destino)
    if sha256(destino) != PBS_SHA256:
        destino.unlink()
        falha("o runtime baixado não bate com o hash fixado; nada foi montado")
    passo("runtime conferido (sha256)")
    return destino


def extrair(tar: Path, destino: Path) -> None:
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp, tarfile.open(tar) as t:
        t.extractall(tmp, filter="data")  # recusa caminhos absolutos, ".." e links para fora
        shutil.move(str(Path(tmp) / "python"), destino)


def sem_links(pasta: Path) -> None:
    """Troca links simbólicos por cópias: o bundle do Tauri e a assinatura lidam melhor com arquivos comuns."""
    for p in sorted(pasta.rglob("*")):
        if p.is_symlink():
            alvo = p.resolve()
            if not alvo.is_relative_to(pasta.resolve()):
                falha(f"link para fora do runtime: {p.relative_to(pasta)}")
            p.unlink()
            if alvo.is_dir():
                shutil.copytree(alvo, p, symlinks=False)
            else:
                shutil.copy2(alvo, p)


def podar(runtime: Path) -> None:
    for rel in PODAR:
        alvo = runtime / rel
        if alvo.is_dir():
            shutil.rmtree(alvo)
        elif alvo.exists():
            alvo.unlink()
    for padrao in PODAR_GLOB:
        for alvo in runtime.glob(padrao):
            shutil.rmtree(alvo) if alvo.is_dir() else alvo.unlink()
    for testes in list((runtime / "lib" / f"python{PY}" / "site-packages").rglob("tests")):
        if testes.is_dir():
            shutil.rmtree(testes)
    for cache in list(runtime.rglob("__pycache__")):
        shutil.rmtree(cache)
    # um executável só (bin/python3), e não três cópias do mesmo binário de 19 MB
    bindir = runtime / "bin"
    real = (bindir / "python3").resolve()
    for nome in ("python", "python3"):
        if (bindir / nome).is_symlink():
            (bindir / nome).unlink()
    if real != bindir / "python3":
        real.rename(bindir / "python3")


def instalar_dependencias(runtime: Path) -> None:
    passo("instalando as dependências do lock (só wheels, com hash)")
    site = runtime / "lib" / f"python{PY}" / "site-packages"
    py = runtime / "bin" / "python3"
    subprocess.run(
        [str(py), "-I", "-m", "pip", "install", "--quiet", "--no-cache-dir", "--disable-pip-version-check", "--no-deps",
         "--require-hashes", "--only-binary=:all:", "--platform", "macosx_13_0_arm64", "--python-version", PY,
         "--implementation", "cp", "--abi", f"cp{PY.replace('.', '')}", "--target", str(site), "-r", str(LOCK)],
        check=True, env={"PATH": "/usr/bin:/bin", "HOME": str(CACHE)},
    )


def copiar_backend(destino: Path) -> None:
    passo("copiando o código do Ombro")
    destino.mkdir()
    for nome in PASTAS:
        shutil.copytree(RAIZ / nome, destino / nome, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    for nome in ARQUIVOS:
        shutil.copy2(RAIZ / nome, destino / nome)


def compilar(runtime: Path, backend: Path) -> None:
    passo("pré-compilando (.pyc unchecked-hash)")
    subprocess.run(
        # -s: o co_filename (o caminho que aparece nos tracebacks) fica relativo a bundle-resources/, sem a pasta
        # de quem montou o app. -B e -f: sem eles, os módulos que o próprio compileall importa ganham um .pyc
        # automático (com o caminho absoluto), que o compileall depois pularia como "já compilado".
        [str(runtime / "bin" / "python3"), "-I", "-B", "-m", "compileall", "-q", "-f", "-j", "0", "--invalidation-mode",
         "unchecked-hash", "-s", str(SAIDA), str(runtime / "lib" / f"python{PY}"), str(backend)],
        check=True, env={"PATH": "/usr/bin:/bin"},
    )


# ---------- verificações ----------
def macho(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
    except OSError:
        return False


def binarios(pasta: Path) -> list[Path]:
    return [p for p in pasta.rglob("*") if p.is_file() and not p.is_symlink() and macho(p)]


def verificar_binarios(runtime: Path) -> int:
    passo("conferindo os binários (dependências, arquitetura, macOS mínimo)")
    problemas = []
    lista = binarios(runtime)
    if sorted(p.name for p in (runtime / "bin").iterdir()) != ["python3"]:
        problemas.append(f"bin/ deveria ter só python3: {sorted(p.name for p in (runtime / 'bin').iterdir())}")
    for b in lista:
        rel = b.relative_to(runtime)
        cargas = subprocess.run(["otool", "-l", str(b)], capture_output=True, text=True, check=True).stdout
        for bloco in cargas.split("Load command")[1:]:
            cmd = re.search(r"cmd (LC_\w+)", bloco)
            nome = re.search(r"name (\S+)", bloco)
            if cmd and cmd.group(1) in ("LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB") and nome:
                if not nome.group(1).startswith(PERMITIDOS):
                    problemas.append(f"{rel}: depende de {nome.group(1)}")
            if cmd and cmd.group(1) == "LC_RPATH" and nome and not nome.group(1).startswith(("@loader_path", "@executable_path")):
                problemas.append(f"{rel}: rpath {nome.group(1)}")
            if cmd and cmd.group(1) in ("LC_BUILD_VERSION", "LC_VERSION_MIN_MACOSX"):
                m = re.search(r"(?:minos|version) (\d+)\.(\d+)", bloco)
                if m and (int(m.group(1)), int(m.group(2))) > MACOS_MIN:
                    problemas.append(f"{rel}: exige macOS {m.group(1)}.{m.group(2)}")
        archs = subprocess.run(["lipo", "-archs", str(b)], capture_output=True, text=True).stdout.split()
        if "arm64" not in archs:
            problemas.append(f"{rel}: sem arm64 ({' '.join(archs) or '?'})")
    if problemas:
        falha("binários com problema:\n  " + "\n  ".join(problemas))
    print(f"    {len(lista)} binários: só bibliotecas do sistema, arm64, macOS {MACOS_MIN[0]}.{MACOS_MIN[1]}+")
    return len(lista)


def verificar_sem_caminho_pessoal(runtime: Path, backend: Path, avisos_md: Path) -> None:
    """Nada do que ESTE build gera leva a pasta pessoal de quem montou (o nome de usuário) para dentro do app.

    Confere o código do Ombro (backend/), o THIRD_PARTY_NOTICES.md e os .pyc que o build compila no runtime. Os
    binários e arquivos de terceiros (o CPython, as wheels) vêm prontos, com os caminhos da máquina de build DELES:
    muitos são montados em runners do GitHub, com /Users/runner, o mesmo $HOME do nosso runner. Esses não contam.
    Um .pyc de terceiro só conta se o próprio .py não tiver o caminho (o numpy/__config__.py, por exemplo, tem).
    """
    passo("conferindo que nada gerado por este build leva a pasta pessoal de quem montou")
    casa = str(Path.home()).encode()
    achados = [p for p in [*backend.rglob("*"), avisos_md] if p.is_file() and casa in p.read_bytes()]
    for pyc in runtime.rglob("*.pyc"):
        if casa in pyc.read_bytes():
            fonte = pyc.parent.parent / (pyc.name.split(".")[0] + ".py")  # __pycache__/x.cpython-314.pyc -> x.py
            if not (fonte.is_file() and casa in fonte.read_bytes()):
                achados.append(pyc)
    if achados:
        falha(f"{len(achados)} arquivo(s) com {casa.decode()}:\n  " + "\n  ".join(str(p.relative_to(SAIDA)) for p in achados[:20]))
    print("    nenhum")


def ambiente_limpo(pasta_ombro: Path) -> dict:
    """Como um Mac sem Homebrew nem Python: PATH mínimo, sem PYTHON*, sem DYLD_*."""
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.environ["HOME"], "OMBRO_SCREEN_DIR": str(pasta_ombro),
            "LANG": "en_US.UTF-8"}


def porta_livre() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def foto(pasta: Path) -> set:
    return {(str(p.relative_to(pasta)), p.stat().st_mtime_ns) for p in pasta.rglob("*") if p.is_file()}


def verificar_execucao(runtime: Path, backend: Path) -> None:
    passo("rodando o backend sem Homebrew no PATH")
    py = str(runtime / "bin" / "python3")
    antes = foto(runtime) | foto(backend)
    # caminho curto: o socket tem limite de 104 bytes
    with tempfile.TemporaryDirectory(prefix="ob", dir="/tmp" if Path("/tmp").is_dir() else None) as tmp:
        env = ambiente_limpo(Path(tmp))
        r = subprocess.run(
            [py, "-I", "-c", "import PIL.Image, psutil, numpy, serial, usb.core, libusb_package, mascotinhos, sqlite3, ssl, sys; "
                             "print(sys.version.split()[0], PIL.__version__, numpy.__version__, psutil.__version__, "
                             "mascotinhos.__version__)"],
            env=env, capture_output=True, text=True)
        if r.returncode:
            falha(f"imports falharam:\n{r.stderr}")
        print(f"    imports ok (python, Pillow, numpy, psutil, mascotinhos): {r.stdout.strip()}")
        r = subprocess.run([py, "-E", "-s", "-B", "-c",
                            "import usb_direto; from PIL import Image; assert len(usb_direto.rgb565_le(Image.new('RGB', (4, 2)))) == 16; "
                            "print(usb_direto.problema() or 'libusb ok'); "
                            "b = usb_direto._backend(); print(b.lib._name if b else 'sem backend')"],
                           env=env, cwd=backend, capture_output=True, text=True)
        linhas = r.stdout.strip().splitlines()
        if r.returncode or not linhas or linhas[0] != "libusb ok" or str(runtime) not in linhas[-1]:
            falha(f"a libusb embarcada não foi usada: {r.stdout.strip()} {r.stderr.strip()}")
        print(f"    libusb: a embarcada ({Path(linhas[-1]).relative_to(runtime)})")
        r = subprocess.run([py, "-E", "-s", "-B", str(backend / "ombro"), "--version"], env=env, capture_output=True, text=True)
        if r.returncode:
            falha(f"a CLI não rodou: {r.stderr}")
        print(f"    CLI: {r.stdout.strip()}")
        d = subprocess.Popen([py, "-E", "-s", "-B", str(backend / "ombrod.py"), "--demo", "--porta-sim", str(porta_livre())],
                             env=env, cwd=backend, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            ok = False
            for _ in range(80):
                time.sleep(0.25)
                r = subprocess.run([py, "-E", "-s", "-B", str(backend / "ombro"), "status", "--json"], env=env,
                                   capture_output=True, text=True)
                if r.returncode == 0 and '"protocol": 1' in r.stdout:
                    ok = True
                    break
            if not ok:
                d.kill()
                falha("o ombrod --demo não respondeu pela IPC:\n" + (d.communicate()[0] or "")[-2000:])
            r = subprocess.run([py, "-E", "-s", "-B", str(backend / "ombro"), "doctor", "--json"], env=env,
                               capture_output=True, text=True)
            if '"schema": "ombro.doctor"' not in r.stdout:
                falha(f"o doctor não devolveu o JSON: {r.stdout[-500:]} {r.stderr[-500:]}")
            print("    ombrod --demo respondeu pela IPC; doctor --json ok")
        finally:
            if d.poll() is None:
                d.send_signal(signal.SIGTERM)
                d.wait(timeout=15)
        # o hook e o LaunchAgent rodam sem -B: com tudo pré-compilado, nada é gravado mesmo assim
        r = subprocess.run([py, str(backend / "ombro_hook.py"), "claude"], input="{}", env=env, capture_output=True, text=True)
        if r.returncode:
            falha(f"o hook falhou: {r.stderr}")
        # o mascote desenha com o mascotinhos e a fonte que vem nele (lida de dentro do runtime)
        r = subprocess.run([py, "-c", "import sys; sys.path.insert(0, '.'); import ombro.cli, ombro.daemon, layout_multiagente, "
                                      "mascote; assert mascote.Mascote().camada(1.0).getbbox(); "
                                      "assert mascote.BIBLIOTECA['ocioso'].olhos == 'fechados'; "  # o pacote vivo
                                      "assert mascote.ESTILO.fonte(10).getname() == ('JetBrains Mono', 'Bold')"],
                           env=env, cwd=backend, capture_output=True, text=True)
        if r.returncode:
            falha(f"import sem -B falhou: {r.stderr}")
        print("    o mascote desenha com o mascotinhos (pacote vivo) e a JetBrains Mono Bold dele")
    depois = foto(runtime) | foto(backend)
    if depois != antes:
        novos = sorted({n for n, _ in depois} ^ {n for n, _ in antes} or {n for n, _ in depois - antes})
        falha("rodar o backend gravou arquivos dentro dele:\n  " + "\n  ".join(novos[:20]))
    print("    nada foi gravado dentro do runtime nem do backend")


def assinar(runtime: Path, identidade: str) -> None:
    """Assina cada binário do runtime com hardened runtime (as bibliotecas antes do executável). A notarização
    exige isso de todo Mach-O dentro do app, e o Tauri só assina o executável principal."""
    passo(f"assinando {'ad-hoc' if identidade == '-' else 'com ' + identidade.split(':')[0]} (hardened runtime)")
    lista = binarios(runtime)
    exe = runtime / "bin" / "python3"
    base = ["codesign", "--force", "--options", "runtime", "--sign", identidade]
    if identidade != "-":
        base.append("--timestamp")
    for b in sorted((p for p in lista if p != exe), key=lambda p: len(p.parts), reverse=True):
        subprocess.run(base + [str(b)], check=True, capture_output=True)
    if identidade == "-":
        # Ad-hoc não tem Team ID, e a validação de bibliotecas do hardened runtime exige o mesmo Team ID no processo e
        # em cada .so. Só nas builds locais, essa (e só essa) exceção: o resto do hardened runtime continua valendo e
        # é testado abaixo. Com a Developer ID todos têm o mesmo Team ID e vale ENTITLEMENTS (vazio).
        ent = CACHE / "runtime-adhoc.entitlements"
        ent.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                       '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
                       '<key>com.apple.security.cs.disable-library-validation</key><true/></dict></plist>\n')
    else:
        ent = ENTITLEMENTS
    subprocess.run(base + ["--entitlements", str(ent), str(exe)], check=True, capture_output=True)
    ruins = [b for b in lista if subprocess.run(["codesign", "--verify", "--strict", str(b)], capture_output=True).returncode]
    if ruins:
        falha("assinatura inválida em: " + ", ".join(str(b.relative_to(runtime)) for b in ruins))
    sem_runtime = [b for b in lista if "runtime" not in subprocess.run(
        ["codesign", "-dv", str(b)], capture_output=True, text=True).stderr.split("flags=")[-1].split()[0]]
    if sem_runtime:
        falha("sem hardened runtime: " + ", ".join(str(b.relative_to(runtime)) for b in sem_runtime))
    print(f"    {len(lista)} binários assinados, todos com hardened runtime")


# ---------- avisos de terceiros (vão para Contents/Resources/licenses) ----------
PYSERIAL_BSD3 = """Copyright (c) 2001-2020 Chris Liechti <cliechti@gmx.net>
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following
   disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following
   disclaimer in the documentation and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote
   products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
MIT = """Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


def _texto_declarado(pkg: dict, apache: Path, mpl: Path) -> str:
    """Para um crate publicado sem arquivo de licença: o texto padrão da licença declarada, com os autores dele."""
    lic = pkg.get("license") or ""
    autores = ", ".join(pkg.get("authors") or []) or pkg["name"] + " contributors"
    cab = (f"{pkg['name']} {pkg['version']}\nLicença declarada: {lic}\nRepositório: {pkg.get('repository') or '—'}\n"
           "(o pacote publicado não traz o arquivo de licença; este é o texto padrão da licença declarada)\n\n")
    opcoes = {t.strip("()") for t in lic.replace("/", " OR ").split()}
    if "Apache-2.0" in opcoes and "AND" not in lic:
        return cab + "Usado sob a Apache-2.0 (uma das opções declaradas):\n\n" + apache.read_text()
    if "MIT" in opcoes and "AND" not in lic:
        return cab + f"Copyright (c) {autores}\n\n" + MIT
    if lic == "MPL-2.0":
        return cab + "Código-fonte: o pacote publicado em crates.io (sem modificação).\n\n" + mpl.read_text()
    if lic == "BSD-3-Clause":
        return cab + PYSERIAL_BSD3.replace("Copyright (c) 2001-2020 Chris Liechti <cliechti@gmx.net>",
                                           f"Copyright (c) {autores}")
    falha(f"{pkg['name']} {pkg['version']}: sem arquivo de licença e com uma licença sem texto padrão aqui ({lic})")


ARQ_LICENCA = ("licen", "copying", "notice", "copyright", "unlicense", "authors")
# Avisos que um pacote precisa trazer (caminhos dentro do .dist-info): o build falha se faltar algum.
AVISOS_EXIGIDOS = {
    # MIT (o código), as partes derivadas do Ombro Screen com o aviso dele, e a OFL da JetBrains Mono Bold que vem junto
    "mascotinhos": ("licenses/LICENSE", "licenses/NOTICE", "licenses/mascotinhos/fontes/OFL.txt"),
}


def _metadados(dist: Path) -> tuple:
    nome = versao = licenca = ""
    for linha in (dist / "METADATA").read_text(encoding="utf-8", errors="replace").splitlines():
        if not linha.strip():
            break
        chave, _, valor = linha.partition(": ")
        if chave == "Name":
            nome = valor
        elif chave == "Version":
            versao = valor
        elif chave == "License-Expression" or (chave == "License" and not licenca):
            licenca = valor.splitlines()[0][:80]
    return nome, versao, licenca


def _copiar_licencas(origem: Path, destino: Path) -> int:
    """Copia os arquivos de licença de um pacote (na raiz, e numa pasta licenses/ se houver)."""
    n = 0
    candidatos = [p for p in origem.iterdir() if p.is_file()] + [p for p in (origem / "licenses").rglob("*") if p.is_file()]
    for p in candidatos:
        if any(t in p.name.lower() for t in ARQ_LICENCA) or p.is_relative_to(origem / "licenses"):
            alvo = destino / p.relative_to(origem)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, alvo)
            n += 1
    return n


def _ambiente_ferramentas() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{Path.home() / '.cargo' / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"
    return env


def avisos(runtime: Path, destino: Path) -> None:
    passo("avisos de terceiros (licenças de tudo o que vai no app)")
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir()
    site = runtime / "lib" / f"python{PY}" / "site-packages"
    py_ver = subprocess.run([str(runtime / "bin" / "python3"), "-I", "-c", "import platform; print(platform.python_version())"],
                            capture_output=True, text=True, check=True).stdout.strip()
    secoes = []

    # o próprio Ombro (MIT: o aviso vai junto de cada cópia)
    shutil.copy2(RAIZ / "LICENSE", destino / "OMBRO-LICENSE.txt")

    # o runtime
    (destino / "python").mkdir()
    shutil.copy2(LICENCAS / "LGPL-2.1.txt", destino / "LGPL-2.1.txt")
    # o que o python-build-standalone liga no CPython (textos guardados no repositório, da distribuição "full")
    pbs = LICENCAS / "python-build-standalone"
    if (pbs / "TAG").read_text().strip() != PBS_TAG:
        falha(f"os textos de {pbs.relative_to(RAIZ)} são de outra release do runtime; atualize-os (ver ORIGEM.txt)")
    shutil.copytree(pbs, destino / "python" / "runtime", ignore=shutil.ignore_patterns("TAG"))
    componentes = {  # arquivo -> (componente, licença, versão quando o runtime informa)
        "openssl-3": ("OpenSSL", "Apache-2.0"), "sqlite": ("SQLite", "blessing (domínio público)"),
        "expat": ("Expat", "MIT"), "libffi": ("libffi", "MIT"), "mpdecimal": ("mpdecimal", "BSD-2-Clause"),
        "liblzma": ("XZ Utils (liblzma)", "0BSD"), "bzip2": ("bzip2", "bzip2-1.0.6"), "ncurses": ("ncurses", "X11"),
        "libedit": ("libedit", "BSD-3-Clause"), "libuuid": ("libuuid", "BSD-3-Clause"), "zlib": ("zlib", "Zlib"),
        "zlib-ng": ("zlib-ng", "Zlib"), "zstd": ("Zstandard", "BSD-3-Clause"),
    }
    linhas_py = [(f"CPython {py_ver}", "PSF-2.0", "python/runtime/LICENSE.cpython.txt"),
                 ("python-build-standalone (a montagem relocável do CPython)", "—",
                  "https://github.com/astral-sh/python-build-standalone")]
    for arq, (nome, lic) in componentes.items():
        if not (pbs / f"LICENSE.{arq}.txt").exists():
            falha(f"falta LICENSE.{arq}.txt em {pbs.relative_to(RAIZ)}")
        linhas_py.append((f"{nome} (ligado no CPython)", lic, f"python/runtime/LICENSE.{arq}.txt"))
    secoes.append(("Python", linhas_py))

    # as dependências Python (e as bibliotecas nativas que os wheels trazem: avisos dentro de cada pasta)
    itens = []
    for dist in sorted(site.glob("*.dist-info"), key=lambda d: d.name.lower()):
        nome, versao, licenca = _metadados(dist)
        pasta = destino / "python" / nome
        pasta.mkdir(parents=True, exist_ok=True)
        n = _copiar_licencas(dist, pasta)
        if nome.lower() == "pyserial":
            (pasta / "LICENSE.txt").write_text(PYSERIAL_BSD3, encoding="utf-8")
            licenca, n = "BSD-3-Clause", n + 1
        if n == 0:
            falha(f"{nome} {versao} não traz o texto da licença; acrescente-o antes de distribuir")
        faltam = [a for a in AVISOS_EXIGIDOS.get(nome.lower(), ()) if not (pasta / a).is_file()]
        if faltam:
            falha(f"{nome} {versao} sem os avisos {', '.join(faltam)}")
        extra = {"numpy": "inclui OpenBLAS, libgfortran e libgcc_s (GPL-3.0 com a exceção de runtime do GCC) e "
                          "libquadmath (LGPL-2.1, texto em LGPL-2.1.txt)",
                 "pillow": "inclui as bibliotecas de imagem que o Pillow embute (FreeType, libjpeg, libpng, libtiff, "
                           "libwebp, HarfBuzz…), com os avisos no mesmo arquivo",
                 "libusb-package": "inclui a libusb 1.0.30 (LGPL-2.1-or-later, texto em LGPL-2.1.txt; código-fonte em "
                                   "https://github.com/libusb/libusb, carregada dinamicamente)",
                 "mascotinhos": "o movimento e o desenho do mascote da telinha; o código é MIT (licenses/LICENSE), o "
                                "licenses/NOTICE lista as partes derivadas do Ombro Screen, e a JetBrains Mono Bold que "
                                "vem junto é OFL-1.1 (licenses/mascotinhos/fontes/OFL.txt)"}.get(nome.lower(), "")
        itens.append((f"{nome} {versao}", licenca or "ver arquivos", f"python/{nome}/" + (f" ({extra})" if extra else "")))
    secoes.append(("Pacotes Python", itens))

    # a fonte
    (destino / "fontes").mkdir()
    shutil.copy2(RAIZ / "fontes" / "OFL.txt", destino / "fontes" / "JetBrainsMono-OFL.txt")
    secoes.append(("Fontes", [("JetBrains Mono (a de fontes/, a Bold na interface do app e a Bold que vem no mascotinhos)", "OFL-1.1",
                               "fontes/JetBrainsMono-OFL.txt")]))

    # os crates Rust do app (só os que entram no binário)
    env = _ambiente_ferramentas()
    arvore = subprocess.run(["cargo", "tree", "-e", "normal", "--target", "aarch64-apple-darwin", "--prefix", "none",
                             "-f", "{p}"], cwd=TAURI, capture_output=True, text=True, check=True, env=env).stdout
    usados = {tuple(l.split()[:2]) for l in arvore.splitlines() if l.strip()}
    meta = json.loads(subprocess.run(["cargo", "metadata", "--format-version", "1", "--locked"], cwd=TAURI,
                                     capture_output=True, text=True, check=True, env=env).stdout)
    tauri = next(p for p in meta["packages"] if p["name"] == "tauri")
    apache = Path(tauri["manifest_path"]).parent / "LICENSE_APACHE-2.0"  # o texto completo da Apache-2.0
    if "Version 2.0, January 2004" not in apache.read_text():
        falha("não achei o texto da Apache-2.0 no crate tauri")
    mpl = next((Path(p["manifest_path"]).parent / "LICENSE" for p in meta["packages"] if p["name"] == "cssparser"), None)
    if not mpl or not mpl.exists() or "Mozilla Public License Version 2.0" not in mpl.read_text():
        falha("não achei o texto da MPL-2.0 no crate cssparser")
    itens = []
    for pkg in sorted(meta["packages"], key=lambda p: (p["name"], p["version"])):
        if (pkg["name"], "v" + pkg["version"]) not in usados or pkg["name"] == "ombro":
            continue
        pasta = destino / "rust" / f"{pkg['name']}-{pkg['version']}"
        pasta.mkdir(parents=True)
        n = _copiar_licencas(Path(pkg["manifest_path"]).parent, pasta)
        if n == 0:
            (pasta / "LICENSE.txt").write_text(_texto_declarado(pkg, apache, mpl), encoding="utf-8")
        itens.append((f"{pkg['name']} {pkg['version']}", pkg.get("license") or "ver o pacote",
                      f"rust/{pasta.name}/" + ("" if n else " (texto padrão da licença declarada)")))
    secoes.append((f"Crates Rust ({len(itens)})", itens))

    # a interface (só o que vai no bundle: dependências de produção, sem tipos)
    lista = subprocess.run(["npm", "ls", "--omit=dev", "--all", "--parseable"], cwd=RAIZ / "app", capture_output=True,
                           text=True, env=env).stdout.splitlines()[1:]
    itens = []
    for d in sorted(set(lista)):
        pj = json.loads((Path(d) / "package.json").read_text())
        if pj["name"].startswith("@types/") or pj["name"] == "csstype":
            continue
        pasta = destino / "npm" / pj["name"].replace("/", "__")
        pasta.mkdir(parents=True)
        if _copiar_licencas(Path(d), pasta) == 0:
            falha(f"{pj['name']} não traz o texto da licença")
        itens.append((f"{pj['name']} {pj['version']}", pj.get("license", "ver o pacote"), f"npm/{pasta.name}/"))
    secoes.append(("Interface (npm)", itens))

    texto = ["# Avisos de terceiros do Ombro.app", "",
             "O Ombro é distribuído sob a licença MIT (texto em OMBRO-LICENSE.txt).", "",
             "O Ombro.app redistribui os componentes abaixo, cada um sob a sua licença. Os textos completos estão nas",
             "pastas indicadas, ao lado deste arquivo. A libusb e a libquadmath (LGPL-2.1) são bibliotecas dinâmicas,",
             "carregadas em tempo de execução, sem modificação. A biblioteca turing-smart-screen-python (GPL-3.0) não",
             "vai no app.", ""]
    for titulo, linhas in secoes:
        texto += [f"## {titulo}", "", "| Componente | Licença | Texto |", "| --- | --- | --- |"]
        texto += [f"| {a} | {b} | {c} |" for a, b, c in linhas] + [""]
    (destino / "THIRD_PARTY_NOTICES.md").write_text("\n".join(texto), encoding="utf-8")
    print(f"    {sum(len(l) for _, l in secoes)} componentes; {sum(1 for _ in destino.rglob('*') if _.is_file())} arquivos")


def tamanho(p: Path) -> str:
    total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return f"{total / 1024 / 1024:.1f} MB"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="só verificar o que já foi montado")
    ap.add_argument("--sign", metavar="IDENTIDADE", help='assinar os binários do runtime ("-" = ad-hoc)')
    args = ap.parse_args()
    if sys.platform != "darwin":
        falha("o Ombro.app só é montado no macOS")
    if sys.version_info < (3, 12):
        falha("rode com Python 3.12 ou mais novo (o build usa tarfile com filter='data')")
    runtime, backend = SAIDA / "runtime", SAIDA / "backend"
    if not args.check:
        tar = baixar_runtime()
        if SAIDA.exists():
            shutil.rmtree(SAIDA)
        SAIDA.mkdir(parents=True)
        passo("extraindo o runtime")
        extrair(tar, runtime)
        instalar_dependencias(runtime)
        podar(runtime)
        sem_links(runtime)
        copiar_backend(backend)
        compilar(runtime, backend)
        avisos(runtime, SAIDA / "licenses")
    elif not runtime.is_dir() or not backend.is_dir():
        falha(f"nada montado em {SAIDA}")
    verificar_binarios(runtime)
    verificar_sem_caminho_pessoal(runtime, backend, SAIDA / "licenses" / "THIRD_PARTY_NOTICES.md")
    if args.sign:
        assinar(runtime, args.sign)
    verificar_execucao(runtime, backend)  # depois de assinar: o backend roda com o hardened runtime ligado
    passo(f"pronto: runtime {tamanho(runtime)}, backend {tamanho(backend)} em {SAIDA.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
