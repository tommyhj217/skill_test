#!/usr/bin/env python3
"""Cross-platform helpers (Windows / macOS / Linux) for the commands the menu prints.

The commands are run by an AI runner's shell: bash / zsh (macOS, Linux, Git Bash on Windows —
Claude Code), PowerShell (Codex on Windows) or cmd. So they must:
  * name an interpreter that exists:  python3 (POSIX) / python (Windows); override MODEL_AGENTS_PYTHON (e.g. "py -3")
  * use forward slashes (Windows Python accepts them; Git Bash would eat backslashes)
  * quote only with double quotes (understood by bash, PowerShell and cmd), and only when needed
  * print UTF-8 even when Windows would default to cp949 / cp1252 (emoji + Korean in the output)
"""
import os
import re
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
_SAFE = re.compile(r"[\w@%+=:,./\-]+")         # \w is Unicode-aware: Hangul is safe too


def python_cmd():
    return os.environ.get("MODEL_AGENTS_PYTHON") or ("python" if IS_WINDOWS else "python3")


def q(arg):
    """Quote one argument for bash, PowerShell and cmd alike."""
    s = str(arg)
    if s and _SAFE.fullmatch(s):
        return s
    return '"' + s.replace('"', '\\"') + '"'


def shell_path(path, cwd=None):
    """Path as written in a command: relative to cwd when below it, else absolute; always '/'."""
    path = Path(path).resolve()
    try:
        rel = Path(os.path.relpath(path, cwd or os.getcwd()))
        if not rel.as_posix().startswith(".."):
            return rel.as_posix()
    except ValueError:                           # Windows: different drive than cwd
        pass
    return path.as_posix()


def command(script, *args):
    return " ".join([python_cmd(), q(shell_path(script)), *map(q, args)])


def utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):     # not a text stream / already detached
            pass
