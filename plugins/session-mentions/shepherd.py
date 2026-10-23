"""Adopt a task on another gateway for the session that reads or comments on it (cross_task).

A session that follows a card on another gateway owns that work, but the sidebar only knew about cards on this
gateway, so the row painted green while the remote card ran. When cross_task reads or comments on a task that the
local hermes-hub replica places on ANOTHER gateway, queue

    hermes-hub request <gw> tasks <board>/<task_id> shepherd --arg session=<sid> --arg profile=<profile>

The owning gateway records a ``rel: shepherd`` session link on the card, and this gateway's sidebar counts it (and
its children) as the session's work. Fire-and-forget: deduped per (session, task) in
``<hermes home>/session_mentions/shepherd-sent.json``; any failure is swallowed and never changes the tool output.
No hermes-hub install (no replica / CLI) = no-op.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

LEDGER_NAME = "shepherd-sent.json"
LEDGER_MAX = 2000
_TASK_ID = re.compile(r"\bt_[0-9a-z]+\b")
_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def hub_db_path() -> Path | None:
    base = os.environ.get("HERMES_HUB_STATE") or str(Path.home() / ".local" / "state" / "hermes-hub")
    p = Path(base) / "hub.db"
    return p if p.is_file() else None


def hub_cli() -> str | None:
    found = shutil.which("hermes-hub")
    if found:
        return found
    p = Path.home() / ".local" / "bin" / "hermes-hub"
    return str(p) if p.is_file() else None


def parse_target(target) -> tuple[str | None, str | None]:
    """``(board or None, task_id)`` from a task link, ``<gw>:<id>`` or bare id."""
    text = str(target or "").strip().strip("`\"'")
    m = re.match(r"^@task:([\w.-]*)(?:@[\w.-]+)?/(t_[0-9a-z]+)", text)
    if m:
        return (m.group(1) or "default"), m.group(2)
    ids = _TASK_ID.findall(text)
    return None, (ids[-1] if ids else None)


def _ro(path: Path) -> sqlite3.Connection:
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=1)
        c.execute("PRAGMA query_only = ON")
        return c


def locate(task_id: str, board: str | None, db: Path) -> tuple[str, str] | None:
    """``(gw, board)`` of a card that lives on ANOTHER gateway, from the hermes-hub replica (None if local/unknown)."""
    conn = _ro(db)
    try:
        row = conn.execute("SELECT v FROM meta WHERE k = 'gateway'").fetchone()
        me = row[0] if row else None
        hits = conn.execute("SELECT gw, key FROM replica WHERE coll = 'tasks' AND key LIKE ? ESCAPE '\\'",
                            ("%/" + task_id.replace("_", "\\_"),)).fetchall()
    finally:
        conn.close()
    hits = [(str(gw), str(key).rsplit("/", 1)[0]) for gw, key in hits if str(key).rsplit("/", 1)[-1] == task_id]
    if board:
        hits = [h for h in hits if h[1] == board] or hits
    if not hits or any(gw == me for gw, _ in hits):
        return None  # unknown, or a card on this gateway (the local board already counts it)
    return hits[0]


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _spawn(argv: list[str]) -> None:
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def maybe_shepherd(target, session_id, profile, ledger_dir: Path, *, db: Path | None = None,
                   cli: str | None = None, spawn=_spawn) -> str | None:
    """Queue one shepherd request for (session, remote task); returns the request id, or None when skipped.
    Never raises."""
    try:
        sid = str(session_id or "")
        profile = str(profile or "default")
        if not (_SAFE.match(sid) and _SAFE.match(profile)):
            return None
        board, tid = parse_target(target)
        db = db or hub_db_path()
        cli = cli or hub_cli()
        if not tid or db is None or cli is None:
            return None
        where = locate(tid, board, db)
        if where is None:
            return None
        gw, board = where
        key = f"{sid}|{gw}/{board}/{tid}"
        ledger_path = Path(ledger_dir) / LEDGER_NAME
        sent = _load(ledger_path)
        if key in sent:
            return None
        rid = "shep-" + hashlib.sha256(key.encode()).hexdigest()[:32]
        spawn([cli, "request", gw, "tasks", f"{board}/{tid}", "shepherd", "--arg", f"session={sid}",
               "--arg", f"profile={profile}", "--id", rid])
        sent[key] = time.time()
        if len(sent) > LEDGER_MAX:
            sent = dict(sorted(sent.items(), key=lambda kv: kv[1])[-LEDGER_MAX:])
        tmp = ledger_path.with_name(f".{ledger_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(sent))
        os.replace(tmp, ledger_path)
        return rid
    except Exception:
        return None
