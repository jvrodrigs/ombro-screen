# Diagnóstico e problemas comuns

## `ombro doctor`

```bash
python3 -m ombro doctor                  # no terminal
python3 -m ombro doctor --json           # estruturado (para scripts e para o Ombro.app)
python3 -m ombro doctor --report         # relatório para colar numa issue, sem dados pessoais
python3 -m ombro doctor --report --output relatorio.txt
```

Só lê. Não roda prompts nos agentes (só `<agente> --version`, com 3 s de limite), não abre a tela USB (só a
procura na lista do USB: "detectada" não garante que as imagens chegam), abre o banco só para leitura e não
instala nem desinstala nada. Sai com 1 se alguma verificação deu `FAIL`.

Exemplo (desta máquina de desenvolvimento, com o LaunchAgent antigo ainda ligado):

```
Ombro Doctor

[PASS] Sistema operacional: macOS
[PASS] Python: 3.14.7
[PASS] Arquivo de configuração: ~/.ombro-screen/config.json é válido
[PASS] Pasta do Ombro: ~/.ombro-screen: privada (0700)
[WARN] ombrod: O ombrod não está rodando.
       -> python3 ombrod.py, ou instale o serviço: python3 -m ombro service install
[WARN] LaunchAgent: o LaunchAgent antigo (com.ombro-screen, tela.py) está ligado
       -> python3 -m ombro service migrate --dry-run (para ver), depois sem --dry-run
[PASS] Banco de estado (SQLite): íntegro, versão 1; 6 sessões e 81 eventos guardados (retenção de 7 dias)
[PASS] Claude Code: 2.1.325 (Claude Code); integração configurada
[SKIP] Codex: não instalado
[PASS] Tela: detectada no USB (o envio de imagens não foi testado)
```

### Esquema do JSON (`ombro.doctor`, versão 1)

```json
{
  "schema": "ombro.doctor", "schema_version": 1, "ombro_version": "0.4.0", "ipc_protocol": 1,
  "generated_at": "2026-11-14T19:00:00",
  "summary": {"status": "WARN", "pass": 12, "warn": 4, "fail": 0, "skip": 1},
  "checks": [
    {"id": "service.daemon", "category": "service", "status": "WARN", "title": "ombrod",
     "message": "O ombrod não está rodando.", "action": "python3 ombrod.py, ...", "details": {}}
  ]
}
```

- `status`: `PASS`, `WARN`, `FAIL` ou `SKIP` (não se aplica: agente não instalado, não é macOS...).
- `category`: `environment`, `configuration`, `service`, `storage`, `providers`, `display`.
- `id` é estável (`env.python`, `config.file`, `storage.dir`, `storage.files`, `storage.sqlite`, `storage.events`,
  `service.daemon`, `service.instances`, `service.launchagent`, `service.socket_path`, `provider.claude`,
  `provider.codex`, `provider.cursor`, `display.driver`, `display.connection`...). Verificações novas podem
  aparecer; uma mudança no formato é a versão 2.
- `action` é um comando ou instrução curta, ou `null`.

### O que o relatório não leva

Prompts, respostas, conteúdo de projetos, nomes de projeto, ids de sessão, credenciais e tokens (textos com
cara de chave viram `<removido>`), o conteúdo do `config.json` ou dos arquivos dos agentes, payloads dos
hooks, a pasta pessoal (vira `~`), o nome do usuário (`<usuario>`) e o da máquina (`<maquina>`). Ele é
mostrado no terminal; só vai para um arquivo com `--output`, e esse arquivo nasce `0600`.

## Problemas comuns

