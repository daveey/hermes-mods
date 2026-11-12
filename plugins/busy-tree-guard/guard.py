"""Decide whether a tool call edits a git tree that a running background test still reads.

A suite started in the background (``terminal(background=true)``) reads its tree for its whole
run. An edit there mid-run makes its "OK" match no revision (self-heal agent-10166983a7: a
20-minute ``t.sh $PWD/fix2`` passed while the agent patched ``fix2``). This module:

- lists THIS task's running background processes from the Hermes process registry and keeps the
  test-like ones (pytest, unittest, nose2, tox/nox, ``t.sh``, any word starting ``test``/``tests``);
- maps each to its busy trees: the git top-level at each command's effective cwd (following
  ``cd``) and of existing absolute or ``$PWD/``-relative arguments (venv interpreters ignored);
- extracts the paths an edit would touch: ``patch``/``write_file`` (``path``, V4A ``*** ... File:``
  headers), and shell commands that change files (``sed -i``/``perl -i``, ``>``/``>>``/``tee``,
  ``rm``/``mv``/``cp``/``touch``/``truncate``, mutating git subcommands, following ``cd DIR``).

An edit whose path lies inside a busy tree is refused. Kill switch: set ``BUSY_TREE_GUARD=0``
in the agent environment before starting Hermes, not inline in a terminal command.
Not covered: ``execute_code`` file writes, edits by other tasks/sessions, foreground runs.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

EDIT_TOOLS = {"patch", "write_file", "mcp__patch", "mcp__write_file"}
SHELL_TOOLS = {"terminal", "mcp__terminal"}
NESTED_TOOLS = {"tool_call", "mcp__tool_call"}

TEST_WORD = re.compile(
    r"(?:^|[/\s'\"=])(?:py\.?test|unittest|nose2|tox|nox|t\.sh|test-all|"
    r"(?:[\w.-]*[_-])?tests?(?:[_\-.][\w.-]*)?)(?=$|[\s'\";|&)/:])", re.I)
SEPARATORS = {"|", "||", "&&", ";", "&", "(", ")", ";;", "|&", "\n"}
WRAPPERS = {"sudo", "env", "nice", "nohup", "time", "timeout", "command", "exec", "stdbuf", "ionice"}
SHELLS = {"bash", "sh", "zsh", "dash"}
FILE_WRITERS = {"rm", "mv", "cp", "touch", "truncate", "install", "ln", "rsync", "unzip", "tar"}
GIT_MUTATORS = {"checkout", "switch", "reset", "stash", "apply", "am", "commit", "restore", "rebase",
                "merge", "pull", "cherry-pick", "revert", "clean", "rm", "mv"}
VENV_PART = re.compile(r"/(?:\.?venv[^/]*|site-packages|\.tox|\.nox)(?:/|$)")
V4A_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$|^\*\*\* Move to:\s*(.+?)\s*$", re.M)

_registry_override: Any = None
_toplevel_cache: dict = {}


def enabled() -> bool:
    return os.environ.get("BUSY_TREE_GUARD", "").strip().lower() not in {"0", "off", "false", "no"}


def set_registry(reg: Any) -> None:
    global _registry_override
    _registry_override = reg


def _registry() -> Any:
    if _registry_override is not None:
        return _registry_override
    from tools.process_registry import process_registry  # core; absent outside Hermes
    return process_registry


def _hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")


# ---------------------------------------------------------------- busy trees

def is_test_command(command: str) -> bool:
    return bool(TEST_WORD.search(" " + command))


def git_toplevel(path: str) -> Optional[str]:
    p = Path(path)
    d = p if p.is_dir() else p.parent
    # Walk up to an existing dir (a new file's parent may not exist yet).
    while not d.exists() and d != d.parent:
        d = d.parent
    key = str(d)
    if key in _toplevel_cache:
        return _toplevel_cache[key]
    top = None
    try:
        out = subprocess.run(["git", "-C", key, "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            top = os.path.realpath(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        top = None
    if len(_toplevel_cache) > 512:
        _toplevel_cache.clear()
    _toplevel_cache[key] = top
    return top


def _command_paths(command: str, cwd: str) -> Iterator[str]:
    for tok in _tokens(command):
        for word in re.split(r"[=:]", tok) if "=" in tok else [tok]:
            word = _expand(word, cwd)
            if not word.startswith("/") or VENV_PART.search(word):
                continue
            if os.path.exists(word):
                yield word


def busy_trees(proc: dict) -> List[str]:
    cwd = str(proc.get("cwd") or "")
    command = str(proc.get("command") or "")
    trees: List[str] = []
    # Registry cwd is the shell's launch directory, not necessarily the test's.
    # Follow cd before assigning a command to a tree; never union in the launch
    # tree unconditionally (it may be a completely unrelated worktree).
    cwd = cwd or "/"
    for argv, _ in _segments(_tokens(command)):
        argv = _strip_wrappers(argv)
        if not argv:
            continue
        if argv[0] == "cd" and len(argv) > 1:
            cwd = _abs(argv[1], cwd)
            continue
        for cand in [cwd] + list(_command_paths(shlex.join(argv), cwd)):
            top = git_toplevel(cand)
            if top and top not in trees:
                trees.append(top)
    return trees


def running_tests(task_id: str) -> List[dict]:
    reg = _registry()
    rows = reg.list_sessions(task_id=task_id or None)
    out = []
    for row in rows:
        if row.get("status") != "running":
            continue
        if (row.get("owner_task_id") or "") != (task_id or ""):
            continue
        command = row.get("command") or ""
        try:
            full = reg.get(row.get("session_id"))
            command = getattr(full, "command", None) or command
        except Exception:
            pass
        if not is_test_command(command):
            continue
        out.append(dict(row, command=command))
    return out


# ---------------------------------------------------------------- edit targets

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
    touches `<`/`>` is an fd; a spaced `rm 2 > x` keeps `2` as an operand, as in bash."""
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

