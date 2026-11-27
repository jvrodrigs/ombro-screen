# Vários agentes no Ombro

O Ombro acompanha o **Claude Code**, o **OpenAI Codex**, o **Cursor** e o **OpenCode** ao mesmo tempo, cada um
por um *provider* isolado: uma falha num não afeta os outros, e o Ombro funciona com um ou com os quatro. O
OpenCode entra por um plugin, não por hooks: ver [docs/providers/opencode.md](providers/opencode.md). Com quatro
agentes, as linhas da visão geral ficam mais juntas; com até três, a tela é a de sempre.

```
Claude Code ─ arquivos locais + hooks (opcional) ─┐
Codex ─────── hooks ──────────────────────────────┤  ombro_hook.py ─► ~/.ombro-screen/eventos/
Cursor ────── hooks ──────────────────────────────┘                          │
                                                                             ▼
   ClaudeProvider · CodexProvider · CursorProvider ◄── Monitor (a cada 5 s: eventos, depois coleta)
                                │
                                ▼
                StateStore (+ EventBus, SQLite)
                                │
                core/resumo.py ─┴─► layout multiagente (ou, só do Claude, os layouts de sempre)
                                │
                                ▼
                         USB / simulador
```

## Transporte dos eventos

Cada vez que um agente dispara um hook configurado, o `ombro_hook.py`:

1. lê o JSON do stdin (até 8 MiB; acima disso, descarta);
2. guarda **só** o provider, o id da sessão, o nome do evento, o horário, o nome curto da pasta e um status
   de uma lista fechada;
3. grava um arquivo de ~200 bytes em `~/.ombro-screen/eventos/`, com permissão `600`. O arquivo é escrito
   com um nome escondido e renomeado no fim, então nunca há arquivo pela metade, mesmo com vários hooks
   ao mesmo tempo;
4. sai com código 0, sem imprimir nada que mude a conversa (no Cursor, `{}`).

O Ombro relê a pasta a cada ciclo de coleta, só quando ela mudou, aplica os eventos em ordem e só então
apaga os arquivos. Com o Ombro desligado, os eventos esperam (até 5000; os com mais de 24 h são
descartados). Eventos repetidos, atrasados ou de sessões já encerradas são ignorados.

**Nunca são gravados:** prompts, respostas, comandos, conteúdo de arquivos, caminhos completos, e-mail,
mensagens de erro, variáveis de ambiente ou credenciais. O hook não usa rede nem roda outros programas.

## Sessões que somem sem avisar

Terminal fechado, Mac dormindo, um evento perdido: para quem só avisa por eventos (Codex e Cursor),
"trabalhando" sem notícia há 15 min vira "sem notícias" (não "ocioso": ninguém confirmou), e uma sessão sem
eventos há 4 h sai da tela. Ao reiniciar, as sessões voltam do banco com estado desconhecido até o próximo
evento confirmar.

## O agente dos layouts de um agente só

Os layouts do Ombro, Minimal v2 e clássico mostram **um** agente, escolhido para cada um (na página **Tela** do app,
em "Agente deste layout", ou no `config.json`):

```json
{"agente_auto": "automatic", "agente_classico": "codex", "agente_minimal": "opencode"}
```

- `agente_auto` vale para os layouts `auto` e `ombro` (são o mesmo desenho); o clássico e o Minimal v2 têm a deles.
  Sem a chave, o Claude: os quadros são os de sempre. Muda na hora, sem reiniciar.
- Valores: `claude`, `codex`, `cursor`, `opencode` ou `automatic` (não confundir com o *layout* `auto`).
- **`automatic`:** entre os agentes ligados com uma sessão que não esteja desconectada, o da sessão que mais precisa
  de você (erro, aprovação, espera), depois quem trabalha, depois a parada e a sem notícias; dentro de cada grupo, a
  atualizada há menos tempo; no empate, a ordem Claude, Codex, Cursor, OpenCode. Sem candidato, a tela diz que
  nenhum agente tem sessão: nada de antes fica, e o Claude não entra no lugar.
- **Sem troca silenciosa:** um agente desligado, não instalado, sem a integração, com a coleta falhando,
  desconectado ou sem sessões continua escolhido, e a tela diz a condição dele.
