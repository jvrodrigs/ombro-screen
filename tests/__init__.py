import logging

# Os testes que olham os logs ligam um handler próprio; nos outros, os avisos não poluem a saída.
logging.getLogger("ombro").addHandler(logging.NullHandler())
