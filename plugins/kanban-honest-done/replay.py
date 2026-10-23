#!/usr/bin/env python3
"""Replay the dropped-work classifier over real boards: which completed cards admitted unfinished work, and which
of those have no follow-up card (= false "done").

    python3 replay.py [--days 14] [--json out.json] [--task t_xxxxxxxx]

Read-only. Follow-up = a card named in the summary, created by the card (creator_task_id) or linked to it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("kanban_honest_done_core", Path(__file__).resolve().with_name("core.py"))
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


def latest_summary(conn, tid):
    row = conn.execute("SELECT summary FROM task_runs WHERE task_id=? AND summary IS NOT NULL AND summary != '' "
                       "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT result FROM tasks WHERE id=?", (tid,)).fetchone()
    return (row[0] if row else "") or ""


def replay(days: float, only: str | None = None):
    since = time.time() - days * 86400
    paths = core.board_paths()
    total, out = 0, []
    for p in paths:
        try:
            conn = core.ro_connect(p)
            rows = conn.execute("SELECT id, title, completed_at, status FROM tasks WHERE status IN ('done','archived') "
                                "AND completed_at > ?", (since,)).fetchall()
        except Exception:
            continue
        for tid, title, done_at, status in rows:
            if only and tid != only:
                continue
            total += 1
            summary = latest_summary(conn, tid)
            hits = core.dropped_work_in_handoff(summary, title or "")
            if not hits:
                continue
            related = core.related_task_ids(conn, tid)
            named = core.valid_followups(tid, summary, paths, statuses=core.ACCEPTED_STATUSES + ("archived",))
            out.append(dict(board=p.parent.name if p.parent.parent.name == "boards" else "default", id=tid,
                            title=title, status=status, completed_at=done_at, kinds=sorted({k for k, _ in hits}),
                            evidence=hits[0][1], named_followups=named, related=sorted(related),
                            gate_would_pass=bool(core.valid_followups(tid, summary, paths)),
                            false_done=not (named or related)))
        conn.close()
    return total, out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--json")
    ap.add_argument("--task")
    a = ap.parse_args(argv)
    total, out = replay(a.days, a.task)
    false = [o for o in out if o["false_done"]]
    print(f"completed cards scanned: {total}; admitted dropped work: {len(out)}; no follow-up card: {len(false)}")
    for o in sorted(false, key=lambda o: -o["completed_at"]):
        day = time.strftime("%m-%d", time.localtime(o["completed_at"]))
        print(f"{o['id']}  {day}  {','.join(o['kinds']):<22} {o['title'][:70]}\n      \"{o['evidence'][:160]}\"")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
