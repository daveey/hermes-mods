"""Decide whether a shell command would truncate an existing evidence file.

Evidence lives under ``*/kanban/evidence/*`` (e.g. ``~/.hermes/kanban/evidence/<card>/``). A run
log there is the only record of a test or mutation run; ``cmd > run.log`` onto a previous run's
file silently destroys it. Refused: truncating redirects (``>``, ``>|``, ``N>``, ``&>``, ``>&FILE``)
and ``tee`` without ``-a``/``--append`` whose target already exists as a file inside an evidence
directory. Allowed: appends (``>>``, ``&>>``, ``tee -a``), new paths, files outside evidence dirs.

This is a bounded lexer, not a shell evaluator: it follows ``cd DIR`` within one command line,
expands ``~``, ``$HOME`` and ``$HERMES_HOME``, and recurses into ``bash -c``/``sh -c`` payloads.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

TRUNCATE = {">", ">|", "&>", ">&"}
SEPARATORS = {"|", "||", "&&", ";", "&", "(", ")", ";;", "|&", "\n"}
SHELLS = {"bash", "sh", "zsh", "dash"}
WRAPPERS = {"sudo", "env", "nice", "nohup", "time", "timeout", "command", "exec", "stdbuf"}
VAR = re.compile(r"\$(?:\{(HOME|HERMES_HOME)\}|(HOME|HERMES_HOME)\b)")


def enabled() -> bool:
    return os.environ.get("HERMES_EVIDENCE_CLOBBER_GUARD", "").strip().lower() not in {"off", "0", "false"}


def _hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")


def _expand(word: str) -> str:
    word = VAR.sub(lambda m: _hermes_home() if (m.group(1) or m.group(2)) == "HERMES_HOME"
                   else str(Path.home()), word)
    if word == "~" or word.startswith("~/"):
        word = str(Path.home()) + word[1:]
    return word


_FD_PREFIX = re.compile(r"\d+(?=[<>])")


def _drop_fd_prefixes(text: str) -> str:
    """`text` without the file-descriptor number of each unquoted redirect (`2>x`, `2>>x`, `2>&1`, `1>|x`, `0<x`).
    shlex drops whitespace, so this is decided on the raw text: only a digit run that starts a word and directly
    touches `<`/`>` is an fd; a spaced `tee 2 > x` keeps `2` as an operand, as in bash."""
    out, quote, i, n = [], "", 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if c in "'\"":
            quote = c
        elif c.isdigit() and (i == 0 or text[i - 1].isspace() or text[i - 1] in ";&|()"):
            m = _FD_PREFIX.match(text, i)
            if m:
                i = m.end()
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _tokens(text: str) -> List[str]:
    lex = shlex.shlex(_drop_fd_prefixes(text.replace("\n", " ; ")), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: let the shell report it
        return []


def _segments(tokens: List[str]) -> Iterator[Tuple[List[str], List[Tuple[str, str]]]]:
    """Yield (argv, redirects) per simple command; redirects are (operator, target)."""
    argv: List[str] = []
    redirs: List[Tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in SEPARATORS:
            yield argv, redirs
            argv, redirs = [], []
            i += 1
            continue
        if set(tok) <= set("<>&|") and (">" in tok or "<" in tok):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            redirs.append((tok, target))
            i += 2
            continue
        argv.append(tok)
        i += 1
    yield argv, redirs


def _strip_wrappers(argv: List[str]) -> List[str]:
    i = 0
    while i < len(argv):
        word = argv[i]
        if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", word) or (word.startswith("-") and i > 0):
            i += 1
            continue
        if os.path.basename(word) in WRAPPERS:
            i += 1
            if os.path.basename(word) == "timeout" and i < len(argv) and not argv[i].startswith("-"):
                i += 1  # timeout DURATION
            continue
        break
    return argv[i:]


def _tee_targets(argv: List[str]) -> List[str]:
    targets, append, opts = [], False, True
    for word in argv[1:]:
        if opts and word == "--":
            opts = False
        elif opts and word.startswith("--"):
            append = append or word == "--append"
        elif opts and word.startswith("-") and len(word) > 1:
            append = append or "a" in word[1:]
        else:
            targets.append(word)
    return [] if append else targets


def _evidence_file(path: str, cwd: str) -> Optional[str]:
    p = Path(_expand(path))
    if not p.is_absolute():
        p = Path(cwd) / p
    p = Path(os.path.normpath(str(p)))
    parts = p.parts
    under = any(parts[k] == "kanban" and parts[k + 1] == "evidence" and k + 2 < len(parts)
                for k in range(len(parts) - 2))
    if not under:
        return None
    try:
        return str(p) if p.is_file() else None
    except OSError:
        return None


def check_shell(text: str, cwd: str, depth: int = 0) -> Optional[str]:
    """Return the existing evidence path the command would truncate, else None."""
    if depth > 3 or not text or ">" not in text and "tee" not in text:
        return None
    for argv, redirs in _segments(_tokens(text)):
        cmd = _strip_wrappers(argv)
        name = os.path.basename(cmd[0]) if cmd else ""
        targets = [t for op, t in redirs if op in TRUNCATE and not (op == ">&" and (t.isdigit() or t == "-"))]
        if name == "tee":
            targets += _tee_targets(cmd)
        for target in targets:
            hit = _evidence_file(target, cwd)
            if hit:
                return hit
        if name == "cd" and len(cmd) > 1:
            nxt = Path(_expand(cmd[1]))
            cwd = str(nxt if nxt.is_absolute() else Path(cwd) / nxt)
        if name in SHELLS:
            for k, word in enumerate(cmd[1:-1], start=1):
                if word.startswith("-") and "c" in word[1:]:
                    hit = check_shell(cmd[k + 1], cwd, depth + 1)
                    if hit:
                        return hit
                    break
    return None


def message(target: str) -> str:
    return (f"evidence-clobber-guard: evidence file exists: {target}. "
            "A truncating write would destroy the previous run's record. "
            "Write a new file `<name>-run<N>.log` (e.g. `<name>-run<N>-<src sha8>.log`) "
            "or append with `>>` / `tee -a`. Operator override: HERMES_EVIDENCE_CLOBBER_GUARD=off.")


def record(tool: str, target: str, command: str) -> None:
    try:
        log = Path(_hermes_home()) / "logs" / "evidence-clobber-guard.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": tool,
               "target": target, "command": command[:300],
               "card": os.environ.get("HERMES_KANBAN_TASK") or os.environ.get("HERMES_KANBAN_WORKER_TASK")}
        with log.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
