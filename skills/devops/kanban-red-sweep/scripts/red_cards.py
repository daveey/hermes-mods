#!/usr/bin/env python3
"""Red-card inventory, sweep log and stats for the kanban-red-sweep skill (stdlib only).

  red_cards.py list            deterministic inventory of red cards on every board (cron monitor: no timestamps)
  red_cards.py log ...         append one sweep finding to ~/.hermes/state/kanban-red-sweep.jsonl
  red_cards.py stats [--days N] counts per day, class, action and avoidable; top prevention slugs
"""
import argparse
import collections
import glob
import json
import os
import sqlite3
import sys
import time

HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
LOG = os.environ.get("RED_SWEEP_LOG") or os.path.join(HOME, "state", "kanban-red-sweep.jsonl")
CLASSES = (
    "already-satisfied", "false-premise", "silent-default", "standing-approval", "headless-refusal",
    "missing-credential", "conflicting-instructions", "scope-permission", "moving-target",
    "review-reserved", "real-decision", "physical-or-signin", "other",
)
ACTIONS = ("cleared", "guessed", "asked", "left-to-owner", "left-blocked")
RED = (
    "select id,status,coalesce(block_kind,''),title,"
    "(select max(id) from task_events e where e.task_id=t.id and e.kind='blocked') "
    "from tasks t where status in ('blocked','gave_up') "
    "or (status='triage' and coalesce(block_kind,'') in ('needs_input','capability')) order by id"
)


def boards():
    out = [("default", os.path.join(HOME, "kanban.db"))]
    for p in sorted(glob.glob(os.path.join(HOME, "kanban", "boards", "*", "kanban.db"))):
        out.append((os.path.basename(os.path.dirname(p)), p))
    return [(b, p) for b, p in out if os.path.isfile(p)]


def _ro_connect(path, timeout=10):
    """Read WAL databases even when sidecars need recovery; never create a DB."""
    from pathlib import Path

    uri = Path(path).resolve().as_uri()
    c = None
    try:
        c = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        if c is not None:
            c.close()
    c = sqlite3.connect(f"{uri}?mode=rw", uri=True, timeout=timeout)
    try:
        c.execute("PRAGMA query_only=ON")
        return c
    except Exception:
        c.close()
        raise


def cmd_list(a):
    from fleet_inventory import inventory, snapshot, render
    if a.local_json:
        print(json.dumps(snapshot(HOME, _ro_connect, replicas=False), sort_keys=True))
    else:
        print(render(inventory(HOME, _ro_connect)))


def cmd_log(a):
    rec = dict(
        ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), gateway=a.gateway,
        board=a.board, card=a.card, title=a.title,
        cls=a.cls, action=a.action, avoidable=a.avoidable, slug=a.slug, prevention=a.prevention,
        blocked_event=a.blocked_event, run=a.run,
    )
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if a.blocked_event:
        for old in read():
            if (old.get("gateway", "local"), old.get("board", "default"), old.get("card"), old.get("blocked_event")) == (a.gateway, a.board, a.card, a.blocked_event):
                print(f"already logged {a.card} blocked_event={a.blocked_event}")
                return
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    print(f"logged {a.card} {a.cls} {a.action}")


def read():
    if not os.path.isfile(LOG):
        return []
    out = []
    with open(LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def cmd_stats(a):
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - a.days * 86400))
    recs = [r for r in read() if r.get("ts", "")[:10] >= cutoff]
    print(f"{len(recs)} red-card findings since {cutoff} ({LOG})")
    for name, key in (("per day", lambda r: r["ts"][:10]), ("class", lambda r: r.get("cls")),
                      ("action", lambda r: r.get("action")), ("avoidable", lambda r: r.get("avoidable"))):
        cnt = collections.Counter(key(r) for r in recs)
        print(f"{name}: " + ", ".join(f"{k}={v}" for k, v in sorted(cnt.items(), key=lambda kv: str(kv[0]))))
    slugs = collections.Counter(r.get("slug") for r in recs if r.get("slug"))
    print("prevention slugs (3+ = file one prevention card):")
    for slug, v in slugs.most_common(15):
        last = next(r for r in reversed(recs) if r.get("slug") == slug)
        print(f"  {v}x [{slug}] {last.get('prevention', '')[:140]}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--local-json", action="store_true", help="local snapshot for authorized fleet readers")
    listing.set_defaults(fn=cmd_list)
    lg = sub.add_parser("log")
    lg.add_argument("--card", required=True)
    lg.add_argument("--gateway", default="local", help="owner gateway from inventory")
    lg.add_argument("--board", default="default")
    lg.add_argument("--title", default="")
    lg.add_argument("--cls", required=True, choices=CLASSES)
    lg.add_argument("--action", required=True, choices=ACTIONS)
    lg.add_argument("--avoidable", required=True, choices=("yes", "partly", "no"))
    lg.add_argument("--slug", default="", help="short kebab-case name of the prevention rule; reuse verbatim")
    lg.add_argument("--prevention", default="", help="what would have kept this card from blocking")
    lg.add_argument("--blocked-event", type=int, default=0, help="id from `list`; dedupes repeat sweeps")
    lg.add_argument("--run", default="", help="chat or cron run that swept it")
    lg.set_defaults(fn=cmd_log)
    st = sub.add_parser("stats")
    st.add_argument("--days", type=int, default=14)
    st.set_defaults(fn=cmd_stats)
    a = ap.parse_args()
    a.fn(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
