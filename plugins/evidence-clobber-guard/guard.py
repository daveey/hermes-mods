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


class _Unclosed(Exception):
    pass


def _expose_substitutions(text: str) -> str:
    """`text` with each command substitution moved out of its double quotes, and backticks spelled `$(...)`.

    Bash runs `"$(cmd)"` and "`cmd`" exactly like the unquoted forms, but shlex keeps a double-quoted string as one
    word, so `out="$(rm -f x 2>&1)"` hid the rm that `out=$(rm -f x 2>&1)` shows. `echo "a $(cmd) b"` becomes
    `echo "a "$(cmd)" b"` (`"$(cmd)"` becomes `$(cmd)`): the substitution is lexed as code, the text around it stays
    quoted data. Quotes inside a substitution start fresh, as in bash (plugins/coach/agent_rules.py `_mask_quoted`).
    Single quotes, `$'...'`, escaped `\\$(`/`` \\` ``, `$((...))` arithmetic and `#` comments are copied unchanged.
    Text whose quotes or substitutions do not close comes back unchanged."""
    n = len(text)

    def comment_at(i: int) -> bool:
        return text[i] == "#" and (i == 0 or text[i - 1] in " \t\n;&|()")

    def arithmetic(i: int) -> int:  # `$((...))` starting at i: index just past it
        depth, j = 0, i + 1
        while j < n:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise _Unclosed

    def single(i: int, ansi: bool) -> int:  # quote opening at i: index just past its close
        j = i + 1
        while j < n:
            if ansi and text[j] == "\\":
                j += 2
                continue
            if text[j] == "'":
                return j + 1
            j += 1
        raise _Unclosed

    def code(i: int, stop: str, level: int) -> tuple:
        if level > 32:
            raise _Unclosed
        out, depth = [], 0
        while i < n:
            c = text[i]
            if c == "\\":
                out.append(text[i:i + 2])
                i += 2
            elif stop == "`" and c == "`":
                return "".join(out), i + 1
            elif comment_at(i):
                j = text.find("\n", i)
                j = n if j < 0 else j
                out.append(text[i:j])
                i = j
            elif c == "'" or text.startswith("$'", i):
                j = single(i + (c == "$"), c == "$")
                out.append(text[i:j])
                i = j
            elif c == '"':
                s, i = dquote(i + 1, level)
                out.append(s)
            elif text.startswith("$((", i):
                j = arithmetic(i + 1)
                out.append(text[i:j])
                i = j
            elif text.startswith("$(", i):
                s, i = code(i + 2, ")", level + 1)
                out.append("$(" + s + ")")
            elif c == "`":
                s, i = code(i + 1, "`", level + 1)
                out.append("$(" + s + ")")
            else:
                if stop == ")" and c == "(":
                    depth += 1
                elif stop == ")" and c == ")":
                    if depth == 0:
                        return "".join(out), i + 1
                    depth -= 1
                out.append(c)
                i += 1
        if stop:
            raise _Unclosed
        return "".join(out), n

    def dquote(i: int, level: int) -> tuple:  # body of a "..." opened just before i
        parts, data = [], []
        while i < n:
            c = text[i]
            if c == "\\":
                data.append(text[i:i + 2])
                i += 2
            elif c == '"':
                if not parts:
                    return '"' + "".join(data) + '"', i + 1
                if data:
                    parts.append('"' + "".join(data) + '"')
                return "".join(parts), i + 1
            elif text.startswith("$((", i):
                j = arithmetic(i + 1)
                data.append(text[i:j])
                i = j
            elif text.startswith("$(", i) or c == "`":
                if data:
                    parts.append('"' + "".join(data) + '"')
                    data = []
                s, i = code(i + 2, ")", level + 1) if c == "$" else code(i + 1, "`", level + 1)
                parts.append("$(" + s + ")")
            else:
                data.append(c)
                i += 1
        raise _Unclosed

    if "$(" not in text and "`" not in text:
        return text
    try:
        return code(0, "", 0)[0]
    except (_Unclosed, RecursionError):
        return text


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


# ---- heredoc bodies, comments, partial lexing ----------------------------------------------------------------
# Byte-identical in live-checkout-guard, mac-heavy-guard, busy-tree-guard, sqlite-live-guard, service-owner-guard,
# evidence-clobber-guard and coach/agent_rules.py (each plugin loads standalone). Change every copy together.

_HD_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ssh"})
_HD_PIPED_SHELL = re.compile(r"\|&?\s*(?:(?:sudo|env|exec|command|nohup)\s+(?:-\S+\s+)*)*(?:\S*/)?"
                             r"(?:ba|z|da|k|mk)?sh(?![\w.-])")
_HD_WORD_BREAK = " \t\r\n;&|()<>"


