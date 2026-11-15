#!/usr/bin/env python3
"""ombrod: o Ombro como serviço local (o LaunchAgent roda este arquivo). Veja ombro/daemon.py.

  python3 ombrod.py              mesmas opções do tela.py (menos --png); Ctrl+C ou SIGTERM para sair
  python3 -m ombro status        de outro terminal: o que o ombrod está vendo
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ombro.daemon import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
