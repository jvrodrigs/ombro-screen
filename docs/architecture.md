# Arquitetura do Ombro Screen

O Ombro lê o que as ferramentas de IA gravam no seu Mac e mostra numa telinha USB (ou no simulador).
Os dados passam por um núcleo (`core/`) que não sabe nada da tela: ele fala de **providers** (cada
ferramenta monitorada: Claude Code, Codex e Cursor), **sessões**, **limites de uso** e **eventos**.

## O caminho dos dados

```
Claude Code: ~/.claude, ~/.claude.json, ~/.ombro-screen/uso.json ──► coletores/claude.py
Claude Code, Codex, Cursor: hooks ──► ombro_hook.py ──► ~/.ombro-screen/eventos/ ──► core/caixa.py
        │  a cada 5 s, Monitor.atualizar_todos(): entrega os eventos e coleta cada provider
        ▼
providers/claude.py · codex.py · cursor.py · opencode.py   convertem para o modelo do core ──► ProviderSnapshot
        (o OpenCode: um plugin em integrations/opencode/ grava eventos na mesma caixa, ver docs/providers/opencode.md)
        ▼
core/state.py              StateStore: consolida, separa por provider, descobre o que mudou
        ├──► core/events.py        EventBus: avisa as mudanças ──► log de depuração
        │                                                     └──► core/persistence.py (SQLite)
        ▼
apresentacao.py            o estado do Claude no formato dos layouts Ombro, Minimal v2 e clássico (UsoClaude)
core/resumo.py             o resumo de todos os agentes, para o layout multiagente
        ▼
mascote.py + layout_*.py / render.py      decidem o estado do cloud e desenham
        ▼
saida.py                   tela USB (direto ou serial) ou simulador HTTP
```

`core/monitor.py` (o `Monitor`) junta registro, estado, eventos e banco; o laço (`ombro/app.py`, o mesmo no
`tela.py` e no `ombrod`) chama `monitor.atualizar_todos()` no ritmo da coleta, nunca a cada quadro. O `--demo` passa pelo mesmo caminho
(`FonteDemo` e `providers/demo.py`), só que sem banco e sem eventos.

O núcleo (`core/`) usa só a biblioteca padrão: não importa Pillow, USB, numpy nem psutil (há um teste
que confere). A tela não conhece o formato dos arquivos de nenhum agente, e um provider não conhece outro.

## Peças

