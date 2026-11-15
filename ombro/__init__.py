"""Ombro Hub: o serviço local (`ombrod`), a CLI de gerenciamento (`python3 -m ombro`) e o que eles usam.

  ombro/app.py           o laço do Ombro (coleta, estado, desenho, saída), usado pelo tela.py e pelo ombrod
  ombro/caminhos.py      pastas privadas, socket e lock em ~/.ombro-screen/run/
  ombro/instancia.py     um Ombro ao vivo por usuário (flock)
  ombro/daemon.py        o ombrod: ciclo de vida, saúde e os métodos da IPC
  ombro/ipc.py           protocolo local (JSON Lines por Unix Domain Socket), servidor e cliente
  ombro/cli.py           `python3 -m ombro ...`: cliente da IPC, integrações, serviço e doctor
  ombro/integracoes.py   instalar, conferir e remover os hooks do Ombro no Claude Code, Codex e Cursor
  ombro/launchagent.py   o LaunchAgent do ombrod (e a migração do LaunchAgent antigo)
  ombro/doctor.py        diagnóstico não destrutivo, em texto ou JSON

Só biblioteca padrão aqui dentro (fora o que o laço do app já usava). Detalhes em docs/architecture.md.
"""
VERSAO = "0.4.0"  # versão do Ombro Hub (o protocolo da IPC tem a sua: ombro.ipc.PROTOCOLO)
