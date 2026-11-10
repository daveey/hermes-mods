#!/usr/bin/env python3
"""Worker-share watch: file ONE Kanban card when the hub is full while a pool worker idles and cards wait unmarked.

The hub's dispatcher offers a card to the worker pool only when creation marked it worker-first
(``placement_policy``); every other ready card waits for one of the hub's own slots (``max_card_runs``). In
kanban-12ee0e4b65 that left the hub at its cap in 95 of 121 ten-minute slots while worker-1 ran 3 cards a day, and
nothing noticed: the rollout check only tested fake probe cards. This watch samples the live board each run (hub
cron, every 10 min): the hub at cap, a pool worker below its cap, and unmarked ready cards waiting. Three samples in
a row file one card (per day) on the hub's board listing why the waiting cards were judged non-portable (core's own
``portable_reason``), so the next fix starts from the top refusal reasons instead of a replay.

    worker_share_watch.py [--dry-run] [--json] [--home PATH] [--streak 3]

Needs the hermes-agent venv (imports ``hermes_cli``). --dry-run samples and prints, saves and files nothing.
Prints only when it filed or failed; exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

AUTHOR = "worker-share-watch"
STREAK = 3
FRESH_MS = 15 * 60 * 1000  # a machine's metrics older than this say nothing about its load
MAX_LISTED = 12


def hermes_home(home: Optional[str] = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _runs(m: dict) -> Optional[int]:
    v = (m.get("metrics") or {}).get("active_card_runs")
    return v if isinstance(v, int) else None


def _fresh(m: dict, now_ms: int) -> bool:
    at = (m.get("metrics") or {}).get("at")
    return m.get("state") == "active" and isinstance(at, (int, float)) and now_ms - at <= FRESH_MS


def _ro_connect(path: Path, timeout: float = 10):
    """Probe WAL reads; fall back to an existing database with SQL writes disabled."""
    uri = path.resolve().as_uri()
    con = None
    try:
        con = sqlite3.connect(f"{uri}?mode=ro", uri=True, timeout=timeout)
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return con
    except sqlite3.OperationalError:
        if con is not None:
            con.close()
    con = sqlite3.connect(f"{uri}?mode=rw", uri=True, timeout=timeout)
    try:
        con.execute("PRAGMA query_only=ON")
        return con
    except Exception:
        con.close()
        raise


def waiting_unmarked(db: Path) -> list:
    """Ready cards on this board the pool is never offered (no worker-first mark, not pinned to a machine)."""
    con = _ro_connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT id, title, body, assignee, requires, skills, runs_on, workspace_kind,"
                           " workspace_path FROM tasks WHERE status = 'ready' AND placement_policy IS NULL"
                           " ORDER BY created_at").fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        if (r["runs_on"] or "").strip() not in ("", "[]", "null"):
            continue
        d = dict(r)
        for k in ("requires", "skills"):
            try:
                d[k] = json.loads(d[k]) if d[k] else None
            except ValueError:
                d[k] = None
        out.append(d)
    return out


def sample(registry: dict, waiting: list, now_ms: int, reason_fn: Callable[[dict], Optional[str]]) -> dict:
    """One observation: is the hub full while a fresh pool worker has room and unmarked cards wait?"""
    machines = registry.get("machines") or {}
    me = registry.get("me") or "hub"
    hub = machines.get(me) or {}
    hub_runs, hub_cap = _runs(hub), hub.get("max_card_runs")
    hub_full = isinstance(hub_cap, int) and hub_runs is not None and hub_runs >= hub_cap
    room = {}
    for name, m in machines.items():
        cap = m.get("max_card_runs")
        if name == me or m.get("pinned_only") or m.get("hold") or not isinstance(cap, int):
            continue
        runs = _runs(m)
        if _fresh(m, now_ms) and runs is not None and runs < cap:
            room[name] = cap - runs
    reasons = collections.Counter()
    for card in waiting:
        try:
            reasons[reason_fn(card) or "portable (not re-enrolled)"] += 1
        except Exception as exc:  # one odd card never hides the rest
            reasons[f"check failed: {type(exc).__name__}"] += 1
    return {"at": now_ms, "hub": me, "hub_runs": hub_runs, "hub_cap": hub_cap, "hub_full": hub_full,
            "room": room, "waiting": len(waiting), "reasons": dict(reasons.most_common()),
            "starved": bool(hub_full and room and waiting)}


def state_path(home: Path) -> Path:
    return home / "coach" / "worker_share" / "state.json"


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


def card_body(s: dict, streak: int, examples: list) -> str:
    reasons = "\n".join(f"- `{k}`: {v}" for k, v in s["reasons"].items())
    cards = "\n".join(f"- {c['id']} ({c.get('assignee') or '-'}): {(c.get('title') or '')[:90]} -> `{c['reason']}`"
                      for c in examples[:MAX_LISTED])
    room = ", ".join(f"{k} ({v} free)" for k, v in s["room"].items())
    return f"""The hub ran {s['hub_runs']}/{s['hub_cap']} cards for {streak} samples in a row (10 min apart) while
{room} had room and {s['waiting']} ready cards waited unmarked: the dispatcher only offers worker-first cards
(`placement_policy`) to the pool, so these wait for a hub slot. Found by {AUTHOR} (hermes-mods
plugins/coach/worker_share_watch.py, kanban-12ee0e4b65).

