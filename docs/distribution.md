# Distribuição do Ombro.app

Como o Ombro.app é montado, assinado (ad-hoc) e publicado. Para quem instala, veja
[macos-installation.md](macos-installation.md); para desenvolver o app, [development.md](development.md).

## O que vai dentro do app

```
Ombro.app/Contents/
├── MacOS/ombro                 o app (Tauri 2: Rust + a interface React)
└── Resources/
    ├── runtime/                CPython 3.14 relocável (python-build-standalone), com Pillow, psutil, pyserial,
    │                           numpy, pyusb, mascotinhos e a libusb do libusb-package; sem pip, tkinter, testes
    │                           nem cabeçalhos
    └── backend/                o código do Ombro (o mesmo do repositório), pré-compilado
```

- **Por que um Python de verdade, e não um executável congelado (PyInstaller):** os hooks dos agentes e o
  LaunchAgent gravam `sys.executable` e o caminho dos scripts. Com um interpretador normal, os gerenciadores de
  integrações e de serviço da Fase 4 funcionam sem nenhuma mudança, e o hook continua sendo
  `…/python3 …/ombro_hook.py claude || true`.
- **O que não vai:** testes, scripts, docs, `.venv`, bancos, logs, configurações pessoais e a biblioteca
  turing-smart-screen-python (GPL-3.0). Sem ela, o app fala com a tela revisão A pelo USB direto (código do
  Ombro); as revisões B, C, D e WeAct continuam pelo Ombro do repositório.
- **Arquitetura:** só Apple Silicon (`aarch64-apple-darwin`), macOS 13 ou mais novo. Intel não é montado nem
  anunciado: nunca foi testado.

## Montar

Pré-requisitos (só de quem monta; quem instala não precisa de nada disso): Xcode Command Line Tools, Node 24,
Rust estável (`rustup`) e um Python 3.12 ou mais novo para rodar o script (o `.venv` do repositório, se existir; senão o `python3` do PATH. O do macOS, 3.9, não serve).

```bash
cd app && npm ci
```

```bash
npm run build:app
```

Isso roda, em ordem:

1. `scripts/build_backend.py`: baixa o runtime (hash fixado no script), instala as wheels de
   `backend/requirements.lock` (`--require-hashes`, só wheels, para macOS 13 ou mais novo), copia o código do Ombro
   por uma lista explícita, poda o runtime, pré-compila tudo (`.pyc` com hash "unchecked") e assina cada binário com
   hardened runtime. Depois **confere** e falha se algo não valer: nenhum binário depende de `/opt/homebrew`,
   `/usr/local` ou de pastas pessoais; todos são arm64 e rodam no macOS 13; o backend roda sem Homebrew no PATH
   (imports, a libusb embarcada, a conversão de pixels da tela, a CLI, um `ombrod --demo` pela IPC e o doctor);
   e rodar o backend não grava nada dentro dele.
2. `tauri build` com `src-tauri/tauri.release.conf.json`, que leva `bundle-resources/` para `Resources/`.

Saída: `src-tauri/target/release/bundle/macos/Ombro.app` e `…/bundle/dmg/Ombro_<versão>_aarch64.dmg` (o workflow de
release o renomeia para `Ombro-v<versão>-macos-arm64.dmg`).

O build não grava a pasta pessoal de quem montou dentro do app: os `.pyc` guardam caminhos relativos e o `build:app`
passa `--remap-path-prefix=$HOME=~` ao Rust. O `build_backend.py` falha se achar o `$HOME` no que o build gera
(o código do Ombro, os `.pyc` que ele compila e os avisos). Binários de terceiros (o CPython, as wheels) não contam:
muitos são montados em runners do GitHub e trazem `/Users/runner` da máquina de build deles.

## Assinatura: ad-hoc, sem notarização

As releases do Ombro saem com **assinatura ad-hoc** (`signingIdentity: "-"` no `tauri.release.conf.json`, e
`build_backend.py --sign -` para os binários do runtime). O projeto não usa uma conta paga do Apple Developer
Program, um certificado Developer ID nem a notarização da Apple.

- **O que a assinatura ad-hoc garante:** cada binário e o bundle têm uma assinatura de código válida (o Apple Silicon
  não roda código sem assinatura), com hardened runtime, e qualquer alteração depois do build a invalida.
- **O que ela não garante:** não diz quem montou o app (não há Team ID) e a Apple não o examinou. **Não é
  notarização.** O Gatekeeper recusa o app baixado da internet (`spctl` responde `rejected`), e quem instala
  autoriza a primeira abertura em Ajustes do Sistema > Privacidade e Segurança > **Abrir Mesmo Assim**
  ([macos-installation.md](macos-installation.md)). A procedência é conferida pelo `SHA256SUMS.txt` publicado junto.
- O Python embarcado roda com hardened runtime. Na assinatura ad-hoc, só a validação de Team ID das bibliotecas é
  dispensada (`com.apple.security.cs.disable-library-validation`, porque ad-hoc não tem Team ID); o build roda o
  backend com o resto do hardened runtime valendo.

Conferir:

```bash
codesign --verify --deep --strict --verbose=2 Ombro.app && codesign -dv Ombro.app 2>&1 | grep Signature
```

