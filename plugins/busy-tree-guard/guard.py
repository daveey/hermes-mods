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


def _tokens(text: str) -> List[str]:
    text = _expose_substitutions(text)  # `out="$(rm x)"` runs rm like `out=$(rm x)`
    lex = shlex.shlex(_mask_quoted_redirects(_drop_fd_prefixes(text.replace("\n", " ; "))), posix=True,
                      punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return _unmask_quoted(list(lex))
    except ValueError:
        return []


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