# Readers known to read a heredoc body only as data. A body is data only when every command that reads it is one
# of these; any other reader, or one this can't name (`su -`, `sudo -s`, `source /dev/stdin`, `"$SHELL"`, `eval`,
# `xargs sh`, `{ ...; }`), may run it as shell code, so the body stays code (fail closed).
_HD_DATA_READERS = frozenset({
    "cat", "tee", "grep", "egrep", "fgrep", "rg", "head", "tail", "wc", "sort", "uniq", "tr", "cut", "paste",
    "column", "fold", "fmt", "nl", "rev", "base64", "md5sum", "sha1sum", "sha256sum", "shasum", "xxd", "od",
    "diff", "cmp", "jq", "yq", "less", "more", "git", "gh", "patch", "pbcopy", "true", ":"})
_HD_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?\Z")
# Commands known to use a command substitution's output only as data (`git commit -m "$(cat <<'EOF' ...)"`,
# `x=$(cat <<EOF ...)`). Any other consumer (a shell, `eval`, `source`, `ssh`, `xargs`, `python -c`, ...) may run it.
_HD_DATA_CONSUMERS = frozenset({"git", "gh", "export", "local", "declare", "typeset", "readonly"})
_HD_WRAPPERS = frozenset({"sudo", "doas", "env", "nohup", "command", "exec", "time"})
_HD_ARG_OPTS = frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U", "--user", "--group"})
_HD_REDIRECT = re.compile(r"(?:\d+|&)?(?:<<<|<<-?|<>|>>|>\||>&|<&|&>>?|<|>)")
_HD_SEPARATOR = re.compile(r"\|\||&&|\|&?|;|\(|\)|(?<![<>])&(?!>)")
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