| Peça | Arquivo | O que faz |
| --- | --- | --- |
| `ProviderAdapter` | `core/providers.py` | Contrato de um provider: `provider_id`, `display_name`, `capabilities` e `collect()`. `close()` é opcional; `receber()` e `restaurar()`, para quem recebe eventos. |
| `ProviderCapabilities` | `core/providers.py` | O que o provider informa: sessões, atenção, tokens, limites, contexto, custo, eventos. Falso = não informa. |
| `ProviderRegistry` | `core/providers.py` | Providers ativos, na ordem do registro. Id repetido ou inválido é erro. Uma coleta que levanta exceção vira um snapshot `ERROR` (registrado uma vez no log), para um provider com defeito não derrubar os outros. |
| `ClaudeProvider` | `providers/claude.py` | Usa o `ColetorClaude` e converte o resultado; os hooks (opcionais) só refinam aprovação e erro. |
| `CodexProvider`, `CursorProvider` | `providers/codex.py`, `providers/cursor.py` | Montam as sessões a partir dos eventos dos hooks (base comum em `providers/hooks.py`). |
| `AgentSession` | `core/models.py` | Uma sessão. Chave `(provider_id, session_id)`. Atividade (`unknown`, `idle`, `working`) separada de atenção (`unknown`, `none`, `waiting_user`, `waiting_approval`, `error`). |
| `UsageMetrics` | `core/models.py` | Tokens num período (`scope`, hoje só `"today"`). `None` = não informado, nunca zero inventado. |
| `QuotaWindow` / `QuotaReport` | `core/models.py` | Janelas de limite genéricas (`window_id`, rótulo, %, quando reinicia) e de onde vieram (fonte, horário, se estão velhas). `quotas=None` = sem fonte; janelas vazias = fonte sem janelas. |
| `ProviderSnapshot` | `core/models.py` | O que um provider sabia num instante: sessões, uso, limites, `status` (`ok`, `partial`, `error`), `failed` (as partes que falharam) e `integration` (`ok`, `not_installed`, `not_configured`). |
| `AgentEvent` / `EventBus` | `core/events.py` | Mudanças (`session_started`, `session_changed`, `session_ended`, `provider_changed`) e o barramento local, síncrono e em ordem que as entrega. |
| `StateStore` | `core/state.py` | O estado consolidado: o último snapshot efetivo de cada provider e as sessões que ele conhece. |
| `StateDB` | `core/persistence.py` | O SQLite em `~/.ombro-screen/state.db`. |
| `CaixaDeEventos` | `core/caixa.py` | Lê, valida e apaga os eventos que os hooks deixam em `~/.ombro-screen/eventos/`. |
| `RastreadorDeSessoes` | `core/rastreador.py` | Sessões de quem só avisa por eventos, com as regras de atraso, repetição e tempo. |
| `resumir` | `core/resumo.py` | Quantos agentes trabalham e quem mais precisa de você (para o layout multiagente). |
| `Monitor` | `core/monitor.py` | Monta tudo e é o que o laço principal usa. |
| `uso_claude`, `status_tela`, `uso_mascote` | `apresentacao.py` | Do estado normalizado para o formato da tela; `status_tela` é o mapa de compatibilidade (esperar você ou aprovação > trabalhando > ocioso; desconhecido e erro aparecem como ocioso nos layouts de sempre). |

Os nomes das classes do núcleo estão em inglês, para quem vem de outros projetos reconhecer; mensagens,
comentários e documentação seguem em português, como o resto do repositório. O id de um provider é um
texto (`"claude"`), não um `Enum`: assim um provider novo não precisa mexer no núcleo.

## Regras do estado (StateStore)

- Um snapshot igual ao anterior só atualiza o horário: nenhum evento, nenhuma gravação.
- Um snapshot mais velho que o último aplicado é ignorado: não apaga informação mais nova.
- Sessões começam, mudam (atividade ou atenção) e acabam; cada mudança vira um evento.
  Outras mudanças (o horário do status, por exemplo) atualizam o estado sem evento.
- **Falha parcial:** se uma parte da coleta falhou (por exemplo, a pasta de sessões ficou ilegível),
  o estado repete o último valor bom dessa parte por até 30 s e marca em `partes_velhas`. Depois
  disso, as sessões ficam com estado desconhecido, e não "encerradas", porque não se sabe se
  acabaram; uso e limites ficam sem dado.
- **Eventos** (`apply_event`): um evento repetido não muda nada; um evento mais velho que o último
  aplicado à mesma sessão é ignorado; um evento de uma sessão já encerrada, com horário de antes do fim,
  também. Codex e Cursor usam as mesmas regras no `RastreadorDeSessoes` e entram no estado como
  snapshots, para todos os providers seguirem o mesmo caminho.

## Eventos dos hooks

`ombro_hook.py` grava cada evento num arquivo pequeno (só agente, sessão, evento, horário, nome da pasta
e um status de uma lista fechada) e sai sem bloquear o agente. O transporte, as regras de tempo das
sessões e a privacidade estão em [multi-agent.md](multi-agent.md); a configuração de cada agente, em
[providers/](providers/).

## Persistência (SQLite)

`~/.ombro-screen/state.db` (segue `OMBRO_SCREEN_DIR`), criado com permissão `600`.

| Tabela | Conteúdo |
| --- | --- |
| `providers` | último status da coleta de cada provider |
| `sessions` | `(provider_id, session_id)`, atividade, atenção, início, desde quando no status, última mudança vista, quando acabou |
| `events` | as mudanças recentes: tipo, sessão, horário, atividade, atenção |

