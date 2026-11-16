# Licenças e redistribuição

O que o Ombro redistribui, sob quais licenças, e o que ainda precisa de decisão antes de uma release pública.

## O Ombro

O repositório ainda **não tem um arquivo LICENSE**. Sem ele, o código não é open source no sentido legal (ninguém
recebe permissão para usar, modificar ou redistribuir), e o Ombro.app não deve ser publicado. A escolha é de quem
mantém o projeto; o que pesa nela:

- Com uma licença permissiva (MIT, Apache-2.0), o Ombro.app continua sem embarcar a biblioteca GPL da tela (abaixo).
- Com a GPL-3.0, daria para embarcar a turing-smart-screen-python (e com ela as revisões B, C, D e WeAct no app),
  cumprindo as obrigações da GPL na distribuição (código-fonte correspondente e o texto da licença).

## A biblioteca da tela (turing-smart-screen-python, GPL-3.0)

Não vai no Ombro.app. No Mac, a revisão A (a Turing de 3,5") é atendida pelo `usb_direto.py`, que é código do
próprio Ombro e fala com o endpoint USB sem a biblioteca. As outras revisões continuam funcionando pelo Ombro do
repositório, com a biblioteca clonada à parte, como antes (README, seção 1). Rodar a biblioteca em outro processo não
foi usado como justificativa para embarcá-la: ela simplesmente não é distribuída.

## O desenho do mascote

O contorno antigo do cloud (`dados_cloud.py`) tinha sido medido num desenho de terceiros (a "KEY de formas dos
avatares do Grok Bot"). Ele **saiu do projeto**, e com ele o ícone do app e o da Menu Bar que vinham dele. O
mascote agora é um avatar, e o padrão é o **Ombro Cloud**, desenhado do zero para o Ombro, como os outros oito
da coleção ([avatars.md](avatars.md)). O ícone do app, o da Menu Bar e a marca da barra lateral são gerados dele
(`app/scripts/gen_tray_icon.py`).

O **formato** dos avatares e o **motor** que os desenha no app são do
[open-bot-avatar](https://github.com/DominikScholz/open-bot-avatar) (MIT, © 2026 Dominik Scholz): o pacote npm
`open-bot-avatar` vai no Ombro.app com a licença dele nos avisos de terceiros. Da galeria do projeto, nada foi
usado: os nove avatares são próprios.

## O que vai dentro do Ombro.app

A cada build, `app/scripts/build_backend.py` monta `Contents/Resources/licenses/` com o `THIRD_PARTY_NOTICES.md`
(cada componente, a versão e a licença) e o texto da licença de cada um, tirado do próprio pacote. O build falha se
um componente não tiver texto. No app: Ajustes > Sobre > Avisos de terceiros.

| Grupo | Licenças | Observações |
| --- | --- | --- |
| CPython 3.14 (python-build-standalone) | PSF-2.0 | |
| O que o python-build-standalone liga no CPython | OpenSSL 3 (Apache-2.0), SQLite (domínio público), expat, libffi (MIT), mpdecimal (BSD-2), liblzma (0BSD), bzip2, ncurses (X11), libedit, libuuid, zstd (BSD-3), zlib e zlib-ng (Zlib) | o `LICENSE.txt` do CPython não traz esses avisos: os textos vêm da distribuição "full" da mesma release e ficam em `app/backend/licenses/python-build-standalone/` (origem e hash em `ORIGEM.txt`). Os do tkinter (Tcl, Tix, X11) ficam de fora, porque o build remove o tkinter |
| Pillow | MIT-CMU | o arquivo de licença dele cobre as bibliotecas de imagem que o wheel embute (FreeType, libjpeg-turbo, libpng, libtiff, libwebp, HarfBuzz, OpenJPEG, LittleCMS, Brotli, libavif, zlib-ng, libxcb…) |
| numpy | BSD-3-Clause e outras permissivas | o wheel traz OpenBLAS (BSD-3), libgfortran e libgcc_s (GPL-3.0 com a exceção de runtime do GCC, que permite a redistribuição) e libquadmath (LGPL-2.1-or-later) |
| libusb 1.0.30 (via libusb-package) | LGPL-2.1-or-later | biblioteca dinâmica, sem modificação; o texto completo da LGPL-2.1 vai junto (`app/backend/licenses/LGPL-2.1.txt`, da gnu.org) e o código-fonte está em <https://github.com/libusb/libusb> |
| psutil, pyusb, pyserial | BSD-3-Clause | o wheel do pyserial não traz o texto: o build gera o BSD-3 padrão com o copyright do próprio pyserial |
| importlib_resources, libusb-package | Apache-2.0 | |
| 249 crates Rust do app | MIT, Apache-2.0, BSD, Zlib, Unicode-3.0, MPL-2.0 (5 crates do Servo) | nenhum GPL/LGPL/AGPL; os 18 que não publicam o arquivo de licença recebem o texto padrão da licença declarada, com os autores do Cargo.toml |
| React, React DOM, scheduler, @tauri-apps/api | MIT; Apache-2.0 ou MIT | |
| open-bot-avatar (o motor dos avatares no app) | MIT | o formato dos avatares (`avatars/`) também é o dele |
| Lucide (ícones da interface) | ISC | |
| JetBrains Mono (fonte da telinha) | OFL-1.1 | |

Conferir de novo depois de mudar dependências: rodar o build (ele refaz a lista) e, para os crates,
`cargo tree -e normal --target aarch64-apple-darwin` dentro de `app/src-tauri`.

## O que não vai

O `macmon` (opcional, do Homebrew) não é redistribuído: se estiver instalado, o Ombro o usa. O Homebrew, o Xcode e as
ferramentas de build também não.
