"""live-checkout-guard core: decide whether a tool call would mutate the protected live checkout in place.

Pure functions, no Hermes imports, shared by the Hermes plugin (__init__.py) and the Claude Code hook.
Each ``check_*`` returns None (allow) or a short reason string (refuse).

Protected root: ``$LIVE_CHECKOUT_ROOT`` (default ``~/code/hermes-mods``), compared after ``realpath`` so symlinks
into it (e.g. ``~/.hermes/plugins/<name>``) count as inside it. Kill switch: ``LIVE_CHECKOUT_GUARD=0``.

Best-effort lexical analysis, not a sandbox. See README.md for limitations.
"""

from __future__ import annotations

import ast
import contextvars
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_ROOT = "~/code/hermes-mods"

GIT_BLOCKED = {"commit", "stash", "rebase", "reset", "checkout", "restore", "apply", "am", "cherry-pick",
               "merge", "switch", "pull", "revert", "clean"}
GIT_STASH_READ = {"list", "show"}
# commands whose every path argument is written/removed
WRITE_ALL = {"rm", "mv", "touch", "truncate", "rmdir", "mkdir", "chmod", "unlink"}
# wrapper -> options that take a value (as the next word, ``--opt=value`` or attached ``-nVALUE``)
WRAPPER_OPTS = {
    "sudo": {"-u", "-g", "-p", "-C", "-U", "-r", "-t", "-T", "-D", "-h", "--user", "--group", "--prompt",
             "--close-from", "--other-user", "--role", "--type", "--command-timeout", "--chdir", "--host"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"},
    "time": {"-f", "-o", "--format", "--output"},
    "caffeinate": {"-t", "-w"},
    "exec": {"-a"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "nohup": set(), "command": set(), "builtin": set(),
}
WRAPPERS = set(WRAPPER_OPTS)
WRAPPER_POSITIONALS = {"timeout": 1}  # `timeout DURATION cmd`
CHDIR_OPTS = {"env": {"-C", "--chdir"}, "sudo": {"-D", "--chdir"}}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;"}
FILE_MUTATORS = {"write_file", "patch"}


def enabled() -> bool:
    return os.environ.get("LIVE_CHECKOUT_GUARD", "1").strip().lower() not in {"0", "false", "off", "no"}


def root() -> str:
    return os.path.realpath(os.path.expanduser(os.environ.get("LIVE_CHECKOUT_ROOT") or DEFAULT_ROOT))


def root_label() -> str:
    return os.environ.get("LIVE_CHECKOUT_ROOT") or DEFAULT_ROOT


# Shell analysis only: variables assigned earlier in the same command (``D=$TMPDIR/x; rm -rf $D``; a None value
# means "assigned, value unknown"). None outside check_shell, so file-tool and python paths are taken literally.
_SHELL_VARS: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("lcg_shell_vars", default=None)
# A cwd we cannot know (``cd $UNSET``, ``cd $(...)``): relative paths under it are not proven inside the root.
UNKNOWN_CWD = "\x00unknown-cwd"
_VAR = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _expand(path: str) -> Optional[str]:
    """Expand ``~``, ``$NAME`` and ``${NAME}`` (in-command assignments first, then the environment) while
    analysing a shell command. None when something stays unresolved (unset variable, ``$(...)``, backticks,
    other ``${...}`` forms): such a path is not proven inside the root."""
    shell_vars = _SHELL_VARS.get()
    if shell_vars is None:
        return os.path.expanduser(path)
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        name = m.group(1) or m.group(2)
        val = shell_vars[name] if name in shell_vars else os.environ.get(name)
        if val is None:
            missing = True
            return ""
        return val

    out = _VAR.sub(sub, os.path.expanduser(path))
    if missing or "$" in out or "`" in out:
        return None
    return os.path.expanduser(out)


def in_root(path: str, cwd: Optional[str] = None) -> bool:
    """True if ``path`` (relative to ``cwd``) resolves, through symlinks, to the protected root or below."""
    if not path or path.startswith(UNKNOWN_CWD):
        return False
    p = _expand(path)
    if p is None:
        return False
    if not os.path.isabs(p):
        if cwd and cwd.startswith(UNKNOWN_CWD):
            return False
        p = os.path.join(cwd or os.getcwd(), p)
    r = os.path.realpath(p)
    base = root()
    return r == base or r.startswith(base + os.sep)


def message(why: str) -> str:
    if why.startswith(HOME_EXPORT_WHY):
        return home_export_message(why)
    if why == "unfenced hermes update":
        return ("[live-checkout-guard] Refused: unfenced hermes update. Core updates go through each "
                "host's fenced route: the hub's hermes-core-update.timer (every 10 min, drain-first, "
                "batched) and hermes-sync's drain windows on the Macs. To wait for a commit, schedule "
                "the card with a wake check like `ssh <hub> 'git -C ~/.hermes/hermes-agent "
                "merge-base --is-ancestor <sha> HEAD'`. Read-only update flags are allowed. "
                "Escape hatch: prefix the command with HERMES_UPDATE_OK=1 for a user-approved update only.")
    r = root_label()
    return (f"[live-checkout-guard] Refused: {why}.\n"
            f"{r} is the live checkout that the sync job and other agents load from; edit it in place and "
            "you break their view of it. Work in a detached worktree instead:\n"
            f"  git -C {r} fetch origin\n"
            f"  git -C {r} worktree add --detach ~/code/wt-$(date +%s) origin/main   # any path outside {r}\n"
            "  cd <that worktree>; edit, test, git commit\n"
            "  git push origin HEAD:main            # publish; the sync job fast-forwards the live checkout\n"
            f"  git -C {r} worktree remove <that worktree>   # when done\n"
            f"Allowed on the live checkout: reads, git fetch, git worktree add, a clean `git merge --ff-only "
            "origin/main`, hermes-sync, skill_manage.")


# ---- paths: patch / write_file / editor tools --------------------------------------------------

_V4A = re.compile(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$|^\*\*\* Move to:\s*(.+?)\s*$", re.M)


def check_file_tool(tool: str, args: dict, cwd: Optional[str] = None) -> Optional[str]:
    cwd = args.get("workdir") or cwd
    paths = [args.get(k) for k in ("path", "file_path", "notebook_path") if isinstance(args.get(k), str)]
    if isinstance(args.get("patch"), str):
        for m in _V4A.finditer(args["patch"]):
            paths.append(m.group(1) or m.group(2))
    for p in paths:
        if in_root(p, cwd):
            return f"{tool} would write {p}, inside the live checkout"
    return None


# ---- shell -------------------------------------------------------------------------------------

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


def _drop_fd_prefixes(cmd: str) -> str:
    """Remove the file-descriptor number of each unquoted redirect (`2>x`, `2>>x`, `2>&1`, `1>|x`, `0<x`).

    shlex with punctuation_chars splits `2>/dev/null` into `2`, `>`, `/dev/null` and drops whitespace, so the
    `2` would read as an operand (`rm -r "$X" 2>/dev/null` was refused as "rm on 2"). Only a digit run that
    starts a word and directly touches `<`/`>` is an fd; a spaced `2 > x` keeps `2` as an operand, as in bash."""
    out, quote, i, n = [], "", 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(cmd[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if c in "'\"":
            quote = c
        elif c.isdigit() and (i == 0 or cmd[i - 1].isspace() or cmd[i - 1] in ";&|()"):
            m = _FD_PREFIX.match(cmd, i)
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


def _mask_quoted_redirects(cmd: str) -> str:
    """Mark each quoted or backslash-escaped `<`/`>` so it survives shlex as data.

    shlex drops the quotes, so `grep -c '>' README.md` lexes exactly like `grep -c > README.md` and README.md
    read as a redirect target. Returns `cmd` unchanged when its quotes do not balance (shlex then fails and the
    caller's fallback judges the raw text, as before)."""
    out, quote, i, n = [], "", 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(c + _QUOTE_MASK.get(cmd[i + 1], cmd[i + 1]))
                i += 2
                continue
            if c == quote:
                quote = ""
                out.append(c)
            else:
                out.append(_QUOTE_MASK.get(c, c))
        elif c == "\\" and i + 1 < n:
            out.append(_QUOTE_MASK.get(cmd[i + 1]) or cmd[i:i + 2])
            i += 2
            continue
        else:
            if c in "'\"":
                quote = c
            out.append(c)
        i += 1
    return cmd if quote else "".join(out)


def _unmask_quoted(toks: list[str]) -> list[str]:
    return [_Quoted(t.translate(_QUOTE_UNMASK)) if any(m in t for m in _QUOTE_MASK.values()) else t for t in toks]


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


def _lex(cmd: str) -> list[str]:
    cmd = _expose_substitutions(cmd)  # `out="$(rm x)"` runs rm like `out=$(rm x)`
    cmd = _drop_fd_prefixes(re.sub(r"\n", " ; ", cmd))
    lex = shlex.shlex(_mask_quoted_redirects(cmd), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return _unmask_quoted(list(lex))


def _tokens(cmd: str) -> list[str]:
    """Tokens of the commands in `cmd`, `;` between lines. Comments are dropped and heredoc bodies are split off
    first (`_split_heredocs`), so an apostrophe in either (`it's`) no longer hides the commands after it, and the
    substitutions of an unquoted-delimiter body are scanned as commands. Each body is then scanned as commands
    too (documented over-approximation), on its own so its quotes cannot reach the real commands. A line whose
    quotes never close is dropped, never the whole command (`_lexable`)."""
    code, bodies = _split_heredocs(cmd.replace("\r", ""))
    dropped: list[str] = []
    toks = _lex(_lexable(code, _lex, dropped))
    for line in dropped:  # a line whose quotes never close: judged by its whitespace words, as before
        toks += [";"] + line.split()
    for body, _quoted in bodies:
        toks += [";"] + _lex(_lexable(body, _lex))
    return toks


def _segments(toks: list[str]) -> list[list[str]]:
    out, cur = [], []
    for t in toks:
        if t in SEPARATORS or set(t) <= set(";&|()") and t:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _redirect_targets(seg: list[str]) -> tuple[list[str], list[str]]:
    """Split a segment into (words, redirect targets that are written). A quoted `'>'` is a word (`_Quoted`)."""
    words, targets, i = [], [], 0
    while i < len(seg):
        t = seg[i]
        m = None if isinstance(t, _Quoted) else re.match(r"^(\d*)(&>>?|>&|>\||>>?)(.*)$", t)
        if m and not t.startswith("<"):
            op, rest = m.group(2), m.group(3)
            if not rest and i + 1 < len(seg):
                rest = seg[i + 1]
                i += 1
            if op == ">&" and (rest.isdigit() or rest == "-"):
                pass
            elif rest:
                targets.append(rest)
        elif not isinstance(t, _Quoted) and re.match(r"^\d*<", t) and not t.startswith("<<"):
            if re.fullmatch(r"\d*<&?", t):
                i += 1  # input redirect target (read only) or input-dup fd (`2<&1`, `<&-`): not an operand
        else:
            words.append(t)
        i += 1
    return words, targets


def _git_clean(d: str) -> bool:
    try:
        out = subprocess.run(["git", "-C", d, "status", "--porcelain", "--untracked-files=all"], capture_output=True,
                             text=True, timeout=10)
    except Exception:
        return False
    return out.returncode == 0 and not out.stdout.strip()


def _join(cwd: str, p: str) -> str:
    q = _expand(p)
    if q is None:
        return UNKNOWN_CWD
    return q if os.path.isabs(q) else os.path.join(cwd, q)


def _check_git(args: list[str], cwd: str, git_env: Optional[dict] = None) -> Optional[str]:
    """``git_env``: GIT_DIR / GIT_WORK_TREE assignments that prefixed the command (relative to ``cwd``)."""
    git_env = git_env or {}
    d, i = cwd, 0
    git_dir = _join(cwd, git_env["GIT_DIR"]) if git_env.get("GIT_DIR") else None
    work_tree = _join(cwd, git_env["GIT_WORK_TREE"]) if git_env.get("GIT_WORK_TREE") else None
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a == "-C" and i + 1 < len(args):
            d = _join(d, args[i + 1])
            i += 1
        elif a in {"--git-dir", "--work-tree"} and i + 1 < len(args):
            if a == "--git-dir":
                git_dir = _join(d, args[i + 1])
            else:
                work_tree = _join(d, args[i + 1])
            i += 1
        elif a.startswith("--git-dir="):
            git_dir = _join(d, a.split("=", 1)[1])
        elif a.startswith("--work-tree="):
            work_tree = _join(d, a.split("=", 1)[1])
        elif a in {"-c", "--namespace", "--exec-path"}:
            i += 1
        i += 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    via = ""
    if git_dir and in_root(git_dir):
        via = " (via --git-dir/GIT_DIR)"
    elif work_tree and in_root(work_tree):
        via = " (via --work-tree/GIT_WORK_TREE)"
    elif not in_root(d) or git_dir or work_tree:
        return None  # an explicit git dir / work tree elsewhere wins over the cwd
    clean_dir = d if in_root(d) else root()
    if sub == "update-ref":
        # stdin can name arbitrary refs; never infer safety from shell input.
        if "--stdin" in rest or any(
                a == "refs/stash" or a.startswith("refs/hermes-retire/") for a in rest):
            return f"raw git update-ref on protected stash/retirement refs in the live checkout{via}"
    if sub == "reflog" and rest and rest[0] in {"delete", "expire", "drop", "write"}:
        if "--all" in rest or any(
                a == "stash" or a.startswith("stash@{") or a == "refs/stash"
                or a.startswith("refs/stash@{") or a.startswith("refs/hermes-retire/") for a in rest[1:]):
            return f"raw git reflog mutation on protected stash/retirement refs in the live checkout{via}"
    if sub == "stash":
        return None if rest and rest[0] in GIT_STASH_READ else f"git stash in the live checkout{via}"
    if sub == "pull":  # only a clean `git merge --ff-only origin/main` may update the live checkout
        return f"git pull in the live checkout{via} (use `git fetch origin && git merge --ff-only origin/main`)"
    if sub == "merge":
        refs = [a for a in rest if not a.startswith("-")]
        if "--ff-only" not in rest:
            return f"git merge without --ff-only in the live checkout{via}"
        if refs != ["origin/main"]:
            return f"git merge --ff-only is only allowed for origin/main in the live checkout{via}"
        if not _git_clean(clean_dir):
            return "git merge --ff-only with uncommitted changes in the live checkout (dirty or untracked files)"
        return None
    if sub in GIT_BLOCKED:
        return f"git {sub} in the live checkout{via}"
    return None


def _strip_wrappers(words: list[str], cwd: str) -> tuple[list[str], str, dict]:
    """Drop env assignments and wrapper commands (with their option values). Returns (words, cwd, git_env);
    ``env -C DIR`` / ``sudo -D DIR`` move the cwd for this command only."""
    git_env: dict = {}
    while words:
        w = words[0]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", w, re.S)
        if m:
            if m.group(1) in {"GIT_DIR", "GIT_WORK_TREE", "HERMES_UPDATE_OK"}:
                git_env[m.group(1)] = m.group(2)
            words = words[1:]
            continue
        name = os.path.basename(w)
        if name not in WRAPPERS:
            break
        opts, chdir = WRAPPER_OPTS[name], CHDIR_OPTS.get(name, set())
        words = words[1:]
        while words and words[0].startswith("-") and words[0] != "-":
            f = words.pop(0)
            if f == "--":
                break
            val = None
            if f.startswith("--") and "=" in f:
                f, val = f.split("=", 1)
            elif f in opts:
                val = words.pop(0) if words else None
            elif not f.startswith("--") and f[:2] in opts and len(f) > 2:
                f, val = f[:2], f[2:]
            if val is not None and f in chdir:
                cwd = _join(cwd, val)
            if val is not None and name == "env" and f in {"-S", "--split-string"}:
                words = _tokens(val) + words  # env -S 'cmd args': the value is the command
        words = words[WRAPPER_POSITIONALS.get(name, 0):]
    return words, cwd, git_env


def _shell_c_script(args: list[str]) -> Optional[str]:
    """The command string of ``bash -c``, ``bash -lc``, ``bash -l -c``, ``bash -o pipefail -c`` ..., else None."""
    seen_c, i = False, 0
    while i < len(args):
        a = args[i]
        if a in {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}:
            i += 2
            continue
        if a == "--":
            i += 1
            break
        if a.startswith("--"):
            i += 1
            continue
        if len(a) > 1 and a[0] in "-+":
            seen_c = seen_c or (a[0] == "-" and "c" in a[1:])
            i += 1
            continue
        break
    return args[i] if seen_c and i < len(args) else None


def _in_root_nofollow(path: str, cwd: str) -> bool:
    """Like in_root, but a final symlink component is not followed unless it points at a directory
    (where tools like ln create the new entry inside it)."""
    p = _join(cwd, path)
    if p.startswith(UNKNOWN_CWD):
        return False
    if os.path.isdir(p):
        return in_root(p)
    parent, base = os.path.split(os.path.normpath(p))
    r = os.path.join(os.path.realpath(parent or "."), base)
    b = root()
    return r == b or r.startswith(b + os.sep)


def _ln_dest(args: list[str], cwd: str) -> Optional[str]:
    tdir, ops, i, opts_done = None, [], 0, False
    while i < len(args):
        a = args[i]
        if not opts_done and a == "--":
            opts_done = True
        elif not opts_done and a.startswith("--target-directory="):
            tdir = a.split("=", 1)[1]
        elif not opts_done and a == "--target-directory" and i + 1 < len(args):
            tdir, i = args[i + 1], i + 1
        elif not opts_done and a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            k = a.find("t")
            if k > 0:
                tdir = a[k + 1:] or (args[i + 1] if i + 1 < len(args) else None)
                i += 0 if a[k + 1:] else 1
        elif not opts_done and a.startswith("--"):
            pass
        else:
            ops.append(a)
        i += 1
    if tdir:
        return tdir
    if len(ops) >= 2:
        return ops[-1]
    return "." if ops else None


def _check_segment(words: list[str], targets: list[str], cwd: str) -> tuple[Optional[str], str]:
    """Returns (reason, new_cwd)."""
    for t in targets:
        if in_root(t, cwd):
            return f"output redirection to {t}, inside the live checkout", cwd
    seg_cwd = cwd
    lead = words[1:] if words and words[0] in {"export", "declare", "typeset", "readonly", "local"} else words
    assigns = [re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", w, re.S) for w in lead]
    shell_vars = _SHELL_VARS.get()
    if lead and all(assigns) and shell_vars is not None:  # `D=x` / `export D=x` alone: D holds for later segments
        for m in filter(None, assigns):
            shell_vars[m.group(1)] = _expand(m.group(2)) if m.group(2) else ""
        return None, seg_cwd
    words, cwd, git_env = _strip_wrappers(words, cwd)
    if not words:
        return None, seg_cwd
    why, new_cwd = _check_words(words, cwd, git_env)
    # env -C / sudo -D move the cwd for that one command only; cd/pushd move it for later segments
    return why, (new_cwd if os.path.basename(words[0]) in {"cd", "pushd"} else seg_cwd)


def _check_words(words: list[str], cwd: str, git_env: dict) -> tuple[Optional[str], str]:
    cmd = os.path.basename(words[0])
    args = words[1:]
    if cmd in {"cd", "pushd"}:
        raw = args[0] if args and not args[0].startswith("-") else (args[1] if len(args) > 1 else "~")
        tgt = _expand(raw)
        if tgt is None or (not os.path.isabs(tgt) and cwd.startswith(UNKNOWN_CWD)):
            return None, UNKNOWN_CWD  # `cd $UNSET` / `cd $(...)`: later relative paths are not proven inside
        return None, os.path.normpath(tgt if os.path.isabs(tgt) else os.path.join(cwd, tgt))
    if cmd == "hermes-sync":
        return None, cwd
    if cmd in SHELLS:
        script = _shell_c_script(args)
        if script is not None:
            return check_shell(script, cwd, _update_checked=True), cwd
    if cmd == "eval" and args:
        return check_shell(" ".join(args), cwd, _update_checked=True), cwd
    if cmd == "git":
        return _check_git(args, cwd, git_env), cwd
    if cmd == "ln":  # only the link being created matters; a source inside the root is just read
        dest = _ln_dest(args, cwd)
        if dest is not None and _in_root_nofollow(dest, cwd):
            return f"ln creates a link inside the live checkout ({dest})", cwd
        return None, cwd
    files = [a for a in args if not a.startswith("-")]
    if cmd in {"sed", "gsed"} and any(re.match(r"^-[a-zA-Z]*i|^--in-place", a) for a in args):
        for f in files[1:] if files else []:  # files[0] may be the script
            if in_root(f, cwd):
                return f"in-place sed on {f}, inside the live checkout", cwd
        for f in files[:1]:
            if in_root(f, cwd) and not any(a == "-e" for a in args) and len(files) == 1:
                return f"in-place sed on {f}, inside the live checkout", cwd
    if cmd == "perl" and any(re.match(r"^-[a-zA-Z]*i", a) for a in args):
        for f in files:
            if in_root(f, cwd):
                return f"in-place perl edit of {f}, inside the live checkout", cwd
    if cmd == "tee":
        for f in files:
            if in_root(f, cwd):
                return f"tee writes {f}, inside the live checkout", cwd
    if cmd in WRITE_ALL:
        for f in files:
            if in_root(f, cwd):
                return f"{cmd} on {f}, inside the live checkout", cwd
    if cmd in {"cp", "install", "rsync"} and files and in_root(files[-1], cwd):
        return f"{cmd} into the live checkout ({files[-1]})", cwd
    return None, cwd


# Flags verified against `hermes update --help`; none of the backup/restart flags is read-only.
UPDATE_READ_ONLY = {"-h", "--help", "--check", "--plan", "--list-venv-holders", "--install-id"}
SSH_VALUE_OPTIONS = set("BbcDEeFIiJLlmOopPQRSWw")


def _update_tokens(source: str) -> list[str]:
    # Preserve quoted newlines as data; turn only unquoted command boundaries into
    # semicolons. shlex otherwise consumes the newline terminating a comment.
    cooked, quote, escaped, comment = [], "", False, False
    for char in _expose_substitutions(source):  # `out="$(hermes update)"` updates like `out=$(hermes update)`
        if comment:
            if char == "\n":
                cooked.append(" ; ")
                comment = False
            continue
        if escaped:
            cooked.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            cooked.append(char)
            escaped = True
        elif char in {"'", '"'} and (not quote or quote == char):
            quote = "" if quote else char
            cooked.append(char)
        elif char == "#" and not quote and (not cooked or cooked[-1][-1:].isspace()):
            comment = True
        elif char == "\n" and not quote:
            cooked.append(" ; ")
        else:
            cooked.append(char)
    lex = shlex.shlex("".join(cooked), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return _tokens(source)


def _update_source(source: str) -> str:
    """Remove heredoc data, not following commands. This pass is update-only: existing
    checkout-write analysis deliberately retains its conservative heredoc behavior."""
    lines, pending = [], []
    for line in source.splitlines(keepends=True):
        if pending:
            delimiter, strip_tabs, execute = pending[0]
            candidate = line.rstrip("\r\n")
            if (candidate.lstrip("\t") if strip_tabs else candidate) == delimiter:
                pending.pop(0)
            elif execute:
                lines.append(line)
            continue
        lines.append(line)
        toks = _update_tokens(line)
        words, _, assignments = _strip_wrappers(toks[:], os.getcwd())
        execute = bool(words and os.path.basename(words[0]) in SHELLS | {"ssh"}
                       and assignments.get("HERMES_UPDATE_OK") != "1")
        for i, token in enumerate(toks[:-1]):
            if token == "<<" and "$((" not in line:
                delimiter = toks[i + 1]
                pending.append((delimiter.lstrip("-"), delimiter.startswith("-"), execute))
    return "".join(lines)


def _ssh_script(args: list[str]) -> str:
    """SSH concatenates remote argv with spaces; options before the host belong to SSH."""
    i, host_seen = 0, False
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            if host_seen:
                break
            continue
        if not arg.startswith("-") or arg == "-":
            if host_seen:
                break
            host_seen = True
            i += 1
            continue
        for j, flag in enumerate(arg[1:], 1):
            if flag in SSH_VALUE_OPTIONS:
                if j == len(arg) - 1:
                    i += 1
                break
        i += 1
    return " ".join(args[i:])


def _check_update(source: str, cwd: str, approved: bool = False, depth: int = 0) -> Optional[str]:
    """Inspect command positions, not arbitrary strings mentioning an update.
    Explicit approval is scoped to one command and its nested shell/SSH payload."""
    if depth > 20:
        return None
    for seg in _segments(_update_tokens(_update_source(source))):
        words, _ = _redirect_targets(seg)
        while words and words[0] in {"{", "!", "then", "do", "else", "elif", "if", "while", "until"}:
            words = words[1:]
        words, _, assignments = _strip_wrappers(words, cwd)
        if not words:
            continue
        allowed = assignments.get("HERMES_UPDATE_OK", "1" if approved else "0") == "1"
        cmd, args = os.path.basename(words[0]), words[1:]
        if cmd == "hermes":
            cli = args
        elif re.fullmatch(r"python(?:\d+(?:\.\d+)*)?t?", cmd):
            cli = []
            for i, arg in enumerate(args):
                if arg == "-m" and args[i + 1:i + 2] == ["hermes_cli.main"]:
                    cli = args[i + 2:]
                    break
                if arg == "-mhermes_cli.main":
                    cli = args[i + 1:]
                    break
        else:
            cli = []
        while cli and (cli[0] in {"-p", "--profile"} or cli[0].startswith("--profile=")):
            cli = cli[1:] if cli[0].startswith("--profile=") else cli[2:]
        if cli and cli[0] == "update" and not allowed and not UPDATE_READ_ONLY.intersection(cli[1:]):
            return "unfenced hermes update"
        script = None
        if cmd == "ssh":
            script = _ssh_script(args)
        elif cmd in SHELLS:
            script = _shell_c_script(args)
        elif cmd == "eval":
            script = " ".join(args)
        if cmd in SHELLS | {"ssh"} and "<<<" in args:
            i = args.index("<<<")
            script = args[i + 1] if i + 1 < len(args) else script
        if script and (why := _check_update(script, cwd, allowed, depth + 1)):
            return why
    return None


def check_shell(cmd: str, cwd: Optional[str] = None, *, _update_checked: bool = False) -> Optional[str]:
    cwd = os.path.expanduser(cwd) if cwd else os.getcwd()
    if not _update_checked and (why := _check_update(cmd, cwd)):
        return why
    if not cmd.strip():
        return None
    token = _SHELL_VARS.set({}) if _SHELL_VARS.get() is None else None  # nested sh -c / eval share the outer vars
    try:
        for seg in _segments(_tokens(cmd)):
            words, targets = _redirect_targets(seg)
            why, cwd = _check_segment(words, targets, cwd)
            if why:
                return why
        return None
    finally:
        if token is not None:
            _SHELL_VARS.reset(token)


# ---- a throwaway HERMES_HOME that would persist in the terminal session -------------------------
# The Hermes terminal tool re-dumps `export -p` after every call and sources it before the next, so
# `export HERMES_HOME=$(mktemp -d)` in one call is still set in every later call. On 2026-11-02 a coach
# test recipe did that, and the same session's next `hermes-sync run` exported the throwaway home into the
# private state repo and re-pointed the real launchd job at it. Only the terminal tool persists: subshells,
# `bash -c '...'`, heredoc bodies and the one-command prefix form (`HERMES_HOME=... cmd`) do not.

HOME_EXPORT_WHY = "a throwaway HERMES_HOME would persist in this terminal session"
_ASSIGN_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PERSIST_BUILTIN = re.compile(r"^(?:export|declare|typeset|readonly)(?:\s+-[A-Za-z]+)*\s(?:.*\s)?$", re.S)
_KEYWORDS = re.compile(r"^\s*(?:(?:then|do|else|elif|if|while|until|time|!)\s+)*")
_TEMP_HINT = re.compile(r"mktemp|\$\{?TMPDIR\b|\$\{?TMP\b|pytest|tmp_path|basetemp")
_TEMP_PREFIXES = ("/tmp", "/private/tmp", "/var/tmp", "/var/folders", "/private/var/folders")


def _word_end(cmd: str, i: int) -> int:
    """Index just past the shell word starting at i (quotes, $(...), ${...}, `...` and escapes kept whole)."""
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\":
            i += 2
        elif c == "'":
            j = cmd.find("'", i + 1)
            i = n if j < 0 else j + 1
        elif c == '"':
            i += 1
            while i < n and cmd[i] != '"':
                i += 2 if cmd[i] == "\\" else 1
            i += 1
        elif c == "`":
            j = cmd.find("`", i + 1)
            i = n if j < 0 else j + 1
        elif c == "$" and i + 1 < n and cmd[i + 1] in "({":
            close, depth, i = (")" if cmd[i + 1] == "(" else "}"), 0, i + 1
            opener = cmd[i]
            while i < n:
                if cmd[i] == opener:
                    depth += 1
                elif cmd[i] == close:
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            i += 1
        elif c.isspace() or c in ";&|()<>":
            return i
        else:
            i += 1
    return n


def _top_level_statements(cmd: str):
    """Yield (start, end) of each top-level simple command: outside quotes, subshells, command substitution,
    backticks, comments and heredoc bodies, i.e. the code whose variable assignments outlive the call."""
    n, i, start, depth = len(cmd), 0, 0, 0
    pending_heredocs: list[str] = []
    while i < n:
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"`" or (c == "$" and i + 1 < n and cmd[i + 1] in "({"):
            i = _word_end(cmd, i) if depth == 0 else i + 1
            continue
        if c == "#" and (i == 0 or cmd[i - 1].isspace() or cmd[i - 1] in ";&|("):
            j = cmd.find("\n", i)
            i = n if j < 0 else j
            continue
        if depth == 0 and cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            m = re.match(r"<<-?\s*(['\"]?)([A-Za-z0-9_.-]+)\1", cmd[i:])
            if m:
                pending_heredocs.append(m.group(2))
                i += m.end()
                continue
        if c == "(":
            if depth == 0 and i > start:
                yield start, i
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
            start = i + 1
        elif depth == 0 and (c in ";&|{}" or c == "\n"):
            if i > start:
                yield start, i
            start = i + 1
            if c == "\n" and pending_heredocs:  # skip each body up to its delimiter line
                for delim in pending_heredocs:
                    m = re.compile(rf"^[ \t]*{re.escape(delim)}[ \t]*$", re.M).search(cmd, start)
                    start = n if m is None else m.end()
                pending_heredocs = []
                i = start
                continue
        i += 1
    if depth == 0 and start < n:
        yield start, n


def _temp_value(raw: str) -> bool:
    if _TEMP_HINT.search(raw):
        return True
    value = raw.strip("'\"")
    if value.startswith(_TEMP_PREFIXES):
        return True
    expanded = os.path.realpath(os.path.expandvars(os.path.expanduser(value))) if value else ""
    roots = {os.path.realpath(r) for r in (os.environ.get("TMPDIR"), "/tmp") if r}
    return any(expanded == r or expanded.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def check_terminal_env(cmd: str) -> Optional[str]:
    """Refuse a top-level `export HERMES_HOME=<temp>` (or an assignment-only `HERMES_HOME=<temp>` statement,
    which keeps an already-exported variable exported) in the terminal tool, whose session carries it into
    every later call. The prefix form `HERMES_HOME=<temp> cmd` and subshells are allowed."""
    if not cmd or "HERMES_HOME=" not in cmd:
        return None
    for s, e in _top_level_statements(cmd):
        stmt = cmd[s:e]
        for m in re.finditer(r"(?<![A-Za-z0-9_])HERMES_HOME=", stmt):
            vstart = m.end()
            vend = _word_end(stmt, vstart)
            value = stmt[vstart:vend]
            if not _temp_value(value):
                continue
            head = _KEYWORDS.sub("", stmt[:m.start()], count=1)
            if _PERSIST_BUILTIN.match(head):
                return f"{HOME_EXPORT_WHY} (`{stmt.strip()[:120]}`)"
            words = head.split()
            if all(_ASSIGN_WORD.match(w) for w in words):  # only assignments before it
                rest = stmt[vend:]
                while True:  # skip further assignments; a command word after them = one-command prefix form
                    rest = rest.lstrip(" \t")
                    a = _ASSIGN_WORD.match(rest)
                    if not a:
                        break
                    rest = rest[_word_end(rest, a.end()):]
                if not rest.strip():
                    return f"{HOME_EXPORT_WHY} (`{stmt.strip()[:120]}`)"
    return None


def home_export_message(why: str) -> str:
    return (f"[live-checkout-guard] Refused: {why}.\n"
            "The terminal tool keeps exported variables between calls, so every later command in this session "
            "would run against the throwaway home while hermes-sync, git and launchd still write the real "
            "~/code/hermes, ~/code/hermes-mods and ~/Library/LaunchAgents (on 2026-11-02 a later `hermes-sync run` "
            "exported a test home into the state repo and re-pointed the real launchd job). Scope it instead:\n"
            "  HERMES_HOME=\"$(mktemp -d)\" python -m pytest ...        # one command\n"
            "  ( export HERMES_HOME=\"$(mktemp -d)\"; cmd1; cmd2 )      # a subshell\n"
            "If an earlier call already exported one, run `unset HERMES_HOME` first.")


# ---- execute_code ------------------------------------------------------------------------------

def _const(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) for e in node.elts):
        return [e.value for e in node.elts]
    return None


def _callname(f: ast.AST) -> str:
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def check_python(code: str, cwd: Optional[str] = None) -> Optional[str]:
    """Inspect literal arguments of terminal()/write_file()/patch()/subprocess/os.system/open(..., 'w') calls."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callname(node.func)
        pos = [_const(a) for a in node.args]
        kw = {k.arg: _const(k.value) for k in node.keywords if k.arg}
        wd = kw.get("workdir") or kw.get("cwd") or cwd
        why = None
        if name in {"terminal", "run_shell"}:
            c = kw.get("command") or (pos[0] if pos else None)
            if isinstance(c, str):
                why = (check_terminal_env(c) if name == "terminal" else None) or check_shell(c, wd)
        elif name in FILE_MUTATORS:
            a = {k: v for k, v in kw.items() if isinstance(v, str)}
            if pos and isinstance(pos[0], str):  # write_file(path, content) / patch(path, old, new, ...)
                a.setdefault("path", pos[0])
            why = check_file_tool(name, a, wd)
        elif name in {"system", "popen"}:
            if pos and isinstance(pos[0], str):
                why = check_shell(pos[0], wd)
        elif name in {"run", "call", "check_call", "check_output", "Popen"}:
            c = kw.get("args") or (pos[0] if pos else None)
            if isinstance(c, list) and all(isinstance(x, str) for x in c):
                c = " ".join(shlex.quote(x) for x in c)
            if isinstance(c, str):
                why = check_shell(c, wd)
        elif name == "open" and isinstance(node.func, ast.Name):
            mode = kw.get("mode") or (pos[1] if len(pos) > 1 else "r")
            if isinstance(pos[0] if pos else None, str) and isinstance(mode, str) and set(mode) & set("wax+") \
                    and in_root(pos[0], cwd):
                why = f"open({pos[0]!r}, {mode!r}) writes inside the live checkout"
        elif name in {"write_text", "write_bytes", "touch", "unlink", "mkdir", "rename", "replace"}:
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call) and _callname(f.value.func) == "Path":
                p = _const(f.value.args[0]) if f.value.args else None
                if isinstance(p, str) and in_root(p, cwd):
                    why = f"Path({p!r}).{name}() writes inside the live checkout"
        if why:
            return why
    return None