- **Não guarda** nome de projeto ou pasta, conteúdo de conversas, prompts, respostas, tokens nem
  credenciais. O nome do projeto vem da fonte a cada coleta, sem custo, então não precisa ir para o disco.
- **Grava só quando algo muda** (um consumidor do EventBus), nunca a cada quadro.
- **Versão do formato** no `PRAGMA user_version`. Cada migração roda numa transação: se falhar,
  nada fica pela metade. Um banco de versão mais nova não é tocado.
- **Retenção:** eventos por 7 dias (no máximo 5000); sessões encerradas por 7 dias; sessões abertas
  sem notícia há 7 dias também saem. A limpeza roda ao abrir e depois no máximo uma vez por hora.
- **Falhas:** banco ilegível, corrompido, de versão mais nova ou num lugar sem permissão faz o Ombro
  seguir só em memória, com um aviso no log. Banco travado (outro processo usando) pula aquela
  gravação e tenta de novo na próxima mudança. O arquivo nunca é apagado: para começar do zero,
  mova `state.db` para outro lugar.
- `--demo` e `--png` não abrem o banco.

### Ao reiniciar

1. Abre o banco e põe o formato em dia.
2. Limpa o que passou do prazo.
3. Recupera as sessões que estavam abertas, **com estado desconhecido**: uma sessão salva como
   "trabalhando" não é considerada trabalhando só porque estava assim quando o Ombro fechou. Os
   providers que só avisam por eventos (Codex e Cursor) também recebem essas sessões.
4. Lê os eventos pendentes e coleta os providers.
5. Concilia: as sessões que continuam voltam com o estado da coleta (`session_changed`); as que
   sumiram são encerradas (`session_ended`). Nenhuma sessão é duplicada.

## Como adicionar um provider

1. Crie `providers/<nome>.py` com uma classe que herda de `ProviderAdapter`:
   - `provider_id = "<nome>"` (minúsculas; vira parte da chave das sessões e do banco);
   - `capabilities = ProviderCapabilities(...)` só com o que a ferramenta realmente informa;
   - `collect()` devolvendo um `ProviderSnapshot` (use `status="partial"` e `failed={...}` quando uma
     parte falhar, em vez de devolver vazio como se não houvesse nada).
2. Se a ferramenta só avisa por hooks, herde de `ProviderPorHooks` (`providers/hooks.py`) e escreva só o
   `mapear()` dos eventos; acrescente os eventos aceitos em `EVENTOS` do `ombro_hook.py`.
3. Acrescente o id em `PROVIDERS` (`config.py`), em `montar_providers` (`ombro/app.py`) e, se ele usa hooks,
   um `Alvo` em `ombro/integracoes.py` (onde fica a configuração e o formato da entrada).
4. Testes: um snapshot com e sem dados, sessões simultâneas, partes que falham, ids estáveis entre
   coletas, eventos repetidos e atrasados.

## Ombro Hub: o serviço local e a gerência

```
Claude Code ─┐
Codex ───────┤ arquivos e hooks ──► providers ──► EventBus / StateStore ──► SQLite
Cursor ──────┘                                          │
                                                        ▼
                                  ombro/app.py (Aplicacao): coleta, cloud, layout, saída ──► USB / simulador
                                         ▲                         │ publica a cada coleta
                     executar(fn) (fila) │                         ▼
                                  ombro/daemon.py (ombrod): lock, ciclo de vida, saúde, métodos
                                                        │
                                          ~/.ombro-screen/run/ombro.sock (IPC v1, docs/ipc.md)
                                                        │
                                  python3 -m ombro (ombro/cli.py) ··· futuro Ombro.app
```