def _hd_word_start(text: str, i: int) -> bool:
    return i == 0 or text[i - 1] in _HD_WORD_BREAK


def _hd_arith_end(text: str, i: int) -> int:
    """Index just past the `((...))` whose first `(` is at i (the end of text when it never closes)."""
    depth, n = 0, len(text)
    while i < n:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _hd_subst_end(text: str, i: int) -> int:
    """Index just past the `)` closing a `$(` whose body starts at i, by command rules (quotes are quotes)."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return n
            i = j + 1
        elif c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 2
                elif text.startswith("$(", i):
                    i = _hd_subst_end(text, i + 2)
                else:
                    i += 1
            i += 1
        elif text.startswith("$(", i):
            i = _hd_subst_end(text, i + 2)
        elif c == "(":
            depth += 1
            i += 1
        elif c == ")":
            if depth == 0:
                return i + 1
            depth -= 1
            i += 1
        else:
            i += 1
    return n


def _hd_substitutions(body: str) -> list:
    """The command text of each `$(...)` and backtick substitution in an unquoted-delimiter heredoc body.

    Heredoc rules: `'` and `"` are literal characters (`it's $(rm x)` runs rm), `\\$`, `` \\` `` and `\\\\` are
    escapes, `$((...))` is arithmetic. Inside a substitution quotes start fresh, as in bash."""
    found, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            i += 2
        elif body.startswith("$((", i):
            i = _hd_arith_end(body, i + 1)
        elif body.startswith("$(", i):
            j = _hd_subst_end(body, i + 2)
            found.append(body[i + 2:j - 1] if j <= n and body[j - 1:j] == ")" else body[i + 2:])
            i = j
        elif c == "`":
            j = i + 1
            while j < n and body[j] != "`":
                j += 2 if body[j] == "\\" else 1
            found.append(re.sub(r"\\([\\`$])", r"\1", body[i + 1:j]))
            i = j + 1
        else:
            i += 1
    return found


def _hd_feeds_shell(prefix: str, rest: str) -> bool:
    """True when a heredoc is read by a shell, so its body runs as code: a shell word (`bash`, `sudo -u x sh -s`,
    `ssh host`) in the command before the operator (`prefix`), or a pipe into one after it (`rest`)."""
    import shlex
    if _HD_PIPED_SHELL.search(rest):
        return True
    seg = re.split(r"[;&|(]", prefix)[-1]
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    return any(os.path.basename(w) in _HD_SHELLS for w in words)


