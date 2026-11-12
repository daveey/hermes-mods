"""Decide whether a shell command or Python snippet would damage a live Hermes SQLite database.

A live database is a ``*.db`` file (or its ``-wal`` / ``-shm`` / ``-journal`` sidecar) under the Hermes
home (``~/.hermes``, ``$HERMES_HOME``), outside scratch, cache, archive and backup paths. Blocked:

- ``rm`` / ``unlink`` / ``shred`` / ``truncate`` / ``mv`` of a live file, or a live file as the target of
  ``cp`` / ``install`` / ``rsync`` / ``ln`` / ``dd of=`` / a ``>`` redirect. Deleting the ``-wal`` or
  ``-shm`` of a database that another process has open, or swapping the main file under it, splits the
  writers onto different WAL views, and the next checkpoint corrupts the b-trees.
- ``sqlite3`` maintenance against a live file (``REINDEX``, ``VACUUM``, checkpoint / journal-mode pragmas,
  ``.restore``); ``sqlite3`` writes (DML/DDL) into a live Kanban board (``kanban.db``) at any time, with the
  refusal naming the ``hermes kanban`` verb that makes the change; and ``sqlite3`` writes into any other live
  file that was quarantined as corrupt in the last few hours (a ``<db>.corrupt.*.bak`` sibling). Reads,
  ``-readonly`` and ``mode=ro``/``immutable=1`` URIs pass.
- ``rm`` / ``mv`` / ``truncate`` of a live database's ``<db>.corrupt.*.bak`` quarantine: deleting it once
  switched the raw-write block off and destroyed the evidence a recovery reads (self-heal agent-6eb3a51e94).

A damaged Kanban board is rebuilt with ``hermes kanban [--board <slug>] recover``: it holds the board's
recovery lock, installs in place through the SQLite backup API (no rename under open WAL connections) and
writes a loss report. The refusal names that command and, while a recovery runs, its holder (pid, session,
card from ``<db>.recover.pid``). Other databases' recovered copies go in through ``swap_db.py`` next to this
file (same lock; it refuses Kanban boards unless an operator passes ``--allow-kanban``). Every refusal is
appended to ``<home>/logs/sqlite-live-guard.log`` (JSON lines; ``HERMES_SQLITE_GUARD_LOG`` overrides).
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Iterable, Optional

HELPER = Path(__file__).resolve().parent / "swap_db.py"
HELPER_DISPLAY = str(HELPER).replace(str(Path.home()), "~")

DISABLE_ENV = "HERMES_SQLITE_GUARD"  # "off" disables (an operator-approved, single-owner recovery only)
LOG_ENV = "HERMES_SQLITE_GUARD_LOG"

_LIVE_NAME = re.compile(r"\.db(?:-wal|-shm|-journal)?$")
# Paths under the Hermes home that never hold a live database.
_NOT_LIVE_PARTS = ("/cache/", "/archive/", "/backups/", "/.hermes-sync-trash/", "/tmp/", "kanban-recovery")
_BOARDS_CHILD = re.compile(r"/kanban/boards/([^/]+)/")
_BOARD_SLUG = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")  # core kanban_db._BOARD_SLUG_RE
_NOT_LIVE_MARKERS = (".bak", ".corrupt.", ".pre-", ".recovered", ".new", ".orig", ".snapshot")
_HOME_PREFIX = re.compile(
    r"^(?:~|\$\{?HOME\}?|/home/[^/]+|/Users/[^/]+|/root)/\.hermes(?:/|$)|^\$\{?HERMES_HOME\}?(?:/|$)")
_SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "|&"}
_WRAPPERS = {"sudo", "env", "command", "nice", "nohup", "exec", "time", "ionice", "stdbuf"}
_SQL_MAINT = re.compile(
    r"^\s*(?:reindex|vacuum|attach)\b"
    r"|^\s*pragma\s+(?:\w+\.)?(?:journal_mode\s*=|wal_checkpoint\b|writable_schema\s*=|page_size\s*=|auto_vacuum\s*=|"
    r"incremental_vacuum\b|schema_version\s*=)"
    r"|^\s*\.(?:restore|clone)\b",
    re.IGNORECASE,
)
_SQL_DML = re.compile(r"^\s*(?:insert|update|delete|replace|upsert|drop|create|alter|begin|with)\b", re.IGNORECASE)
QUARANTINE_WINDOW_S = 2 * 3600


def _statements(text: str) -> Iterable[str]:
    for stmt in re.split(r"[;\n]", text or ""):
        yield re.sub(r"^\s*(?:--[^\n]*|/\*.*?\*/)\s*", "", stmt, flags=re.S)


def _sql_kind(text: str) -> Optional[str]:
    """``"maintenance"`` (REINDEX, VACUUM, checkpoint/journal pragmas), ``"write"`` (DML/DDL) or ``None``.
    Only a statement's leading verb counts: ``replace(`` or ``like '%delete%'`` inside a SELECT do not."""
    kind = None
    for stmt in _statements(text):
        if _SQL_MAINT.search(stmt):
            return "maintenance"
        if _SQL_DML.search(stmt):
            kind = "write"
    return kind


