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
providers/claude.py · codex.py · cursor.py       convertem para o modelo do core ──► ProviderSnapshot
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

`core/monitor.py` (o `Monitor`) junta registro, estado, eventos e banco; o `tela.py` chama
`monitor.atualizar_todos()` no ritmo da coleta, nunca a cada quadro. O `--demo` passa pelo mesmo caminho
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
3. Acrescente o id em `PROVIDERS` (`config.py`) e em `montar_providers` (`tela.py`).
4. Testes: um snapshot com e sem dados, sessões simultâneas, partes que falham, ids estáveis entre
   coletas, eventos repetidos e atrasados.

## Limitações conhecidas

- Os horários são locais e sem fuso, como no resto do Ombro.
- Sem os hooks, o Claude Code não diz se uma sessão parada espera uma resposta ou uma aprovação: as duas
  viram `waiting_user`.
- A ordem entre snapshots e eventos do mesmo provider é decidida pelo horário de cada um; não há relógio
  comum entre fontes diferentes.
