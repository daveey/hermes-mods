#!/usr/bin/env python3
"""Worker-share watch: spill audited portable cards when hub admission is constrained.

The hub's dispatcher offers a card to the worker pool only when creation marked it worker-first
(``placement_policy``); every other ready card waits for one of the hub's own slots (``max_card_runs``). In
kanban-12ee0e4b65 that left the hub at its cap in 95 of 121 ten-minute slots while worker-1 ran 3 cards a day, and
nothing noticed: the rollout check only tested fake probe cards. This watch samples the live board each run (hub
cron, every 10 min): the hub at cap, draining, or refusing admission; a pool worker with room and cards waiting. Three samples in
a row move at most three never-started, explicitly audited portable cards through core's move CLI. Pending
pins reserve capacity. Missing audits and host-bound reasons never move. If no move succeeds, one diagnostic
card per day is pinned to a pool worker with room, listing core's refusal reasons.

    worker_share_watch.py [--dry-run] [--json] [--home PATH] [--streak 3]

Needs the hermes-agent venv (imports ``hermes_cli``). --dry-run samples and prints, saves and files nothing.
Prints only when it moved, filed or failed; exit 0 unless the scan itself crashes.
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

try:
    from .placement_evidence import fence_evidence
except ImportError:  # run as a script, or loaded by path: take the sibling file
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_coach_placement_evidence", Path(__file__).with_name("placement_evidence.py"))
    _pe = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_pe)
    fence_evidence = _pe.fence_evidence

AUTHOR = "worker-share-watch"
STREAK = 3
FRESH_MS = 15 * 60 * 1000  # a machine's metrics older than this say nothing about its load
MAX_LISTED = 12
MAX_MOVES = 3


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
        rows = con.execute("SELECT t.*, (SELECT e.payload FROM task_events e"
                           " WHERE e.task_id=t.id AND e.kind='created' ORDER BY e.id LIMIT 1) AS creation_payload,"
                           " EXISTS(SELECT 1 FROM task_runs r WHERE r.task_id=t.id) OR"
                           " EXISTS(SELECT 1 FROM task_events e WHERE e.task_id=t.id AND e.kind='claimed') AS prior_run"
                           " FROM tasks t WHERE status = 'ready' AND placement_policy IS NULL"
                           " ORDER BY created_at").fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        if (r["runs_on"] or "").strip() not in ("", "[]", "null"):
            continue
        d = dict(r)
        try:
            audit = json.loads(d.pop("creation_payload") or "{}")
        except (ValueError, TypeError):
            audit = {}
        d["reason_source"] = "current replay (creation audit unavailable)"
        if (isinstance(audit, dict) and "portable_reason" in audit
                and (audit["portable_reason"] is None or isinstance(audit["portable_reason"], str))):
            d["creation_reason"] = audit["portable_reason"]
            d["reason_source"] = "created event"
        # Scratch paths are allocated on first dispatch, not requested at creation.
        # Never erase a custom path or a dir/worktree binding when replaying old cards.
        generated = db.parent / "kanban" / "workspaces" / d["id"]
        if ((d.get("workspace_kind") or "scratch") == "scratch"
                and d.get("workspace_path") == str(generated)):
            d["workspace_path"] = None
        for k in ("requires", "skills", "human_request"):
            try:
                d[k] = json.loads(d[k]) if d.get(k) else None
            except ValueError:
                d[k] = None
        out.append(d)
    return out


def card_reason(card: dict, reason_fn: Callable[[dict], Optional[str]]) -> str:
    value = card["creation_reason"] if "creation_reason" in card else reason_fn(card)
    return value or "portable (not re-enrolled)"


def hub_pressure(home: Path, now_ms: int) -> list[str]:
    """Read local admission evidence in the last ten-minute sampling window."""
    from datetime import datetime

    pressure = ['drain'] if (home / 'core-update' / 'drain-hold').exists() else []
    # Bounded tails, including the last rotation. Logging uses local wall time.
    for name in ('agent.log', 'agent.log.1'):
        try:
            with (home / 'logs' / name).open('rb') as stream:
                stream.seek(0, 2)
                start = max(0, stream.tell() - 2 * 1024 * 1024)
                stream.seek(start)
                if start:
                    stream.readline()
                lines = stream.read().decode('utf-8', errors='replace').splitlines()
        except OSError:
            continue
        for line in lines:
            if 'Hub admission refused' not in line:
                continue
            try:
                at = datetime.fromisoformat(line[:23].replace(',', '.')).timestamp() * 1000
            except (ValueError, OverflowError):
                continue
            if now_ms - 600000 <= at <= now_ms:
                return pressure + ['admission']
    return pressure


def sample(registry: dict, waiting: list, now_ms: int, reason_fn: Callable[[dict], Optional[str]],
           pressure: Optional[list[str]] = None) -> dict:
    """One observation: is hub admission constrained while a fresh pool worker has room?"""
    machines = registry.get("machines") or {}
    me = registry.get("me") or "hub"
    hub = machines.get(me) or {}
    hub_runs, hub_cap = _runs(hub), hub.get("max_card_runs")
    hub_full = (_fresh(hub, now_ms) and isinstance(hub_cap, int) and hub_cap > 0
                and hub_runs is not None and hub_runs >= hub_cap)
    pressure = (["cap"] if hub_full else []) + list(pressure or [])
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
            reasons[card_reason(card, reason_fn)] += 1
        except Exception as exc:  # one odd card never hides the rest
            reasons[f"check failed: {type(exc).__name__}"] += 1
    return {"at": now_ms, "hub": me, "hub_runs": hub_runs, "hub_cap": hub_cap, "hub_full": hub_full,
            "room": room, "waiting": len(waiting), "reasons": dict(reasons.most_common()),
            "hub_pressure": pressure, "starved": bool(pressure and room and waiting)}


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
                      f" ({c.get('reason_source', 'current replay')})"
                      for c in examples[:MAX_LISTED])
    room = ", ".join(f"{k} ({v} free)" for k, v in s["room"].items())
    evidence = fence_evidence(f"""The hub was constrained ({', '.join(s.get('hub_pressure', ['cap']))}); latest runs {s['hub_runs']}/{s['hub_cap']},
