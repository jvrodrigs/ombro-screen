# Integrações: os hooks do Ombro no Claude Code, Codex e Cursor

O Ombro só instala hooks quando você pede. `status`, `doctor` e o próprio `ombrod` nunca mexem nas
configurações dos agentes.

```bash
python3 -m ombro integrations list                     # o estado dos três
python3 -m ombro integrations check codex              # detalhes de um
python3 -m ombro integrations install claude --dry-run # mostra a diferença, sem gravar
python3 -m ombro integrations install claude
python3 -m ombro integrations uninstall cursor
python3 -m ombro integrations rollback claude          # volta o arquivo para o último backup
```

Todos aceitam `--json`. `install` aceita `--python /caminho/python3` (padrão: o python que já está nas
entradas do Ombro, se ele ainda existir; senão, o python que roda a CLI).

## Onde e como

Formatos conferidos nas documentações oficiais
([Claude Code](https://code.claude.com/docs/en/hooks), [Codex](https://developers.openai.com/codex/hooks),
[Cursor](https://cursor.com/docs/hooks)):

| Agente | Arquivo | Entrada do Ombro |
| --- | --- | --- |
| Claude Code | `$CLAUDE_CONFIG_DIR/settings.json` (padrão `~/.claude/settings.json`) | `{"hooks": [{"type": "command", "command": "…", "async": true}]}` em `SessionStart`, `SessionEnd`, `PermissionRequest`, `Stop`, `StopFailure` |
| Codex | `$CODEX_HOME/hooks.json` (padrão `~/.codex/hooks.json`) | o mesmo formato, nos 8 eventos de `docs/providers/codex.md`; `SessionEnd` sem `async` e com `"timeout": 3` |
| Cursor | `~/.cursor/hooks.json` | `{"command": "…"}` nos 6 eventos de `docs/providers/cursor.md`, com `"version": 1` no topo |

O comando é sempre `PYTHON CAMINHO/ombro_hook.py PROVIDER` mais um fallback que garante código 0 mesmo se o
script sumir: `|| true` (Claude Code e Codex) ou `2>/dev/null || echo '{}'` (Cursor, onde o código 2 bloqueia
a ação). Caminhos com espaço vão entre aspas. Os eventos vêm de `EVENTOS` em `ombro_hook.py`, então hook e
instalador nunca divergem.

Depois de instalar no **Codex**, abra o Codex e rode `/hooks` (no app: Settings > Hooks) para confiar nos hooks
do Ombro: o Codex não roda hooks novos sem isso. O **Cursor** recarrega o arquivo sozinho. O **Claude Code**
percebe a mudança do `settings.json`.

## O que é do Ombro

Uma entrada é do Ombro se, e só se, o comando é exatamente `PYTHON …/ombro_hook.py PROVIDER` seguido de nada,
de `|| true` ou de `2>/dev/null || echo '{}'`. Qualquer outro comando que cite `ombro_hook.py` (dentro de um
script, com `&&`, de outro provider) é **ambíguo**: o Ombro não remove, não altera e não instala por cima. Ele
avisa e pede que você resolva à mão.

Estados de `check`:

| Estado | Quer dizer |
| --- | --- |
| `configured` | todos os eventos com a entrada do Ombro, com este `ombro_hook.py` e um python que existe |
| `partial` | faltam eventos, ou há entradas do Ombro desatualizadas (outro caminho, python que sumiu, campos antigos) |
| `not_configured` | nenhuma entrada do Ombro |
| `conflict` | há entradas ambíguas |
| `invalid` | o arquivo não é JSON válido ou tem um formato desconhecido |

## Instalar: a mudança mínima

1. Lê o arquivo (um link simbólico é seguido: grava no arquivo de verdade e o link fica).
2. Confere o formato (`hooks` é um objeto, cada evento uma lista de objetos). Formato desconhecido: para.
3. Em cada evento, a primeira entrada do Ombro vira a certa, **no mesmo lugar**; as outras entradas do
   Ombro (repetidas ou de eventos que o Ombro não usa mais) saem. Eventos sem entrada do Ombro ganham uma
   no fim. Entradas de outros programas, matchers, preferências e a ordem das chaves ficam como estavam.
4. Se nada muda, nada é gravado (nem backup): instalar duas vezes é igual a instalar uma.
5. Backup do arquivo atual (abaixo).
6. Grava num temporário na mesma pasta, `fsync`, e troca pelo nome final (`os.replace`). Antes da troca,
   confere que ninguém mexeu no arquivo desde a leitura (o agente salvando uma preferência, por exemplo);
   se mexeu, desiste sem gravar.
7. Relê e confere. Se não ficou como deveria, devolve o conteúdo original.

Um arquivo que não existe é criado (`0600`) se a pasta do agente existe; se nem a pasta existe, o Ombro não
cria pasta de agente: abra o agente uma vez antes.

`uninstall` faz o mesmo caminho, tirando só as entradas do Ombro. Um grupo que fica vazio sai; um evento que
fica sem nada sai; o arquivo nunca é apagado.

**Formatação:** o JSON é regravado com a indentação que o arquivo já usava (2 espaços por padrão). Os dados e
a ordem ficam; um arquivo escrito à mão em forma compacta, numa linha só, volta expandido. Arquivos JSON não
têm comentários a preservar.

## Backups e rollback

- Em `~/.ombro-screen/backups/integrations/<agente>/`, pastas `0700`, arquivos `0600`.
- Cada backup é o arquivo original (`.bak`) e um `.json` que diz de qual arquivo ele é.
- Ficam os 10 mais recentes de cada agente; os mais velhos são apagados.
- Sem backup (disco cheio, sem permissão), nada é gravado.
- `rollback` restaura o mais recente e, antes, guarda o estado atual como backup: um rollback se desfaz com
  outro rollback. Ele recusa um backup de outro arquivo (outro `CLAUDE_CONFIG_DIR`, por exemplo).
- O conteúdo dos backups nunca vai para os logs nem para o `doctor --report`.

## Falhas tratadas

JSON inválido, formato desconhecido, arquivo inexistente, pasta do agente inexistente, permissão negada,
arquivo somente leitura, conflito com entrada ambígua, gravação interrompida (o temporário é apagado e o
original fica), backup indisponível, mudança concorrente de outro programa, duas CLIs ao mesmo tempo (uma
trava em `~/.ombro-screen/backups/.integrations.lock`), caminhos com espaço, `CLAUDE_CONFIG_DIR` e
`CODEX_HOME`. Em todos os casos o arquivo fica como estava.

## Limitações

- Só a configuração do usuário. Hooks por projeto (`.claude/settings.json`, `.codex/hooks.json`,
  `.cursor/hooks.json` de um repositório) não são tocados; o Ombro só os descobre quando os eventos chegam.
- O Codex guarda a confiança nos hooks por definição: reinstalar com outro caminho pede `/hooks` de novo.
- Hooks inline no `config.toml` do Codex não são lidos nem escritos (o formato `hooks.json` é o documentado
  para arquivos separados, e editar TOML preservando comentários exigiria uma dependência).
