"""Точка входа:  python -m multics_sim [--demo]"""
import os
import sys

from .demo import run_demo
from .shell import Shell


def main():
    if os.name == "nt":
        os.system("chcp 65001 >nul")           # консоль Windows -> UTF-8
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if "--demo" in sys.argv:
        run_demo()
        return
    Shell().repl()


if __name__ == "__main__":
    main()