for {streak} samples in a row (10 min apart) while
{room} had room and {s['waiting']} ready cards waited unmarked: the dispatcher only offers worker-first cards
(`placement_policy`) to the pool, so these wait for a hub slot. Found by {AUTHOR} (hermes-mods
plugins/coach/worker_share_watch.py, kanban-12ee0e4b65).

## Placement evidence (created `portable_reason`; legacy rows replay current rules)
{reasons}

## Examples
{cards}""")
    return f"""Fix the portable repo producer from the carried evidence below; clone and push the repo as needed.
Live host-only verification belongs on a separate capability owner, not this portable implementation.

{evidence}

## Acceptance
1. For the top reason(s), find the producer (card title prefix / creator) and what the card really needs. A
   repo change landed by push does not need the hub: fix the producer (declared `requires`, brief text; quoted
   incident evidence goes inside `<!-- placement:evidence -->` ... `<!-- /placement:evidence -->`), or the
   placement rule in core if the marker is wrong, with a regression test.
2. A reason that is right (the card truly reads hub-only state) needs no change: say so per reason.
3. Every `created` event now carries `portable_reason`: cite the share of marked cards before and after.
"""


def _run(runner: Callable, argv: list, home: Optional[Path] = None):
    kw = {} if home is None else {'env': {**os.environ, 'HERMES_HOME': str(home),
                                        'HERMES_KANBAN_DB': str(home / 'kanban.db')}}
    return runner(argv, capture_output=True, text=True, timeout=120, **kw)


def file_card(s: dict, streak: int, examples: list, runner: Callable = subprocess.run,
              day: Optional[str] = None, home: Optional[Path] = None) -> Optional[str]:
    day = day or time.strftime("%Y-%m-%d", time.gmtime(s["at"] / 1000))
    top = next(iter(s["reasons"]), "?")
    argv = ["hermes", "kanban", "create", f"worker share: hub constrained, {s['waiting']} cards wait unmarked (top: {top})",
            "--body", card_body(s, streak, examples), "--assignee", "default", "--created-by", AUTHOR,
            "--idempotency-key", f"{AUTHOR}:{day}", "--requires", "terminal", "--requires", "file",
            "--requires", "git", "--runs-on", f"machine:{max(s['room'], key=s['room'].get)}", "--json"]
    r = _run(runner, argv, home)
    if r.returncode != 0:
        raise RuntimeError(f"kanban create failed ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    import re
    m = re.search(r'"id"\s*:\s*"(t_[0-9a-f]+)"', r.stdout or "") or re.search(r"\b(t_[0-9a-f]{6,})\b", r.stdout or "")
    return m.group(1) if m else None


def pending_pins(db: Path) -> collections.Counter:
    """Reserve queued pins too: registry metrics count running cards, not waiting moves."""
    con = _ro_connect(db)
    try:
        rows = con.execute("SELECT runs_on FROM tasks WHERE status='ready' AND runs_on IS NOT NULL").fetchall()
    finally:
        con.close()
    pins = collections.Counter()
    for (raw,) in rows:
        try:
            labels = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(labels, list):
            for label in labels:
                if isinstance(label, str) and label.startswith('machine:'):
                    pins[label[8:]] += 1
    return pins


def move_verified(db: Path, task_id: str, machine: str, after_id: int) -> bool:
    con = _ro_connect(db)
    try:
        row = con.execute("SELECT runs_on FROM tasks WHERE id=?", (task_id,)).fetchone()
        event = con.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='moved' AND id>?"
                            " ORDER BY id DESC LIMIT 1", (task_id, after_id)).fetchone()
        return (bool(row and event) and json.loads(row[0]) == [f'machine:{machine}']
                and json.loads(event[0]).get('runs_on') == [f'machine:{machine}'])
    except (ValueError, TypeError, AttributeError):
        return False
    finally:
        con.close()


def spill(home: Path, s: dict, reason_fn: Callable, runner: Callable, limit: int) -> tuple:
    """Only move explicitly audited, still-portable, never-started cards. Core owns the write/event."""
    moved, errors = [], []
    if not s['starved']:
        return moved, errors
    db = home / 'kanban.db'
    pending = pending_pins(db)
    room = {m: max(0, n - pending[m]) for m, n in s['room'].items()}
    attempts = 0
    for card in waiting_unmarked(db):
        if attempts >= max(0, limit) or not any(room.values()):
            break
        # Missing audits are not NULL audits. Prior runs may own host-local scratch files.
        if 'creation_reason' not in card or card['creation_reason'] is not None or card['prior_run']:
            continue
        try:
            if reason_fn(card) is not None:
                continue
        except Exception:
            continue
        machine = max(room, key=lambda m: room[m])
        attempts += 1
        con = _ro_connect(db)
        try:
            after_id = con.execute('SELECT COALESCE(MAX(id),0) FROM task_events').fetchone()[0]
        finally:
            con.close()
        argv = ['hermes', 'kanban', 'move', card['id'], '--runs-on', f'machine:{machine}',
                '--reason', f'{AUTHOR}: hub constrained; audited portable, never started; pool has room', '--json']
        try:
            result = runner(argv, capture_output=True, text=True, timeout=120,
                            env={**os.environ, 'HERMES_HOME': str(home), 'HERMES_KANBAN_DB': str(db)})
            if result.returncode != 0 or not move_verified(db, card['id'], machine, after_id):
                raise RuntimeError('move refused or readback did not confirm target and moved event')
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            errors.append({'task_id': card['id'], 'error': str(exc)})
            # An ambiguous CLI outcome may have reserved the slot. Stop, never blind retry.
            break
        room[machine] -= 1
        moved.append({'task_id': card['id'], 'machine': machine})
    return moved, errors


def run(home: Path, *, registry_fn: Callable[[], object], reason_fn: Callable[[dict], Optional[str]],
        now_ms: Optional[int] = None, streak_needed: int = STREAK, dry_run: bool = False,
        runner: Callable = subprocess.run, max_moves: int = MAX_MOVES) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    waiting = waiting_unmarked(home / "kanban.db")
    s = sample(dict(registry_fn() or {}), waiting, now_ms, reason_fn, hub_pressure(home, now_ms))
    st = load_state(home)
    streak = (int(st.get("streak") or 0) + 1) if s["starved"] else 0
    out = {"sample": s, "streak": streak, "filed": None, "moved": [], "move_errors": []}
    if dry_run:
        return out
    if streak >= streak_needed:
        out["moved"], out["move_errors"] = spill(home, s, reason_fn, runner, max_moves)
        waiting = waiting_unmarked(home / "kanban.db")
        s = sample(dict(registry_fn() or {}), waiting, now_ms, reason_fn, hub_pressure(home, now_ms))
    day = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
    pending = pending_pins(home / 'kanban.db')
    s['room'] = {m: n - pending[m] for m, n in s['room'].items() if n > pending[m]}
    if (s["starved"] and s['room'] and not out['moved'] and not out['move_errors']
            and streak >= streak_needed and st.get("filed_day") != day):
        examples = []
        for c in waiting:
            try:
                reason = card_reason(c, reason_fn)
            except Exception:
                reason = "check failed"
            examples.append({**c, "reason": reason})
        out["filed"] = file_card(s, streak, examples, runner=runner, day=day, home=home)
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
    ap.add_argument("--max-moves", type=int, default=MAX_MOVES)
    a = ap.parse_args(argv)
    home = hermes_home(a.home)
    registry_fn, reason_fn = _core()
    if a.dry_run:
        out = run(home, registry_fn=registry_fn, reason_fn=reason_fn, streak_needed=a.streak,
                  dry_run=True, max_moves=a.max_moves)
    else:
        import fcntl
        lock_path = state_path(home).with_suffix('.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            out = run(home, registry_fn=registry_fn, reason_fn=reason_fn, streak_needed=a.streak,
                      max_moves=a.max_moves)
    if a.json or a.dry_run:
        print(json.dumps(out, indent=1))
    elif out["moved"] or out["move_errors"]:
        print(json.dumps(out))
    elif out["filed"]:
        print(f"{AUTHOR}: filed {out['filed']} (hub {out['sample']['hub_runs']}/{out['sample']['hub_cap']}, "
              f"{out['sample']['waiting']} waiting, room {out['sample']['room']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
