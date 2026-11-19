# Instalar o Ombro no macOS

O Ombro.app traz tudo o que precisa: não é preciso instalar Python, Homebrew nem abrir o terminal.

**Requisitos:** um Mac com Apple Silicon (M1 ou mais novo) e macOS 13 ou mais novo. Claude Code, Codex e Cursor são
opcionais, e a telinha USB também: sem ela, o app mostra a prévia do que a tela mostraria.

## Instalar

1. Em [Releases](https://github.com/jvrodrigs/ombro-screen/releases), baixe `Ombro-v<versão>-macos-arm64.dmg`
   (por exemplo, `Ombro-v0.9.0-beta.3-macos-arm64.dmg`) e o `SHA256SUMS.txt`.
2. Abra o DMG e arraste o **Ombro** para **Aplicativos**.
3. Abra o Ombro pela pasta Aplicativos (ou pelo Spotlight). O assistente de primeiro uso explica o resto.

Abra sempre a partir de Aplicativos. Aberto direto do DMG ou da pasta Downloads, o macOS roda o app de um lugar
temporário, e o Ombro não deixa instalar integrações nem o serviço em segundo plano (eles gravam o caminho do app).

### Primeira abertura (beta sem notarização)

As versões beta têm **assinatura ad-hoc** e **não são notarizadas pela Apple**: o projeto não usa um certificado
Developer ID. A assinatura ad-hoc garante que o app não foi alterado depois de montado, mas não identifica quem o
montou, e a Apple não o conferiu. Por isso, na primeira vez, o macOS avisa que não pôde verificar o Ombro e não o
abre. Se você confia na origem (este repositório), autorize uma vez, como a Apple explica em
[Abrir apps com segurança no Mac](https://support.apple.com/pt-br/102445):

1. Abra o Ombro pela pasta Aplicativos. O macOS mostra o aviso: feche-o (sem mover o app para o Lixo).
2. Abra **Ajustes do Sistema > Privacidade e Segurança**, role até o fim e clique em **Abrir Mesmo Assim** ao lado
   do Ombro. O macOS pode pedir a senha de um administrador do Mac.
3. O aviso aparece de novo: clique em **Abrir**.

Depois disso o Ombro fica registrado como exceção e abre normalmente, com duplo clique. Uma versão nova baixada
depois pede a mesma autorização uma vez.

- Não é preciso (nem recomendado) desligar o Gatekeeper, mexer no SIP ou remover atributos pelo terminal.
- Macs gerenciados por uma empresa ou escola (MDM) podem não mostrar o botão **Abrir Mesmo Assim**. Nesse caso, a
  instalação depende da política de quem administra o Mac.
- Se o botão não aparecer, tente abrir o Ombro de novo e volte em **Privacidade e Segurança**: ele só aparece
  depois de uma tentativa de abrir o app.

### Conferir o download (recomendado)

Como a beta não é notarizada, conferir o hash é a forma de saber que o DMG é o mesmo que o GitHub Actions montou.
Cada release traz um `SHA256SUMS.txt` (o hash também está nas notas da release). Na pasta onde estão os dois arquivos:

```bash
shasum -a 256 -c SHA256SUMS.txt
```

A resposta deve ser `OK`.

## Primeiro uso

O assistente confere o Mac, procura os agentes, pergunta pela telinha USB e pelas preferências. Nada nos seus
agentes muda sem a sua confirmação: cada integração mostra antes o que vai mudar no arquivo do agente, e faz um
backup.

- **Claude Code:** funciona sem integração (o Ombro lê os arquivos locais). A integração acrescenta quando uma
  sessão pede aprovação ou para com erro.
- **Codex e Cursor:** precisam da integração para aparecer. No Codex, depois de instalar, confie nos hooks (`/hooks`).
- **Telinha USB (revisão A, a Turing de 3,5"):** ligue o cabo e marque "Tenho a telinha USB". As revisões B, C, D e
  WeAct ainda precisam do Ombro do repositório (ver o README).

## Janela, Menu Bar e segundo plano

- Fechar a janela (botão vermelho, ⌘W ou ⌘Q) deixa o Ombro na Menu Bar (o cloud no topo da tela). Para fechar o
  app, use "Sair do Ombro" no menu do cloud.
- Sem o serviço em segundo plano, o monitoramento (e a telinha) param quando você sai do app.
- Com **Ajustes > Continuar monitorando depois de sair do Ombro**, o macOS mantém o serviço rodando, mesmo com o
  app fechado e depois de reiniciar o Mac.
- **Ajustes > Abrir o Ombro ao entrar no Mac** abre o app direto na Menu Bar.

### Já usava o Ombro pelo repositório?

Se o LaunchAgent antigo (`com.ombro-screen`, o `tela.py --usb`) estiver instalado, o app avisa e não inicia outro
Ombro (os dois disputariam a tela). Em **Ajustes**, a migração guarda um backup do antigo, liga o novo e pode ser
desfeita. Configurações e histórico ficam onde estão (`~/.ombro-screen`).

## Desinstalar

1. **Agentes:** remova as integrações que você instalou (botão **Remover** em cada agente). Se esquecer, nada
   quebra: o hook termina em `|| true` e o agente segue normalmente, só sem avisar o Ombro.
2. **Ajustes:** desligue "Continuar monitorando depois de sair" e "Abrir o Ombro ao entrar no Mac".
3. Saia do Ombro e arraste o app de Aplicativos para o Lixo.
4. Opcional, para apagar também os dados: `~/.ombro-screen` (configuração, histórico, backups),
   `~/Library/Logs/ombro-screen` e `~/Library/Application Support/io.github.jvrodrigs.ombro`.

## Problemas

Abra **Diagnóstico** no app: ele confere a instalação inteira sem mudar nada, e **Copiar relatório** gera um texto
sem dados pessoais para abrir uma issue. Mais casos em [troubleshooting.md](troubleshooting.md).
