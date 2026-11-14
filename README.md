<p align="center">
  <img src="docs/icon.png" width="128" height="128" alt="Ombro Screen">
</p>

<h1 align="center">Ombro Screen</h1>
<p align="center">O uso do Claude Code e do seu Mac numa telinha USB, com um cloud que reage ao que o Claude está fazendo.</p>

<p align="center">
  <img src="docs/tela.png" width="480" alt="A tela do Ombro Screen, com dados de demonstração">
</p>

---

O uso do Claude Code, o CPU e a RAM do Mac numa telinha USB de 3,5" deitada (480×320), com um mascote, o **cloud**, que reage ao que o Claude está fazendo. A imagem acima usa dados de demonstração (`--demo`).

- **Uso do plano**: sessão de 5 h e semana, com o horário em que cada uma reinicia
- **Hoje**: tokens gastos, tokens de saída, sessões abertas (uma bolinha por sessão) e a curva de tokens por hora
- **Hardware**: CPU e RAM
- **Aviso**: qual projeto espera você e há quanto tempo (ou "nenhuma sessão esperando")
- **Cloud**: ocioso, trabalhando, esperando, alerta, reset ou concluído, cada um com a sua animação

Tudo sai de arquivos locais: não usa a API nem lê credenciais.

## 1. Instalar

O Ombro Screen **precisa da biblioteca [turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)** (GPL-3.0) para falar com a telinha USB. Ela **não vem neste repositório**: clone-a dentro da pasta do projeto, com o nome `turing-smart-screen-python` (é onde o `tela.py` a procura; use `--lib` para outro caminho):

```bash
git clone https://github.com/jvrodrigs/ombro-screen.git ombro-screen
cd ombro-screen
git clone --depth 1 --filter=blob:none --sparse https://github.com/mathoudebine/turing-smart-screen-python.git
git -C turing-smart-screen-python sparse-checkout set library
```

O Ombro só usa a pasta `library/` da biblioteca. Baixar só ela leva cerca de 1 MB; o repositório inteiro passa de 1,9 GB por causa dos temas em `res/`. Um `git clone` comum também funciona.

Só o simulador (sem `--usb`) funciona mesmo sem ela.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # Pillow, psutil, pyserial e numpy (a biblioteca da tela usa o numpy)
brew install macmon                 # opcional: uso de CPU por tipo de núcleo no Apple Silicon (sem sudo)
brew install libusb                 # no Mac: necessário para a tela revisão A mostrar a imagem certa (seção 5)
```

Funciona do Python 3.9 (o `python3` que vem com o macOS) ao 3.14. Se o `python3` do seu terminal for o do sistema, prefira o do Homebrew: `/opt/homebrew/bin/python3 -m venv .venv`.

Sem o `macmon` a tela funciona: CPU e RAM vêm do `psutil`. O `macmon` só é necessário para GPU, temperatura e consumo, que aparecem apenas no layout clássico (`--layout classico`).

## 2. Ver no simulador

```bash
python3 tela.py                          # seus dados de verdade
python3 tela.py --demo                   # dados inventados (o cloud fica em "esperando")
python3 tela.py --demo --demo-ciclo 7    # o cloud passa por todos os estados, 7 s cada
```

Abre `http://localhost:5680` no navegador. Para salvar um quadro: `python3 tela.py --png quadro.png`.

O layout do Ombro é o padrão e é sempre 480×320. O painel antigo (anéis, GPU, rede, disco) continua disponível com `--layout classico`; nele, `--paisagem` deita a tela.

## 3. Os estados do cloud

O estado vem do que o Claude Code grava, na ordem de prioridade abaixo:

| Estado | Quando | Dado usado |
| --- | --- | --- |
| **reset** | a janela de 5 h ou da semana reiniciou | o horário de reset avançou mais de 1 h |
| **esperando** | alguma sessão espera sua resposta ou permissão | `status: waiting` em `~/.claude/sessions` |
| **alerta** | o uso do plano passou de 90 % | `% de uso` da sessão de 5 h ou da semana |
| **trabalhando** | alguma sessão está ocupada | `status: busy` em `~/.claude/sessions` |
| **concluído** | o Claude terminou o que fazia, há menos de 5 min | as sessões saíram de `busy` sem passar a esperar você |
| **ocioso** | nada acima, há mais de 3 s | |