def _hd_data_reader(words: list, consumer: bool = False) -> bool:
    """True when the simple command `words` reads its stdin only as data: a `_HD_DATA_READERS` name or Python,
    also behind `sudo`/`env`/`exec`-style wrappers, or no command word at all (`> f <<EOF`). `sudo -s`/`-i` is
    a shell. With `consumer`: whether it uses a command substitution in its words only as data instead (one of
    `_HD_DATA_CONSUMERS`, or a bare assignment)."""
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        m = _HD_REDIRECT.match(w)
        if m:
            i += 2 if m.end() == len(w) else 1
            continue
        if re.match(r"[A-Za-z_]\w*\+?=", w):
            i += 1
            continue
        base = os.path.basename(w)
        if base in _HD_WRAPPERS:
            i += 1
            while i < n and words[i].startswith("-") and words[i] != "-":
                opt = words[i]
                if base in ("sudo", "doas") and (opt in ("--shell", "--login") or (
                        not opt.startswith("--") and set(opt[1:]) & {"s", "i"})):
                    return False
                i += 2 if opt in _HD_ARG_OPTS else 1
            continue
        if consumer:
            return base in _HD_DATA_CONSUMERS
        return base in _HD_DATA_READERS or bool(_HD_PYTHON.match(base))
    return True


def _hd_feeds_shell(prefix: str, rest: str) -> bool:
    """False only when a heredoc body is known to be read as data; True (its body may run as code) otherwise.
    `prefix` is the line before the `<<` operator, `rest` the line from it on. The body goes to the command that
    holds the operator and, through `|`/`|&`, to each later stage of that pipeline: every one must be a data
    reader (`_hd_data_reader`). So `su - <<EOF`, `sudo -s <<EOF`, `source /dev/stdin <<EOF`, `"$SHELL" <<EOF`
    and `cat <<EOF | sudo -s` are all code, and so is any reader this does not know (fail closed)."""
    import shlex

    def words(seg: str) -> list:
        try:
            return shlex.split(seg)
        except ValueError:
            return seg.split()

    stages, pos = [], 0
    for m in _HD_SEPARATOR.finditer(rest):
        stages.append(rest[pos:m.start()])
        pos = m.end()
        if m.group() not in ("|", "|&"):
            pos = -1
            break
    if pos >= 0:
        stages.append(rest[pos:])
    stages[0] = _HD_SEPARATOR.split(prefix)[-1] + stages[0]
    if re.search(r"[<>]\(", prefix + rest):  # a process substitution on the line (`tee >(bash)`) may run it
        return True
    return not all(_hd_data_reader(words(seg)) for seg in stages)


def _hd_subst_data(text: str, openers: tuple) -> bool:
    """True when every command substitution enclosing a heredoc (`openers`: the index of each enclosing `$(`,
    backtick, `<(` or `>(`, outermost first) hands its output to a data consumer (`_hd_data_reader` with
    `consumer`): `git commit -m "$(cat <<'EOF' ...)"` and `x=$(cat <<EOF ...)` are data, while
    `bash -c "$(cat <<'EOF' ...)"`, `eval "$(cat ...)"` or a process substitution may run the body."""
    import shlex

    for k in range(len(openers) - 1, -1, -1):
        pos = openers[k]
        if text[pos] in "<>":
            return False
        lo = 0 if k == 0 else openers[k - 1] + (1 if text[openers[k - 1]] == "`" else 2)
        start = text.rfind("\n", 0, pos) + 1
        while start >= 2 and text[start - 2] == "\\":  # a `\`-continued command line
            start = text.rfind("\n", 0, start - 2) + 1
        seg = text[max(lo, start):pos].replace("\\\n", " ")
        prev = None
        while prev != seg:  # earlier, closed substitutions on the line are one word each
            prev, seg = seg, re.sub(r"\$\([^()]*\)|`[^`]*`", "_", seg)
        seg = _HD_SEPARATOR.split(seg)[-1]
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if not _hd_data_reader(words, consumer=True):
            return False
    return True


