# Arquitetura do Ombro Screen

O Ombro lê o que as ferramentas de IA gravam no seu Mac e mostra numa telinha USB (ou no simulador).
Desde a Fase 2, os dados passam por um núcleo (`core/`) que não sabe nada da tela: ele fala de
**providers** (cada ferramenta monitorada), **sessões**, **limites de uso** e **eventos**. Hoje há um
provider só, o do Claude Code. Codex e Cursor ainda **não** são suportados.

## O caminho dos dados

```
Arquivos locais do Claude Code (~/.claude, ~/.claude.json, ~/.ombro-screen/uso.json)
        │  a cada 5 s
        ▼
coletores/claude.py        lê e interpreta os arquivos (a única parte que conhece o formato deles)
        ▼
providers/claude.py        ClaudeProvider: converte para o modelo do core ──► ProviderSnapshot
        ▼
core/state.py              StateStore: consolida, separa por provider, descobre o que mudou
        ├──► core/events.py        EventBus: avisa as mudanças ──► log de depuração
        │                                                     └──► core/persistence.py (SQLite)
        ▼
apresentacao.py            o estado do Claude no formato de sempre da tela (UsoClaude)
        ▼
mascote.py + layout_ombro.py / render.py      decidem o estado do cloud e desenham
        ▼
saida.py                   tela USB (direto ou serial) ou simulador HTTP
```

Antes da Fase 2, o `tela.py` chamava o coletor e entregava o resultado direto ao layout. Agora
`core/monitor.py` (o `Monitor`) junta registro, estado, eventos e banco, e o `tela.py` chama
`monitor.atualizar("claude")` no ritmo da coleta, nunca a cada quadro. O `--demo` passa pelo mesmo
caminho (`FonteDemo`), só que sem banco.

O núcleo (`core/`) usa só a biblioteca padrão: não importa Pillow, USB, numpy nem psutil (há um teste
que confere). A tela não conhece o formato dos arquivos do Claude, e um provider não conhece outro.

## Peças

| Peça | Arquivo | O que faz |
| --- | --- | --- |
| `ProviderAdapter` | `core/providers.py` | Contrato de um provider: `provider_id`, `display_name`, `capabilities` e `collect()`. `close()` é opcional. |
| `ProviderCapabilities` | `core/providers.py` | O que o provider informa: sessões, atenção, tokens, limites, contexto, custo, eventos. Falso = não informa. |
| `ProviderRegistry` | `core/providers.py` | Providers ativos, na ordem do registro. Id repetido ou inválido é erro. Uma coleta que levanta exceção vira um snapshot `ERROR` (registrado uma vez no log), para um provider com defeito não derrubar os outros. |
| `ClaudeProvider` | `providers/claude.py` | Usa o `ColetorClaude` de sempre e converte o resultado. Não lê arquivo nenhum por conta própria. |
| `AgentSession` | `core/models.py` | Uma sessão. Chave `(provider_id, session_id)`. Atividade (`unknown`, `idle`, `working`) separada de atenção (`unknown`, `none`, `waiting_user`). |
| `UsageMetrics` | `core/models.py` | Tokens num período (`scope`, hoje só `"today"`). `None` = não informado, nunca zero inventado. |
| `QuotaWindow` / `QuotaReport` | `core/models.py` | Janelas de limite genéricas (`window_id`, rótulo, %, quando reinicia) e de onde vieram (fonte, horário, se estão velhas). `quotas=None` = sem fonte; janelas vazias = fonte sem janelas. |
| `ProviderSnapshot` | `core/models.py` | O que um provider sabia num instante: sessões, uso, limites, `status` (`ok`, `partial`, `error`) e `failed` (as partes que falharam). |
| `AgentEvent` / `EventBus` | `core/events.py` | Mudanças (`session_started`, `session_changed`, `session_ended`, `provider_changed`) e o barramento local, síncrono e em ordem que as entrega. |
| `StateStore` | `core/state.py` | O estado consolidado: o último snapshot efetivo de cada provider e as sessões que ele conhece. |
| `StateDB` | `core/persistence.py` | O SQLite em `~/.ombro-screen/state.db`. |
| `Monitor` | `core/monitor.py` | Monta tudo e é o que o laço principal usa. |
| `uso_claude`, `status_tela` | `apresentacao.py` | Do estado normalizado para o formato da tela; `status_tela` é o mapa de compatibilidade (esperar você > trabalhando > ocioso; desconhecido aparece como ocioso). |