O **concluído** começa com um pulinho e um ✓ acima da cabeça, com olhos em arco (^ ^), e depois só respira devagar. Ele dura até 5 min; se nesse tempo o Claude voltar a trabalhar (outra mensagem, na mesma sessão ou em outra), o cloud volta para trabalhando, e os 5 min recomeçam quando ele terminar de novo. Passados os 5 min sem nada, o cloud volta a dormir (ocioso, com o zZ). Uma sessão que esperava você e foi cancelada não conta como concluída. No `--demo-ciclo`, o concluído dura um cenário em vez de 5 min.

A cada troca de estado, o cloud ganha uma cor sorteada (matiz livre, saturação e luminosidade limitadas para ler bem sobre o fundo escuro). Para fixar uma cor: `--cor-mascote "#3D7A4F"`.

A forma do cloud e os olhos vêm da KEY de formas dos avatares do Grok Bot; os pontos do contorno estão em `dados_cloud.py`.

## 4. De onde vem o uso de 5 h e da semana

A tela usa a fonte mais recente entre as três abaixo:

1. `~/.ombro-screen/uso.json`, gravado pelo `statusline_tap.py` (ao vivo; opcional)
2. `~/.habblaud/usage/<conta>.json`, se o Habblaud estiver instalado (opcional)
3. o que o Claude Code guarda ao rodar `/usage` (só atualiza quando você roda o comando)

Números com mais de 30 min aparecem em âmbar como "números de N min atrás". Uma janela cujo horário de reset já passou é descartada.

### Sem o Habblaud

O Habblaud é opcional: sem ele, a tela cai sozinha para a próxima fonte disponível. Você tem duas saídas:

1. **Não fazer nada.** Rode `/usage` no Claude Code de vez em quando. O Claude Code guarda o resultado em `~/.claude.json` e a tela o usa. Os números só atualizam quando você roda o comando, então depois de 30 min aparecem em âmbar como "números de N min atrás".
2. **Ligar o `statusline_tap.py`** (próxima seção). Os números passam a chegar ao vivo, a cada resposta do Claude Code, sem precisar do Habblaud nem do `/usage`.

Sem nenhuma das fontes, o uso do plano aparece como "—"; o resto da tela (tokens de hoje, CPU, RAM, sessões e o cloud) funciona normalmente.

### Ligar o `statusline_tap.py` (opcional)

