# Vários agentes no Ombro

O Ombro acompanha o **Claude Code**, o **OpenAI Codex** e o **Cursor** ao mesmo tempo, cada um por um
*provider* isolado: uma falha no Codex não afeta o Claude, e o Ombro funciona com um, dois ou os três.

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
O cloud do layout multiagente reage a todos os agentes; nos layouts de sempre, só ao Claude, como antes.

Estados de um agente, todos com texto (não só cor): trabalhando, ocioso, aguarda você, aguarda aprovação,
parou com erro, limite no fim, sem notícias, sem sessões, **não configurado** (instalado, sem os hooks do
Ombro), **indisponível** (a coleta falhou) e **não instalado** (não aparece na tela).

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

Por padrão os três providers ficam ligados; um que não está instalado não aparece e não custa nada.