def _split_heredocs(text: str, _level: int = 0) -> tuple:
    """(code, bodies) of shell `text`, by bash rules: the command text a shell parses, and the heredoc bodies.

    `code` is `text` without heredoc bodies, their terminator lines and `#` comments (a `#` that begins a word
    outside quotes, to the end of its line). Text after a terminator stays code. A body is data, so an apostrophe
    in it (`it's done`) can no longer unbalance the quotes of the commands after it. Every `<<WORD`/`<<-WORD` form
    is handled (bare, 'quoted', "quoted", \\escaped), several on one line in order, `<<-` tab stripping, an
    unterminated body to the end. Two kinds of body still reach `code`:
    - a body fed to a shell (`_hd_feeds_shell`) is code that runs: it is split recursively and kept;
    - each `$(...)`/backtick substitution of an unquoted-delimiter body runs before the command does: its command
      text is appended as its own line (`_hd_substitutions`).
    `bodies` lists every other body as (text, quoted delimiter), for a guard that scans data conservatively.
    Quotes that never close leave the rest of `text` as code, unchanged (see `_lexable`)."""
    if "<<" not in text and "#" not in text:
        return text, []
    out, bodies, pending, stack = [], [], [], []
    n, i, line_start = len(text), 0, 0

    def take_bodies(i: int) -> int:
        for delim, strip_tabs, quoted, op_start, nl in pending:
            lines = []
            while i < n:
                e = text.find("\n", i)
                e = n if e < 0 else e
                line = text[i:e]
                i = min(e + 1, n)
                if strip_tabs:
                    line = line.lstrip("\t")
                if line.rstrip("\r") == delim:
                    break
                lines.append(line)
            body = "\n".join(lines)
            if _hd_feeds_shell(text[line_start:op_start], text[op_start:nl]):
                runs = [body]
            else:
                bodies.append((body, quoted))
                runs = [] if quoted else _hd_substitutions(body)
            for run in runs:
                sub_code, sub_bodies = _split_heredocs(run, _level + 1) if _level < 16 else (run, [])
                out.append(sub_code + "\n")
                bodies.extend(sub_bodies)
        pending.clear()
        return i

    while i < n:
        c = text[i]
        top = stack[-1] if stack else ""
        if top == '"':
            if c == "\\":
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == '"':
                stack.pop()
            elif text.startswith("$((", i):
                j = _hd_arith_end(text, i + 1)
                out.append(text[i:j])
                i = j
                continue
            elif text.startswith("$(", i):
                stack.append(0)
                out.append("$(")
                i += 2
                continue
            elif c == "`":
                stack.append("`")
            out.append(c)
            i += 1
            continue
        if c == "\\":
            out.append(text[i:i + 2])
            i += 2
        elif c == "'" or text.startswith("$'", i):
            j = i + 1 + (c == "$")
            while j < n and text[j] != "'":
                j += 2 if c == "$" and text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif c == '"':
            stack.append('"')
            out.append(c)
            i += 1
        elif c == "`" and top == "`":
            stack.pop()
            out.append(c)
            i += 1
        elif isinstance(top, int) and c in "()":
            if c == ")" and top == 0:
                stack.pop()
            else:
                stack[-1] += 1 if c == "(" else -1
            out.append(c)
            i += 1
        elif text.startswith("$((", i) or (text.startswith("((", i) and _hd_word_start(text, i)):
            j = _hd_arith_end(text, i + (c == "$"))
            out.append(text[i:j])
            i = j
        elif c == "#" and _hd_word_start(text, i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("<<", i) and not text.startswith("<<<", i) and (i == 0 or text[i - 1] != "<"):
            k = i + 2
            strip_tabs = text.startswith("-", k)
            k += strip_tabs
            while k < n and text[k] in " \t":
                k += 1
            word, quoted, w = [], False, k
            while w < n and text[w] not in _HD_WORD_BREAK:
                if text[w] in "'\"":
                    e = text.find(text[w], w + 1)
                    e = n if e < 0 else e
                    word.append(text[w + 1:e])
                    quoted, w = True, e + 1
                elif text[w] == "\\":
                    word.append(text[w + 1:w + 2])
                    quoted, w = True, w + 2
                else:
                    word.append(text[w])
                    w += 1
            if w == k:  # `<<` with no word: not a heredoc (bash reports it)
                out.append("<<")
                i += 2
                continue
            nl = text.find("\n", w)
            pending.append(("".join(word), strip_tabs, quoted, i, n if nl < 0 else nl))
            out.append(text[i:w])
            i = w
        elif c == "\n":
            out.append(c)
            i += 1
            if pending:
                i = take_bodies(i)
            line_start = i
        else:
            out.append(c)
            i += 1
    return "".join(out), bodies


def _lexable(text: str, lex, dropped: Optional[list] = None) -> str:
    """`text` when `lex(text)` lexes it; otherwise the part of it that does, never nothing.

    A quote that never closes (`echo don't`) makes a lexer raise ValueError for the whole text, but bash has
    already run every line before it. So keep each run of whole lines that lexes on its own, joining a line with
    the following ones (up to 32) while a multi-line quote may close; a line no run closes is dropped (appended to
    `dropped` when given, for a guard that judges it some other way) and the scan resumes at the next line."""
    try:
        lex(text)
        return text
    except ValueError:
        pass
    lines, keep, i = text.split("\n"), [], 0
    while i < len(lines):
        for j in range(i, min(len(lines), i + 32)):
            chunk = "\n".join(lines[i:j + 1])
            try:
                lex(chunk)
            except ValueError:
                continue
            keep.append(chunk)
            i = j + 1
            break
        else:
            if dropped is not None:
                dropped.append(lines[i])
            i += 1
    return "\n".join(keep)

# ---- end of the shared heredoc block ---------------------------------------------------------------------------


def _strip_heredocs(text: str) -> str:
    """Drop here-doc bodies (`cat > f <<'EOF' ... EOF`), comments and the `<<EOF` operators: the body is data,
    and an apostrophe in it would otherwise make the whole command unlexable (and so unchecked). The `$(...)`
    and backtick substitutions of an unquoted-delimiter body run, so they stay as commands (`_split_heredocs`)."""
    return HEREDOC.sub(" ", _split_heredocs(text)[0])


def _lex(text: str) -> List[str]:
    text = _expose_substitutions(text)  # `out="$(cmd > f)"` writes f like `out=$(cmd > f)`
    lex = shlex.shlex(_mask_quoted_redirects(_drop_fd_prefixes(text.replace("\n", " ; "))), posix=True,
                      punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return _unmask_quoted(list(lex))


def _tokens(text: str) -> List[str]:
    """Tokens of the commands in `text`. A line whose quotes never close is dropped, never the whole command:
    bash has already run the lines before it (`_lexable`)."""
    code = _strip_heredocs(text)
    return _lex(_lexable(code, _lex))


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
