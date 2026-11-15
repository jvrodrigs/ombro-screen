# Cursor no Ombro

O Ombro acompanha o agente do Cursor pelos **hooks oficiais** (<https://cursor.com/docs/hooks>). Sem os
hooks, o Cursor aparece como "não configurado".

## O que dá para ver

| Evento do Cursor | Estado no Ombro |
| --- | --- |
| `sessionStart` | conversa aberta, ociosa |
| `beforeSubmitPrompt`, `postToolUse`, `postToolUseFailure` | trabalhando |
| `stop` com `status` `completed` ou `aborted` | ociosa |
| `stop` com `status` `error` | parou com erro |
| `sessionEnd` | conversa encerrada |

A sessão é a conversa do Composer (`conversation_id`). As mesmas regras de tempo do Codex valem aqui.

O Ombro só usa hooks que **não decidem nada**. Hooks como `preToolUse` e `beforeShellExecution` tratam
uma saída inválida como recusa, então ficam de fora. Mesmo assim, o hook responde `{}` (um JSON válido e
vazio) em todos os eventos.

## O que não dá

- **Esperando aprovação:** o Cursor não tem evento para isso. A atenção das conversas fica desconhecida
  (exceto quando param com erro).
- **Tokens, custo e limites de uso:** o Cursor não passa esses dados aos hooks.
- **Agentes na nuvem (Cloud Agents):** não rodam `sessionStart` e `sessionEnd`, nem leem `~/.cursor/hooks.json`.

## Como ligar

```bash
python3 -m ombro integrations install cursor --dry-run
python3 -m ombro integrations install cursor
```

Ou à mão: em `~/.cursor/hooks.json` (todos os projetos) ou em `.cursor/hooks.json` de um projeto, **junto** dos
hooks que você já tem. O Cursor recarrega o arquivo ao salvar.

```json
{
  "version": 1,
  "hooks": {
    "sessionStart":       [{"command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py cursor 2>/dev/null || echo '{}'"}],
    "beforeSubmitPrompt": [{"command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py cursor 2>/dev/null || echo '{}'"}],
    "postToolUse":        [{"command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py cursor 2>/dev/null || echo '{}'"}],
    "postToolUseFailure": [{"command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py cursor 2>/dev/null || echo '{}'"}],
    "stop":               [{"command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py cursor 2>/dev/null || echo '{}'"}],
    "sessionEnd":         [{"command": "/usr/bin/python3 /CAMINHO/ombro-screen/ombro_hook.py cursor 2>/dev/null || echo '{}'"}]
  }
}
```

O `2>/dev/null || echo '{}'` é importante: se o `ombro_hook.py` não existir (outro branch, pasta movida), o
Python sai com código 2, e no Cursor o código 2 **bloqueia** a ação. Com o fallback, sai sempre `{}` e 0.

## Validação

Com o `cursor-agent` 3.29.9 (`cursor-agent -p`), o Cursor CLI **roda** os hooks de `~/.cursor/hooks.json`
(a documentação não dizia isso para o CLI): chegaram `sessionStart` e `sessionEnd` (`completed`) com o id
real da conversa, e o Ombro os aplicou. No modo `-p`, `beforeSubmitPrompt` e `stop` não chegaram (o prompt
vai como argumento). O Cursor IDE não foi testado; os outros eventos, só no formato da documentação.
