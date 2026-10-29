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
    return not any(part in rel for part in _NOT_LIVE_PARTS)


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
                takes_arg = tok == "sudo" and seg[i] in ("-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t")
                i += 2 if takes_arg else 1
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
        if tok in (">", ">>", ">|", "&>") and is_live(seg[i + 1], in_home):
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
        ops = _operands([a for a in args if a not in ("<", ">", ">>", "<<", "<<<")])
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
