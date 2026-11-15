# Claude Code no Ombro

**Sem configurar nada**, o Ombro já acompanha o Claude Code pelos arquivos que ele mesmo grava:

| O quê | De onde |
| --- | --- |
| Sessões abertas e o status de cada uma (trabalhando, esperando você, ociosa) | `~/.claude/sessions/<pid>.json` (só processos vivos) |
| Tokens de hoje, por hora | `~/.claude/projects/**/*.jsonl` (lidos aos poucos, sem repetir) |
| Uso de 5 h e da semana, e quando reiniciam | `~/.ombro-screen/uso.json` (statusline), `~/.habblaud/usage`, ou o `/usage` (`~/.claude.json`); vale o mais recente |

Isso continua igual. O statusline (`statusline_tap.py`) continua sendo a fonte ao vivo dos limites de uso;
os hooks não substituem nem duplicam essa leitura.

## Hooks (opcional): aprovação e erro

Os arquivos dizem "esperando você", mas não dizem se é uma resposta ou uma **aprovação**, nem se o
Claude **parou por erro**. Com os hooks do Ombro ligados, ele passa a distinguir:

| Evento do Claude Code | O que o Ombro faz |
| --- | --- |
| `PermissionRequest` | a sessão que está esperando passa a "aguarda aprovação" |
| `StopFailure` | a sessão parada passa a "parou com erro" (até o próximo `Stop`) |
| `Stop`, `SessionStart`, `SessionEnd` | limpam o que ficou para trás |

Um evento só vale se for mais novo que o status atual da sessão no arquivo, então um hook atrasado não
desfaz o que os arquivos dizem. Os layouts do Ombro e Minimal v2 continuam mostrando "esperando", como
antes; a diferença aparece no layout multiagente.

### Como ligar

```bash
python3 -m ombro integrations install claude --dry-run   # mostra a mudança
python3 -m ombro integrations install claude             # com backup (docs/integrations.md)
```

Ou à mão: acrescente ao `~/.claude/settings.json`, **junto** dos hooks que você já tem (o Claude Code soma os
hooks de todos os arquivos de configuração; não apague os seus). Troque `/CAMINHO` pela pasta do projeto:

```json
{
  "hooks": {
    "SessionStart":      [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py claude || true", "async": true}]}],
    "PermissionRequest": [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py claude || true", "async": true}]}],
    "Stop":              [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py claude || true", "async": true}]}],
    "StopFailure":       [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py claude || true", "async": true}]}],
    "SessionEnd":        [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py claude || true", "async": true}]}]
  }
}
```

- `async: true`: o Claude Code não espera o hook (que leva ~20 ms de qualquer jeito).
- `|| true`: se o `ombro_hook.py` sumir (outro branch, pasta movida), o comando ainda sai com 0, sem aviso de erro.
- O hook não imprime nada e sempre sai com código 0: não bloqueia, não aprova, não muda a conversa.
- Para desligar, apague essas entradas.

### Conferir

Com o Ombro rodando com `--debug`, a primeira vez que um evento chega aparece
`eventos dos hooks do Claude Code chegando`. Com o Ombro desligado, os eventos ficam em
`~/.ombro-screen/eventos/` até ele voltar (no máximo 5000; arquivos com mais de 24 h são descartados).

## Validação

Com o Claude Code 2.1.325 de verdade (`claude -p`), os hooks gravaram `SessionStart` (`startup`) e
`SessionEnd` com o `session_id` real e só o nome da pasta, e o Ombro os aplicou. No modo `-p`, o `Stop`
não chega: o Claude Code encerra hooks `async` que ainda estão rodando quando ele sai. Em sessões
interativas (o uso normal), não há esse corte. `PermissionRequest` e `StopFailure` foram testados só com
eventos no formato da documentação.

## Limitações

- Sem hooks, aprovação e resposta aparecem as duas como "aguarda você".
- O `Notification` (avisos de ~6 s e ~60 s) não é usado: o `PermissionRequest` já chega na hora.
