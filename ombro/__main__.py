"""`python3 -m ombro ...` (na pasta do projeto) ou `python3 /caminho/ombro-screen/ombro ...` (de qualquer pasta)."""
import sys
from pathlib import Path

RAIZ = str(Path(__file__).resolve().parent.parent)
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)  # config.py, core/, providers/ e o próprio pacote ombro ficam na raiz do projeto

from ombro.cli import main  # noqa: E402

sys.exit(main())