O Claude Code envia os percentuais ao comando de **statusline**; o tap grava só esses números. Isso altera o `~/.claude/settings.json`, então faça um backup antes:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /CAMINHO/ombro-screen/statusline_tap.py"
  }
}
```

Já tem um statusline? Coloque o comando dele depois de `--` e ele continua igual (teste antes de confiar):

```json
"command": "python3 /CAMINHO/ombro-screen/statusline_tap.py -- npx -y ccstatusline"
```

Os números chegam depois da próxima resposta do Claude Code.

## 5. Usar a tela de verdade

```bash
ls /dev/cu.*                                  # a tela aparece como usbmodem
python3 tela.py --usb --revisao A
```

**No Mac, a revisão A vai direto pelo USB.** Pela porta serial, o macOS reagrupa os bytes e o firmware desta tela se perde: a imagem vira faixas e ruído azul que piscam. É um problema conhecido da biblioteca ([#7](https://github.com/mathoudebine/turing-smart-screen-python/issues/7), [#512](https://github.com/mathoudebine/turing-smart-screen-python/issues/512)) que ajustes na serial não resolvem. Por isso, no Mac com a revisão A, o Ombro escreve direto no endpoint USB da tela (`usb_direto.py`), sem a porta serial e sem a biblioteca da tela. Precisa do `pyusb` (já está no `requirements.txt`) e do `brew install libusb`.

- `--transporte auto` (padrão): USB direto no Mac com a revisão A; porta serial nos outros casos.
- `--transporte usb` força o USB direto; `--transporte serial` força a porta serial da biblioteca.
- **A tela ficou embaralhada?** Depois que ela se perde, nem o comando de reset chega: tire o cabo, espere uns segundos e plugue de novo. Acontece, por exemplo, se outro programa (ou o Ombro com `--transporte serial`) mandou imagem pela serial.

- `--revisao`: A (Turing 3.5" e UsbPCMonitor), B (XuanFang), C (Turing 5"), D (Kipye Qiye) ou WEACT_A.
  Para descobrir a sua: <https://github.com/mathoudebine/turing-smart-screen-python/wiki/Hardware-revisions>.
  A tela com serial `USB35INCHIPSV2` é a revisão A.
- `--porta /dev/cu.usbmodemXXXX` se a detecção automática falhar (na revisão A ela costuma ser `/dev/cu.usbmodemUSB35INCHIPSV21`).
- `--girar` se ela aparecer de cabeça para baixo no case.
- `--brilho 30` é o padrão: a revisão A esquenta acima de 50 %.
- `--intervalo 0.2` é o padrão do layout do Ombro (5 quadros por segundo). O cloud anima, então o ritmo é maior que no painel clássico (1 por segundo).
- Feche outros programas que usem a tela (o app do fabricante, por exemplo): só um processo consegue abrir a porta serial.

**Cabo solto ou tela desligada:** o programa não fecha. Ele avisa no terminal, tenta de novo a cada 3 s e, quando a tela volta, reconfigura brilho e orientação e manda o quadro inteiro. Também dá para iniciar com a tela desconectada: ela começa a mostrar assim que for plugada.

**Velocidade medida** (revisão A): a ligação serial leva cerca de 244 KiB/s. Um quadro inteiro demora 1,25 s e, na animação, só os trechos que mudam são reenviados (cerca de 21 KiB por quadro, ≈ 86 ms). Desenhar leva ≈ 33 ms, então o limite é de uns 8 quadros por segundo. O processo usa cerca de 24 % de um núcleo do Mac.

No USB direto (Mac Apple Silicon, tela ligada por hub) o envio medido foi de ≈ 150 KiB/s: um quadro inteiro leva ≈ 2 s. Com a animação do cloud, o processo usa cerca de 3 % de um núcleo (medido em 120 s; antes de a parte parada da tela ser reaproveitada entre quadros, eram 5 %). Com a tela desconectada, o Ombro para de desenhar até a próxima tentativa de reconexão e fica abaixo de 1 %.

## 6. Deixar rodando sempre (opcional)

Um LaunchAgent inicia o Ombro quando você entra no Mac e o reinicia se ele cair. Salve como `~/Library/LaunchAgents/com.ombro-screen.plist`, trocando `/CAMINHO` pela pasta do projeto e `SEU_USUARIO` pelo seu usuário (o launchd não entende `~` nesses caminhos):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ombro-screen</string>
  <key>ProgramArguments</key><array>
    <string>/CAMINHO/ombro-screen/.venv/bin/python</string>
    <string>/CAMINHO/ombro-screen/tela.py</string>
    <string>--usb</string>
  </array>
  <key>WorkingDirectory</key><string>/CAMINHO/ombro-screen</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/Users/SEU_USUARIO/Library/Logs/ombro-screen.log</string>
  <key>StandardErrorPath</key><string>/Users/SEU_USUARIO/Library/Logs/ombro-screen.log</string>
</dict></plist>
```

- `StandardOutPath` e `StandardErrorPath`: sem terminal, os avisos (tela desconectada, reconexão) vão para esse arquivo. `PYTHONUNBUFFERED` faz eles aparecerem na hora.
- `ThrottleInterval`: se o processo cair, espera 10 s antes de reiniciar, em vez de ficar reiniciando sem parar.

```bash
plutil -lint ~/Library/LaunchAgents/com.ombro-screen.plist                      # confere o arquivo
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ombro-screen.plist  # liga (e já inicia)
tail -f ~/Library/Logs/ombro-screen.log                                         # avisos
launchctl kickstart -k gui/$(id -u)/com.ombro-screen                            # reinicia (depois de mudar o código)
launchctl bootout gui/$(id -u)/com.ombro-screen                                 # desliga
```

Só um Ombro pode falar com a tela por vez: desligue o LaunchAgent antes de rodar `tela.py --usb` no terminal. Ele roda o código que estiver na pasta (inclusive o branch atual) e o `.venv` dela; se o Homebrew trocar a versão do Python, recrie o `.venv`.

Opções do Ombro (brilho, cor do cloud etc.) podem ficar no arquivo de configuração (seção 7), em vez de mais linhas `<string>` no `ProgramArguments`. Para investigar um problema, acrescente `<string>--debug</string>` ao `ProgramArguments` e reinicie com o `kickstart` (seção 8).

## 7. Arquivo de configuração (opcional)

Tudo funciona sem ele. Se quiser deixar opções fixas sem repetir na linha de comando, crie `~/.ombro-screen/config.json` (o Ombro não cria esse arquivo sozinho):

