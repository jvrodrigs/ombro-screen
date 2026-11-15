# Protocolo local do Ombro (IPC v1)

O `ombrod` responde num **Unix Domain Socket** local. É por ele que a CLI (`python3 -m ombro`) e, no futuro,
o Ombro.app consultam e ajustam o Ombro, sem tocar nos objetos internos do processo. Código: `ombro/ipc.py`
(servidor e cliente) e `ombro/daemon.py` (os métodos).

## Transporte

| | |
| --- | --- |
| Socket | `~/.ombro-screen/run/ombro.sock` (segue `OMBRO_SCREEN_DIR`) |
| Formato | JSON Lines, UTF-8: uma mensagem JSON por linha, terminada em `\n` |
| Conexão | várias chamadas na mesma conexão, uma de cada vez (pedido, resposta, pedido...) |
| Versão | `"version": 1` em todo pedido e toda resposta |
| Rede | nenhuma: não há porta TCP, nem em `localhost` |

## Mensagens

Pedido:

```json
{"version": 1, "request_id": "req-001", "method": "status", "params": {}}
```

- `version` (obrigatório, inteiro): a versão do protocolo. Outra versão recebe `UNSUPPORTED_VERSION`.
- `method` (obrigatório, texto): um dos métodos abaixo.
- `params` (opcional, objeto): só as chaves que o método aceita, com os tipos e as faixas dele.
- `request_id` (opcional): de 1 a 64 caracteres `[A-Za-z0-9._:-]`, devolvido na resposta.
- Qualquer outro campo no topo é recusado (`INVALID_REQUEST`).

Resposta de sucesso e de erro:

```json
{"version": 1, "request_id": "req-001", "ok": true, "result": {"daemon": "running", "...": "..."}}
{"version": 1, "request_id": "req-001", "ok": false, "error": {"code": "UNKNOWN_METHOD", "message": "método desconhecido: x"}}
```

## Erros

| Código | Quando |
| --- | --- |
| `INVALID_REQUEST` | não é JSON, não é objeto, falta `version` ou `method`, campo desconhecido, mensagem grande demais |
| `UNKNOWN_METHOD` | o método não existe (não há `shell.execute`, `python.eval`, `agent.*`) |
| `UNSUPPORTED_VERSION` | `version` diferente de 1 |
| `VALIDATION_ERROR` | parâmetro desconhecido, de tipo errado, fora da faixa, preferência que não pode mudar, `config.json` inválido |
| `SERVICE_UNAVAILABLE` | clientes demais, o laço não respondeu a tempo, o ombrod está fechando (métodos que mudam algo) |
| `INTERNAL_ERROR` | erro inesperado; o detalhe fica só no log local do ombrod |
| `PERMISSION_DENIED` | o processo conectado é de outro usuário |

Mensagens de erro são curtas e não trazem traceback, caminhos pessoais nem conteúdo de arquivos.

## Limites

| Limite | Valor |
| --- | --- |
| Tamanho de um pedido | 64 KiB, com o `\n` |
| Clientes ao mesmo tempo | 8 (o nono recebe `SERVICE_UNAVAILABLE` e a conexão fecha) |
| Conexão parada | fecha depois de 5 s sem receber nada |
| Pedidos por conexão | 1000; depois o servidor fecha (o `ClienteIPC` reconecta sozinho) |
| Tempo de um método que muda algo | 2 s esperando o laço; depois, `SERVICE_UNAVAILABLE` |
| Resposta (no cliente) | até 4 MiB |

Clientes lentos não atrasam a tela: cada conexão tem a sua thread, e os métodos de leitura usam o que o laço
publicou a cada coleta (5 s), sem trava nenhuma no laço. Os métodos que mudam algo entram numa fila que o
laço atende entre dois quadros.

## Métodos de leitura

