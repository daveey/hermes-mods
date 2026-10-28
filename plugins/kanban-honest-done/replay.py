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


def latest_handoff(conn, tid):
    """Latest run's summary + every metadata text value (what Gate B reads at completion)."""
    row = conn.execute("SELECT summary, metadata FROM task_runs WHERE task_id=? AND "
                       "((summary IS NOT NULL AND summary != '') OR (metadata IS NOT NULL AND metadata != '')) "
                       "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if row:
        return "\n".join(x for x in (row[0] or "", core.metadata_text(row[1]) if row[1] else "") if x)
    row = conn.execute("SELECT result FROM tasks WHERE id=?", (tid,)).fetchone()
    return (row[0] if row else "") or ""


def origin_unreached(conn, tid):
    """Gate D replayed: origin chats the card's prose names with no link / subscription / SENT comment. Released
    links count as reached here (they are usually released AFTER completion), so this is a lower bound."""
    body = (conn.execute("SELECT body FROM tasks WHERE id=?", (tid,)).fetchone() or [""])[0] or ""
    comments = [r[0] or "" for r in conn.execute("SELECT body FROM task_comments WHERE task_id=?", (tid,))]
    wanted = core.origin_sessions("\n".join([body] + comments))
    if not wanted:
        return [], []
    reached = {r[0] for r in conn.execute("SELECT chat_session FROM task_chat_links WHERE task_id=?", (tid,))}
    try:
        reached |= {(r[0] or "").rsplit("/", 1)[-1] for r in conn.execute(
            "SELECT chat_id FROM kanban_notify_subs WHERE task_id=?", (tid,))}
    except Exception:
        pass
    for c in comments:
        if core.REPORTED_RE.search(c):
            reached |= {s for s in wanted if s in c}
    return wanted, [s for s in wanted if s not in reached]


def replay(days: float, only: str | None = None):
    since = time.time() - days * 86400
    paths = core.board_paths()
    total, out, origin = 0, [], {"named": 0, "unreached": []}
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
            try:
                wanted, missing = origin_unreached(conn, tid)
            except Exception:
                wanted, missing = [], []
            origin["named"] += bool(wanted)
            if missing:
                origin["unreached"].append((tid, title, missing))
            summary = latest_handoff(conn, tid)
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
    return total, out, origin


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--json")
    ap.add_argument("--task")
    ap.add_argument("--kinds", help="comma-separated: only report hits of these classifier kinds")
    a = ap.parse_args(argv)
    total, out, origin = replay(a.days, a.task)
    if a.kinds:
        keep = set(a.kinds.split(","))
        out = [o for o in out if keep & set(o["kinds"])]
    false = [o for o in out if o["false_done"]]
    print(f"completed cards scanned: {total}; admitted dropped work: {len(out)}; no follow-up card: {len(false)}")
    print(f"gate D: cards naming an origin chat: {origin['named']}; origin never linked/reported: "
          f"{len(origin['unreached'])}")
    for tid, title, missing in origin["unreached"]:
        print(f"  D {tid}  {(title or '')[:70]}  -> {', '.join(missing)}")
    for o in sorted(false, key=lambda o: -o["completed_at"]):
        day = time.strftime("%m-%d", time.localtime(o["completed_at"]))
        print(f"{o['id']}  {day}  {','.join(o['kinds']):<22} {o['title'][:70]}\n      \"{o['evidence'][:160]}\"")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
