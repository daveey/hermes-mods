#!/usr/bin/env python3
"""stalled_goal: a chat's card graph whose delivery card has not moved while the rest of the graph keeps spending runs.

Defect agent-38a817308d: the rollout card of a 5-card graph sat in `todo` for 4 days while 3 watcher/incident cards
ran 306 times. Every supervisor checked that an owner was *alive* (a run spawned, the card is ready), never that the
card delivering the user's result *moved*, so nobody took the work over.

Graph: every card with an unreleased chat link to a session (task_chat_links, any kind), plus everything reachable from
those cards through task_links. Delivery cards: open sinks of the graph (no open children) that have at least one
parent. A delivery card is stalled when

  * its status is todo/ready/triage/blocked/review (not running, scheduled = an intended wait, a human block
    `block_kind` needs_input/time or status blocked with a human_request, done, archived), and
  * its last status event (created/promoted/claimed/spawned/completed/blocked/unblocked/scheduled/...) and last run
    start are older than COACH_STALLED_GOAL_HOURS (default 24), and
  * the graph's OTHER cards started at least COACH_STALLED_GOAL_RUNS runs (default 20) since then, and
  * its open-ancestor frontier is not entirely a human wait.

Open-ancestor frontier: the nearest ancestors that are not done/archived and whose own parents are all done or
archived (walk past open intermediates and through done cards). If every frontier card is a human wait, the
delivery card is `waiting_on_human` instead of `stalled_goal`: there is nothing for an agent to take over. A
frontier card that is running, scheduled, ready or todo is agent work — liveness is not progress, so that still
emits `stalled_goal`. The hook only speaks for `stalled_goal`. The CLI prints both.

Two delivery paths:
  * hook (coach `pre_llm_call`, the origin chat's own gateway): the next turn of a linked chat gets one
    "take over" context line per incident, and the card gets one Kanban comment (local boards only; a replica is
    commented by its owner's coach). An idle chat gets it on its next turn: there is no push into an idle chat.
  * CLI (read-only unless --comment): `stalled_goal.py [--db PATH ...] [--session SID] [--now TS] [--json]`.
    Without --db it scans this home's boards (kanban.db, kanban/boards/*/kanban.db). `--replicas` adds
    kanban/sync-shadow/*.db (other gateways' boards, report only).

COACH_STALLED_GOAL=0 turns the hook off.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

OPEN = ("todo", "ready", "triage", "blocked", "review")
STATUS_EVENTS = ("created", "promoted", "claimed", "spawned", "completed", "blocked", "unblocked", "scheduled",
                 "review_requested", "changes_requested", "archived", "reclaimed", "crashed", "gave_up", "timed_out",
                 "continued", "status", "goal_verified")
HUMAN_WAITS = ("needs_input", "time")
AGENT_STATUSES = ("running", "scheduled", "ready", "todo")  # a frontier card in one of these is not a human wall
CLOSED = ("done", "archived")
MAX_GRAPH = 400


def _has_request(raw) -> bool:
    text = str(raw).strip() if raw else ""
    return bool(text) and text not in ("null", "{}", "[]")


def card_human_wait(info: Optional[dict]) -> bool:
    """The card itself is an intended human wait, whatever else it is doing."""
    if not info:
        return False
    if info.get("block_kind") in HUMAN_WAITS:
        return True
    return info.get("status") == "blocked" and _has_request(info.get("human_request"))


def frontier_human_wait(info: Optional[dict]) -> bool:
    """A frontier card silences the stall only when it is a human wait and not agent work.

    running/scheduled/ready/todo stay stalls even with a stale needs_input block_kind: liveness is not progress.
    """
    if not info or info.get("status") in AGENT_STATUSES:
        return False
    return card_human_wait(info)


def hours() -> float:
    try:
        return float(os.environ.get("COACH_STALLED_GOAL_HOURS", "24"))
    except ValueError:
        return 24.0


def min_runs() -> int:
    try:
        return int(os.environ.get("COACH_STALLED_GOAL_RUNS", "20"))
    except ValueError:
        return 20


def enabled() -> bool:
    return os.environ.get("COACH_STALLED_GOAL", "1").strip().lower() not in ("0", "off", "false", "no")


def _ro(path) -> sqlite3.Connection:
    """Read-only open that survives a WAL db whose -wal/-shm were removed (see reply_check._ro_connect)."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
        c.execute("PRAGMA query_only=ON")
        return c


def home(h: Optional[str] = None) -> Path:
    """Hermes home: an explicit `h`, else core get_hermes_home() (task override > HERMES_HOME > platform default)."""
    if h:
        return Path(h).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        env = os.environ.get("HERMES_HOME", "").strip()
        return Path(env).expanduser() if env else Path.home() / ".hermes"


