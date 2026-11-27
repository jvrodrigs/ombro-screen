# OpenCode no Ombro

O Ombro acompanha o OpenCode por um **plugin de servidor** (V2) que está em `integrations/opencode/` e que
**você** liga na configuração do OpenCode. O Ombro não fala com o servidor do OpenCode e não lê nem grava a
configuração, o banco, o histórico, as conversas ou as credenciais dele.

## Versão

**OpenCode 2.4.2 ou mais novo** (mínimo provisório). O plugin confere a versão e as funções que usa
(`session.list`, `session.get`, `permission.list`, `event.subscribe`, `storage`). Com a 2.0.17, a 2.1.0 ou
qualquer versão sem isso, ele não coleta nada e o Ombro mostra **versão sem suporte**, com a orientação de
atualizar à mão. O Ombro nunca atualiza o OpenCode.

## Como ligar

```bash
python3 -m ombro integrations install opencode                # registra uma origem e mostra o trecho
python3 -m ombro integrations check opencode                  # o trecho de novo, e se o plugin já conectou
python3 -m ombro integrations install opencode --new-origin   # outra origem, para outra instalação ou uma cópia
python3 -m ombro integrations uninstall opencode              # revoga as origens
```

Cole o trecho na configuração do OpenCode, junto dos plugins que você já tem, e reinicie o OpenCode:

```json
{
  "plugins": [
    {
      "package": "/CAMINHO/ombro-screen/integrations/opencode",
      "options": { "origin": "oc-…" }
    }
  ]
}
```

Com `OMBRO_SCREEN_DIR`, o trecho leva também `"dir"`: o OpenCode não herda a variável do seu terminal. Para
desligar: `integrations uninstall opencode` e tire o trecho. A origem **revogada** nunca volta a ser aceita,
mesmo com o plugin ainda ligado; registrar de novo cria outra. No app, o mesmo está na página **Agentes**.

Só entra o que vem de uma origem que você registrou. Um trecho com origem inventada, revogada ou de um
registro antigo não é aceito: registre de novo.

## O que sai do OpenCode

Só metadados, em arquivos na pasta do Ombro (`0600`): ids de sessão, de evento e de pedido aberto, horários, o
tipo do evento e o **nome curto** da pasta do projeto. Nunca título, prompt, resposta, comando, conteúdo,
caminho completo ou credencial.

A API de plugins do OpenCode não restringe o que um plugin pode fazer: o contexto também dá acesso a
conversas, respostas de permissão e credenciais. Usar só o que está acima é uma garantia do **código do plugin**
(`integrations/opencode/server.js`), que você pode ler, e dos testes dele, não do OpenCode.

| Arquivo | Para quê |
| --- | --- |
| `eventos/*.json` | os eventos, na mesma caixa dos hooks |
| `opencode/live/<origem>.<pid>.json` | o pulso de cada processo (a cada 20 s): vivo, âncora, versão, coletando |
| `opencode/resync` | o Ombro o toca ao reiniciar; o plugin relê as sessões no pulso seguinte |
| `opencode/registry.json` | as origens registradas, a que cada uma está ligada e as revogadas |
| `opencode/state.json` | sequências, ordem, pedidos respondidos, remoções e eventos já vistos |
| `opencode/known/<origem>.json` | os ids que o Ombro conhece, para o plugin notar uma remoção perdida |

## Identidade

Uma sessão é `"<origem>:<ses_…>"`. A **origem** é o registro opaco do Ombro que você põe nas opções do plugin.
Processo, porta, projeto e o "boot" do plugin não fazem parte dela. O OpenCode cria uma instância do plugin por
projeto, e todas recebem os mesmos eventos: cada `evt_` conta uma vez por origem.

Na primeira conexão, o Ombro **liga a origem à instância local** por duas provas, que precisam vir juntas dali
em diante:

- a **âncora**: um resumo do inode e do instante de criação do arquivo do banco que aquele processo tem aberto;
- a **marca**: um valor que o plugin guarda no banco (`ctx.storage`).

A marca viaja numa cópia do banco; a âncora não. Uma cópia, um banco recriado ou restaurado, ou outra instalação
com o mesmo trecho, ao mesmo tempo ou depois (mesmo com o Ombro reiniciado), **não é aplicada**: aparece
**origem em conflito**. Cada instalação ou cópia independente precisa do seu registro (`--new-origin`, ou
remover e instalar em Agentes). Duas apresentando-se antes de haver vínculo deixam a origem inutilizada.

### Como a âncora é comprovada

O plugin monta o caminho como o OpenCode (`OPENCODE_DB`, ou `opencode.db` / `opencode-<canal>.db` na pasta de
dados) e só o aceita se um descritor que **o próprio processo já tem aberto** for o mesmo arquivo, usando apenas
`stat` e `fstat`. Ele **nunca abre, lê, duplica ou fecha** o banco: fechar um segundo descritor poderia soltar
travas POSIX do SQLite. Sem essa prova (banco em memória, caminho diferente do aberto, arquivo trocado com o
OpenCode no ar), nada é coletado e o Ombro mostra **não comprovado**.

### O que não dá para distinguir

- uma máquina ou um disco clonado inteiro, que preserva inode e instante de criação;
- o conteúdo de outro banco gravado por cima do mesmo arquivo;
- o caminho montado pelo plugin é o contrato observado no 2.4.2: se uma versão passar a guardar o banco em outro
  lugar, o resultado é "não comprovado", não uma associação errada.

