# Desenvolver o Ombro.app

O app fica em `app/`: a interface (React + TypeScript + Vite) em `app/src/` e o lado nativo (Tauri 2, Rust) em
`app/src-tauri/`. O backend é o mesmo Python do repositório; o app não tem cópia da lógica dele.

```
React (app/src)  ──comandos Tauri tipados──►  Rust (app/src-tauri/src)
                                               ├─ ipc.rs      cliente da IPC v1 → ~/.ombro-screen/run/ombro.sock → ombrod
                                               ├─ backend.rs  a CLI do Ombro (integrações, serviço, doctor) e o ombrod filho
                                               ├─ monitor.rs  o único laço de atualização (janela, Menu Bar, notificações)
                                               ├─ tray.rs     a Menu Bar
                                               ├─ prefs.rs    preferências só do app (onboarding, notificações)
                                               └─ commands.rs os comandos que o JavaScript pode chamar
```

## Rodar

Pré-requisitos: Node 24, Rust estável (`rustup`) e o `.venv` do repositório (README, seção 1).

```bash
cd app && npm ci
```

```bash
npm run tauri dev
```

Em desenvolvimento o app usa o backend do repositório e o python do `.venv`. Para não mexer nos seus dados, rode um
ombrod de demonstração numa pasta à parte e aponte o app para ela (o caminho precisa ser curto: o socket tem limite
de 104 bytes):

```bash
mkdir -p /tmp/ombro-demo && OMBRO_SCREEN_DIR=/tmp/ombro-demo .venv/bin/python ombrod.py --demo
```

```bash
cd app && OMBRO_SCREEN_DIR=/tmp/ombro-demo npm run tauri dev
```

Só a interface, no navegador, com o mock do contrato (sem Tauri nem ombrod): `npm run dev` e abra
`http://localhost:1420`.

## Testes

```bash
cd app && npm run typecheck && npm test
```

```bash
cd app/src-tauri && cargo clippy --all-targets -- -D warnings && cargo test
```

- **Interface:** Vitest com o mock do contrato IPC (`src/services/mock.ts`, com as formas que o ombrod --demo
  devolve de verdade).
- **Rust:** o cliente IPC contra um servidor de mentira, as notificações, os textos da Menu Bar e as preferências.
- **Contra um ombrod de verdade** (todos os métodos da allowlist e a CLI; só leitura e dry-run):
  `OMBRO_SCREEN_DIR=/tmp/ombro-demo cargo test -- --ignored`, com o ombrod --demo acima rodando.
- **Ciclo de vida da janela** (esconder, fechar, reabrir, a Menu Bar e o Sair), só em debug:
  `OMBRO_APP_SELFTEST=1 OMBRO_SCREEN_DIR=/tmp/ombro-demo src-tauri/target/debug/ombro`, com o Vite rodando
  (`npx vite`). Imprime cada fase e o tipo do app no sistema (Foreground = no Dock, UIElement = só na Menu Bar).

## Regras do app

- O JavaScript não tem shell, sistema de arquivos nem plugins: só os comandos de `commands.rs`, listados em
  `build.rs` e liberados em `capabilities/main.json`. Cada comando tem parâmetros de tipo fixo (enums para agentes e
  ações); nenhum aceita método de IPC, comando, caminho ou URL vindos da interface.
- Tudo o que é estado do Ombro vem do ombrod. A interface não lê o banco, os arquivos dos agentes nem o
  `config.json`; as preferências saem do `config.schema` e são gravadas pelo `config.update`.
- Integrações, serviço e doctor passam pela CLI da Fase 4 (`ombro integrations|service|doctor --json`), com
  dry-run e confirmação antes de qualquer mudança.
- Um ombrod por vez: o app conversa com o que já roda; se o LaunchAgent estiver instalado, espera o launchd; senão,
  inicia um filho e o encerra ao sair. Com o LaunchAgent de antes da Fase 4 instalado, não inicia nada.

## Ícones

`app/scripts/gen_tray_icon.py` gera o ícone da Menu Bar (template monocromático), a marca SVG da barra lateral e o
`docs/icon.png` a partir do avatar Ombro Cloud (`avatars/defs/ombro-cloud.json`), a mesma forma do mascote da tela.
Os tamanhos do macOS saem do `docs/icon.png` com `npx tauri icon ../docs/icon.png -o src-tauri/icons`.

## Avatares

`src/features/avatars/`: o catálogo (os mesmos arquivos de `avatars/` que a telinha usa, validados pelo
mascotinhos), a regra de escolha (igual à do `avatares.py`, inclusive o hash do aleatório por sessão), as animações
(`animacoes.ts`: os pacotes `vivo` e `classico`, com alerta e reset do `classico`, como no `mascote.py`, e a troca de pacote no ponto seguro de cada mascote; `pacote.tsx`: o pacote em uso que o ombrod publica), o `AvatarView` (o player
do mascotinhos nos cartões dos agentes e na prévia da galeria; um SVG parado nas listas e miniaturas) e as seções
Aparência > Avatares e Aparência > Animações. Detalhes:
[avatars.md](avatars.md).

Montar, assinar e publicar: [distribution.md](distribution.md).