def _hd_written(prefix: str, rest: str) -> list:
    """The files the command holding a heredoc writes its body to: `> P`, `>> P`, `>| P` or `tee P`."""
    import shlex

    seg = _HD_SEPARATOR.split(prefix)[-1] + _HD_SEPARATOR.split(rest)[0]
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    paths, tee = [], False
    for k, w in enumerate(words):
        m = re.match(r"(?:\d|&)?(?:>>|>\||>)(.*)", w)
        if m and not w.startswith((">&", "&>&")):
            target = m.group(1) or (words[k + 1] if k + 1 < len(words) else "")
            if target and not target.startswith("&"):
                paths.append(target)
        elif os.path.basename(w) == "tee":
            tee = True
        elif tee and not w.startswith("-") and not _HD_REDIRECT.match(w) and not (
                k and _HD_REDIRECT.fullmatch(words[k - 1])):
            paths.append(w)
    return paths


def _hd_assigned(text: str, openers: tuple) -> str:
    """The variable a data heredoc is assigned to (`x=$(cat <<EOF ...)`, `export x="$(cat <<EOF ...)"`), else ''.
    Only the innermost enclosing substitution counts: its text must directly follow `NAME=`."""
    if not openers or text[openers[-1]] not in "$`":
        return ""
    k = len(openers) - 1
    lo = 0 if k == 0 else openers[k - 1] + (1 if text[openers[k - 1]] == "`" else 2)
    start = text.rfind("\n", 0, openers[-1]) + 1
    while start >= 2 and text[start - 2] == "\\":
        start = text.rfind("\n", 0, start - 2) + 1
    seg = _HD_SEPARATOR.split(text[max(lo, start):openers[-1]].replace("\\\n", " "))[-1]
    m = re.search(r"(?:^|\s)(?:(?:export|local|declare|typeset|readonly)\s+(?:-\S+\s+)*)?([A-Za-z_]\w*)\+?=\S*\Z", seg)
    return m.group(1) if m else ""


_HD_DQ_WORD = re.compile(r"(?:then|do|else|elif|while|until|if)\b")


def _hd_mask(code: str, keep: tuple = ()) -> str:
    """`code` (same length) with what bash does not expand made inert for pattern matching: the text of '...'
    (unless it is one plain path-like word or one of the `keep` paths), backslash pairs, and in "..." (outside a
    `$(...)`) the command separators `;` `&` `|` `(` `{` newline and keywords (`echo "do $x"` is not a loop), so a
    quoted argument stays one word. `$x` in "..." stays visible (even named like a keyword: `"$if"`, `"${do}"`), `$x` in '...' or after `\\` does not."""
    out, i, n, dq, depth = [], 0, len(code), False, 0
    while i < n:
        c = code[i]
        if c == "\\":
            out.append("  " if code[i + 1:i + 2] == "\n" else "__")
            i += 2
        elif dq:
            if code.startswith("$(", i) and not code.startswith("$((", i):
                depth += 1
                out.append("$(")
                i += 2
                continue
            if depth:
                depth -= c == ")"
                out.append(c)
            elif c == '"':
                dq = False
                out.append(c)
            elif c in ";&|\n({" and not (c == "{" and code[i - 1:i] == "$") and not (c == "(" and code[i - 1:i] == "$"):
                out.append(" ")
            elif (m := _HD_DQ_WORD.match(code, i)) and not code[i - 1:i].isalnum() \
                    and code[i - 1:i] != "$" and code[i - 2:i] != "${":  # `$if`/`${if}` is a variable, not a keyword
                out.append("_" * len(m.group()))
                i += len(m.group())
                continue
            else:
                out.append(c)
            i += 1
        elif c == '"':
            dq = True
            out.append(c)
            i += 1
        elif c == "'" or code.startswith("$'", i):
            d = c == "$"
            j = i + 1 + d
            while j < n and code[j] != "'":
                j += 2 if d and code[j] == "\\" else 1
            seg, inner = code[i:j + 1], code[i + 1 + d:j]
            keepit = not d and (re.fullmatch(r"[\w./~+-]+", inner) or inner in keep)
            out.append(seg if keepit or len(seg) < 2 else "'" + "_" * (len(seg) - 2) + seg[-1:])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


