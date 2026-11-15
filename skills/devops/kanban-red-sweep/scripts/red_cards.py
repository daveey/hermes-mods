#!/usr/bin/env python3
"""Red-card inventory, sweep log and stats for the kanban-red-sweep skill (stdlib only).

  red_cards.py list            deterministic inventory of red cards on every board (cron monitor: no timestamps)
  red_cards.py log ...         append one sweep finding to ~/.hermes/state/kanban-red-sweep.jsonl
  red_cards.py stats [--days N] counts per day, class, action and avoidable; top prevention slugs
  red_cards.py waiting [--json] "not ready" audit: Todo + Scheduled cards by what each waits on, actionable first
"""
import argparse
import collections
import glob
import json
import os
import re
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


TODO_CLASSES = {
    # class: (actionable, what to do)
    "todo-parents-done": (True, "every parent finished but it never promoted: re-check links / promote it"),
    "todo-parent-missing": (True, "a parent id no longer exists on this board: unlink or re-point it"),
    "todo-parent-blocked": (True, "chain waits on a red card: clear that card (red sweep) and this follows"),
    "todo-no-parents": (True, "todo with nothing to wait on: find why it was parked, then ready or archive it"),
    "todo-parent-active": (False, "parent is running/ready/in review"),
    "todo-parent-waiting": (False, "parent is itself todo/scheduled (downstream of a wait)"),
    "sched-overdue": (True, "wake time passed over 1 h ago and it did not wake: check its wake_check/dispatcher"),
    "sched-awaited-done": (True, "the card it awaits already finished: wake it now"),
    "sched-waiting": (False, "waiting on a clock, CI, deploy, card or outside event that is still pending"),
}
_TID = re.compile(r"\bt_[0-9a-f]{8,16}\b")


def waiting_sources():
    """(board, path) per board: the fleet replica (what the Board UI shows) when present, else the local DB."""
    out = []
    for board, local in boards():
        shadow = os.path.join(HOME, "kanban", "sync-shadow", f"{board}.db")
        out.append((board, shadow if os.path.isfile(shadow) else local))
    return out


def audit_waiting(conn, now):
    cols = {r[1] for r in conn.execute("pragma table_info(tasks)")}
    gw = "coalesce(created_on,'local')" if "created_on" in cols else "'local'"
    status = dict(conn.execute("select id,status from tasks"))
    rows = conn.execute(
        f"select id,status,title,coalesce(priority,0),{gw} from tasks "
        "where status in ('todo','scheduled') order by id").fetchall()
    cards = []
    for tid, st, title, prio, gateway in rows:
        parents = {p: status.get(p, "MISSING") for (p,) in conn.execute(
            "select parent_id from task_links where child_id=?", (tid,))}
        ev = conn.execute(
            "select payload from task_events where task_id=? and kind in ('scheduled','continued','dependency_wait')"
            " order by id desc limit 1", (tid,)).fetchone()
        try:
            payload = json.loads(ev[0] or "{}") if ev else {}
        except ValueError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        wake_at = payload.get("wake_at")
        aw = payload.get("awaiting")
        aw = aw if isinstance(aw, dict) else {}
        refs = set(_TID.findall(json.dumps(aw) + str(payload.get("wake_check") or "")))
        if aw.get("kind") == "card" and aw.get("ref") and not aw.get("gateway"):
            refs.add(str(aw["ref"]))
        awaited = {r: status.get(r, "?") for r in sorted(refs) if r != tid}
        if st == "todo":
            open_p = [v for v in parents.values() if v not in ("done", "archived")]
            if not parents:
                cls = "todo-no-parents"
            elif not open_p:
                cls = "todo-parents-done"
            elif "MISSING" in open_p:
                cls = "todo-parent-missing"
            elif "blocked" in open_p or "gave_up" in open_p:
                cls = "todo-parent-blocked"
            elif all(v in ("running", "ready", "review") for v in open_p):
                cls = "todo-parent-active"
            else:
                cls = "todo-parent-waiting"
        else:
            if isinstance(wake_at, (int, float)) and wake_at < now - 3600:
                cls = "sched-overdue"
            elif awaited and all(v in ("done", "archived") for v in awaited.values()):
                cls = "sched-awaited-done"
            else:
                cls = "sched-waiting"
        cards.append(dict(
            id=tid, status=st, gateway=gateway, title=title or "", priority=prio, cls=cls,
            actionable=TODO_CLASSES[cls][0], parents=parents, awaited=awaited, wake_at=wake_at,
            reason=str(payload.get("reason") or "")[:200]))
    return cards


def cmd_waiting(a):
    now = time.time()
    report: dict = {"counts": {}, "sources": {}, "cards": []}
    for board, path in waiting_sources():
        conn = _ro_connect(path)
        try:
            cards = audit_waiting(conn, now)
        finally:
            conn.close()
        report["sources"][board] = path
        counts = report["counts"].setdefault(board, {})
        for c in cards:
            c["board"] = board
            g = counts.setdefault(c["gateway"], {})
            g[c["status"]] = g.get(c["status"], 0) + 1
        report["cards"].extend(cards)
    report["counts"] = {b: v for b, v in report["counts"].items() if v}
    if a.json:
        print(json.dumps(report, sort_keys=True, default=str))
        return
    cards = report["cards"]
    print("Not-ready audit: Todo + Scheduled columns (red/blocked cards are `list`, not this).")
    for board, gws in sorted(report["counts"].items()):
        for gwn, c in sorted(gws.items()):
            print(f"  {board}/{gwn}: todo {c.get('todo', 0)}, scheduled {c.get('scheduled', 0)}")
    tally = collections.Counter(c["cls"] for c in cards)
    print("by class: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    act = [c for c in cards if c["actionable"]]
    print(f"\nactionable ({len(act)}): act on these first")
    for c in sorted(act, key=lambda c: (c["cls"], -c["priority"])):
        extra = {k: v for k, v in (("parents", c["parents"]), ("awaited", c["awaited"])) if v}
        print(f"  [{c['cls']}] {c['board']}/{c['gateway']} {c['id']} {c['title'][:70]} {extra or ''}")
        print(f"      -> {TODO_CLASSES[c['cls']][1]}")
    rest = [c for c in cards if not c["actionable"]]
    print(f"\nwaiting on purpose ({len(rest)}):")
    for cls in sorted({c["cls"] for c in rest}):
        group = [c for c in rest if c["cls"] == cls]
        print(f"  [{cls}] {len(group)}: {TODO_CLASSES[cls][1]}")
        for c in group[: a.limit]:
            print(f"    {c['board']}/{c['gateway']} {c['id']} {c['title'][:70]} | {c['reason'][:90]}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    wt = sub.add_parser("waiting", help="audit Todo + Scheduled (\"not ready\") cards by what each waits on")
    wt.add_argument("--json", action="store_true")
    wt.add_argument("--limit", type=int, default=15, help="rows shown per waiting class")
    wt.set_defaults(fn=cmd_waiting)
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