def kanban_root(h: Optional[Path] = None) -> Path:
    """Board root like core kanban_db.kanban_home(): an explicit `h`, else HERMES_KANBAN_HOME, else the default Hermes
    root (boards are shared across profiles, so a profile's HERMES_HOME is not the board root)."""
    if h:
        return Path(h).expanduser()
    override = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from hermes_constants import get_default_hermes_root  # type: ignore
        return Path(get_default_hermes_root())
    except Exception:
        return home()


def board_dbs(h: Optional[Path] = None, replicas: bool = False) -> list:
    """[(board slug or None for default, path, owned)] for this home; replicas are other gateways' boards.
    Enumerates like core kanban_session_activity._board_db_paths: HERMES_KANBAN_DB (a worker's one-board pin) is not
    the default board, so it never relabels or hides the default board."""
    h = kanban_root(h)
    out = [(None, h / "kanban.db", True)]
    out += [(p.parent.name, p, True) for p in sorted((h / "kanban" / "boards").glob("*/kanban.db"))]
    if replicas:
        out += [(f"replica:{p.stem}", p, False) for p in sorted((h / "kanban" / "sync-shadow").glob("*.db"))]
    return [b for b in out if b[1].is_file()]


def _cols(c, table) -> set:
    return {r[1] for r in c.execute(f"pragma table_info({table})")}


def _scan_db(c, sessions: Optional[set], now: float, h: float, n: int) -> list:
    since_limit = now - h * 3600
    q = "select task_id, chat_session from task_chat_links where released=0"
    params: list = []
    if sessions is not None:
        q += f" and chat_session in ({','.join('?' * len(sessions))})"
        params = sorted(sessions)
    links = list(c.execute(q, params))
    by_session: dict = {}
    for tid, sid in links:
        if sessions is None or sid in sessions:
            by_session.setdefault(sid, set()).add(tid)
    if not by_session:
        return []
    has_bk = "block_kind" in _cols(c, "tasks")
    has_hr = "human_request" in _cols(c, "tasks")
    edges = list(c.execute("select parent_id, child_id from task_links"))
    parents: dict = {}
    children: dict = {}
    for p, ch in edges:
        parents.setdefault(ch, set()).add(p)
        children.setdefault(p, set()).add(ch)
    info_cache: dict = {}

    def info(tid):
        if tid not in info_cache:
            extra = (", block_kind" if has_bk else "") + (", human_request" if has_hr else "")
            r = c.execute(f"select title, status, created_at{extra} from tasks where id=?", (tid,)).fetchone()
            if not r:
                info_cache[tid] = None
            else:
                row = {"title": r[0], "status": r[1], "created_at": r[2], "block_kind": None, "human_request": None}
                idx = 3
                if has_bk:
                    row["block_kind"] = r[idx]
                    idx += 1
                if has_hr:
                    row["human_request"] = r[idx]
                info_cache[tid] = row
        return info_cache[tid]

    def frontier(delivery):
        """Nearest non-closed ancestors whose own parents are all done or archived.

        Walk past open intermediates (their parents are not all closed) and through done/archived cards.
        A missing ancestor is kept: it is not a human wait, so the stall is not silenced. A walk that hits
        MAX_GRAPH is truncated and must not silence the stall either.
        """
        found, seen, truncated = [], set(), False

        def walk(tid):
            nonlocal truncated
            if truncated or tid in seen:
                return
            if len(seen) >= MAX_GRAPH:
                truncated = True
                return
            seen.add(tid)
            node = info(tid)
            if node is None:
                found.append(tid)
                return
            if node["status"] in CLOSED:
                for p in parents.get(tid, ()):
                    walk(p)
                return
            blocking = [p for p in parents.get(tid, ())
                        if not (info(p) and info(p)["status"] in CLOSED)]
            if blocking:
                for p in blocking:
                    walk(p)
                return
            found.append(tid)

        for p in parents.get(delivery, ()):
            walk(p)
        return found, truncated

    found: dict = {}
    for sid, seeds in by_session.items():
        # the chat's own cards plus everything downstream of them. Not upstream or sideways: boards share parents
        # (a common epic, an old fan-out), and an undirected walk merges unrelated graphs into one component.
        graph, todo = set(), [t for t in seeds]
        while todo and len(graph) < MAX_GRAPH:
            t = todo.pop()
            if t in graph:
                continue
            i = info(t)
            if not i or i["status"] == "archived":
                continue
            graph.add(t)
            todo += list(children.get(t, ()))
        for d in graph:
            i = info(d)
            if i["status"] not in OPEN or card_human_wait(i):
                continue
            if not parents.get(d):  # a delivery card waits on other work; a lone card is its own (watcher, task)
                continue
            if any(info(ch) and info(ch)["status"] not in ("done", "archived") for ch in children.get(d, ()) if ch in graph):
                continue
            last = max([i["created_at"] or 0] + [r[0] or 0 for r in c.execute(
                f"select max(created_at) from task_events where task_id=? and kind in "
                f"({','.join('?' * len(STATUS_EVENTS))})", (d, *STATUS_EVENTS))] + [r[0] or 0 for r in c.execute(
                "select max(started_at) from task_runs where task_id=?", (d,))])
            if last > since_limit:
                continue
            others = [t for t in graph if t != d]
            busy = []
            for t in others:
                k = c.execute("select count(*) from task_runs where task_id=? and started_at>?", (t, last)).fetchone()[0]
                if k:
                    busy.append({"card": t, "runs": k, "status": info(t)["status"], "title": info(t)["title"]})
            total = sum(b["runs"] for b in busy)
            if total < n:
                continue
            fr, truncated = frontier(d)
            fr_rows = []
            for f in sorted(set(fr)):
                fi = info(f) or {}
                fr_rows.append({"card": f, "title": fi.get("title") or "", "status": fi.get("status"),
                                "block_kind": fi.get("block_kind")})
            human = bool(fr) and not truncated and all(frontier_human_wait(info(f)) for f in fr)
            kind = "waiting_on_human" if human else "stalled_goal"
            rec = found.setdefault(d, {"card": d, "title": i["title"], "status": i["status"], "since": last,
                                       "stalled_hours": round((now - last) / 3600, 1), "other_runs": -1,
                                       "busy": [], "sessions": [], "kind": kind, "frontier": fr_rows})
            if kind == "waiting_on_human":
                rec["waiting_on"] = fr_rows
            rec["sessions"].append(sid)
            if total > rec["other_runs"]:  # report the busiest chat's graph; each chat's own is in by_session
                rec["other_runs"], rec["busy"] = total, sorted(busy, key=lambda b: -b["runs"])[:5]
            rec.setdefault("by_session", {})[sid] = {"other_runs": total,
                                                     "busy": sorted(busy, key=lambda b: -b["runs"])[:5]}
    return list(found.values())


