#!/usr/bin/env python3
"""Count delivered ask-gate "withheld" notices and file ONE Kanban card per host per day when any appear.

ask-gate used to replace a whole reply with a fixed notice ("I withheld a request after checking authoritative
approval or lifecycle state ...") when an ask in it was refused. David saw only that sentence, with no findings and
no reason (self-heal agent-37decfd37a: 17 notices in 7 sessions, Oct 27-29). The gate now holds first and, after its
budget, keeps the draft minus the refused ask; a bare notice is left only as a last resort. This watcher makes any
remaining bare notice visible to an agent instead of to David: it scans state.db for assistant replies that START
with one of the notice prefixes, newer than the last scan, and files (or comments on) the day's card.

    withheld_notice_watch.py [--dry-run] [--json] [--seed] [--home PATH] [--host NAME] [--hours N]

--dry-run prints the findings and files nothing. No Inbox items. Exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

PREFIXES = ("I withheld a", "The workflow remains stopped", "I suppressed a repeated")
AUTHOR = "withheld-notice-watch"


def hermes_home(home: Optional[str] = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _ro_connect(path: Path, timeout: float = 10):
    """mode=ro fails on a WAL db whose -wal/-shm writers removed; fall back to rw + query_only (never writes)."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


def is_notice(text: object) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(PREFIXES)


def scan(home: Path, since: float) -> list:
    """Assistant replies newer than ``since`` that start with a withheld notice, oldest first."""
    db = home / "state.db"
    if not db.exists():
        return []
    con = _ro_connect(db)
    try:
        like = " or ".join("ltrim(content) like ?" for _ in PREFIXES)
        rows = con.execute(
            f"select id, session_id, timestamp, substr(content, 1, 300) from messages where role='assistant' "
            f"and timestamp > ? and ({like}) order by timestamp", (since, *[p + "%" for p in PREFIXES])).fetchall()
    finally:
        con.close()
    return [{"id": r[0], "session": r[1], "ts": r[2], "text": r[3]} for r in rows if is_notice(r[3])]


def state_path(home: Path) -> Path:
    return home / "coach" / "withheld_notice" / "state.json"


def load_state(home: Path) -> dict:
    try:
        return json.loads(state_path(home).read_text())
    except (OSError, ValueError):
        return {}


def save_state(home: Path, st: dict) -> None:
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, p)


def _hermes_bin() -> str:
    import shutil
    if shutil.which("hermes"):
        return "hermes"
    for p in (Path.home() / ".local" / "bin" / "hermes", Path("/opt/homebrew/bin/hermes"), Path("/usr/local/bin/hermes")):
        if p.exists():
            return str(p)
    return "hermes"


def _run(runner: Callable, argv: list, body: Optional[str] = None):
    if runner is subprocess.run and argv and argv[0] == "hermes":
        argv = [_hermes_bin(), *argv[1:]]
    return runner(argv, input=body, capture_output=True, text=True, timeout=120)


def _hits_md(hits: list) -> str:
    return "\n".join(f"- {datetime.fromtimestamp(h['ts']).strftime('%m-%d %H:%M')} session `{h['session']}` "
                     f"(message {h['id']}): {h['text'][:160]!r}" for h in hits[:20])


def card_body(host: str, hits: list) -> str:
    sessions = sorted({h["session"] for h in hits})
    return f"""ask-gate delivered {len(hits)} bare "withheld" notice(s) to David on {host} in {len(sessions)} session(s).
A bare notice means the gate replaced a whole reply with a fixed sentence: David lost the findings and got no reason.
Since the agent-37decfd37a fix the gate holds first and then keeps the draft minus the refused ask, so a bare notice
is now the last-resort path: the refused ask could not be cut out cleanly, or the gate errored after a rejection.

## Hits
{_hits_md(hits)}

## Do this
1. For each session, read `~/.hermes/logs/ask-gate.jsonl` around the hit time (`refused_held`, `refused_replaced`
   with `kept: false`, `refused_strip_error`, `gate_error`) and the session's turn (state.db) to see which ask was
   refused and why it could not be stripped.
2. Fix the cause in `~/code/hermes-mods/plugins/ask-gate` (five-whys skill for the chain), with a regression test in
   `tests/test_refusal_hold.py` that reproduces the draft.

## Acceptance
- The regression test fails before the fix and passes after; the plugin's whole suite passes.
- `python3 ~/code/hermes-mods/plugins/coach/withheld_notice_watch.py --dry-run --hours 48` shows no hit from a session
  started after the fix. Paste it in a comment before completing.
"""


def file_card(home: Path, host: str, now: datetime, hits: list, runner: Callable) -> dict:
    key = f"withheld-notice:{host}:{now.strftime('%Y-%m-%d')}"
    st = load_state(home)
    tid = (st.get("cards") or {}).get(key)
    if not tid:
        title = f"ask-gate delivered {len(hits)} bare withheld notice(s) on {host}"
        r = _run(runner, ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", key,
                          "--created-by", AUTHOR, "--body-file", "-", "--json", title], card_body(host, hits))
        if r.returncode != 0:
            return {"key": key, "action": "create_failed", "out": ((r.stdout or "") + (r.stderr or ""))[-300:]}
        try:
            tid = json.loads(r.stdout).get("id")
        except (ValueError, TypeError, AttributeError):
            return {"key": key, "action": "create_unparsed", "out": (r.stdout or "")[-300:]}
        st.setdefault("cards", {})[key] = tid
        save_state(home, st)
        return {"key": key, "action": "created", "task_id": tid}
    r = _run(runner, ["hermes", "kanban", "comment", tid, "--author", AUTHOR,
                      f"{len(hits)} more bare notice(s):\n{_hits_md(hits)}"])
    return {"key": key, "action": "commented" if r.returncode == 0 else "comment_failed", "task_id": tid}


def main(argv=None, runner: Optional[Callable] = None, now: Optional[float] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--home")
    ap.add_argument("--host", default=socket.gethostname().split(".")[0])
    ap.add_argument("--hours", type=float, default=24.0, help="look-back on the first run / with --dry-run")
    ap.add_argument("--seed", action="store_true", help="mark everything up to now as seen (run once at install)")
    a = ap.parse_args(argv)
    home = hermes_home(a.home)
    now = now or time.time()
    st = load_state(home)
    if a.seed:
        st["last_ts"] = now
        save_state(home, st)
        print(json.dumps({"seeded": now}))
        return 0
    since = now - a.hours * 3600 if a.dry_run or not st.get("last_ts") else max(float(st["last_ts"]), now - 7 * 86400)
    hits = scan(home, since)
    rep = {"host": a.host, "since": since, "count": len(hits), "sessions": sorted({h["session"] for h in hits}),
           "hits": hits[:20]}
    if not a.dry_run:
        if hits:
            rep["card"] = file_card(home, a.host, datetime.fromtimestamp(now), hits, runner or subprocess.run)
        st = load_state(home)
        if not hits or rep["card"]["action"] in ("created", "commented"):
            st["last_ts"] = max([since] + [h["ts"] for h in hits]) if hits else now
        save_state(home, st)
    if a.json or a.dry_run:
        print(json.dumps(rep, indent=1))
    elif hits:
        print(f"withheld-notice-watch: {len(hits)} bare notice(s) -> {rep['card']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
