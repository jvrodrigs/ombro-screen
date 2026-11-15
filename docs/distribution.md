# Distribuição do Ombro.app

Como o Ombro.app é montado, assinado, notarizado e publicado. Para quem instala, veja
[macos-installation.md](macos-installation.md); para desenvolver o app, [development.md](development.md).

## O que vai dentro do app

```
Ombro.app/Contents/
├── MacOS/ombro                 o app (Tauri 2: Rust + a interface React)
└── Resources/
    ├── runtime/                CPython 3.14 relocável (python-build-standalone), com Pillow, psutil, pyserial,
    │                           numpy, pyusb e a libusb do libusb-package; sem pip, tkinter, testes nem cabeçalhos
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

Saída: `src-tauri/target/release/bundle/macos/Ombro.app` e `…/bundle/dmg/Ombro_<versão>_aarch64.dmg`.

Sem `APPLE_SIGNING_IDENTITY`, tudo sai com **assinatura ad-hoc**: serve para testar nesta máquina, mas não é
equivalente a um app assinado e notarizado. O Gatekeeper recusa um app ad-hoc baixado da internet
(`spctl` responde `rejected`).

## Assinatura e notarização

O Ombro.app precisa de um certificado **Developer ID Application** (conta paga do Apple Developer Program) para
abrir sem avisos em outros Macs. Com ele:

```bash
APPLE_SIGNING_IDENTITY="Developer ID Application: Nome (TEAMID)" npm run build:app
```

- O script assina os 53 binários do runtime com essa identidade, `--options runtime` e `--timestamp`. O Tauri
  assina o executável principal e o bundle.
- O Python embarcado roda com hardened runtime **sem nenhuma exceção** (`backend/runtime.entitlements` está
  vazio). O build testa isso: nas builds ad-hoc, só a validação de Team ID é dispensada (ad-hoc não tem Team ID);
  todo o resto do hardened runtime vale durante o teste.
- Notarização: o Tauri notariza (e grampeia o ticket) quando acha as credenciais no ambiente:
  - chave de API do App Store Connect (preferida): `APPLE_API_ISSUER`, `APPLE_API_KEY` e `APPLE_API_KEY_PATH`;
  - ou Apple ID: `APPLE_ID`, `APPLE_PASSWORD` (senha de app) e `APPLE_TEAM_ID`.

Conferir um app assinado:

```bash
codesign --verify --deep --strict --verbose=2 Ombro.app && spctl --assess --type execute -vv Ombro.app && xcrun stapler validate Ombro.app
```

**Ainda não validado:** nenhuma build foi assinada com Developer ID nem notarizada até agora (o projeto não tem o
certificado). O caminho está pronto e testado até onde dá sem ele.

## GitHub Actions

| Workflow | Quando | O que faz |
| --- | --- | --- |
| `tests.yml` | todo push e PR | os testes do backend Python (3.9 a 3.14) e o ruff |
| `app.yml` | push e PR que mexem em `app/` | tipos, testes e build da interface (Ubuntu); fmt, clippy e testes do Rust (macOS) |
| `release.yml` | tag `v*`, ou manual | monta o app e o DMG; com os secrets, assina e notariza; numa tag, cria um **rascunho** de release |

O `release.yml` confere que a tag, o `app/package.json` e o `app/src-tauri/Cargo.toml` têm a mesma versão,
renomeia o DMG para `Ombro-<versão>-macos-arm64.dmg` e publica o `SHA256SUMS.txt` junto. Publicar o rascunho é
sempre manual.

### Secrets

Ficam no environment **release** (Settings > Environments > release), que deve ter uma regra de deployment
aceitando só tags `v*` (e, se quiser, um revisor obrigatório). Pull requests nunca rodam o `release.yml`.

| Secret | O que é |
| --- | --- |
| `APPLE_CERTIFICATE` | o `.p12` do Developer ID Application, em base64 (`openssl base64 -A -in cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | a senha do `.p12` |
| `APPLE_API_ISSUER`, `APPLE_API_KEY` | issuer e Key ID da chave de API do App Store Connect |
| `APPLE_API_PRIVATE_KEY` | o conteúdo do arquivo `AuthKey_<id>.p8` |
| `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` | alternativa à chave de API (senha de app, não a da conta) |

O certificado é importado num keychain temporário com senha aleatória, que é apagado no fim do job (mesmo se ele
falhar). Nenhum secret é impresso; sem `APPLE_CERTIFICATE`, o workflow avisa e a build sai ad-hoc.

## Versões

Versionamento semântico, com pré-releases antes da 1.0: `0.5.0-alpha.1`, `0.5.0-beta.1`, `0.9.0`… A versão vive
em `app/package.json` (o `tauri.conf.json` lê dela) e em `app/src-tauri/Cargo.toml`; as duas precisam bater com a
tag. Uma tag com `-` vira pré-release no GitHub.

```bash
git tag v0.5.0-alpha.1 && git push origin v0.5.0-alpha.1
```

## Atualizações

O app **não** se atualiza sozinho nesta versão. O updater do Tauri exige artefatos assinados com um par de chaves
próprio (`TAURI_SIGNING_PRIVATE_KEY`), `createUpdaterArtifacts`, a chave pública no app e um endpoint servindo o
`latest.json`. Ele só deve ser ligado junto com a assinatura Developer ID; até lá, Ajustes > Atualizações mostra a
versão instalada e abre a página de releases.

O backend vai dentro do app: atualizar o app atualiza o `ombrod`. Se o serviço em segundo plano estiver instalado,
o LaunchAgent aponta para o mesmo caminho do app, então a versão nova vale no próximo início do serviço.

## Checklist de uma release

Antes de criar a tag:

- [ ] O repositório tem LICENSE e a origem do desenho do cloud está resolvida ([licenses.md](licenses.md)).
- [ ] A versão foi atualizada em `app/package.json` e `app/src-tauri/Cargo.toml`.
- [ ] CI verde: `tests.yml` e `app.yml`.
- [ ] `npm run build:app` passa localmente (o build confere binários, a execução sem Homebrew e as licenças).
- [ ] Testes de integração contra um ombrod --demo: `cargo test -- --ignored` (development.md).
- [ ] No app montado: abrir, assistente de primeiro uso, Painel, prévia, fechar a janela (fica na Menu Bar),
      reabrir e sair; com a telinha: brilho, layout e reconexão (tirar e pôr o cabo).

Depois do workflow:

- [ ] O rascunho tem o DMG e o SHA256SUMS; as notas dizem o estado da assinatura.
- [ ] Num Mac sem Homebrew nem Python (ou num usuário novo do macOS): baixar, conferir o SHA256, instalar em
      Aplicativos, abrir sem avisos do Gatekeeper (se assinado), integrar um agente, ligar o serviço em segundo plano,
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