```json
{
  "versao": 1,
  "usb": true,
  "brilho": 40,
  "cor_mascote": "#3D7A4F"
}
```

- As chaves têm o nome das opções da linha de comando, com `_` no lugar de `-`: `usb`, `revisao`, `porta`, `transporte`, `brilho`, `lib`, `paisagem`, `girar`, `sem_macmon`, `claude_dir`, `layout`, `intervalo`, `cor_mascote`, `porta_sim`, `nao_abrir` e `debug`. `--demo`, `--demo-ciclo` e `--png` só valem na linha de comando.
- **Precedência:** opção dada na linha de comando > arquivo > padrão. Uma opção que você não deu na linha de comando não apaga a do arquivo.
- As opções de ligar e desligar têm a forma negativa, para a linha de comando desfazer o arquivo: `--no-usb`, `--no-paisagem`, `--no-girar`, `--no-sem-macmon`, `--no-nao-abrir`, `--no-debug`.
- Outro arquivo: `--config /caminho/arquivo.json`. A pasta segue a variável `OMBRO_SCREEN_DIR`, a mesma do `uso.json` do `statusline_tap.py`.
- Caminhos relativos no arquivo (como `lib`) são relativos à pasta do arquivo; `~` funciona.
- Valores inválidos impedem o Ombro de iniciar e a mensagem diz qual opção e por quê, por exemplo `cor_mascote deve ser uma cor como "#3D7A4F"` ou `brilho deve ser de 0 a 100`. Chaves desconhecidas viram um aviso e são ignoradas; `versao` existe para o formato poder mudar no futuro.
- Não coloque credenciais: o Ombro não usa nenhuma.

## 8. Diagnóstico de problemas

Os avisos saem no terminal (ou, no LaunchAgent, em `~/Library/Logs/ombro-screen.log`), um por linha:

```
2026-11-14 12:30:00 INFO [ombro.usb] tela USB conectada (USB direto)
2026-11-14 12:31:10 WARNING [ombro.usb] a tela USB parou de responder ([Errno 19] No such device); tentando reconectar a cada 3 s
2026-11-14 12:31:16 INFO [ombro.usb] tela USB reconectada (USB direto)
```

- `python3 tela.py --usb --debug` mostra também os detalhes (nível DEBUG): a configuração resolvida, o traceback de cada falha e, a cada minuto, um resumo do laço (quadros por segundo, tempo de desenho e quantos KiB foram para a tela).
- Cada parte tem o seu nome: `ombro.main`, `ombro.config`, `ombro.claude`, `ombro.hardware`, `ombro.render`, `ombro.usb`, `ombro.simulador`, e, no núcleo, `ombro.providers`, `ombro.estado`, `ombro.eventos` e `ombro.persistencia`.
- Um problema que se repete (porta ocupada, arquivo estranho, macmon fora) entra no log uma vez, e a volta ao normal entra uma vez; nada é registrado a cada quadro.
- O log não guarda conteúdo de prompts, respostas ou arquivos do Claude Code, nomes de projeto nem credenciais; a sua pasta pessoal aparece como `~`.

| O que aparece | O que fazer |
| --- | --- |
| `tela USB não encontrada` | Confira o cabo e o hub (`ls /dev/cu.*`); o Ombro tenta de novo sozinho a cada 3 s. |
| `não consegui iniciar a tela USB (... busy ...)` | Outro programa está com a tela: o app do fabricante ou outro Ombro (o LaunchAgent, por exemplo). |
| `usando a porta serial, que no macOS costuma embaralhar a imagem` | Falta o `pyusb` ou o `libusb` (seção 5). |
| `sem dados do uso do plano` | Nenhuma fonte de uso de 5 h e da semana ainda (seção 4). |
| `o uso do plano (/usage) tem mais de 30 min` | Rode `/usage` no Claude Code ou ligue o `statusline_tap.py` (seção 4). |
| `macmon falhou` ou `macmon parou` | O Ombro segue com o psutil; reinstale com `brew install macmon` se quiser GPU e temperatura. |
| `falha ao desenhar o quadro ... o Ombro vai fechar` | É um bug: abra uma issue com o trecho do log (rode com `--debug` para ter o traceback). |

## Arquitetura

