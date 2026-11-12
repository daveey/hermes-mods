"""Decide whether a shell command would truncate an existing evidence file.

Evidence lives under ``*/kanban/evidence/*`` (e.g. ``~/.hermes/kanban/evidence/<card>/``). A run
log there is the only record of a test or mutation run; ``cmd > run.log`` onto a previous run's
file silently destroys it. Refused: truncating redirects (``>``, ``>|``, ``N>``, ``&>``, ``>&FILE``)
and ``tee`` without ``-a``/``--append`` whose target already exists as a file inside an evidence
directory. Allowed: appends (``>>``, ``&>>``, ``tee -a``), new paths, files outside evidence dirs.

Shared scratch (self-heal agent-c97f5ffde4): a subagent inherits its parent's cwd, and a folder under
``$HERMES_HOME/cache/scratch/<dir>/`` can be shared by hundreds of sessions. There the same truncating
writes, and ``rm`` (incl. ``-r`` and globs), are refused for files older than the calling task's first
tool call (``task_started``, persisted per task_id): they were someone else's before this task began.
The task's own new files, appends, reads and paths outside scratch pass.

This is a bounded lexer, not a shell evaluator: it follows ``cd DIR`` within one command line,
expands ``~``, ``$HOME``, ``$HERMES_HOME`` and ``$TMPDIR``, strips here-doc bodies, and recurses into
``bash -c``/``sh -c`` payloads.
"""

from __future__ import annotations

import glob
import hashlib
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
VAR = re.compile(r"\$(?:\{(HOME|HERMES_HOME|TMPDIR)\}|(HOME|HERMES_HOME|TMPDIR)\b)")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1")
RM_WALK_LIMIT = 5000


def enabled() -> bool:
    return os.environ.get("HERMES_EVIDENCE_CLOBBER_GUARD", "").strip().lower() not in {"off", "0", "false"}


def _hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")


def _var(name: str) -> str:
    if name == "HERMES_HOME":
        return _hermes_home()
    if name == "TMPDIR":
        return os.environ.get("TMPDIR") or "$TMPDIR"
    return str(Path.home())


def _expand(word: str) -> str:
    word = VAR.sub(lambda m: _var(m.group(1) or m.group(2)), word)
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


_QUOTE_MASK = {"<": "\ue03c", ">": "\ue03e"}
_QUOTE_UNMASK = str.maketrans({v: k for k, v in _QUOTE_MASK.items()})


class _Quoted(str):
    """A word whose `<`/`>` were quoted or escaped (`'>>'`, `"2>x"`, `\\>`): an argument, never a redirect."""


def _mask_quoted_redirects(text: str) -> str:
    """Mark each quoted or backslash-escaped `<`/`>` so it survives shlex as data: shlex drops the quotes, so
    `grep -c '>' f` would lex like `grep -c > f`. `text` comes back unchanged when its quotes do not balance."""
    out, quote, i, n = [], "", 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(c + _QUOTE_MASK.get(text[i + 1], text[i + 1]))
                i += 2
                continue
            if c == quote:
                quote = ""
                out.append(c)
            else:
                out.append(_QUOTE_MASK.get(c, c))
        elif c == "\\" and i + 1 < n:
            out.append(_QUOTE_MASK.get(text[i + 1]) or text[i:i + 2])
            i += 2
            continue
        else:
            if c in "'\"":
                quote = c
            out.append(c)
        i += 1
    return text if quote else "".join(out)


def _unmask_quoted(tokens: list) -> list:
    """Tokens lexed from `_mask_quoted_redirects` text, each word that held a quoted `<`/`>` as `_Quoted`."""
    return [_Quoted(t.translate(_QUOTE_UNMASK)) if any(m in t for m in _QUOTE_MASK.values()) else t for t in tokens]


def _strip_heredocs(text: str) -> str:
    """Drop here-doc bodies (`cat > f <<'EOF' ... EOF`) and their `<<EOF` operators: the body is data,
    and an apostrophe in it would otherwise make the whole command unlexable (and so unchecked)."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(HEREDOC.sub(" ", line))
        i += 1
        for m in HEREDOC.finditer(line):
            delim, dash = m.group(2), m.group(0).startswith("<<-")
            while i < len(lines):
                body = lines[i].lstrip("\t") if dash else lines[i]
                i += 1
                if body == delim:
                    break
    return "\n".join(out)


def _tokens(text: str) -> List[str]:
    text = _strip_heredocs(text)
    lex = shlex.shlex(_mask_quoted_redirects(_drop_fd_prefixes(text.replace("\n", " ; "))), posix=True,
                      punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return _unmask_quoted(list(lex))
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
        if set(tok) <= set("<>&|") and (">" in tok or "<" in tok) and not isinstance(tok, _Quoted):
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


def _scratch_root() -> str:
    return os.path.join(_hermes_home(), "cache", "scratch")


def _in_shared_scratch(p: Path) -> bool:
    """True when `p` lies inside a folder under `$HERMES_HOME/cache/scratch/` (not directly in the root)."""
    root = _scratch_root()
    for base in {os.path.normpath(root), os.path.realpath(root)}:
        for cand in {str(p), os.path.join(os.path.realpath(str(p.parent)), p.name)}:
            rel = os.path.relpath(cand, base)
            if rel != ".." and not rel.startswith("../") and len(Path(rel).parts) >= 2:
                return True
    return False


def _abs(path: str, cwd: str) -> Path:
    p = Path(_expand(path))
    if not p.is_absolute():
        p = Path(cwd) / p
    return Path(os.path.normpath(str(p)))


def _older(p: Path, started: float) -> bool:
    """An existing non-directory whose inode changed last before `started`. ctime, not mtime: `tar x`,
    `cp -p` and `rsync -a` give a task's own new files old mtimes, but ctime can't be set back."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return not os.path.isdir(p) and st.st_ctime < started