_HD_SHELL = r"(?<![\w./$-])(?:[\w./-]*/)?(?:ba|z|da|k|mk)?sh(?![\w.-])"
_HD_RUN_WRAP = (r"(?:(?:sudo|doas|env|nohup|command|exec|time)[ \t]+(?:(?:-[ugCDhprtTU]|--user|--group)[ \t]+\S+[ \t]+"
                r"|-\S*[ \t]+|\w+=\S*[ \t]+)*)*")
_HD_RUN_START = (r"(?:^|(?<![=(])\(|[;&|`{]|\b(?:then|do|else|elif|while|until|if)\b)\s*(?:\w+=\S*[ \t]+)*"
                 + _HD_RUN_WRAP)


def _hd_exec_re(src: str, extra: tuple = ()):
    """Regex (on `_hd_mask`ed text) for a command that runs the text `src` matches as code: `src` at command
    position, an `eval`/`source`/`.` argument, the argument of `SHELL -c`, `echo src | SHELL`, `SHELL <<< src`.
    Each form is bounded to one command (`[^;&|\\n]`) and starts at a command position, so `echo bash -c $x` and
    `'$x'` do not match. `extra` are further whole patterns (already starting with `_HD_RUN_START`)."""
    s, sh = _HD_RUN_START, _HD_SHELL
    forms = [
        s + '"?' + src,
        s + r"(?:eval\b|source\b|\.[ \t])[^;&|\n]*?" + src,
        s + sh + r"[ \t][^;&|\n]*?" + src,  # any argument of a shell: `bash "$x"`, `bash -x $x`, `bash -c "$x"`
        s + r"(?:echo|printf|print)\b[^;&|\n]*?" + src + r"[^;&|\n]*\|\s*" + _HD_RUN_WRAP + sh,
        s + sh + r"[^;&|\n]*?<<<\s*\"?" + src,
    ]
    return re.compile("|".join("(?:%s)" % f for f in forms + list(extra)), re.M)


def _hd_file_run_re(path: str):
    """`_hd_exec_re` for a file a heredoc wrote: `bash < P`, `cat P | bash`, `source /dev/stdin < P`,
    `bash <(cat P)`, and `$(cat P)`, `$(< P)`, backticks or `<(cat P)` where code runs (`_hd_exec_re`)."""
    p = r"[\"']?(?:\./)?" + re.escape(path[2:] if path.startswith("./") else path) + r"[\"']?(?![\w/.-])"
    s, sh = _HD_RUN_START, _HD_SHELL
    subst = r"(?:\$\(|<\(|`)\s*(?:cat\s+(?:-\S+\s+)*|<\s*)" + p + r"\s*[)`]"
    return _hd_exec_re(subst, (
        s + sh + r"(?:[ \t]+-\S+)*[ \t]*<[ \t]*" + p,
        s + r"cat[ \t]+(?:-\S+[ \t]+)*" + p + r"[ \t]*\|[ \t]*" + _HD_RUN_WRAP + sh,
        s + r"(?:source|\.)[ \t]+/dev/stdin[ \t]*<[ \t]*" + p,
        s + sh + r"[ \t]+(?:-\S+[ \t]+)*<\(\s*cat[ \t]+(?:-\S+[ \t]+)*" + p,
    ))


def _hd_var_run_re(name: str):
    """`_hd_exec_re` for the variable `name` a heredoc body was assigned to: `$name` / `${name...}`."""
    return _hd_exec_re(r"\$(?:" + re.escape(name) + r"(?!\w)|\{" + re.escape(name) + r"[}:\-+=?/%^,\[@])")


