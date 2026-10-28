"""Decide whether a shell command or Python snippet would damage a live Hermes SQLite database.

A live database is a ``*.db`` file (or its ``-wal`` / ``-shm`` / ``-journal`` sidecar) under the Hermes
home (``~/.hermes``, ``$HERMES_HOME``), outside scratch, cache, archive and backup paths. Blocked:

- ``rm`` / ``unlink`` / ``shred`` / ``truncate`` / ``mv`` of a live file, or a live file as the target of
  ``cp`` / ``install`` / ``rsync`` / ``ln`` / ``dd of=`` / a ``>`` redirect. Deleting the ``-wal`` or
  ``-shm`` of a database that another process has open, or swapping the main file under it, splits the
  writers onto different WAL views, and the next checkpoint corrupts the b-trees.
- ``sqlite3`` writes against a live file (DML, DDL, ``REINDEX``, ``VACUUM``, ``.recover``/``.restore`` into
  it, journal or checkpoint pragmas). Reads, ``-readonly`` and ``mode=ro``/``immutable=1`` URIs pass.

Swapping a recovered copy in goes through ``swap_db.py`` next to this file, which takes a per-database
recovery lock, refuses while any process holds the file, checks the replacement and keeps the originals.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Iterable, Optional

HELPER = Path(__file__).resolve().parent / "swap_db.py"
HELPER_DISPLAY = str(HELPER).replace(str(Path.home()), "~")

DISABLE_ENV = "HERMES_SQLITE_GUARD"  # "off" disables (an operator-approved, single-owner recovery only)

_LIVE_NAME = re.compile(r"\.db(?:-wal|-shm|-journal)?$")
# Paths under the Hermes home that never hold a live database.
_NOT_LIVE_PARTS = ("/cache/", "/archive/", "/backups/", "/.hermes-sync-trash/", "/tmp/", "kanban-recovery")
_NOT_LIVE_MARKERS = (".bak", ".corrupt.", ".pre-", ".recovered", ".new", ".orig", ".snapshot")
_HOME_PREFIX = re.compile(
    r"^(?:~|\$\{?HOME\}?|/home/[^/]+|/Users/[^/]+|/root)/\.hermes(?:/|$)|^\$\{?HERMES_HOME\}?(?:/|$)")
_SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "|&"}
_WRAPPERS = {"sudo", "env", "command", "nice", "nohup", "exec", "time", "ionice", "stdbuf"}
_SQL_WRITE = re.compile(
    r"\b(?:insert|update|delete|replace|upsert|reindex|vacuum|drop|create|alter|attach)\b"
    r"|\bpragma\s+(?:main\.)?(?:journal_mode|wal_checkpoint|writable_schema|user_version|schema_version|"
    r"secure_delete|page_size|auto_vacuum|incremental_vacuum|optimize)\b"
    r"|^\s*\.(?:recover|restore|import|load|read|clone)\b",
    re.IGNORECASE | re.MULTILINE,
)


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
    return not any(part in path for part in _NOT_LIVE_PARTS)


def _tokens(cmd: str) -> list[str]:
    lexer = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to whitespace
        return cmd.split()


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
                i += 1
        elif tok == "timeout":
            i += 1
            while i < len(seg) and (seg[i].startswith("-") or re.match(r"^\d+[smhd]?$", seg[i])):
                i += 1
        else:
            break
    return seg[i:]


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
        if tok in (">", ">>", ">|", "&>") or (tok == ">" and i + 1 < len(seg)):
            if is_live(seg[i + 1], in_home):
                return f"a `>` redirect would overwrite live database file {seg[i + 1]}"
    return None


def _check_sqlite3(args: list[str], seg: list[str], in_home: bool) -> Optional[str]:
    readonly = any(a in ("-readonly", "--readonly", "-safe") for a in args)
    sql: list[str] = []
    db = None
    i = 0
    while i < len(args):
        a = args[i]
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
    if db is None or not is_live(db, in_home):
        return None
    if readonly or re.search(r"[?&](?:mode=ro|immutable=1)", db):
        return None
    text = "\n".join(sql)
    fed = "<" in seg or "<<" in seg or "<<<" in seg
    if _SQL_WRITE.search(text) or (fed and not text):
        return f"`sqlite3` would write to live database {db}"
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
        ops = _operands([a for a in args if a not in ("<", ">", ">>", "<<", "<<<")])
        if word in ("rm", "unlink", "shred", "truncate", "mv"):
            hit = next((o for o in ops if is_live(o, in_home)), None)
            if hit:
                verb = "move" if word == "mv" else "delete or truncate"
                return f"`{word}` would {verb} live database file {hit}"
        elif word in ("cp", "install", "rsync", "ln", "sqlite3_rsync"):
            if ops and is_live(ops[-1], in_home):
                return f"`{word}` would overwrite live database file {ops[-1]}"
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


_PY_DESTRUCTIVE = re.compile(
    r"\bos\.(?:remove|unlink|rename|replace|truncate)\s*\(|\bshutil\.(?:move|copy|copy2|copyfile)\s*\("
    r"|\.(?:unlink|rename|replace|write_bytes|write_text)\s*\(|\bopen\s*\([^)]*[\"'][wa+][b+]?[\"']")
_PY_STR = re.compile(r"""(?P<q>["'])(?P<s>[^"'\n]{1,400})(?P=q)""")


def check_python(code: str) -> Optional[str]:
    code = code or ""
    literals = [m.group("s") for m in _PY_STR.finditer(code)]
    live = [s for s in literals if is_live(os.path.expanduser(s)) or is_live(s)]
    if live and _PY_DESTRUCTIVE.search(code):
        return f"Python file operations on live database file {live[0]}"
    for lit in literals:  # os.system("rm ...") / subprocess.run("...", shell=True)
        if " " in lit:
            why = check_shell(lit)
            if why:
                return why
    return None


def message(why: str) -> str:
    return (
        f"SQLITE LIVE GUARD: blocked: {why}. Another process may have this database open; deleting its "
        "-wal/-shm or swapping the file under it splits writers across WAL views and corrupts the board on the "
        "next checkpoint (hub incident: default kanban.db, 2026-10-28). Read with `sqlite3 -readonly` or a "
        "`file:...?immutable=1` copy; change Kanban data through the kanban tools or `hermes kanban` (repair: "
        "`hermes kanban repair`). To put a recovered copy in place, one owner uses the locked helper: "
        f"`python3 {HELPER_DISPLAY} --db <live.db> --replacement <recovered.db>` (it refuses while any process "
        "holds the file or another recovery holds the lock, verifies integrity, and keeps the originals). "
        "Do not work around this guard."
    )


def enabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() != "off"
