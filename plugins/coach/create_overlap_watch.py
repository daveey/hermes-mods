#!/usr/bin/env python3
"""Detect kanban_create overlap overrides that turned out to be duplicates (one card per host and ISO week).

kanban_create refuses a card when open cards on any board/gateway already cover the work (core
`hermes_cli/kanban_overlap.py`). An agent can override with `distinct_from` + `distinct_reason`; each override is a
`create_overlap` event (`outcome: override`) on the new card. An override whose card was later ARCHIVED (a
coordinator closing it as a duplicate) means the override let a duplicate through. More than THRESHOLD of those in
the last 7 days files ONE Kanban card for an agent to tighten the check or the guidance.

    create_overlap_watch.py [--dry-run] [--json] [--home PATH] [--days N]

--dry-run prints the findings JSON and creates nothing. Exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

THRESHOLD = 2
EVENT = "create_overlap"


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


def board_dbs(home: Path) -> list:
    out = [("default", home / "kanban.db")] if (home / "kanban.db").exists() else []
    root = home / "kanban" / "boards"
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and (d / "kanban.db").exists():
                out.append((d.name, d / "kanban.db"))
    return out


def scan(home: Optional[str] = None, days: float = 7, now: Optional[float] = None) -> list:
    """Overrides in the window whose new card is archived: one dict per card."""
    since = int((now or time.time()) - days * 86400)
    found = []
    for board, path in board_dbs(hermes_home(home)):
        try:
            con = _ro_connect(path)
        except sqlite3.Error:
            continue  # a board that won't open has nothing this scan can judge
        try:
            rows = con.execute(
                "SELECT e.task_id, e.payload, e.created_at, t.title, t.status FROM task_events e "
                "JOIN tasks t ON t.id = e.task_id WHERE e.kind = ? AND e.created_at >= ?", (EVENT, since)).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        for tid, raw, at, title, status in rows:
            try:
                p = json.loads(raw or "{}")
            except ValueError:
                continue
            if p.get("outcome") == "override" and status == "archived":
                found.append({"board": board, "id": tid, "title": title, "at": at, "reason": p.get("reason"),
                              "overrode": [m.get("id") for m in p.get("matches") or []]})
    return sorted(found, key=lambda f: f["at"])


def card_key(host: str, now: Optional[float] = None) -> str:
    y, w, _ = datetime.datetime.fromtimestamp(now or time.time(), datetime.timezone.utc).isocalendar()
    return f"create-overlap-overrides:{host}:{y}-W{w:02d}"


def card_body(host: str, findings: list) -> str:
    lines = "\n".join(f"- {f['id']} ({f['board']}): {f['title']!r}; overrode {', '.join(f['overrode'])}; "
                      f"reason: {f['reason']!r}" for f in findings)
    return f"""{len(findings)} kanban_create overlap overrides on {host} in the last 7 days were later archived (the
override let a duplicate through). Threshold: more than {THRESHOLD}.

## Evidence (`create_overlap` events, outcome=override, new card archived)
{lines}

## What to do
1. For each card, read its `create_overlap` event and the card it overrode: was it really a duplicate (archived as
   one), or archived for another reason? Count only real duplicates.
2. If the overrides were duplicates, find why the agent overrode: the refusal note, the tool description, or the
   kanban-orchestrator skill pointed it the wrong way. Fix that at the source (core `hermes_cli/kanban_overlap.py`
   note text or matching, or the skill), with a test.
3. If they were not duplicates, this detector is miscounting: fix the filter in
   hermes-mods `plugins/coach/create_overlap_watch.py`.
"""


def create_argv(host: str, findings: list, now: Optional[float] = None) -> list:
    return ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", card_key(host, now),
            "--created-by", "create-overlap-watch", "--body-file", "-", "--json",
            f"kanban_create overlap overrides let {len(findings)} duplicates through on {host} this week"]


def main(argv=None, runner: Optional[Callable] = None, now: Optional[float] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print findings JSON, create no card")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--home", help="HERMES_HOME to scan (default $HERMES_HOME or ~/.hermes)")
    ap.add_argument("--days", type=float, default=7)
    a = ap.parse_args(argv)
    host = socket.gethostname().split(".")[0]
    findings = scan(a.home, a.days, now)
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host, "dry_run": a.dry_run,
           "threshold": THRESHOLD, "findings": findings, "card": None}
    if len(findings) > THRESHOLD and not a.dry_run:
        run = runner or subprocess.run
        r = run(create_argv(host, findings, now), input=card_body(host, findings), capture_output=True, text=True,
                timeout=120)
        res["card"] = {"key": card_key(host, now), "rc": r.returncode,
                       "out": ((r.stdout or "") + (r.stderr or ""))[-300:]}
    if a.json or a.dry_run:
        print(json.dumps(res))
    else:
        print(f"{res['ts']} {host}: {len(findings)} archived overrides (threshold > {THRESHOLD})"
              + (f" -> {res['card']['out'].strip()[-80:]}" if res["card"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