| Sintoma | O que fazer |
| --- | --- |
| `O ombrod não está rodando.` | `python3 ombrod.py` num terminal, ou `python3 -m ombro service install`. |
| `já existe um Ombro rodando (ombrod, PID …)` ao abrir o `tela.py` | Só um Ombro ao vivo por vez. Pare o serviço (`python3 -m ombro service stop`) ou use `--demo`/`--png`, que rodam ao lado. |
| `já existe um Ombro rodando (tela.py, PID …)` ao abrir o ombrod | Feche o `tela.py` daquele terminal. |
| ombrod `degraded` com `instance: legacy_conflict` | Um `tela.py` de antes da Fase 4 (o LaunchAgent antigo) está com a tela. O ombrod espera ele sair, conferindo a cada 10 s. Migre: `python3 -m ombro service migrate`. |
| `display: error` | A tela está lá mas falhou (porta ocupada por outro programa, por exemplo). `python3 -m ombro display` mostra o último erro; o ombrod tenta de novo a cada 3 s. |
| `display: disconnected` | Sem a tela. Não é falha: o ombrod segue e começa a mostrar quando ela for plugada. |
| `storage: memory_only` | O `state.db` não abriu (corrompido, de versão mais nova, sem permissão). O Ombro segue sem lembrar das sessões ao reiniciar. `ombro doctor` diz o motivo; para começar do zero, mova o arquivo. |
| simulador `error`: porta indisponível | Outra coisa usa a porta 5680. Use `--porta-sim` (ou `porta_sim` no config.json). |
| `integração parcial` ou `desatualizada` | `python3 -m ombro integrations install <agente>` (veja `--dry-run` antes). |
| `conflito` numa integração | Há uma entrada com `ombro_hook.py` que o Ombro não reconhece. Ajuste à mão (veja [integrations.md](integrations.md)). |
| A CLI demora ou diz que o ombrod não respondeu | `--timeout 10`; se continuar, o log do ombrod (abaixo). |

## Logs

- ombrod como serviço: `~/Library/Logs/ombro-screen/ombrod.log` (`tail -f`).
- ombrod ou `tela.py` num terminal: o próprio terminal. `--debug` dá os detalhes.
- Partes: `ombro.daemon`, `ombro.ipc`, `ombro.integrations`, `ombro.service`, `ombro.doctor`, além das de
  sempre (`ombro.main`, `ombro.usb`, `ombro.estado`...). Problemas que se repetem entram uma vez.
- O launchd não gira o arquivo de log; ele cresce devagar (só mudanças de estado), mas pode ser apagado com o
  serviço parado.

## Ombro.app

| O que aparece | O que fazer |
| --- | --- |
| O macOS diz que não pode abrir o Ombro | Versão sem assinatura da Apple (alpha): Ajustes do Sistema > Privacidade e Segurança > Abrir Mesmo Assim ([macos-installation.md](macos-installation.md)). |
| "O Ombro antigo está cuidando da tela" | O LaunchAgent de antes do Ombro Hub está instalado. Ajustes > Ver a migração: guarda um backup, liga o novo e pode ser desfeita. |
| "Instalar integrações está bloqueado" | O app está fora de Aplicativos (ou rodando direto do DMG). Arraste-o para Aplicativos e abra de lá. |
| "O Ombro não está monitorando" | O serviço local não responde. "Iniciar monitoramento" tenta de novo; o Diagnóstico mostra o porquê, e o log fica em `~/Library/Logs/ombro-screen/ombrod.log`. |
| "Esperando o serviço do Ombro" | O serviço em segundo plano está instalado, mas parado: "Iniciar serviço", ou Ajustes para removê-lo. |
| "Serviço de outra versão" | Um ombrod de outra versão (do repositório, por exemplo) está rodando. Pare-o e abra o app de novo. |
| "O backend do Ombro não foi encontrado" | O app está incompleto: baixe o DMG de novo e substitua o app. |
| A prévia diz "Esperando o primeiro quadro" | O serviço acabou de iniciar (alguns segundos) ou está esperando o Ombro antigo sair (veja a migração). |
| Revisão B, C, D ou WeAct não funciona no app | Essas revisões precisam da biblioteca turing-smart-screen-python, que o app não traz (GPL): use o Ombro do repositório. |