| Método | Params | Resultado |
| --- | --- | --- |
| `ping` | | `{pong, protocol, daemon}` |
| `status` | | `daemon` (estado do ciclo de vida), `version`, `protocol`, `pid`, `started_at`, `uptime_s`, `health`, `providers {configured, installed, operational}`, `sessions`, `waiting`, `errors`, `display {kind, connection, layout}`, `storage`, `demo`, `collected_at`, `conflicts` |
| `health` | | `{status, components}`; ver "Saúde" |
| `providers.list` | | `providers`: `id`, `name`, `enabled`, `capabilities`, `installed`, `integration` (`ok`, `not_installed`, `not_configured`), `collection` (`ok`, `partial`, `error`), `state`, `sessions`, `working`, `waiting`, `errors`, `quota_max_percent`, `stale_parts`, `recovered`, `last_collected_at`, `last_event_at` |
| `sessions.list` | `provider`: `claude`, `codex`, `cursor` ou `null` | `sessions`: `provider`, `session_id`, `project` (nome curto da pasta, nunca o caminho), `status` (`working`, `idle`, `waiting`, `approval`, `error`, `unknown`), `activity`, `attention`, `started_at`, `status_since`, `updated_at` |
| `display.status` | | `kind` (`usb`, `simulator`, `none`), `connection` (`connected`, `disconnected`, `error`, `conflict`), `transport`, `revision`, `port` (só o nome), `brightness`, `orientation`, `layout`, `frames`, `last_frame_at`, `connected_since`, `last_error`, `url` (simulador) |
| `display.preview` | `format`: `png` | `{available: false}` sem quadro ainda; senão `available`, `format`, `width`, `height`, `captured_at`, `data_base64` |
| `config.get` | | `file`, `file_exists`, `values` (a configuração em uso, caminhos com `~`), `providers_enabled` (`{id: bool}`), `editable`, `live`, `layouts_live`, `views` |
| `config.schema` | | `{version, options}`: cada preferência que a IPC aceita, com `key`, `group`, `type`, `description`, `live`, `nullable` e, quando há, `choices`, `min`, `max` |

`display.preview` devolve o último quadro que foi para a tela: nada é desenhado de novo. São 50–120 KiB em
base64, em ~2 ms. Não há streaming: quem quer acompanhar pede de novo (o simulador atualiza 4 vezes por segundo).

Horários em ISO 8601, hora local, sem fuso, como no resto do Ombro.

## Métodos que mudam algo

| Método | Params | O que faz |
| --- | --- | --- |
| `config.update` | `changes` (objeto, obrigatório), `persist` (padrão `true`) | valida, aplica o que vale na hora e, com `persist`, grava no `config.json` |
| `display.set_brightness` | `value` (0–100, obrigatório), `persist` (padrão `false`) | muda o brilho da tela USB na hora |
| `display.set_layout` | `layout` (obrigatório), `persist` (padrão `false`) | troca o layout entre `auto`, `ombro`, `minimal-v2` e `multiagente` na hora |

Toda a configuração que não é caminho de pasta pode mudar por aqui. `config.schema` devolve a lista, que é a
fonte da verdade (`ombro/preferencias.py`); o Ombro.app monta a tela de ajustes a partir dela, sem lista própria.

| Grupo | Chaves | Vale |
| --- | --- | --- |
| `display` | `brilho` | na hora |
| `display` | `usb`, `revisao`, `transporte`, `porta` (`AUTO` ou `/dev/cu.<nome>`), `paisagem`, `girar`, `porta_sim` (1024–65535) | ao reiniciar |
| `appearance` | `layout` (de ou para o `classico`, só ao reiniciar), `cor_mascote` | na hora |
| `appearance` | `intervalo` (0,05–10 s, ou `null` para o padrão do layout) | ao reiniciar |
| `multi_agent` | `vista`, `foco`, `rotacao`, `intervalo_rotacao` | na hora |
| `providers` | `providers.claude`, `providers.codex`, `providers.cursor` (`true`/`false`) | ao reiniciar |
| `hardware` | `sem_macmon` | ao reiniciar |
| `diagnostics` | `debug` | ao reiniciar |

Regras:

- Cada valor passa pela mesma validação do `config.json`; além disso, `porta` só aceita `AUTO` ou um dispositivo
  `/dev/cu.*`/`/dev/tty.*`, e pelo menos um agente precisa ficar ligado.
