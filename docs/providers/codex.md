# OpenAI Codex no Ombro

O Codex não grava o status das sessões num arquivo estável para o Ombro reler: tudo o que o Ombro
sabe dele chega pelos **hooks oficiais** do Codex (<https://learn.chatgpt.com/docs/hooks>). Sem os hooks,
o Codex aparece como "não configurado" (ou some, se não estiver instalado).

## O que dá para ver

| Evento do Codex | Estado no Ombro |
| --- | --- |
| `SessionStart` | sessão aberta, ociosa |
| `UserPromptSubmit`, `PostToolUse` | trabalhando |
| `PermissionRequest` | aguarda aprovação |
| `PostModelRequest` com status `failed` | parou com erro (até o próximo prompt) |
| `Stop`, `Interrupt` | ociosa (fim do turno, não da sessão) |
| `SessionEnd` | sessão encerrada |

Sessões são identificadas pelo `session_id` do Codex. "Trabalhando" sem nenhum evento há 15 min vira
"sem notícias" (um fim de turno pode ter se perdido); uma sessão sem eventos há 4 h sai da tela.

## O que não dá (e por quê)

- **Tokens e limites de uso do Codex:** não. O único caminho documentado é o **App Server**
  (`codex app-server`), e ele não serve a um monitor passivo: o Ombro teria de abrir o seu próprio
  processo do Codex, que só enxerga as threads que ele mesmo abrir (não as do seu terminal ou do app), e
  a documentação não diz se ler os limites tem efeito na conta. O Ombro também não lê
  `~/.codex/sessions/`, que a OpenAI diz não ser um formato estável.
- **Esperando uma resposta sua:** o Codex não tem evento para isso; o fim do turno aparece como "ocioso".

## Como ligar

```bash
python3 -m ombro integrations install codex --dry-run
python3 -m ombro integrations install codex
```

Ou à mão: em `~/.codex/hooks.json` (ou num `.codex/hooks.json` do projeto), **junto** dos hooks que você já tem:

```json
{
  "hooks": {
    "SessionStart":     [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "async": true}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "async": true}]}],
    "PermissionRequest":[{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "async": true}]}],
    "PostToolUse":      [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "async": true}]}],
    "PostModelRequest": [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "async": true}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "async": true}]}],
    "Interrupt":        [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "async": true}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py codex || true", "timeout": 3}]}]
  }
}
```

- O Codex só roda um hook novo depois que você o aprova: rode `/hooks` no Codex (ou, no app,
  **Settings > Hooks**) e confie nos hooks do Ombro.
- `SessionEnd` sempre roda sem `async` e tem 1 s por padrão; o hook leva ~20 ms.
- O hook não imprime nada (no Codex, o stdout de alguns eventos entraria na conversa) e sempre sai com 0;
  o `|| true` garante isso mesmo se o `ombro_hook.py` sumir.

## Validação

Testado com eventos no formato da documentação (testes automáticos). **Não foi validado com o Codex
rodando**: o Codex não estava instalado na máquina em que esta integração foi escrita.
