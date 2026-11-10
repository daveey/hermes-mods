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

def _tokens(text: str) -> List[str]:
    lex = shlex.shlex(text.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
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
        if set(tok) <= set("<>&|") and (">" in tok or "<" in tok):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if argv and argv[-1].isdigit():
                argv.pop()
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