A resposta esperada é `valid on disk`, `satisfies its Designated Requirement` e `Signature=adhoc`.

O `build_backend.py` ainda aceita `--sign "Developer ID Application: …"` para quem tiver um certificado, mas esse
caminho não é usado nas releases nem foi validado.

## GitHub Actions

| Workflow | Quando | O que faz |
| --- | --- | --- |
| `tests.yml` | todo push e PR | os testes do backend Python (3.9 a 3.14) e o ruff |
| `app.yml` | push e PR que mexem em `app/` | tipos, testes e build da interface (Ubuntu); fmt, clippy e testes do Rust (macOS) |
| `release.yml` | tag `v*`, ou manual | testa, monta o app e o DMG com assinatura ad-hoc e confere; numa tag, cria um **rascunho** de pré-release |

O `release.yml` tem dois jobs:

1. **build** (`contents: read`): confere que a tag, o `app/package.json`, o `app/src-tauri/Cargo.toml` e o
   `ombro/__init__.py` têm a mesma versão; roda os testes do Python, da interface e do Rust; monta; confere a
   assinatura ad-hoc, a versão do `Info.plist`, que não há bancos, configurações, logs nem a biblioteca GPL no app,
   que o `$HOME` do runner não está no executável, no código do Ombro nem nos avisos, e o `hdiutil verify` do DMG; gera `Ombro-v<versão>-macos-arm64.dmg` e o
   `SHA256SUMS.txt`.
2. **draft-release** (só em tag; o único com `contents: write`): cria o rascunho com o DMG, o `SHA256SUMS.txt` e
   notas com a instalação e a primeira abertura. Uma versão com `-` (ex.: `0.9.0-beta.1`) vira pré-release.

Não há secrets: o workflow usa só o `GITHUB_TOKEN`. Pull requests nunca rodam o `release.yml`. Publicar o rascunho
é sempre manual.

## Versões

Versionamento semântico, com pré-releases antes da 1.0: `0.9.0-beta.1`, `0.9.0-beta.2`, `0.9.0`… A versão vive
em `app/package.json` (o `tauri.conf.json` lê dela), em `app/src-tauri/Cargo.toml` e no `VERSAO` de
`ombro/__init__.py` (o ombrod e a CLI); as três precisam bater com a tag. Uma tag com `-` vira pré-release no GitHub.

```bash
git tag v0.9.0-beta.1 && git push origin v0.9.0-beta.1
```

## Atualizações

O app **não** se atualiza sozinho nesta versão. O updater do Tauri exige artefatos assinados com um par de chaves
próprio (`TAURI_SIGNING_PRIVATE_KEY`), `createUpdaterArtifacts`, a chave pública no app e um endpoint servindo o
`latest.json`. Ele não está ligado nesta beta; até lá, Ajustes > Atualizações mostra a
versão instalada e abre a página de releases.

O backend vai dentro do app: atualizar o app atualiza o `ombrod`. Se o serviço em segundo plano estiver instalado,
o LaunchAgent aponta para o mesmo caminho do app, então a versão nova vale no próximo início do serviço.

## Checklist de uma release

Antes de criar a tag:

- [ ] A versão foi atualizada em `app/package.json` (e no `package-lock.json`), `app/src-tauri/Cargo.toml` (e no
      `Cargo.lock`) e `ombro/__init__.py`.
- [ ] CI verde: `tests.yml` e `app.yml`.
- [ ] `npm run build:app` passa localmente (o build confere binários, a execução sem Homebrew e as licenças).
- [ ] Testes de integração contra um ombrod --demo: `cargo test -- --ignored` (development.md).
- [ ] No app montado: abrir, assistente de primeiro uso, Painel, prévia, fechar a janela (fica na Menu Bar),
      reabrir e sair; com a telinha: brilho, layout e reconexão (tirar e pôr o cabo).

Depois do workflow:

- [ ] O rascunho é uma pré-release com o DMG e o SHA256SUMS; as notas explicam a primeira abertura.
- [ ] Num Mac sem Homebrew nem Python (ou num usuário novo do macOS): baixar, conferir o SHA256, instalar em
      Aplicativos, autorizar a primeira abertura (Abrir Mesmo Assim), integrar um agente, ligar o serviço em segundo plano,
      reiniciar o Mac e conferir que o serviço voltou.
- [ ] Publicar o rascunho.

## Voltar para uma versão anterior

1. Saia do Ombro (menu do cloud > Sair). Se o serviço em segundo plano estiver ligado, ele continua: pare em
   Ajustes antes, ou depois de trocar o app reinicie o Mac.
2. Baixe o DMG da versão anterior em Releases e confira o SHA256 (ver [macos-installation.md](macos-installation.md)).
3. Arraste o Ombro.app do DMG para Aplicativos, substituindo o atual.

Configurações (`~/.ombro-screen/config.json`), histórico e integrações ficam como estão. O banco
(`~/.ombro-screen/state.db`) guarda a versão do formato: uma versão anterior do Ombro que encontra um banco mais novo
**não o converte nem o apaga**; ela segue com o histórico só em memória e avisa no Diagnóstico. Não há downgrade
automático do banco.