Os nomes das classes do núcleo estão em inglês, como na especificação da arquitetura, para quem vem
de outros projetos reconhecer; mensagens, comentários e documentação seguem em português, como o
resto do repositório. O id de um provider é um texto (`"claude"`), não um `Enum`: assim um provider
novo não precisa mexer no núcleo.

## Regras do estado (StateStore)

- Um snapshot igual ao anterior só atualiza o horário: nenhum evento, nenhuma gravação.
- Um snapshot mais velho que o último aplicado é ignorado: não apaga informação mais nova.
- Sessões começam, mudam (atividade ou atenção) e acabam; cada mudança vira um evento.
  Outras mudanças (o horário do status, por exemplo) atualizam o estado sem evento.
- **Falha parcial:** se uma parte da coleta falhou (por exemplo, a pasta de sessões ficou ilegível),
  o estado repete o último valor bom dessa parte por até 30 s e marca em `partes_velhas`. Depois
  disso, as sessões ficam com estado desconhecido, e não "encerradas", porque não se sabe se
  acabaram; uso e limites ficam sem dado. Antes da Fase 2, a parte que falhava aparecia vazia na
  hora: essa é a única mudança de comportamento, e só acontece quando algo dá errado.
- **Eventos** (`apply_event`, para providers que avisam na hora): um evento repetido não muda nada;
  um evento mais velho que o último aplicado à mesma sessão é ignorado; um evento de uma sessão já
  encerrada, com horário de antes do fim, também.

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
   "trabalhando" não é considerada trabalhando só porque estava assim quando o Ombro fechou.
4. Coleta o Claude Code.
5. Concilia: as sessões que continuam voltam com o estado da coleta (`session_changed`); as que
   sumiram são encerradas (`session_ended`). Nenhuma sessão é duplicada.

Para o Claude Code, que tem tudo em arquivos, isso pouco muda na tela: a primeira coleta acontece antes
do primeiro quadro. A recuperação existe para providers que só avisam por eventos e não têm um arquivo
para reler.

## Como adicionar um provider (Fase 3)

1. Crie `providers/<nome>.py` com uma classe que herda de `ProviderAdapter`:
   - `provider_id = "<nome>"` (minúsculas; vira parte da chave das sessões e do banco);
   - `capabilities = ProviderCapabilities(...)` só com o que a ferramenta realmente informa;
   - `collect()` devolvendo um `ProviderSnapshot` (use `status="partial"` e `failed={...}` quando uma
     parte falhar, em vez de devolver vazio como se não houvesse nada).
2. Se a ferramenta avisa mudanças na hora (hooks), entregue `AgentEvent`s ao `StateStore.apply_event`;
   as regras de repetição e atraso já valem.
3. Registre o provider onde o `Monitor` é montado (`tela.py`).
4. Testes: um snapshot com e sem dados, sessões simultâneas, partes que falham, ids estáveis entre coletas.

O que ainda falta para outro provider aparecer **na tela**: o layout atual mostra só o Claude
(`apresentacao.uso_claude`). Um layout multiagente vai ler o `StateStore` direto, de todos os providers.

## Limitações conhecidas

- Só o Claude Code tem provider. O `apply_event` existe e é testado, mas nenhum provider produz eventos ainda.
- Os horários são locais e sem fuso, como no resto do Ombro.
- O Claude Code não diz se uma sessão parada espera uma resposta ou uma permissão: as duas viram `waiting_user`.
- A ordem entre snapshots e eventos do mesmo provider é decidida pelo horário de cada um; não há relógio
  comum entre fontes diferentes.