- Com um agente que não é o Claude, aparece só o que ele informa: condição, até três sessões, quantas há e quem
  espera ou parou com erro. Tokens e limites ficam como "—" ou "não informado", nunca zero. O Cursor não informa
  quando espera você, e a tela diz isso. Um erro aparece em texto; o mascote reage como sempre reagiu no Claude.
- O mascote passa a usar o avatar e a cor do agente mostrado (as preferências de sempre). Ao trocar de agente, ele
  esquece o que sabia do anterior: a troca não vira "concluído" nem "reset".
- Limitação: ligar ou desligar um agente só vale ao reiniciar o monitoramento.
- Nenhum agente, o Claude inclusive, "conclui" quando o trabalho some, fica sem notícias, sai do ar ou a coleta
  falha: o mascote só comemora um fim confirmado.

### O Claude no desenho de sempre

Com o Claude ligado e tudo confirmado (trabalhando, esperando, ocioso, com erro ou no limite, e nenhuma sessão sem
notícias), os quadros são **idênticos** aos de antes. O que muda é só o que não está confirmado:

| Situação | O que a tela diz |
| --- | --- |
| Alguma sessão sem notícias | ponto cinza; "há sessão sem notícias" embaixo; sem nada confirmado em andamento, o título vira "sem notícias" |
| Sem sessões | "sem sessões" / "nenhuma sessão aberta" |
| Coleta falhando, com limites ou tokens de antes | "coleta falhou"; sessões "—" |
| Dado de tokens ausente numa dessas situações | "—", nunca 0 |
| Não instalado, coleta falhando sem dado nenhum, ou desligado | a mesma tela de condição dos outros agentes |

Os limites de uso e os tokens do Claude continuam na tela quando existem; um limite no fim continua em alerta.

## O layout multiagente

```bash
python3 tela.py --layout multiagente                              # visão geral
python3 tela.py --layout multiagente --vista focus --foco codex   # um agente
python3 tela.py --layout multiagente --vista attention            # quem precisa de você
python3 tela.py --layout multiagente --demo --demo-ciclo 7        # com dados inventados, mudando
```

- **Visão geral:** uma linha por agente (estado, sessões e um detalhe), o total, CPU e RAM, as duas sessões
  mais relevantes e, no rodapé, a mensagem mais urgente.
- **Foco:** um agente, só com o que ele de fato informa. Claude: limites de uso, tokens e a curva por hora.
  Codex e Cursor: as sessões, e uma linha dizendo que tokens e limites não vêm deles (nada de barra vazia).
- **Atenção:** a sessão que mais precisa de você, em destaque. Sem nenhuma, mostra a visão geral.
- **Rotação** (`"auto_rotate": true`): visão geral → cada agente com sessões → visão geral, a cada
  `rotate_interval` segundos (mínimo 5). Enquanto alguém precisa de você, a tela fica parada na atenção.

Prioridade de atenção: erro > aguarda aprovação > aguarda você > limite de uso no fim > trabalhando > ocioso.
O cloud do layout multiagente reage a todos os agentes; nos layouts de um agente só, ao agente do layout.

Estados de um agente, todos com texto (não só cor): trabalhando, ocioso, aguarda você, aguarda aprovação,
parou com erro, limite no fim, sem notícias, sem sessões, **não configurado** (instalado, sem os hooks do
Ombro), **indisponível** (a coleta falhou) e **não instalado** (não aparece na tela). Só do OpenCode: **versão
sem suporte**, **origem em conflito**, **não comprovado**, **lendo sessões**, **sem o plugin** (nunca conectou) e **desconectado** (as sessões aparecem como "último: …").

## Configuração

Tudo opcional, em `~/.ombro-screen/config.json`:

```json
{
  "versao": 1,
  "layout": "multiagente",
  "providers": {"claude": {"enabled": true}, "codex": {"enabled": true}, "cursor": {"enabled": false}},
  "multi_agent": {"view": "overview", "focus_provider": null, "auto_rotate": false, "rotate_interval": 15}
}
```

Por padrão os quatro providers ficam ligados; um que não está instalado não aparece e não custa nada.