Os dados do Claude Code passam por um núcleo que não depende da tela (`core/`): um **provider** por
ferramenta monitorada (hoje só o Claude Code, em `providers/claude.py`), um modelo comum de sessões,
uso e limites, um estado consolidado (`StateStore`), um barramento de eventos (`EventBus`) e um banco
SQLite pequeno (`~/.ombro-screen/state.db`) para lembrar das sessões ao reiniciar. A tela continua
recebendo os mesmos dados de antes, por `apresentacao.py`, então nada visual muda.

O banco guarda só o status das sessões e as mudanças recentes (sem nome de projeto, conversas ou
tokens), apaga o que tem mais de 7 dias e nunca impede o Ombro de rodar: se der problema com ele, o
Ombro segue em memória e avisa no log. `--demo` e `--png` não usam o banco.

Detalhes, regras e como adicionar um provider: [docs/architecture.md](docs/architecture.md). Codex e
Cursor ainda não são suportados.

## Arquivos

| Arquivo | O que faz |
| --- | --- |
| `tela.py` | Ponto de entrada: laço, opções, simulador ou USB |
| `core/` | Núcleo sem tela: modelos, providers, estado, eventos, banco (ver `docs/architecture.md`) |
| `providers/claude.py` | O provider do Claude Code, sobre o coletor de sempre |
| `apresentacao.py` | Do estado do núcleo para o formato que o layout e o cloud usam |
| `config.py` | Padrões, arquivo de configuração e validação das opções, resolvidos num lugar só |
| `logging_config.py` | Formato dos logs, `--debug` e o filtro que evita repetir o mesmo aviso |
| `layout_ombro.py` | O layout do Ombro (480×320): texto, barras, curva de tokens, aviso e o cloud |
| `mascote.py` | O cloud: animação por estado, extras (zZ, "?", pontinhos, suor, confete) e a máquina de estados |
| `dados_cloud.py` | Contorno e olhos do cloud, extraídos da KEY de formas |
| `render.py` | Cores, fontes, formatação pt-BR e o painel clássico |
| `saida.py` | Simulador no navegador e envio para a tela USB (com reconexão) |
| `usb_direto.py` | Driver da revisão A direto no endpoint USB, para o Mac |
| `coletores/hardware.py` | psutil + macmon |
| `coletores/claude.py` | Uso do plano, tokens de hoje (e por hora), sessões abertas e quem espera você |
| `coletores/demo.py` | Dados inventados para `--demo` e `--demo-ciclo` |
| `statusline_tap.py` | Captura o uso de 5 h e semanal do statusline (opcional) |
| `tests/` | Testes (`unittest`, sem dependências extras) |
| `scripts/` | `benchmark.py` (quanto custa cada parte) e `quadros_referencia.py` (confere que nenhum pixel mudou) |
| `.github/workflows/tests.yml` | CI: os testes do Python 3.9 ao 3.14 e o lint, a cada push e pull request |

Se algum arquivo do Claude Code estiver estranho, o erro vira um aviso no terminal e a tela segue; campos sem dado aparecem como "—".

Contas extras do Claude Code: `--claude-dir ~/.claude-conta2`.

Fonte: JetBrains Mono (licença OFL, em `fontes/`). A biblioteca da tela (turing-smart-screen-python, GPL-3.0) não é redistribuída aqui.

## Testes

```bash
python3 -m unittest discover -s tests -t .
```

Não precisam da tela, da biblioteca da tela, do macmon nem do Claude Code: a `TelaUSB` e o envio direto pelo USB são testados com dispositivos falsos, o hardware com um psutil falso, e o coletor do Claude com pastas temporárias. Nada lê os seus arquivos de verdade.

**CI:** o GitHub Actions (`.github/workflows/tests.yml`) roda os testes no Python 3.9, 3.10, 3.11, 3.12, 3.13 e 3.14 (Ubuntu, sem hardware) e o lint (`ruff check --select F,E9 .`) a cada push e pull request.

**Nenhum pixel mudou?** Os testes não comparam imagens pixel a pixel, porque outra versão do Pillow ou do FreeType desenha as letras um pouco diferente. Para conferir uma mudança na sua máquina:

```bash
python3 scripts/quadros_referencia.py --salvar /tmp/antes.json     # antes de mexer no código
python3 scripts/quadros_referencia.py --comparar /tmp/antes.json   # depois: "34 quadros idênticos"
```

**Quanto custa cada parte:** `python3 scripts/benchmark.py` mede desenho, coleta, preparação do envio e o laço completo, com entradas fixas e uma tela falsa (o USB em si não entra). Compare execuções feitas na mesma máquina.