## Why the waiting cards stayed on the hub (core `portable_reason`)
{reasons}

## Examples
{cards}

## Acceptance
1. For the top reason(s), find the producer (card title prefix / creator) and what the card really needs. A
   repo change landed by push does not need the hub: fix the producer (declared `requires`, brief text; quoted
   incident evidence goes inside `<!-- placement:evidence -->` ... `<!-- /placement:evidence -->`), or the
   placement rule in core if the marker is wrong, with a regression test.
2. A reason that is right (the card truly reads hub-only state) needs no change: say so per reason.
3. Every `created` event now carries `portable_reason`: cite the share of marked cards before and after.
"""


def _run(runner: Callable, argv: list):
    return runner(argv, capture_output=True, text=True, timeout=120)


def file_card(s: dict, streak: int, examples: list, runner: Callable = subprocess.run,
              day: Optional[str] = None) -> Optional[str]:
    day = day or time.strftime("%Y-%m-%d", time.gmtime(s["at"] / 1000))
    top = next(iter(s["reasons"]), "?")
    argv = ["hermes", "kanban", "create", f"worker share: hub full, {s['waiting']} cards wait unmarked (top: {top})",
            "--body", card_body(s, streak, examples), "--assignee", "default", "--created-by", AUTHOR,
            "--idempotency-key", f"{AUTHOR}:{day}", "--requires", "terminal", "--requires", "file",
            "--requires", "git", "--requires", "host_repo", "--json"]
    r = _run(runner, argv)
    if r.returncode != 0:
        raise RuntimeError(f"kanban create failed ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    import re
    m = re.search(r'"id"\s*:\s*"(t_[0-9a-f]+)"', r.stdout or "") or re.search(r"\b(t_[0-9a-f]{6,})\b", r.stdout or "")
    return m.group(1) if m else None


def run(home: Path, *, registry_fn: Callable[[], object], reason_fn: Callable[[dict], Optional[str]],
        now_ms: Optional[int] = None, streak_needed: int = STREAK, dry_run: bool = False,
        runner: Callable = subprocess.run) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    waiting = waiting_unmarked(home / "kanban.db")
    s = sample(dict(registry_fn() or {}), waiting, now_ms, reason_fn)
    st = load_state(home)
    streak = (int(st.get("streak") or 0) + 1) if s["starved"] else 0
    out = {"sample": s, "streak": streak, "filed": None}
    if dry_run:
        return out
    day = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
    if streak >= streak_needed and st.get("filed_day") != day:
        examples = []
        for c in waiting:
            try:
                reason = reason_fn(c)
            except Exception:
                reason = "check failed"
            if reason:
                examples.append({**c, "reason": reason})
        out["filed"] = file_card(s, streak, examples, runner=runner, day=day)
        st["filed_day"], st["filed_card"] = day, out["filed"]
    st.update({"streak": streak, "last": {k: s[k] for k in ("at", "hub_runs", "hub_cap", "room", "waiting")}})
    save_state(home, st)
    return out


def _core():
    from hermes_cli import kanban_placement as placement
    from hermes_cli import kanban_worker_node as node

    cfg = placement.config()
    return node.registry, (lambda card: placement.portable_reason(card, cfg))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--home")
    ap.add_argument("--streak", type=int, default=STREAK)
    a = ap.parse_args(argv)
    home = hermes_home(a.home)
    registry_fn, reason_fn = _core()
    out = run(home, registry_fn=registry_fn, reason_fn=reason_fn, streak_needed=a.streak, dry_run=a.dry_run)
    if a.json or a.dry_run:
        print(json.dumps(out, indent=1))
    elif out["filed"]:
        print(f"{AUTHOR}: filed {out['filed']} (hub {out['sample']['hub_runs']}/{out['sample']['hub_cap']}, "
              f"{out['sample']['waiting']} waiting, room {out['sample']['room']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