- Uma chave que só vale ao reiniciar precisa de `persist: true` (sem gravar, ela não mudaria nada): sem isso, o
  pedido é recusado com `VALIDATION_ERROR`.
- Ficam de fora, de propósito: `lib` e `claude_dir` (caminhos de pastas para ler: a IPC não aceita caminhos do
  cliente), `nao_abrir` (só faz sentido no terminal) e as ações de uma vez (`demo`, `demo_ciclo`, `png`).
- A resposta diz o que valeu na hora (`applied`), o que vale ao reiniciar (`restart_required`) e onde gravou
  (`file`). Os agentes vão para o bloco `providers` do arquivo (`{"codex": {"enabled": false}}`), e as opções do
  multiagente, para o bloco `multi_agent`, no mesmo formato que o README ensina.

A gravação muda só essas chaves (as outras, a ordem e o `versao` ficam), é atômica e é recusada se o
`config.json` não for um JSON válido. Opções dadas na linha de comando do ombrod vencem o arquivo no próximo
início, como sempre.

Instalar ou remover integrações e mexer no LaunchAgent **não** passa pela IPC: são mudanças em arquivos fora
do Ombro e ficam na CLI, que roda com o seu consentimento explícito.

## Saúde

Cada componente tem `status` e `state`. `unavailable` quer dizer "não se aplica agora" e não piora o estado geral.

| Componente | `healthy` | `degraded` | `unavailable` |
| --- | --- | --- | --- |
| `daemon` | rodando | | (`unhealthy` ao fechar) |
| `ipc` | ouvindo | não abriu (o ombrod segue sem ela) | |
| `storage` | `persistent` | `memory_only` (banco com problema) | `disabled` (`--demo`) |
| `display` | `connected` | `error`, `conflict` | `disconnected` (sem a tela) |
| `provider.<id>` | coleta ok | `partial`, `error` | `not_installed`, `not_configured`, `disabled` |
| `instance` | | `legacy_conflict` | |

O geral é `healthy`, `degraded` (algum componente `degraded`) ou `unhealthy` (o daemon fechando). Tela
desconectada ou agente não instalado não são falhas.

## Ciclo de vida (`status.daemon`)

`starting` → `running` ⇄ `degraded` → `stopping` → `stopped`. `degraded` = o laço roda, mas a saúde geral não é
`healthy`, ou um Ombro antigo (sem lock) está com a tela e o ombrod espera ele sair.

## Segurança

- A pasta `run/` é criada com `0700` e conferida: link simbólico, outro dono ou outro tipo são recusados.
- O socket nasce `0600` (umask no `bind`), e é conferido depois.
- O servidor confere o usuário de quem conecta (`LOCAL_PEERCRED` no macOS, `SO_PEERCRED` no Linux).
- Um socket que sobrou só é trocado com o lock de instância na mão (então ele é de uma instância morta) e se for
  mesmo um socket do usuário; um arquivo comum nesse caminho nunca é apagado. Ao fechar, o ombrod apaga o
  socket só se ainda for o que ele criou.
- O cliente também confere que o caminho é um socket do usuário antes de conectar.
- Nenhum método executa comandos, lê ou grava caminhos vindos do cliente, ou fala com os agentes.

Permissões de arquivo e a checagem do usuário protegem contra outros usuários da mesma máquina. Não protegem
contra programas que já rodam como você: eles poderiam ler os mesmos arquivos que o Ombro lê.

## Compatibilidade

- Campos novos podem aparecer em resultados da versão 1; quem consome deve ignorar o que não conhece.
- Mudar o significado de um campo, tirar um campo ou um método, ou mudar o formato das mensagens é a
  versão 2 do protocolo.
- `status.protocol` e `ping.protocol` dizem a versão; a CLI recusa conversar com outra.

## Exemplo com `nc`

```bash
printf '{"version":1,"method":"status"}\n' | nc -U ~/.ombro-screen/run/ombro.sock
```