def _split_heredocs(text: str, _level: int = 0) -> tuple:
    """(code, bodies) of shell `text`, by bash rules: the command text a shell parses, and the heredoc bodies.

    `code` is `text` without heredoc bodies, their terminator lines and `#` comments (a `#` that begins a word
    outside quotes, to the end of its line). Text after a terminator stays code. A body is data, so an apostrophe
    in it (`it's done`) can no longer unbalance the quotes of the commands after it. Every `<<WORD`/`<<-WORD` form
    is handled (bare, 'quoted', "quoted", \\escaped), several on one line in order, `<<-` tab stripping, an
    unterminated body to the end. Two kinds of body still reach `code`:
    - a body fed to a shell, or to any reader not known to read it as data (`_hd_feeds_shell`), is code that may
      run: it is split recursively and kept. So is a body read inside a command substitution whose output is not
      used only as data (`bash -c "$(cat <<'EOF' ...)"`, `_hd_subst_data`), and a body written to a file that
      `text` then runs (`cat > s.sh <<EOF ... EOF` then `bash s.sh`, `_hd_written`);
    - each `$(...)`/backtick substitution of an unquoted-delimiter body runs before the command does: its command
      text is appended as its own line (`_hd_substitutions`).
    `bodies` lists every other body as (text, quoted delimiter), for a guard that scans data conservatively.
    Quotes that never close leave the rest of `text` as code, unchanged (see `_lexable`)."""
    if "<<" not in text and "#" not in text:
        return text, []
    out, bodies, pending, stack, subs, written, assigned = [], [], [], [], [], [], []
    n, i, line_start = len(text), 0, 0

    def take_bodies(i: int) -> int:
        for delim, strip_tabs, quoted, op_start, nl, openers, at in pending:
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
            if _hd_feeds_shell(text[line_start:op_start], text[op_start:nl]) or not _hd_subst_data(text, openers):
                runs = [body]
            else:
                bodies.append((body, quoted))
                runs = [] if quoted else _hd_substitutions(body)
                written.extend((path, body, at) for path in _hd_written(text[line_start:op_start], text[op_start:nl]))
                name = _hd_assigned(text, openers)
                if name:
                    assigned.append((name, body, at))
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
                subs.append(i)
                out.append("$(")
                i += 2
                continue
            elif c == "`":
                stack.append("`")
                subs.append(i)
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
            subs.pop()
            out.append(c)
            i += 1
        elif c == "`":
            stack.append("`")
            subs.append(i)
            out.append(c)
            i += 1
        elif isinstance(top, int) and c in "()":
            if c == ")" and top == 0:
                stack.pop()
                subs.pop()
            else:
                stack[-1] += 1 if c == "(" else -1
            out.append(c)
            i += 1
        elif text.startswith("$((", i) or (text.startswith("((", i) and _hd_word_start(text, i)):
            j = _hd_arith_end(text, i + (c == "$"))
            out.append(text[i:j])
            i = j
        elif text.startswith("$(", i) or (c in "<>" and text.startswith("(", i + 1)):  # `$(`, `<(`, `>(`
            stack.append(0)
            subs.append(i)
            out.append(text[i:i + 2])
            i += 2
        elif text.startswith("${", i):  # parameter expansion: a `<<` or `#` in it (`${x/<</y}`) is text
            j, depth = i + 2, 1
            while j < n and depth:
                if text[j] == "\\":
                    j += 1
                elif text[j] in "{}":
                    depth += 1 if text[j] == "{" else -1
                j += 1
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
            pending.append(("".join(word), strip_tabs, quoted, i, n if nl < 0 else nl, tuple(subs),
                            sum(map(len, out))))
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
    code = "".join(out)
    masked = _hd_mask(code, tuple(w[0] for w in written)) if written or assigned else ""
    ran = []
    for name, body, at in assigned:  # a body assigned to a variable that is run after it (`x=$(cat <<EOF ...)`, then `$x`, `eval "$x"`)
        if _hd_var_run_re(name).search(masked, at):
            ran.append(body)
    for path, body, at in written:  # a script written from a body and run later in `text` (`bash s.sh`, `./s.sh`, `bash < s.sh`)
        p = r"[\"']?(?:\./)?" + re.escape(path[2:] if path.startswith("./") else path) + r"[\"']?(?![\w/.-])"
        if re.compile(r"(?<![\w/.-])(?:(?:ba|z|da|k|mk)?sh|source|\.)\s+(?:-\S+\s+)*" + p).search(code, at) \
                or re.compile(r"(?:^|[;&|(`])\s*" + p, re.M).search(code, at) or _hd_file_run_re(path).search(masked, at):
            ran.append(body)
    for body in ran:
        sub_code, sub_bodies = _split_heredocs(body, _level + 1) if _level < 16 else (body, [])
        code += "\n" + sub_code + "\n"
        bodies.extend(sub_bodies)
    return code, bodies


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