| Peça | Arquivo | O que faz |
| --- | --- | --- |
| `Aplicacao` | `ombro/app.py` | O laço de sempre (era o `tela.rodar`), agora numa classe. A espera entre quadros é a fila de pedidos: `executar(fn)` roda `fn` no laço, entre dois quadros, sem trava. |
| `tela.py` | `tela.py` | Entrada de compatibilidade: as mesmas opções; `--png` e `--demo` sem lock nem IPC. |
| `Daemon` | `ombro/daemon.py` | O `ombrod`: lock, sinais, espera por um Ombro antigo, `Aplicacao`, IPC, saúde e os métodos. |
| `Trava` | `ombro/instancia.py` | Um Ombro ao vivo por usuário (`flock` em `run/ombro.lock`); o sistema solta numa queda. |
| `ServidorIPC`, `ClienteIPC` | `ombro/ipc.py` | O protocolo e os limites (docs/ipc.md). |
| CLI | `ombro/cli.py` | Cliente da IPC; integrações, serviço e doctor rodam localmente. |
| Integrações | `ombro/integracoes.py` | Hooks nos agentes, com backup e rollback (docs/integrations.md). |
| LaunchAgent | `ombro/launchagent.py` | `com.ombro-screen.daemon` e a migração do antigo. |
| Doctor | `ombro/doctor.py` | Diagnóstico não destrutivo (docs/troubleshooting.md). |

**Quem é "ao vivo":** o `ombrod` sempre, e o `tela.py` fora do `--demo` e do `--png` (ou com `--usb`). Ao vivo
quer dizer ler os eventos dos hooks, gravar o `state.db` e falar com a tela, então só um por vez: o segundo sai
com código 75 e diz quem está rodando. O lock é um `flock`, não um arquivo de PID: uma instância que caiu
(até com `kill -9`) não trava a próxima. Um `tela.py` de antes da Fase 4 não segura o lock; o `ombrod` o
encontra pelo nome do processo (psutil, só processos do usuário) e espera ele sair, em `degraded`, sem
disputar a tela nem mandar sinal a ninguém.

**Threads:** o laço continua no thread principal. A IPC tem uma thread de `accept` e uma por cliente (no
máximo 8). Os métodos de leitura usam dicionários que o laço troca inteiros a cada coleta (`publicado`), o
último quadro (`ultimo_quadro`) e atributos simples da saída; nada disso pede trava. Os métodos que mudam algo
vão para a fila do laço e esperam até 2 s.

**Encerramento:** SIGTERM ou SIGINT só marcam o pedido e acordam o laço. Na volta: `stopping`; recusa métodos
que mudam algo; libera pedidos pendentes; fecha a tela, o banco (as gravações são transações curtas no
próprio laço, então nunca ficam pela metade) e o leitor de hardware; fecha a IPC e apaga o socket se ainda for
o nosso; solta o lock; `stopped`. Agentes nunca recebem sinal.

**Transporte dos hooks:** continua o da Fase 3 (arquivos atômicos em `eventos/`, lidos pelo laço a cada
coleta). Comparado a mandar cada evento pelo socket, ele não depende de o ombrod estar no ar (os eventos
esperam, até 5000 e 24 h), não deixa o hook esperando um servidor, não precisa de um segundo caminho para
quando o socket não responde, e já tem as garantias de ordem, repetição e atraso. O custo é a latência de até
5 s (o ritmo da coleta), a mesma de antes.

## O mascote e os avatares