def recently_quarantined(db: str) -> Optional[str]:
    """Name of a ``<db>.corrupt.*.bak`` quarantine younger than the window, if any (the board was judged
    corrupt recently, so raw writes into it are a repair attempt on a damaged file)."""
    path = Path(_expand(db[5:].split("?", 1)[0] if db.startswith("file:") else db))
    try:
        now = time.time()
        for bak in path.parent.glob(path.name + ".corrupt.*.bak"):
            if now - bak.stat().st_mtime < QUARANTINE_WINDOW_S:
                return bak.name
    except OSError:
        return None
    return None


def _home() -> str:
    return os.path.realpath(os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes"))


def _expand(path: str) -> str:
    """Expand the home spellings an agent uses (``~``, ``$HOME``, ``${HOME}``, ``$HERMES_HOME``)."""
    path = re.sub(r"^\$\{?HERMES_HOME\}?", _home(), path)
    path = re.sub(r"^\$\{?HOME\}?", os.path.expanduser("~"), path)
    return os.path.expanduser(path)


def _under_home(path: str) -> bool:
    if _HOME_PREFIX.match(path):
        return True
    if path.startswith("/"):
        home = _home()
        return path == home or path.startswith(home.rstrip("/") + "/")
    return False


def is_live(path: str, in_home: bool = False) -> bool:
    """True when ``path`` names a live Hermes database file or sidecar. ``in_home``: the command's working
    directory is the Hermes home, so a relative path resolves there."""
    if not path or path.startswith("-"):
        return False
    if path.startswith("file:"):
        path = path[5:].split("?", 1)[0]
    base = path.rsplit("/", 1)[-1]
    if not _LIVE_NAME.search(base) or any(m in base for m in _NOT_LIVE_MARKERS):
        return False
    rooted = _under_home(path) or (in_home and not path.startswith(("/", "~", "$")))
    if not rooted:
        return False
    # Judge scratch/backup locations only below the Hermes home (the home itself may sit under /tmp in tests).
    full = _expand(path)
    home = _home().rstrip("/") + "/"
    rel = "/" + (full[len(home):] if full.startswith(home) else _HOME_PREFIX.sub("", path))
    if any(part in rel for part in _NOT_LIVE_PARTS):
        return False
    # Only a board-slug folder under kanban/boards/ holds a board (core's ``named_board_db_files``); ``_archived/``
    # and other non-slug folders hold archived copies or strays no Hermes process opens.
    board = _BOARDS_CHILD.search(rel)
    return not (board and not _BOARD_SLUG.match(board.group(1)))


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
    touches `<`/`>` is an fd; a spaced `2 > x` keeps `2` as an operand, as in bash."""
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


def _lex(cmd: str) -> list[str]:
    cmd = _expose_substitutions(cmd)  # `out="$(rm x)"` runs rm like `out=$(rm x)`
    lexer = shlex.shlex(_mask_quoted_redirects(_drop_fd_prefixes(cmd.replace("\n", " ; "))), posix=True,
                        punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return _unmask_quoted(list(lexer))


def _tokens(cmd: str) -> list[str]:
    """Tokens of the commands in `cmd`. Comments and heredoc bodies are split off first (`_split_heredocs`: an
    apostrophe in them no longer hides the commands after them, and an unquoted body's `$(...)` is a command);
    a line whose quotes never close is dropped, never the whole command (`_lexable`), and the `;` between lines
    is kept (a whitespace split lost it, so no later command was at command position)."""
    code = _split_heredocs(cmd)[0]
    return _lex(_lexable(code, _lex))


def _segments(tokens: list[str]) -> Iterable[list[str]]:
    seg: list[str] = []
    for tok in tokens:
        if tok in _SEPARATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def _strip_wrappers(seg: list[str]) -> list[str]:
    i = 0
    while i < len(seg):
        tok = seg[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):  # VAR=value prefix
            i += 1
        elif tok in _WRAPPERS:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                takes_arg = tok == "sudo" and seg[i] in ("-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t")
                i += 2 if takes_arg else 1
        elif tok == "timeout":
            i += 1
            while i < len(seg) and (seg[i].startswith("-") or re.match(r"^\d+[smhd]?$", seg[i])):
                i += 1
        else:
            break
    return seg[i:]


def _without_redirects(args: list[str]) -> list[str]:
    """`args` without redirect operators and their targets: in `cp a ~/.hermes/state.db 2>/dev/null` the
    destination is `~/.hermes/state.db`, not `/dev/null` (`_check_redirects` judges redirect targets)."""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a and set(a) <= set("<>&|") and set(a) & set("<>") and not isinstance(a, _Quoted):
            skip = True
        else:
            out.append(a)
    return out


def _operands(args: list[str]) -> list[str]:
    out, end = [], False
    for a in args:
        if a == "--":
            end = True
        elif end or not a.startswith("-"):
            out.append(a)
    return out


def _check_redirects(seg: list[str], in_home: bool) -> Optional[str]:
    for i, tok in enumerate(seg[:-1]):
        if tok in (">", ">>", ">|", "&>") and not isinstance(tok, _Quoted) and is_live(seg[i + 1], in_home):
            return f"a `>` redirect would overwrite live database file {seg[i + 1]}"
    return None


# Writes into a live Kanban board (any SQL statement on these tables) map to the verb that makes the change.
_KANBAN_VERBS = (
    (re.compile(r"\binto\s+task_comments\b", re.I), "comment <id> \"<text>\""),
    (re.compile(r"\bstatus\s*=\s*'ready'", re.I), "unblock <id>"),
    (re.compile(r"\bstatus\s*=\s*'blocked'", re.I), "block <id> \"<reason>\""),
    (re.compile(r"\bstatus\s*=\s*'done'", re.I), "complete <id> --summary \"...\""),
    (re.compile(r"\bstatus\s*=\s*'scheduled'", re.I), "schedule <id> ..."),
    (re.compile(r"\bstatus\s*=\s*'archived'", re.I), "archive <id>"),
    (re.compile(r"\binto\s+task_links\b", re.I), "link <parent> <child>"),
    (re.compile(r"\bfrom\s+task_links\b", re.I), "unlink <parent> <child>"),
    (re.compile(r"\b(?:into|from)\s+task_attachments\b", re.I), "attach <id> <file> / attach-rm <id> <att>"),
    (re.compile(r"\bassignee\s*=", re.I), "reassign <id> <profile>"),
    (re.compile(r"\b(?:priority|title|body)\s*=", re.I), "edit <id> --priority/--title/--body ..."),
)


def _board_of(db: str, in_home: bool) -> Optional[str]:
    """``hermes kanban`` prefix for the live Kanban board ``db`` names (``hermes kanban``, ``hermes kanban
    --board <slug>``, ``HERMES_HOME=<dir> hermes kanban`` for a profile's board), or None for other databases."""
    path = db[5:].split("?", 1)[0] if db.startswith("file:") else db
    if path.rsplit("/", 1)[-1] != "kanban.db":
        return None
    if in_home and not path.startswith(("/", "~", "$")):
        path = os.path.join(_home(), path)
    full = Path(_expand(path))
    home = Path(_home())
    if full == home / "kanban.db":
        return "hermes kanban"
    if full.parent.parent == home / "kanban" / "boards":
        return f"hermes kanban --board {full.parent.name}"
    return "HERMES_HOME=" + str(full.parent).replace(str(Path.home()), "~") + " hermes kanban"


def _kanban_verb(sql: str, prefix: str) -> str:
    for pat, verb in _KANBAN_VERBS:
        if pat.search(sql or ""):
            return f"`{prefix} {verb}` (or the matching kanban_* tool)"
    if re.search(r"\btask_events\b", sql or "", re.I):
        return (f"the `{prefix} <verb>` that makes the change: it writes its own task_events row (an event "
                "row alone moves nothing)")
    return f"the kanban_* tools or the matching `{prefix} <verb>` (`{prefix} --help` lists them)"


# ``<db>.corrupt.<hash>.bak``: the copy Kanban/SQLite recovery kept of a board judged corrupt. It is the evidence
# ``hermes kanban recover`` and a human need; Kanban rotates these itself (kanban_db_connect cap).
_QUARANTINE_NAME = re.compile(r"\.db\.corrupt\.[^/]*\.bak$")


def is_quarantine(path: str, in_home: bool = False) -> bool:
    """True when ``path`` (globs included) names a live database's ``.corrupt.*.bak`` quarantine."""
    if not path or path.startswith("-"):
        return False
    base = path.rsplit("/", 1)[-1]
    if not _QUARANTINE_NAME.search(base):
        return False
    live = path[: -len(base)] + base.split(".corrupt.", 1)[0]
    return is_live(live, in_home)


def _check_sqlite3(args: list[str], seg: list[str], in_home: bool) -> Optional[str]:
    readonly = any(a in ("-readonly", "--readonly", "-safe") for a in args)
    sql: list[str] = []
    db = None
    i = 0
    fed = False
    while i < len(args):
        a = args[i]
        if a in ("<", "<<", "<<<"):
            fed = True
            i += 2
            continue
        if a in (">", ">>", ">|"):
            i += 2
            continue
        if a in ("-cmd", "--cmd", "-init", "--init", "-separator", "-newline", "-nullvalue"):
            if a in ("-cmd", "--cmd") and i + 1 < len(args):
                sql.append(args[i + 1])
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        if db is None:
            db = a
        else:
            sql.append(a)
        i += 1
    text = "\n".join(sql)
    for stmt in _statements(text):  # `.backup [schema] FILE` / `.save FILE` write FILE, whatever db is open
        m = re.match(r"\s*\.(?:backup|save)\s+(?:(?!-)\S+\s+)?(?P<t>\S+)\s*$", stmt)
        target = m.group("t").strip("'\"") if m else ""
        if target and is_live(target, in_home):
            return f"`sqlite3 .backup/.save` would overwrite live database file {target}"
    if db is None or not is_live(db, in_home):
        return None
    if readonly or re.search(r"[?&](?:mode=ro|immutable=1)", db):
        return None
    kind = _sql_kind(text) or ("write" if fed and not text else None)
    if kind == "maintenance":
        return f"`sqlite3` would run maintenance (REINDEX/VACUUM/checkpoint/journal pragma) on live database {db}"
    if kind == "write":
        board = _board_of(db, in_home)
        if board is not None:
            return (f"`sqlite3` would write to live Kanban board {db}; a raw write skips the board's own "
                    f"transitions, events and locks (hub incident 2026-10-28). Use {_kanban_verb(text, board)}")
        bak = recently_quarantined(os.path.join(_home(), db) if in_home and not db.startswith(("/", "~", "$", "file:"))
                                   else db)
        if bak:
            return (f"`sqlite3` would write to live database {db}, which was quarantined as corrupt "
                    f"recently ({bak}); writes into a damaged b-tree spread the damage")
    return None


def _dir_like(path: str) -> bool:
    if path.endswith("/"):
        return True
    full = _expand(path).rstrip("/")
    if full == _home().rstrip("/") or _HOME_PREFIX.sub("", path) == "" or re.fullmatch(
            r"(?:~|\$\{?HOME\}?|/home/[^/]+|/Users/[^/]+|/root)/\.hermes/?|\$\{?HERMES_HOME\}?/?", path):
        return True
    if re.search(r"/kanban/boards/[^/]+/?$", path):
        return True
    try:
        return os.path.isdir(full)
    except OSError:
        return False


def _into_directory(args: list[str], ops: list[str], in_home: bool) -> Optional[str]:
    """``mv|cp|rsync|install SRC... DIR`` (or ``-t DIR`` / ``--target-directory=DIR``): the file lands at
    DIR/basename(SRC). Returns that live path, if any."""
    tdir = None
    for i, a in enumerate(args):
        if a in ("-t", "--target-directory") and i + 1 < len(args):
            tdir = args[i + 1]
        elif a.startswith("--target-directory="):
            tdir = a.split("=", 1)[1]
    if tdir is not None:
        srcs = [o for o in ops if o != tdir]
    elif len(ops) >= 2:
        tdir, srcs = ops[-1], ops[:-1]
    else:
        return None
    if not _dir_like(tdir):
        return None
    for src in srcs:
        cand = tdir.rstrip("/") + "/" + src.rstrip("/").rsplit("/", 1)[-1]
        if is_live(cand, in_home):
            return cand
    return None


def check_shell(cmd: str, cwd: str = "") -> Optional[str]:
    in_home = bool(cwd) and os.path.realpath(os.path.expanduser(cwd)) == _home()
    for seg in _segments(_tokens(cmd or "")):
        why = _check_redirects(seg, in_home)
        if why:
            return why
        seg = [t for t in _strip_wrappers(seg)]
        if not seg:
            continue
        word, args = os.path.basename(seg[0]), seg[1:]
        if word == "cd":
            target = (_operands(args) or ["~"])[0]
            in_home = os.path.realpath(_expand(target)) == _home()
            continue
        ops = _operands(_without_redirects(args))
        if word in ("rm", "unlink", "shred", "truncate", "mv"):
            hit = next((o for o in ops if is_live(o, in_home)), None)
            if hit:
                verb = "move" if word == "mv" else "delete or truncate"
                return f"`{word}` would {verb} live database file {hit}"
            bak = next((o for o in ops if is_quarantine(o, in_home)), None)
            if bak:
                verb = "move" if word == "mv" else "delete or truncate"
                return (f"`{word}` would {verb} the corruption quarantine {bak} of live database "
                        f"{bak.split('.corrupt.', 1)[0]}; it is the evidence a recovery needs, and Kanban rotates "
                        "these copies itself (deleting one also proves nothing about the board's health)")
        elif word in ("cp", "install", "rsync", "ln", "sqlite3_rsync"):
            if ops and is_live(ops[-1], in_home):
                return f"`{word}` would overwrite live database file {ops[-1]}"
        if word in ("mv", "cp", "install", "rsync", "ln"):
            hit = _into_directory(args, ops, in_home)
            if hit:
                return f"`{word}` would put a file over live database file {hit}"
        if word == "tee":
            hit = next((o for o in ops if is_live(o, in_home)), None)
            if hit:
                return f"`tee` would overwrite live database file {hit}"
        elif re.fullmatch(r"python(?:3(?:\.\d+)?)?", word) and "-c" in args:
            i = args.index("-c")
            why = check_python(args[i + 1]) if i + 1 < len(args) else None
            if why:
                return why
        elif word == "dd":
            hit = next((a[3:] for a in args if a.startswith("of=") and is_live(a[3:], in_home)), None)
            if hit:
                return f"`dd` would overwrite live database file {hit}"
        elif word in ("sqlite3", "sqlite3.exe"):
            why = _check_sqlite3(args, seg, in_home)
            if why:
                return why
        elif word == "find" and "-delete" in args:
            if any(is_live(a, in_home) or a.endswith((".db-wal", ".db-shm", "*.db*", "kanban.db*")) for a in args):
                return "`find -delete` would delete live database files"
    return None


_Q = r"""["'](?P<p>[^"'\n]{1,400})["']"""
_PY_DESTRUCTIVE = (
    re.compile(r"\bos\.(?:remove|unlink|rename|replace|truncate)\s*\((?P<a>[^)]*)\)"),
    re.compile(r"\bshutil\.move\s*\((?P<a>[^)]*)\)"),
    re.compile(r"\bshutil\.(?:copy|copy2|copyfile)\s*\((?P<dst>[^)]*)\)"),  # only the destination counts
    re.compile(r"\bPath\s*\(\s*" + _Q + r"\s*\)\s*\.(?:unlink|rename|replace|write_bytes|write_text)\s*\("),
    re.compile(r"\bopen\s*\(\s*" + _Q + r"\s*,\s*[\"'][wa+]"),
)
_PY_STR = re.compile(r"""(?P<q>["'])(?P<s>[^"'\n]{1,400})(?P=q)""")
# Board files built from a home-derived path (``home / 'kanban.db'``, ``os.path.join(home, 'kanban.db')``,
# ``kb.kanban_db_path()``) are found with an AST walk, so code that merely prints such text is not a hit.
_BOARD_FILE = re.compile(r"^kanban\.db(?:-wal|-shm|-journal)?$")
_HOME_HINT = re.compile(r"\.hermes\b|HERMES_HOME|hermes_home|get_hermes_home|kanban_home|kanban_db_path")
_TMP_HINT = re.compile(r"tmp|temp|scratch|fixture|cache", re.I)
_PY_MOVE_FUNCS = {("os", "rename"), ("os", "replace"), ("shutil", "move"), ("shutil", "copy"), ("shutil", "copy2"),
                  ("shutil", "copyfile")}
_PY_DELETE_FUNCS = {("os", "remove"), ("os", "unlink"), ("os", "truncate")}


def _py_assignments(tree: ast.AST) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def _py_src(node: ast.AST, names: dict, depth: int = 0) -> str:
    """Source text of an expression with simple variable assignments inlined (to judge home vs tmp)."""
    try:
        text = ast.unparse(node)
    except Exception:
        return ""
    if depth < 3:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id in names:
                text += " " + _py_src(names[n.id], names, depth + 1)
    return text


def _py_board(node: Optional[ast.AST], names: dict, depth: int = 0) -> Optional[str]:
    """The live board file an expression names, or None."""
    if node is None or depth > 4:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if is_live(_expand(node.value)) else None
    if isinstance(node, ast.Name) and node.id in names:
        return _py_board(names[node.id], names, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = node.right
        if isinstance(right, ast.Constant) and isinstance(right.value, str) and _BOARD_FILE.match(right.value):
            base = _py_src(node.left, names)
            if _HOME_HINT.search(base) and not _TMP_HINT.search(base):
                return right.value
        return None
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
        if name == "kanban_db_path":
            return "kanban.db"
        if name == "join" and node.args:
            last = node.args[-1]
            if isinstance(last, ast.Constant) and isinstance(last.value, str) and _BOARD_FILE.match(last.value):
                base = " ".join(_py_src(a, names) for a in node.args[:-1])
                if _HOME_HINT.search(base) and not _TMP_HINT.search(base):
                    return last.value
        if name in ("str", "Path", "PurePath", "fspath", "expanduser", "resolve", "absolute") and node.args:
            return _py_board(node.args[0], names, depth + 1)
        if name in ("expanduser", "resolve", "absolute") and isinstance(fn, ast.Attribute):
            return _py_board(fn.value, names, depth + 1)
    return None


def _check_python_ast(code: str) -> Optional[str]:
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    names = _py_assignments(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        fn = node.func
        mod = fn.value.id if isinstance(fn.value, ast.Name) else ""
        target = None
        if (mod, fn.attr) in _PY_MOVE_FUNCS and len(node.args) >= 2:
            target = node.args[1]
        elif (mod, fn.attr) in _PY_DELETE_FUNCS and node.args:
            target = node.args[0]
        elif fn.attr in ("replace", "rename") and len(node.args) == 1 and not node.keywords:
            target = node.args[0]  # Path(x).replace(dst); str.replace takes two arguments
        elif fn.attr in ("unlink", "write_bytes", "write_text", "touch") and mod not in ("os", "shutil"):
            target = fn.value  # (home / 'kanban.db-wal').unlink()
        hit = _py_board(target, names)
        if hit:
            return f"Python file operations on live database file {hit} ({ast.unparse(node)[:160]})"
    return None


def check_python(code: str) -> Optional[str]:
    code = code or ""
    for pat in _PY_DESTRUCTIVE:
        for m in pat.finditer(code):
            groups = m.groupdict()
            if groups.get("p"):
                cands = [groups["p"]]
            elif groups.get("dst") is not None:
                cands = [s.group("s") for s in _PY_STR.finditer(groups["dst"])][-1:]
            else:
                cands = [s.group("s") for s in _PY_STR.finditer(groups.get("a") or "")]
            hit = next((c for c in cands if is_live(_expand(c)) or is_quarantine(_expand(c))), None)
            if hit:
                return f"Python file operations on live database file {hit}"
    why = _check_python_ast(code)
    if why:
        return why
    for lit in (m.group("s") for m in _PY_STR.finditer(code)):  # os.system("rm ...") / shell=True strings
        if " " in lit:
            why = check_shell(lit)
            if why:
                return why
    return None


_BOARD_IN_WHY = re.compile(r"(?:^|(?<=[\s`'\"(]))(?P<p>(?:[^\s`'\"()]*/)?kanban\.db)(?:-wal|-shm|-journal)?(?![\w.])")


def kanban_board(why: str) -> Optional[tuple[str, Path]]:
    """``(slug, db_path)`` of the Kanban board a refusal is about, or None for any other database.
    ``<home>/kanban.db`` (or a bare relative ``kanban.db``) is ``default``; ``<home>/kanban/boards/<slug>/kanban.db``
    is ``<slug>``; any other ``.../kanban.db`` (a profile's own home) is ``""`` with its own path."""
    m = _BOARD_IN_WHY.search(why or "")
    if not m:
        return None
    raw = m.group("p")
    home = Path(_home())
    if raw == "kanban.db":
        return "default", home / "kanban.db"
    full = Path(_expand(raw))
    if full == home / "kanban.db":
        return "default", full
    if full.parent.parent == home / "kanban" / "boards":
        return full.parent.name, full
    return "", full


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return False
    return True


def recovery_holder(db: Path) -> Optional[dict]:
    """The live holder of ``<db>.recovery.lock`` as recorded in ``<db>.recover.pid`` by ``hermes kanban
    recover`` / ``swap_db.py`` (``{pid, session_id, task_id, started_at}``), or None. Read-only: probing the
    flock itself could make a recovery that starts at that instant report busy."""
    try:
        raw = db.with_name(db.name + ".recover.pid").read_text().strip()
    except OSError:
        return None
    try:
        info = json.loads(raw)
    except ValueError:
        info = {"pid": raw}
    if not isinstance(info, dict):
        info = {"pid": info}
    try:
        info["pid"] = int(info.get("pid"))
    except (TypeError, ValueError):
        return None
    if not _pid_alive(info["pid"]):
        return None
    if info.get("proc_start") and proc_start(info["pid"]) not in (None, str(info["proc_start"])):
        return None  # the recorded process died and its pid was reused
    return info


def proc_start(pid: int) -> Optional[str]:
    """Linux process start time in clock ticks (/proc/<pid>/stat field 22); tells a reused pid apart."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def describe_holder(info: dict) -> str:
    parts = [f"pid {info['pid']}"]
    if info.get("session_id"):
        parts.append(f"session {info['session_id']}")
    if info.get("task_id"):
        parts.append(f"card {info['task_id']}")
    return ", ".join(parts)


def recover_command(slug: str, db: Optional[Path] = None) -> str:
    if slug == "default":
        return "hermes kanban recover"
    if slug:
        return f"hermes kanban --board {slug} recover"
    home = str(db.parent if db else "<home>").replace(str(Path.home()), "~")
    return f"HERMES_HOME={home} hermes kanban recover"


def message(why: str) -> str:
    head = (
        f"SQLITE LIVE GUARD: blocked: {why}. Another process may have this database open; deleting its "
        "-wal/-shm or swapping the file under it splits writers across WAL views and corrupts the board on the "
        "next checkpoint (hub incident: default kanban.db, 2026-10-28). Read with `sqlite3 -readonly` or a "
        "`file:...?immutable=1` copy. "
    )
    board = kanban_board(why)
    if board:
        slug, db = board
        holder = recovery_holder(db)
        running = (f"A recovery is running now ({describe_holder(holder)}): do not start another; comment on "
                   f"{'card ' + holder['task_id'] if holder.get('task_id') else 'its owner'} and wait for its report. "
                   if holder else "")
        return head + running + (
            "This is a Kanban board: change its data through the kanban tools or `hermes kanban` (index damage: "
            f"`hermes kanban repair`). A damaged board is rebuilt by ONE owner with `{recover_command(slug, db)}` "
            "(single-owner recovery lock, installs in place through the SQLite backup API without renaming the "
            "file, writes a loss report; a second run exits busy and names the holder). Verify afterwards with "
            "`sqlite3 -readonly <db> 'pragma integrity_check'` (must print ok) and the report path it prints. "
            "Do not hand-swap the file and do not work around this guard."
        )
    return head + (
        "To put a recovered copy in place, one owner uses the locked helper: "
        f"`python3 {HELPER_DISPLAY} --db <live.db> --replacement <recovered.db>` (it refuses while any process "
        "holds the file or another recovery holds the lock, verifies integrity, and keeps the originals). "
        "Do not work around this guard."
    )


def log_path() -> Path:
    return Path(os.environ.get(LOG_ENV) or os.path.join(_home(), "logs", "sqlite-live-guard.log"))


def log_refusal(tool: str, why: str, command: str, session_id: str = "", task_id: str = "") -> None:
    """Append one JSON line per refusal (best effort: logging never changes the decision)."""
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": tool, "reason": why,
              "command": (command or "")[:500], "pid": os.getpid(),
              "session_id": session_id or os.environ.get("HERMES_SESSION_ID") or None,
              "task_id": task_id or os.environ.get("HERMES_KANBAN_TASK") or None}
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def enabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() != "off"