def _lex(text: str) -> List[str]:
    text = _expose_substitutions(text)  # `out="$(rm x)"` runs rm like `out=$(rm x)`
    lex = shlex.shlex(_mask_quoted_redirects(_drop_fd_prefixes(text.replace("\n", " ; "))), posix=True,
                      punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return _unmask_quoted(list(lex))


def _tokens(text: str) -> List[str]:
    """Tokens of the commands in `text`. Comments and heredoc bodies are split off first (`_split_heredocs`: an
    apostrophe in them no longer hides the commands after them, and an unquoted body's `$(...)` is a command);
    a line whose quotes never close is dropped, never the whole command (`_lexable`)."""
    code = _split_heredocs(text)[0]
    return _lex(_lexable(code, _lex))


def _expand(word: str, cwd: str) -> str:
    word = re.sub(r"\$(?:\{PWD\}|PWD\b)", cwd, word)
    word = re.sub(r"\$(?:\{HOME\}|HOME\b)", str(Path.home()), word)
    word = re.sub(r"\$(?:\{HERMES_HOME\}|HERMES_HOME\b)", _hermes_home(), word)
    if word == "~" or word.startswith("~/"):
        word = str(Path.home()) + word[1:]
    return word


def _abs(word: str, cwd: str) -> str:
    word = _expand(word, cwd)
    return os.path.normpath(word if word.startswith("/") else os.path.join(cwd, word))


def _segments(tokens: List[str]) -> Iterator[Tuple[List[str], List[Tuple[str, str]]]]:
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
    while argv and (os.path.basename(argv[0]) in WRAPPERS or re.match(r"^\w+=", argv[0])):
        head = os.path.basename(argv[0])
        argv = argv[1:]
        if head == "timeout" and argv and not argv[0].startswith("-"):
            argv = argv[1:]
        while argv and argv[0].startswith("-") and head in {"nice", "sudo", "env", "ionice", "stdbuf"}:
            argv = argv[1:]
    return argv


def shell_targets(command: str, cwd: str, depth: int = 0) -> List[str]:
    """Paths (dirs or files) a shell command would change, best effort."""
    out: List[str] = []
    for argv, redirs in _segments(_tokens(command)):
        for op, target in redirs:
            # >&N duplicates, >&- closes, >&N- moves a descriptor. Bash's
            # >&file remains a file write, as do >1 and &>file.
            if op in {">&", "<&"} and re.fullmatch(r"(?:\d+-?|-)", target):
                continue
            if ">" in op and target and not target.startswith("&") and target != "/dev/null":
                out.append(_abs(target, cwd))
        argv = _strip_wrappers(argv)
        if not argv:
            continue
        head = os.path.basename(argv[0])
        rest = argv[1:]
        if head == "cd":
            if rest:
                cwd = _abs(rest[0], cwd)
            continue
        if head in SHELLS and depth < 3:
            for i, a in enumerate(rest[:-1]):
                if a == "-c" or (a.startswith("-") and a.endswith("c") and not a.startswith("--")):
                    out += shell_targets(rest[i + 1], cwd, depth + 1)
                    break
            continue
        files = [a for a in rest if not a.startswith("-")]
        if head in {"sed", "perl"} and any(a == "-i" or a.startswith("-i") or a == "--in-place"
                                           or (re.match(r"^-[a-zA-Z]*i", a) and not a.startswith("--"))
                                           for a in rest):
            out += [_abs(f, cwd) for f in files[1:] or files]
        elif head == "tee":
            out += [_abs(f, cwd) for f in files]
        elif head in FILE_WRITERS:
            out += [_abs(f, cwd) for f in files]
        elif head == "git":
            gdir, i = cwd, 0
            while i < len(rest) and rest[i].startswith("-"):
                if rest[i] == "-C" and i + 1 < len(rest):
                    gdir = _abs(rest[i + 1], gdir)
                    i += 2
                    continue
                i += 2 if rest[i] in {"-c", "--git-dir", "--work-tree"} else 1
            sub = rest[i] if i < len(rest) else ""
            if sub in GIT_MUTATORS:
                if sub == "stash" and len(rest) > i + 1 and rest[i + 1] in {"list", "show"}:
                    continue
                out.append(gdir)
    return out


def edit_targets(tool_name: str, args: Any, cwd: str) -> List[str]:
    args = args if isinstance(args, dict) else {}
    if tool_name in EDIT_TOOLS:
        paths = []
        if args.get("path"):
            paths.append(_abs(str(args["path"]), cwd))
        patch = args.get("patch")
        if isinstance(patch, str):
            for m in V4A_FILE.finditer(patch):
                paths.append(_abs(m.group(1) or m.group(2), cwd))
        return paths
    if tool_name in SHELL_TOOLS:
        return shell_targets(str(args.get("command") or ""), str(args.get("workdir") or cwd))
    if tool_name in NESTED_TOOLS:
        out: List[str] = []
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                out += edit_targets(str(call.get("name") or ""), call.get("arguments"), cwd)
        return out
    return []


def _inside(path: str, tree: str) -> bool:
    real = os.path.realpath(path)
    return real == tree or real.startswith(tree.rstrip("/") + "/")


def check(tool_name: str, args: Any, task_id: str, cwd: str) -> Optional[Tuple[str, str, dict]]:
    """Return (target, tree, process) for the first edit inside a busy tree, else None."""
    targets = edit_targets(tool_name, args, cwd)
    if not targets:
        return None
    procs = running_tests(task_id)
    if not procs:
        return None
    for proc in procs:
        for tree in busy_trees(proc):
            for target in targets:
                if _inside(target, tree):
                    return target, tree, proc
    return None


def message(target: str, tree: str, proc: dict) -> str:
    return (f"busy-tree-guard: refused, {target} is inside {tree}, which your background test run "
            f"{proc.get('session_id')} (pid {proc.get('pid')}, started {proc.get('started_at')}) "
            f"is still reading: `{str(proc.get('command'))[:200]}`. An edit now makes its result match "
            "no revision. Ways out: make the edit in another worktree (`git worktree add --detach "
            "<dir> HEAD`), wait for the run to finish (process wait), or kill it "
            "(process kill) and rerun after the edit. Operator override: set BUSY_TREE_GUARD=0 "
            "in the agent process environment before starting Hermes (e.g. BUSY_TREE_GUARD=0 hermes). "
            "An inline assignment in a terminal command does not change the running agent's environment.")


def record(tool: str, target: str, tree: str, proc: dict) -> None:
    try:
        log = Path(_hermes_home()) / "logs" / "busy-tree-guard.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": tool, "target": target,
               "tree": tree, "proc": proc.get("session_id"), "pid": proc.get("pid"),
               "command": str(proc.get("command"))[:300],
               "card": os.environ.get("HERMES_KANBAN_TASK") or os.environ.get("HERMES_KANBAN_WORKER_TASK")}
        with log.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
