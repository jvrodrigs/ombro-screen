# Avatares

O mascote do Ombro é um avatar: o **Ombro Cloud** por padrão, e um por agente se você quiser. A telinha, a
prévia e o Ombro.app desenham os mesmos avatares, a partir dos mesmos arquivos.

<p align="center">
  <img src="avatares.png" width="760" alt="Os doze avatares do Ombro como a telinha os desenha, cada um num estado">
</p>

## De onde vem o formato

Os avatares seguem o formato do [open-bot-avatar](https://github.com/DominikScholz/open-bot-avatar) (MIT,
© Dominik Scholz), o contrato v1: **um path SVG numa caixa 0..100 e onde ficam os dois olhos**. Só geometria. A
cor, os olhos (pílulas que piscam e olham em volta), o volume e o movimento são do renderer.

- **No Ombro.app** o desenho e o movimento são os do [mascotinhos](https://github.com/GilsonB12/mascotinhos) para
  JavaScript (pacote npm `mascotinhos`, versão fixada), com as mesmas animações da telinha: o pacote de animações
  em uso (o `vivo`, o padrão, ou o `classico`; ver [Pacote de animações](#pacote-de-animações)) nos seis estados
  (`app/src/features/avatars/animacoes.ts`, o par do `PACOTES` do `mascote.py`). Ele também valida cada definição. Os cartões dos agentes e a
  prévia da galeria usam o player da biblioteca, animado pelo estado real de cada agente: a pose, os olhos, as
  piscadas, os extras e a mistura de um estado no outro são dela, e trocar de avatar ou de cor passa pela transição
  dela (o app não faz movimento em CSS). No app ele desenha a no máximo 30 quadros por segundo (a opção `fps` da
  biblioteca: as mesmas animações, com menos quadros; a telinha segue com a frequência dela). O player tem um laço só
  para a página, para quando o avatar sai da tela ou a janela fica escondida e respeita o "reduzir movimento" do
  macOS (fica parado na pose do estado). A lista de sessões e as miniaturas da galeria ficam paradas, num SVG da pose
  do estado, sem laço nenhum.
  Os avatares pequenos usam o enquadre `corpo` da biblioteca (a caixa justa no corpo); a prévia grande da galeria usa
  o `palco`, com o espaço dos extras dentro da caixa, para o zzZ, o "?" e o ✓ não serem cortados pelo diálogo. Os
  extras de texto usam a JetBrains Mono Bold de `fontes/` (a mesma da telinha), carregada localmente pelo app.
- **Na telinha** o desenho é um adaptador mínimo em Python (`avatares.py`): lê o mesmo JSON, transforma o path em
  polígonos (os comandos `M L H V C S Q T A Z`, a regra nonzero do SVG) e põe os olhos como o contrato descreve
  (4x11 unidades vezes a escala, aumentados até 1,45x onde cabem, como no motor). O leitor de path é uma cópia do
  `mascotinhos.formas`: o `avatares.py` fica só com a biblioteca padrão porque o `config`, o `doctor` e o
  `launchagent` o importam sem carregar o Pillow; um teste confere que as duas cópias dão os mesmos polígonos. O movimento e o desenho (os seis
  estados, os olhos, as piscadas e os extras) são os da biblioteca [mascotinhos](https://github.com/GilsonB12/mascotinhos):
  os seis estados são os do pacote de animações em uso: o `vivo`, o padrão e o da demonstração dela (dorme de olhos
  fechados com zzZ; trabalha de olhos semicerrados com linhas e engrenagem; espera com "?"; conclui feliz, com ✓ e
  faíscas; perto do limite, encolhe e treme vigiando uma bateria vermelha que pisca, suando; no reset, a bateria enche
  de novo e ele dá um pulo feliz com faíscas), ou o `classico` (os de antes: exatamente o motor antigo do Ombro, nos
  seis estados, com a gota no alerta e o confete no reset). A mistura de
  um estado no outro e a troca de avatar também são as da biblioteca (o `Animador`); o `mascote.py` só decide o
  estado e qual avatar e cor usar. Só a forma vem do avatar. Quando o corpo
  inclina (esperando, concluído), os olhos andam de lado como andavam no cloud antigo, cujos olhos ficavam mais
  altos: num avatar de olhos mais baixos, a diferença vira um deslize a mais (`ALTURA_OLHAR`).
- A telinha desenha o avatar plano (sem o volume do motor), como sempre desenhou o cloud: em 480x320, a cada
  0,2 s, um contorno chapado lê melhor e custa menos. A prévia do app mostra exatamente o que a telinha mostra.

Do open-bot-avatar, só o formato (o contrato v1). Os nove primeiros avatares foram desenhados para o Ombro (e
depois viraram as formas de exemplo do mascotinhos); **Bolota**, **Broto** e **Fantasma** vêm das formas de exemplo
do mascotinhos (MIT), copiadas sem mudança.

## Pacote de animações

Uma escolha só, em **Aparência → Animações** (ou `pacote_animacao` no `config.json` e na IPC), para todos os agentes,
no app e na telinha; os avatares e as cores continuam por agente.

| Pacote | O que é |
| --- | --- |
| `vivo` (padrão) | O da demonstração do mascotinhos: olhos que mudam com o estado, zzZ, engrenagem, "?", ✓ com faíscas, uma bateria vermelha perto do limite e a bateria enchendo no reset. |
| `classico` | Os movimentos de antes (o motor antigo do Ombro), mais contidos: pulinhos, zZ, um ✓ simples, a gota perto do limite e o confete no reset. |

O pacote vale para os **seis** estados, alerta e reset inclusive. As regras do Ombro continuam as mesmas nos dois:
a prioridade dos estados (esperando > alerta > trabalhando > concluído > ocioso, com o reset por cima), o reset por 3 s
(a história do reset do `vivo`, da bateria enchendo ao pouso, cabe nesse tempo; um teste confere) e os alertas de
cota, que são do layout e do estado, não do pacote. A prévia em **Aparência → Animações** mostra os seis.

O reset é igual no app e na telinha: começa quando o horário de reset de uma janela (5 h ou semana) avança mais de
1 h entre duas leituras seguidas e dura 3 s, sem recomeçar a cada leitura. Se o trabalho termina junto (o caso da
demonstração: do alerta com uma sessão trabalhando para parado), o reset aparece inteiro e o concluído vem depois,
pelo prazo dele contado do fim do trabalho. Uma janela que some (a fonte caiu, o ombrod reiniciou, o app reabriu) é
esquecida: a volta dela não reapresenta um reset antigo.

Os dois desenham chapado, como a telinha sempre desenhou. A biblioteca tem mais dois pacotes, que o Ombro ainda não
oferece (o app mostra "em avaliação"). Os dois funcionam no mascotinhos 0.2.0, em Python e em JavaScript; o que falta
é do Ombro:

- **3D** (`3d`, com luz e volume): lê bem na coluna do mascote e nos layouts menores e mantém a cor de cada agente.
  Mas ele muda o **desenho** do corpo, não só o movimento: o Ombro troca de pacote misturando a pose e cruzando os
  extras, e o corpo chapado viraria o corpo com volume de uma vez, num salto (no app, o player desenha uma camada só e
  não cruza dois desenhos). O volume do player JavaScript também é uma aproximação do de Python (gradientes), e
  ainda não foi conferido na tela USB. Custo: ~2 ms por quadro em Python (o `vivo`, ~0,3 ms) e um SVG ~2x maior no app.
- **Terminal** (`terminal`, em células de letra): troca a cor de cada agente pelo verde de fósforo (os agentes ficam
  iguais), as legendas dele ("> trabalhando", "confirma? [s/n]") são cortadas no tamanho dos layouts menores e
  repetem o que o layout já escreve, e o alerta mostra um limite fixo ("! LIMITE 92%") se o Ombro não passar a cota
  real. Custo: ~2,8 ms por quadro em Python e um SVG ~5x maior no app (~15x mais lento para gerar).

Para oferecer um deles, o Ombro precisaria trocar o estilo junto com o pacote (telinha e app) sem salto, passar os
dados que o pacote escreve (a cota, no Terminal) e validar na tela USB. Até lá, ficam fora da escolha.

**A troca nunca corta o que o mascote está mostrando.** Quem decide é o ombrod (`ombro/animacoes.py`):

- sem atividade, a escolha entra na hora (no app e na telinha, sem reiniciar nada);
- com algum agente trabalhando, esperando você (resposta ou aprovação) ou com erro, ela fica **pendente** e o pacote de
  antes segue animando; entra quando **todos** os agentes ligados param, seja qual for o layout ou o cartão em foco;
- depois de um concluído, espera 2 s (a comemoração: pulo, ✓ se desenhando, faíscas) e um ponto seguro do mascote
  da telinha (ocioso ou alerta assentados; nunca no meio de trabalhando, esperando ou reset);
- várias escolhas seguidas: só a última conta (nada enfileira); voltar à que está em uso cancela a troca;
- a troca em si é a mesma mistura de 0,5 s que a biblioteca faz entre dois estados: a pose do pacote de antes vira a
  do novo no mesmo instante do estado (nada recomeça, o concluído não comemora de novo). Só a pose é misturada, com o
  `misturar` da biblioteca; os movimentos, olhos e extras são os dela. Na telinha, os olhos e os extras dos dois
  pacotes se cruzam sobre o mesmo corpo (os de antes esmaecem enquanto os novos aparecem, desenhados pelo
  `desenhar` da biblioteca), então o ✓, o zzZ ou o "?" nunca somem; no app, o player desenha uma camada só, e os
  olhos e extras do novo entram inteiros no começo da mistura (sem surgir do zero, para não piscarem);
- o pacote em uso é gravado à parte, em `~/.ombro-screen/animacao.json` (`{"versao": 1, "aplicado": "vivo"}`): ao
  reiniciar, o mascote volta no que estava em uso e uma escolha pendente espera um momento seguro, mesmo que a
  atividade tenha continuado. Sem o arquivo (a primeira vez), vale o `vivo`, o de antes desta opção;
- um pacote que não carrega não derruba nada: fica o em uso, e o app avisa.

No app, cada mascote animado passa para o pacote em uso no próximo ponto seguro dele (o mesmo critério); a prévia do
cartão Animações troca na hora, para mostrar a escolha. Os avatares parados (lista de sessões, miniaturas) trocam na
hora, porque não há movimento a interromper.

## A coleção

| Avatar | Padrão de | Ideia |
| --- | --- | --- |
| **Ombro Cloud** | Claude Code, e o Ombro sem agente em foco | O personagem do Ombro: uma nuvem que senta numa base macia, com a cúpula puxada para um lado |
| **Sereno** | | Hexágono de cantos macios: calmo, técnico |
| **Órbita** | Codex | Planeta com um anel inclinado: objetivo, analítico |
| **Cubo** | Cursor | Bloco com um canto dobrado, como uma página: quem constrói |
| **Scout** | | Gota de mapa: explorar, pesquisar |
| **Pixel** | | Cantos em degrau: interface e design |
| **Chevron** | | Escudo com a ponta em chevron: backend e sistemas |
| **Maestro** | | Engrenagem macia de oito lobos: coordenar |
| **Nova** | | Estrela cheinha: neutra, futurista |
| **Bolota** | | Bolinha macia e um pouco torta: simples, simpática |
| **Broto** | | Bolinha com um broto de folhas no alto: aprende e cresce |
| **Fantasma** | | Fantasminha de barra ondulada: discreto, em segundo plano |

Os doze passam no validador do mascotinhos (o app recusa o que não passa) e os nove primeiros também no validador
estrito do open-bot-avatar (rosto com espaço para os olhos grandes e para o olhar ir até o fim; contorno sem emendas
pequenas). Todos entram no sorteio do "aleatório".

O **Ombro Cloud** substitui o contorno antigo do cloud, que tinha sido medido num desenho de terceiros. O novo foi
desenhado do zero (outro contorno, outros olhos, as pílulas do contrato do open-bot-avatar), mantendo só o papel
dele no produto: um companheiro simpático que lê bem pequeno e no fundo escuro.

## Escolher

No app: **Aparência > Avatares**. No `config.json`:

```json
{
  "avatars": { "claude": "sereno", "codex": "aleatorio", "default": "ombro-cloud", "random": "sessao" },
  "cor_modo": "agente"
}
```

| Chave | Valores | O que é |
| --- | --- | --- |
| `claude`, `codex`, `cursor` | `"padrao"` (o padrão), `"aleatorio"` ou o id de um avatar | o avatar de cada agente |
| `default` | os mesmos | o do Ombro, quando nenhum agente está em foco no multiagente |
| `random` | `"inicio"` (padrão) ou `"sessao"` | como o `"aleatorio"` escolhe |

- **Padrão:** o do catálogo (`avatars/catalog.json`): Claude → Ombro Cloud, Codex → Órbita, Cursor → Cubo.
- **Aleatório ao iniciar:** um avatar sorteado quando o Ombro inicia, o mesmo até reiniciar.
- **Aleatório por sessão:** cada sessão ganha o seu, sempre o mesmo para ela (o hash FNV-1a de `agente:sessão`).
  A telinha e o app fazem a mesma conta.
- Só entram no sorteio os avatares com `"random": true` no catálogo.
- **Um avatar que não existe** (apagado do catálogo, ou digitado errado no arquivo) vale o padrão do agente,
  com um aviso no log: nunca impede o Ombro de abrir.

## De quem é o mascote agora

- Layouts do Ombro e Minimal v2: o Claude Code (e, no aleatório por sessão, a sessão que espera você, senão a
  que trabalha, senão a mais recente).
- Multiagente: o agente mais urgente (erro, aprovação, esperando você); senão o da visão em foco; senão o único
  que trabalha, ou o único com sessões; senão o Ombro (`default`).
- Alerta e reset vêm dos limites do Claude: nesses estados o mascote é o do Claude, mesmo no multiagente.
- Quando muda de quem é o mascote, a troca leva 0,6 s: o corpo encolhe e fecha os olhos, a forma (e a cor) muda no
  meio e ele volta. O movimento do estado continua sem pular. Uma mudança feita nos ajustes vale na hora.

`display.status.mascot` (IPC) diz qual avatar, de qual agente e em qual estado.

## Cores

`cor_modo`: `"agente"` (padrão) usa a cor de cada agente (Claude laranja, Codex verde, Cursor azul; o Ombro, a do
avatar), e a cor clareia por meio segundo a cada troca de estado, para a troca continuar visível; `"sorteada"` sorteia
uma cor a cada troca de estado (o comportamento de antes). `cor_mascote` (uma cor
fixa, `#RRGGBB`) vale por cima dos dois.

### Cor do corpo por avatar

Cada avatar pode ter a sua cor, por **id** (a cor é do avatar, não do agente: no "aleatório", o avatar que sai leva a
dele). Em **Aparência > Avatares > Escolher…**, embaixo da prévia: **Padrão**, **Personalizada** (um seletor e o campo
`#RRGGBB`, com a amostra) ou **RGB**. A prévia mostra o rascunho assim que ele é válido; **Aplicar** grava (persistente)
e **Usar a cor global** remove a entrada. No `config.json`:

```json
{ "cores_avatares": { "sereno": "#3D7A4F", "cubo": "rgb", "pixel": "default" } }
```

| Valor | O que é |
| --- | --- |
| `"default"` | a cor original (a do agente, ou a do avatar), por cima do `cor_mascote` e do `cor_modo` |
| `"#RRGGBB"` | essa cor, parada (com ou sem `#`, em qualquer caixa; é gravada como `#RRGGBB`) |
| `"rgb"` | o corpo passa por todas as cores, uma volta a cada 12 s |
| sem entrada (ou `null`) | o avatar herda o que valia antes: `cor_mascote` e `cor_modo`. Pela IPC, `null` remove a entrada |

- **Só o corpo muda**: a forma, os olhos, os extras (zzZ, "?", ✓), a transparência, as poses e as transições de forma e
  de pacote são os da biblioteca, nos doze avatares e nos dois pacotes.
- **Vale a cor do avatar realmente desenhado**: numa troca de avatar, a cor (e o ciclo) do destino entra no ponto médio,
  junto com a forma, na telinha e no app. Mudar a cor de qualquer id, inclusive do destino, nunca corta a transição em
  andamento. Uma entrada explícita não tem o realce de meio segundo da troca de estado: `"default"` mantém a cor-base em
  todo quadro, sem pulso, na telinha e no app; sem entrada, vale o caminho de antes (com o realce).
- **O ciclo `rgb`** é função só de um relógio monotônico (a mesma fase para todos os avatares, sem recomeçar a cada
  estado), nos quadros que já são desenhados: no app, nos do player; na telinha, nos dela. Luminosidade 0,58 e saturação
  0,78, para ler bem sobre o fundo escuro (`mascote.cor_do_ciclo` e `cores.tsx` dão os mesmos valores).
- **Reduzir movimento**: no app, a media query `prefers-reduced-motion` do WebView; na telinha, o ombrod lê a mesma
  preferência do macOS sozinho (`NSWorkspace.accessibilityDisplayShouldReduceMotion`, `ombro/movimento.py`, com `ctypes`,
  guardada por 5 s e só consultada quando há um avatar em `rgb`), com o app fechado. Ligada, ou sem como ler, o `rgb`
  fica parado na cor original; o `rgb` salvo continua no arquivo.
- O PNG (`--png`) também respeita o "reduzir movimento": o quadro final sai na cor original se ele estiver ligado.
- `providers.list` (`avatar_colors`) e `config.get` publicam só o estado **confirmado**, o mesmo para os dois: a
  configuração, a revisão e o pendente saem juntos de uma referência trocada de uma vez ao fim de um `config.update` bem
  sucedido. Durante o update (pôr em uso, gravar e, se falhar, desfazer) nenhum leitor vê o valor provisório nem um mapa
  com a revisão de outro estado; os leitores não esperam por trava.
- As miniaturas e a lista de sessões do app ficam paradas (sem laço): no `rgb`, mostram a cor original.
- Um id que não está no catálogo é aceito no arquivo (com um aviso) e ignorado; pela IPC só os avatares do catálogo.

## Adicionar um avatar

1. Desenhe no [editor do open-bot-avatar](https://dominikscholz.github.io/open-bot-avatar/) ou à mão: um path na
   caixa 0..100 (de preferência dentro de 8..92) e os olhos `{x, y, spacing, scale}`.
2. Salve em `avatars/defs/<id>.json`, só com os campos do contrato (`version`, `id`, `name`, `body`, `eyes`).
3. Confira no validador do mascotinhos (o app usa o `validarDefinicao` dele) e, se quiser o rigor de antes, no
   validador estrito do open-bot-avatar (do repositório dele):
   `node packages/core/scripts/avatar-validate.ts --strict avatars/defs/<id>.json --svg /tmp/<id>.svg`.
4. Acrescente em `avatars/catalog.json`: `id`, `description`, `tags`, `color` (`#RRGGBB`) e `random`.
5. Rode os testes (`python3 -m unittest tests.test_avatares` e, em `app/`, `npm test`) e veja como fica:
   `.venv/bin/python scripts/imagens_readme.py` refaz o `docs/avatares.png`.

Para mudar o padrão de um agente, troque o `avatar` (e a `color`) dele em `providers` no `catalog.json`.

## Licença

O formato é o do open-bot-avatar (MIT, © Dominik Scholz), citado aqui e em [licenses.md](licenses.md); o código
dele não vai mais no Ombro.app. Os avatares em `avatars/` seguem a licença do projeto (Bolota, Broto e Fantasma, a do
mascotinhos, MIT). O mascotinhos, o desenho e o movimento na telinha e no app (MIT, com o NOTICE das partes que
vieram do Ombro e a OFL da fonte que ele traz), vai nos avisos de terceiros.