def _scratch_file(path: str, cwd: str, started: Optional[float]) -> Optional[str]:
    """The existing shared-scratch file `path` names, when it predates this task's start."""
    if started is None:
        return None
    p = _abs(path, cwd)
    return str(p) if _in_shared_scratch(p) and _older(p, started) else None


def _rm_hit(argv: List[str], cwd: str, started: Optional[float]) -> Optional[str]:
    """The first shared-scratch file `rm ARGS` would delete that predates this task's start."""
    if started is None:
        return None
    recursive, opts, operands = False, True, []
    for word in argv[1:]:
        if opts and word == "--":
            opts = False
        elif opts and word.startswith("--"):
            recursive = recursive or word == "--recursive"
        elif opts and word.startswith("-") and len(word) > 1:
            recursive = recursive or "r" in word[1:] or "R" in word[1:]
        else:
            operands.append(word)
    seen = 0
    for word in operands:
        p = str(_abs(word, cwd))
        for item in map(Path, sorted(glob.glob(p)) if glob.has_magic(p) else [p]):
            if not _in_shared_scratch(item):
                continue
            if _older(item, started):
                return str(item)
            if recursive and item.is_dir() and not item.is_symlink():
                for dirpath, _dirs, files in os.walk(item):
                    for f in files:
                        seen += 1
                        if seen > RM_WALK_LIMIT:  # bounded: a huge tree is not worth a stalled tool call
                            return None
                        if _older(Path(dirpath) / f, started):
                            return str(Path(dirpath) / f)
    return None


def check_shell(text: str, cwd: str, depth: int = 0, started: Optional[float] = None) -> Optional[str]:
    """Return the existing evidence path, or older shared-scratch file, the command would clobber; else None.

    `started` is the calling task's first tool call (``task_started``); without it only evidence is checked."""
    if depth > 3 or not text or ">" not in text and "tee" not in text and "rm" not in text:
        return None
    for argv, redirs in _segments(_tokens(text)):
        cmd = _strip_wrappers(argv)
        name = os.path.basename(cmd[0]) if cmd else ""
        targets = [t for op, t in redirs if op in TRUNCATE and not (op == ">&" and (t.isdigit() or t == "-"))]
        if name == "tee":
            targets += _tee_targets(cmd)
        for target in targets:
            hit = _evidence_file(target, cwd) or _scratch_file(target, cwd, started)
            if hit:
                return hit
        if name == "rm":
            hit = _rm_hit(cmd, cwd, started)
            if hit:
                return hit
        if name == "cd" and len(cmd) > 1:
            nxt = Path(_expand(cmd[1]))
            cwd = str(nxt if nxt.is_absolute() else Path(cwd) / nxt)
        if name in SHELLS:
            for k, word in enumerate(cmd[1:-1], start=1):
                if word.startswith("-") and "c" in word[1:]:
                    hit = check_shell(cmd[k + 1], cwd, depth + 1, started)
                    if hit:
                        return hit
                    break
    return None


def task_started(key: str) -> Optional[float]:
    """When this guard first saw a tool call from `key` (task_id, else session_id). Persisted under
    ``$HERMES_HOME/cache/evidence-clobber-guard/task-starts/`` so a gateway restart or guard reload keeps it:
    the first call creates the stamp (O_EXCL, so concurrent calls agree), later calls read it."""
    if not key:
        return None
    d = Path(_hermes_home()) / "cache" / "evidence-clobber-guard" / "task-starts"
    f = d / hashlib.sha256(key.encode()).hexdigest()[:32]

    def read() -> Optional[float]:
        try:
            return float(f.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    got = read()
    if got is not None:
        return got
    now = time.time()
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{now} {key[:200]}\n")
        _prune_starts(d, now)
    except FileExistsError:
        return read() or now
    except OSError:
        pass
    return now


def _prune_starts(d: Path, now: float, keep_days: int = 30) -> None:
    """At most once a day, drop start stamps untouched for `keep_days` (a task that old is long gone)."""
    marker = d / ".pruned"
    try:
        if marker.exists() and now - marker.stat().st_mtime < 86400:
            return
        marker.touch()
        for entry in os.scandir(d):
            if entry.name != ".pruned" and now - entry.stat().st_mtime > keep_days * 86400:
                os.unlink(entry.path)
    except OSError:
        pass


def message(target: str) -> str:
    if _in_shared_scratch(Path(target)) and "/kanban/evidence/" not in target:
        return (f"evidence-clobber-guard: {target} is in a shared scratch folder and existed before this task "
                "started (a parent's or sibling session's file). Overwriting or deleting it can destroy their "
                "work. Put your own files in a private folder: `d=$(mktemp -d -p $TMPDIR)` and work in \"$d\" "
                "(or use a name that does not exist yet). Operator override: HERMES_EVIDENCE_CLOBBER_GUARD=off.")
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