O `mascote.py` decide o estado do mascote (qual mostrar) e pede o avatar e a cor; o resto é da biblioteca
[mascotinhos](https://github.com/GilsonB12/mascotinhos), fixada em `requirements.txt` e, com o hash do wheel, em
`app/backend/requirements.lock`: o movimento e o desenho (os pulos, os olhos, os extras) do pacote de animações
escolhido (`vivo`, o padrão e o da demonstração dela, ou `classico`, nos seis estados), e o `Animador`, de quem o `Mascote` herda a
mistura de um estado no outro, a troca de avatar e o realce da cor. A forma vem de um avatar
(`avatars/`, no formato do open-bot-avatar), lido pelo `avatares.py`, que transforma o path em polígonos e decide
qual avatar e qual cor usar. A cada coleta, o laço (`ombro/app.py`) diz ao mascote de quem ele fala (o Claude nos
layouts de um agente; o agente mais urgente, o em foco ou o único ativo no multiagente) e o avatar dele entra na
próxima troca de estado. O Ombro.app desenha os mesmos arquivos com o mascotinhos para JavaScript, com as mesmas animações.

O pacote de animações tem um desejado (a preferência `pacote_animacao`) e um aplicado (o que o app e a telinha
mostram, gravado em `~/.ombro-screen/animacao.json`). A cada quadro, o laço pergunta a `ombro/animacoes.py` se a
troca pode acontecer: só com o Ombro já tendo lido os agentes, nenhuma sessão de nenhum agente em andamento
(trabalhando, esperando ou com erro), tudo quieto há 2 s e o mascote da telinha num ponto seguro. O aplicado vai
pela IPC (`animation`) e o app desenha com ele. Detalhes: [avatars.md](avatars.md#pacote-de-animações).

## Ombro.app: a interface gráfica

```
React (app/src) ──comandos Tauri tipados──► Rust (app/src-tauri)
                                              ├─ IPC v1 (socket Unix) ───────────► ombrod ──► core / providers / layout / USB
                                              └─ CLI com --json (python embarcado) ► integrações, LaunchAgent, doctor
```

- **Fonte da verdade:** o ombrod. O app não lê o banco, os arquivos dos agentes nem o `config.json`; a prévia da tela
  é o último quadro que o ombrod desenhou (`display.preview`), não um desenho refeito em HTML.
- **Preferências, um caminho só:** uma mudança sai do app (`useConfig`), passa pelo comando Tauri e pela IPC
  (`config.update`) e chega ao `Daemon._mudar_preferencias`, que valida, põe em uso no laço
  (`Aplicacao.aplicar_cfg`: brilho, orientação, layout, cores, avatares, visão), grava no `config.json` e responde
  com o que vale agora. O app mostra o que voltou na resposta, não o que foi clicado: `values` é o que está em uso,
  `pending` o que está gravado e só vale ao reiniciar. As mudanças saem em fila (uma de cada vez) e uma resposta
  mais velha que a última vista é descartada pela revisão. Layout, girar 180° e clássico deitado mudam na conexão
  USB que já está aberta; só o que escolhe a tela e os agentes pede reinício. Detalhes em [ipc.md](ipc.md).
- **Um laço só:** `monitor.rs` pede `status`, `providers.list`, `sessions.list` e `health` (a cada 3 s com a janela
  visível, 10 s escondida) e alimenta a janela (evento), a Menu Bar e as notificações. Com a janela escondida, a
  página não recebe nada; ao reabrir, recebe o retrato na hora.
- **Quem cuida do ombrod:** o app conversa com um que já roda; se o LaunchAgent existe, espera o launchd; senão,
  inicia um filho e o encerra ao sair (também com SIGTERM). Com o LaunchAgent de antes da Fase 4 instalado, não
  inicia nada e oferece a migração.
- **Empacotamento:** o app traz um CPython relocável e o código do Ombro pré-compilado (`Contents/Resources`); os
  hooks e o LaunchAgent apontam para eles, por isso o app só os instala estando em Aplicativos.
- Detalhes: [development.md](development.md) e [distribution.md](distribution.md).

## Limitações conhecidas

- Os horários são locais e sem fuso, como no resto do Ombro.
- Sem os hooks, o Claude Code não diz se uma sessão parada espera uma resposta ou uma aprovação: as duas
  viram `waiting_user`.
- A ordem entre snapshots e eventos do mesmo provider é decidida pelo horário de cada um; não há relógio
  comum entre fontes diferentes.
- Um `tela.py` de antes da Fase 4 não usa o lock: o `ombrod` o detecta pelo nome do processo, não pelo lock.
- O log do LaunchAgent não é girado (o launchd não faz isso).
