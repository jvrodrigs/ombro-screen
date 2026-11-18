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
  JavaScript (pacote npm `mascotinhos`, versão fixada), com as mesmas animações da telinha: o pacote `vivo` para
  ocioso, trabalhando, esperando e concluído, e o `classico` para alerta e reset (`app/src/features/avatars/animacoes.ts`,
  o par do `PACOTE`/`DO_CLASSICO` do `mascote.py`). Ele também valida cada definição. Os cartões dos agentes e a
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
  (4x11 unidades vezes a escala, aumentados até 1,45x onde cabem, como no motor). O movimento e o desenho (os seis
  estados, os olhos, as piscadas e os extras) são os da biblioteca [mascotinhos](https://github.com/GilsonB12/mascotinhos):
  ocioso, trabalhando, esperando e concluído são os do pacote `vivo`, o da demonstração dela (dorme de olhos fechados
  com zzZ; trabalha de olhos semicerrados com linhas e engrenagem; espera com "?"; conclui feliz, com ✓ e faíscas);
  alerta e reset ainda são os do `classico` (os de antes), até passarem ao `vivo` numa etapa seguinte. A mistura de
  um estado no outro e a troca de avatar também são as da biblioteca (o `Animador`); o `mascote.py` só decide o
  estado e qual avatar e cor usar. Só a forma vem do avatar. Quando o corpo
  inclina (esperando, concluído), os olhos andam de lado como andavam no cloud antigo, cujos olhos ficavam mais
  altos: num avatar de olhos mais baixos, a diferença vira um deslize a mais (`ALTURA_OLHAR`).
- A telinha desenha o avatar plano (sem o volume do motor), como sempre desenhou o cloud: em 480x320, a cada
  0,2 s, um contorno chapado lê melhor e custa menos. A prévia do app mostra exatamente o que a telinha mostra.

Do open-bot-avatar, só o formato (o contrato v1). Os nove primeiros avatares foram desenhados para o Ombro (e
depois viraram as formas de exemplo do mascotinhos); **Bolota**, **Broto** e **Fantasma** vêm das formas de exemplo
do mascotinhos (MIT), copiadas sem mudança.

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
