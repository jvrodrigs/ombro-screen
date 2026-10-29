#!/usr/bin/env python3
"""Captura o uso do plano (5h e semana) que o Claude Code envia ao statusline.

O Claude Code roda o comando de statusline passando um JSON pelo stdin, com
`rate_limits.five_hour` e `rate_limits.seven_day` ({used_percentage, resets_at}).
Este script grava SÓ esses números em ~/.ombro-screen/uso.json e depois:
  - se houver um comando depois de `--`, roda o seu statusline original com o mesmo stdin;
  - senão, imprime um statusline simples ("5h 42% · semana 61%").

Uso no ~/.claude/settings.json:
  "statusLine": {"type": "command", "command": "python3 /caminho/ombro-screen/statusline_tap.py"}
ou, mantendo o statusline que você já tem:
  "statusLine": {"type": "command", "command": "python3 /caminho/ombro-screen/statusline_tap.py -- npx -y ccstatusline"}

Qualquer falha na captura é ignorada: o statusline nunca quebra por causa disto.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PASTA = Path(os.environ.get("OMBRO_SCREEN_DIR", "~/.ombro-screen")).expanduser()


def janela(w):
    if not isinstance(w, dict):
        return None
    pct = w.get("used_percentage", w.get("utilization"))
    try:
        pct = max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return None
    out = {"utilization": pct}
    if w.get("resets_at") is not None:
        out["resets_at"] = w["resets_at"]
    return out


def capturar(texto):
    try:
        if "rate_limits" not in texto:
            return None
        rl = json.loads(texto).get("rate_limits") or {}
        rec = {"fetchedAt": int(time.time() * 1000)}
        for chave in ("five_hour", "seven_day"):
            w = janela(rl.get(chave))
            if w:
                rec[chave] = w
        if len(rec) == 1:
            return None
        PASTA.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = PASTA / f"uso.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, PASTA / "uso.json")
        return rec
    except Exception:
        return None


def main():
    try:
        texto = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    except Exception:
        texto = b""
    args = sys.argv[1:]
    original = " ".join(args[args.index("--") + 1:]) if "--" in args else ""

    if original:
        proc = subprocess.Popen(original, shell=True, stdin=subprocess.PIPE)
        try:
            proc.stdin.write(texto)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        capturar(texto.decode("utf-8", "replace"))
        sys.exit(proc.wait())

    rec = capturar(texto.decode("utf-8", "replace"))
    if rec:
        partes = []
        if "five_hour" in rec:
            partes.append(f"5h {rec['five_hour']['utilization']:.0f}%")
        if "seven_day" in rec:
            partes.append(f"semana {rec['seven_day']['utilization']:.0f}%")
        print(" · ".join(partes))


if __name__ == "__main__":
    main()