def find_incidents(dbs: Iterable, now: Optional[float] = None, sessions: Optional[set] = None,
                   h: Optional[float] = None, n: Optional[int] = None) -> list:
    """Stalled delivery cards and human-wait frontiers across the given board dbs.

    Each record has ``kind`` ``stalled_goal`` or ``waiting_on_human``. Paths or (board, path, owned) tuples.
    Oldest idle card first.
    """
    now = time.time() if now is None else now
    h = hours() if h is None else h
    n = min_runs() if n is None else n
    out = []
    for item in dbs:
        board, path, owned = item if isinstance(item, tuple) else (None, item, True)
        try:
            c = _ro(path)
        except sqlite3.Error:
            continue
        try:
            for rec in _scan_db(c, sessions, now, h, n):
                out.append({**rec, "board": board, "db": str(path), "owned": owned})
        except sqlite3.Error:
            continue
        finally:
            c.close()
    return sorted(out, key=lambda r: r["since"])


def find_stalls(dbs: Iterable, now: Optional[float] = None, sessions: Optional[set] = None,
                h: Optional[float] = None, n: Optional[int] = None) -> list:
    """``stalled_goal`` records only. The hook tells a chat to take over these, never a human wait."""
    return [r for r in find_incidents(dbs, now=now, sessions=sessions, h=h, n=n) if r.get("kind") == "stalled_goal"]


def _busy_text(s: dict) -> str:
    return ", ".join(f"{b['card']} ({b['runs']} runs, {b['status']})" for b in s["busy"][:3])


def takeover_message(s: dict) -> str:
    return (f"[coach] stalled_goal: the card that delivers this chat's result, {s['card']} \"{s['title'][:90]}\", has "
            f"been {s['status']} with no status change for {s['stalled_hours']:.0f} h, while the rest of its graph "
            f"started {s['other_runs']} runs ({_busy_text(s)}). Liveness is not progress. Take over: do the next step "
            f"on {s['card']} yourself in this chat (implement, merge, deploy, prove), or name the one specific wall "
            f"that stops it. Do not reopen, re-review or add another watcher card.")


def comment_text(s: dict) -> str:
    return (f"stalled_goal (coach): no status change on this card for {s['stalled_hours']:.0f} h while linked cards "
            f"started {s['other_runs']} runs ({_busy_text(s)}). The origin chat(s) "
            f"{', '.join(s['sessions'][:3])} were told to take the next step over themselves. One comment per stall.")