O `ctx.storage` não tem escrita condicional: dois processos estreando juntos o mesmo banco podem criar duas
marcas, e o resultado é um conflito.

## Ordem, repetições e remoções

- Eventos de sessão seguem a **sequência durável** do OpenCode, não a ordem de chegada nem o relógio.
- Permissões, perguntas e releituras seguem a ordem dada pelo plugin, comparável só dentro do mesmo boot. Um
  boot encerrado não altera o que um vivo sustenta, e dois vivos não disputam esses eventos.
- Pedidos respondidos, remoções e eventos vistos ficam gravados: uma repetição, também depois de reiniciar,
  não muda nada. Uma sessão apagada só volta se o OpenCode a criar de novo.
- Nada avança antes de isso estar gravado. Se o Ombro não consegue gravar, os eventos esperam na caixa (os dos
  outros agentes continuam sendo entregues) e o estado fica como estava, marcado como incompleto.
- Uma remoção que se perdeu é percebida na próxima releitura, e só quando `session.get` responde que a sessão
  não existe. Estar fora de uma lista não remove nada.
- Um evento sem horário válido da fonte deixa a sessão **desconhecida**; o relógio do Ombro nunca entra no
  lugar dele.

## O que dá para ver

| Evento do OpenCode | Estado no Ombro |
| --- | --- |
| `session.created` | sessão aberta, ociosa |
| `session.execution.started`, `session.retry.scheduled` | trabalhando |
| `session.execution.succeeded` e `interrupted` | ociosa |
| `session.execution.failed` | parou com erro |
| `permission.asked` / `permission.replied` | esperando aprovação / deixou de esperar esse pedido |
| `form.created` / `form.replied`, `form.cancelled` | esperando você / deixou de esperar essa pergunta |
| `session.deleted` | sessão encerrada |

Sessões paradas há mais de 4 horas saem da lista (o histórico continua no OpenCode).

## Recuperação: quando o Ombro não sabe

Ao carregar, ao reconectar e quando o Ombro reinicia, o plugin assina os eventos **antes** de ler as sessões
recentes (até 10 páginas de 100). O que a leitura não prova fica **desconhecido**:

- "ociosa" só quando o último fim é posterior ao último início;
- uma permissão aberta aparece (vista pela instância do projeto da sessão); a falta dela não quer dizer nada;
- **perguntas pendentes e atividade em andamento não podem ser consultadas**: só os próximos eventos dizem;
- uma sessão que não veio na leitura não é encerrada; uma leitura com falha ou truncada é marcada como parcial;
- processo que sumiu, plugin parado ou fluxo perdido deixam "trabalhando" e "esperando" como desconhecidos,
  sem anunciar conclusão (nem no mascote da telinha, nem no app).

Eventos perdidos não são reenviados pelo OpenCode. Uma espera recuperada numa releitura aparece na tela e no
app, mas não gera notificação.

## Diagnóstico

O diagnóstico separa: desligado, não instalado, sem integração, registrado e nunca conectado, saudável,
conectado com sessões desconhecidas (recuperando), desconectado, leitura incompleta, falha de coleta, origem sem
registro, versão sem suporte, instância não comprovada e origem em conflito.

## Desconectado: o último estado conhecido

Quando o OpenCode de uma origem sai do ar, as sessões dela continuam na lista com o que se sabia, marcadas como
**último** ("último: ocioso", "último: erro", ou "sem notícias"). Elas não contam como sessões, trabalho, espera ou
erro de agora, não pedem você, não notificam e não animam o mascote como uma conclusão. A marca é **por origem**:
com duas instalações, uma pode sair do ar com a outra no ar, e só as sessões da primeira são marcadas. Sem nenhuma
no ar, o OpenCode aparece como **desconectado**, diferente de ocioso (confirmado agora), sem notícias (conectado,
ainda sem confirmação), sem sessões, sem o plugin (nunca conectou) e desligado.

Quando a origem volta, o pulso do plugin confirma que **ela** está no ar, não como cada sessão está: até um evento
ou uma releitura desse boot, a sessão fica **sem notícias** (e o estado anterior, erro inclusive, não volta como se
fosse de agora). Uma releitura confirma que a sessão está parada ou esperando uma permissão, não como terminou. Se a
origem cair de novo sem ter confirmado nada, o último estado conhecido volta a aparecer.

Um evento que só chega ao Ombro depois de o OpenCode fechar (um erro num `opencode run` curto, ou com o Ombro
parado) também entra como último estado, sem notificação.

## O que não dá

**Tokens, custo e limites de uso:** não aparecem (a semântica e o período não foram comprovados por este
caminho). Subagentes aparecem como sessões próprias.

## Validação

Com o binário **embarcado no OpenCode Desktop 2.4.2**, executado como servidor isolado (pastas temporárias,
só `127.0.0.1`, modelo falso no próprio processo, permissões e perguntas criadas pela API pública), o plugin e
o `ombrod` foram exercitados de ponta a ponta. **Não** foram testados: o aplicativo Desktop em si, o CLI numa
versão com suporte instalada pelo gerenciador de pacotes, um provedor de modelo real e permissões ou perguntas
disparadas por ferramentas reais.