def post_comment(board: Optional[str], card: str, text: str) -> bool:
    if os.environ.get("COACH_SELFHEAL_NO_EXEC") == "1":  # tests: never touch a real board
        return False
    argv = ["hermes", "kanban"] + (["--board", board] if board else []) + ["comment", card, "--author", "coach", text]
    try:
        return subprocess.run(argv, capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def _bg(fn: Callable, *a) -> None:
    threading.Thread(target=fn, args=a, daemon=True).start()


class Ledger:
    """<coach root>/stalled_goal.json: which incident (card, since) each session was told, and which got a comment."""

    def __init__(self, root: Path):
        self.path = Path(root) / "stalled_goal.json"

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {"told": {}, "commented": {}}

    def write(self, d: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d))
            tmp.replace(self.path)
        except Exception:
            pass


_CACHE: dict = {}  # session -> (checked_at, stalls)
CACHE_S = 600


def session_hint(session_ids: list, root: Path, now: Optional[float] = None, dbs=None, log=None) -> Optional[str]:
    """Hook path: the take-over message for this chat's newest untold stall, else None. Comments on the card once."""
    if not enabled() or not session_ids:
        return None
    now = time.time() if now is None else now
    key = session_ids[0]
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_S and dbs is None:
        stalls = hit[1]
    else:
        stalls = find_stalls(dbs if dbs is not None else board_dbs(), now=now, sessions=set(session_ids))
        if dbs is None:
            _CACHE[key] = (now, stalls)
    if not stalls:
        return None
    led = Ledger(root)
    d = led.read()
    told, commented = d.setdefault("told", {}), d.setdefault("commented", {})
    out = None
    for s in stalls:
        inc = f"{s['card']}@{int(s['since'])}"
        if inc in told.get(key, []):
            continue
        told.setdefault(key, []).append(inc)
        own = next((s["by_session"][x] for x in session_ids if x in s.get("by_session", {})), None)
        out = {**s, **own} if own else s  # this chat's own graph numbers, not another linked chat's
        break
    if out is None:
        return None
    inc = f"{out['card']}@{int(out['since'])}"
    if out["owned"] and inc not in commented:
        commented[inc] = now
        _bg(post_comment, out["board"], out["card"], comment_text(out))
    led.write(d)
    if log:
        log({"event": "agent_hint", "rule": "stalled_goal", "session_id": key, "card": out["card"],
             "stalled_hours": out["stalled_hours"], "other_runs": out["other_runs"], "board": out["board"]})
    return takeover_message(out)


def _frontier_text(s: dict) -> str:
    rows = s.get("waiting_on") or s.get("frontier") or []
    return ", ".join(
        f"{f['card']} ({f.get('status') or '?'}"
        + (f", {f['block_kind']}" if f.get("block_kind") else "")
        + (", human_request" if f.get("status") == "blocked" and not f.get("block_kind") else "")
        + ")"
        for f in rows[:6])


def _print_incident(s: dict) -> None:
    kind = s.get("kind") or "stalled_goal"
    print(f"{kind} {s['card']} [{s['board'] or 'default'}] {s['status']} {s['stalled_hours']:.0f}h, "
          f"{s['other_runs']} other runs: {s['title'][:80]}")
    wall = _frontier_text(s)
    if wall:
        label = "waits on" if kind == "waiting_on_human" else "frontier"
        print(f"  {label}: {wall}")
    print(f"  busy: {_busy_text(s)}")
    print(f"  chats: {', '.join(s['sessions'][:5])}{' …' if len(s['sessions']) > 5 else ''}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", action="append", help="board db to scan (repeatable); default: this home's boards")
    ap.add_argument("--replicas", action="store_true", help="also scan kanban/sync-shadow/*.db (report only)")
    ap.add_argument("--session", action="append", help="only graphs linked to this chat session (repeatable)")
    ap.add_argument("--now", type=float, help="evaluate as of this unix time")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--comment", action="store_true", help="comment once per stall on cards of boards this home owns")
    a = ap.parse_args(argv)
    dbs = [(None, Path(p), True) for p in a.db] if a.db else board_dbs(replicas=a.replicas)
    incidents = find_incidents(dbs, now=a.now, sessions=set(a.session) if a.session else None)
    stalls = [s for s in incidents if s.get("kind") == "stalled_goal"]
    if a.comment:
        led = Ledger(home() / "coach")
        d = led.read()
        for s in stalls:
            inc = f"{s['card']}@{int(s['since'])}"
            if s["owned"] and inc not in d.setdefault("commented", {}) and post_comment(s["board"], s["card"],
                                                                                       comment_text(s)):
                d["commented"][inc] = time.time()
        led.write(d)
    if a.json:
        print(json.dumps(incidents, indent=1))
    else:
        for s in incidents:
            _print_incident(s)
        if not incidents:
            print("no stalled goals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
