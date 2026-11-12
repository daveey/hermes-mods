#!/usr/bin/env python3
"""Watch the end-of-turn reply-check latency and file ONE Kanban card per host per day when its p90 stays over 20 s.

A registered ``pre_verify`` hook (the coach's reply check) runs after the answer has streamed, so every second it
takes is a second the user watches a finished-looking answer spin (self-heal agent-7aa110cfd7: a confirm pass held a
Desktop turn 23 s and David asked "why are you stuck"). Core now shows "Checking the reply before finishing…" and logs
``pre_verify done session=<id> seconds=<n> continued=<bool>`` (agent/turn_stop_gates.py). This watcher reads those
lines from ``<HERMES_HOME>/logs/agent.log*`` (rotated every ~hour at 5 MB, so it keeps its own 24 h sample window in
state), and when the window spans a day with enough samples and its p90 exceeds the limit, it files a card on the coach
(never an Inbox post). A later hit the same day files nothing more.

    pre_verify_latency_watch.py [--dry-run] [--json] [--home PATH] [--host NAME] [--limit-s 20] [--min-samples 20]

--dry-run reads the logs and prints the stats (window = whatever the logs hold), files and saves nothing.
Exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    from .placement_evidence import fence_evidence, home_pin_note
except ImportError:  # run as a script, or loaded by path: take the sibling file
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_coach_placement_evidence", Path(__file__).with_name("placement_evidence.py"))
    _pe = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_pe)
    fence_evidence, home_pin_note = _pe.fence_evidence, _pe.home_pin_note

AUTHOR = "pre-verify-latency-watch"
WINDOW_S = 24 * 3600
SPAN_S = 23 * 3600  # "stays over the limit for a day": the window must cover (nearly) a whole day of samples
# Anchored at line start: timestamp, level, [session], emitting logger, then the message. Card bodies, comments and
# tool-call echoes that quote the phrase never match.
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ INFO (?:\[[^\]]*\] )?agent\.conversation_loop: "
                  r"pre_verify done session=(\S*) seconds=([0-9.]+) continued=(True|False)")


def hermes_home(home: Optional[str] = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def parse_line(line: str) -> Optional[dict]:
    m = LINE.match(line)
    if not m:
        return None
    try:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        return {"ts": ts, "session": m.group(2), "seconds": float(m.group(3)), "continued": m.group(4) == "True"}
    except ValueError:
        return None


def scan(home: Path, since: float) -> list:
    """`pre_verify done` samples newer than ``since`` from agent.log and its rotations, oldest first."""
    out = []
    logs = home / "logs"
    for p in sorted(logs.glob("agent.log*")) if logs.is_dir() else []:
        if not re.fullmatch(r"agent\.log(\.\d+)?", p.name):
            continue
        try:
            with p.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "pre_verify done " not in line:
                        continue
                    s = parse_line(line)
                    if s and s["ts"] > since:
                        out.append(s)
        except OSError:
            continue
    out.sort(key=lambda s: s["ts"])
    return out


def p90(values: list) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return round(v[max(0, math.ceil(0.9 * len(v)) - 1)], 2)


def state_path(home: Path) -> Path:
    return home / "coach" / "pre_verify_latency" / "state.json"


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


def merge(st: dict, new: list, now: float) -> list:
    """Window samples [[ts, seconds, session], ...] within 24 h, deduped by (ts, session, seconds)."""
    seen = set()
    rows = []
    for r in list(st.get("samples") or []) + [[s["ts"], s["seconds"], s["session"]] for s in new]:
        try:
            key = (float(r[0]), str(r[2]), float(r[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if key in seen or key[0] <= now - WINDOW_S:
            continue
        seen.add(key)
        rows.append([key[0], key[2], key[1]])
    rows.sort(key=lambda r: r[0])
    return rows


def stats(rows: list, now: float, first_seen: Optional[float]) -> dict:
    secs = [r[1] for r in rows]
    return {"count": len(secs), "p90_s": p90(secs),
            "p50_s": round(sorted(secs)[len(secs) // 2], 2) if secs else None, "max_s": max(secs) if secs else None,
            "over_20s": sum(1 for s in secs if s > 20), "span_s": round(now - first_seen) if first_seen else 0}


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


def card_body(host: str, s: dict, limit_s: float, slow: list) -> str:
    worst = "\n".join(f"- {datetime.fromtimestamp(r[0]).strftime('%m-%d %H:%M:%S')} session `{r[2]}`: {r[1]:.1f}s"
                      for r in slow[:15])
    seen = fence_evidence(f"Seen on {host}: the end-of-turn reply check (`pre_verify` hooks) took p90 {s['p90_s']}s "
                          f"over the last day ({s['count']} turns, {s['over_20s']} over 20 s, max {s['max_s']}s; "
                          f"limit {limit_s}s).\n\n## Slowest turns\n{worst}")
    return f"""The end-of-turn reply check (`pre_verify` hooks) is slow. That time is spent after the answer
has streamed: the user watches "Checking the reply before finishing…" under a finished answer (agent-7aa110cfd7).

{seen}

## Do this
1. Match the slow sessions/times to `<HERMES_HOME>/coach/log.jsonl` `report_only_scored` / `reply_checked` /
   `reply_nudge` records: `elapsed_s` says when each judge finished; `confirm_attempted` marks the second LLM pass.
2. Find which judge or call dominates (screen vs confirm, aux model latency, timeouts) and cut it at the source in
   `~/code/hermes-mods/plugins/coach` (five-whys skill for the chain), with a regression test.

## Acceptance
- The coach's tests pass; `python3 ~/code/hermes-mods/plugins/coach/pre_verify_latency_watch.py --dry-run` shows p90
  under {limit_s}s for turns started after the fix. Paste it in a comment before completing.
"""


def file_card(home: Path, host: str, now: datetime, s: dict, limit_s: float, slow: list, runner: Callable) -> dict:
    key = f"pre-verify-latency:{host}:{now.strftime('%Y-%m-%d')}"
    st = load_state(home)
    tid = (st.get("cards") or {}).get(key)
    if tid:
        return {"key": key, "action": "already_filed", "task_id": tid}
    title = f"coach reply check p90 {s['p90_s']}s over a day (limit {limit_s:g}s)"
    r = _run(runner, ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", key,
                      "--created-by", AUTHOR, "--body-file", "-", "--json", title], card_body(host, s, limit_s, slow))
    if r.returncode != 0:
        return {"key": key, "action": "create_failed", "out": ((r.stdout or "") + (r.stderr or ""))[-300:]}
    try:
        tid = json.loads(r.stdout).get("id")
    except (ValueError, TypeError, AttributeError):
        return {"key": key, "action": "create_unparsed", "out": (r.stdout or "")[-300:]}
    st.setdefault("cards", {})[key] = tid
    save_state(home, st)
    return {"key": key, "action": "created", "task_id": tid}


def main(argv=None, runner: Optional[Callable] = None, now: Optional[float] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--home")
    ap.add_argument("--host", default=socket.gethostname().split(".")[0])
    ap.add_argument("--limit-s", type=float, default=20.0)
    ap.add_argument("--min-samples", type=int, default=20)
    a = ap.parse_args(argv)
    home = hermes_home(a.home)
    now = now or time.time()
    st = load_state(home)
    if a.dry_run:
        rows = merge({}, scan(home, now - WINDOW_S), now)
        s = stats(rows, now, rows[0][0] if rows else None)
        print(json.dumps({"host": a.host, "dry_run": True, **s, "limit_s": a.limit_s}, indent=1))
        return 0
    rows = merge(st, scan(home, float(st.get("last_ts") or 0) - 1), now)  # -1: same-second lines; merge dedupes
    first_seen = float(st.get("first_seen") or now)
    s = stats(rows, now, first_seen)
    rep = {"host": a.host, **s, "limit_s": a.limit_s}
    due = (s["count"] >= a.min_samples and s["span_s"] >= SPAN_S and (s["p90_s"] or 0) > a.limit_s)
    st.update({"samples": rows, "first_seen": first_seen, "last_ts": rows[-1][0] if rows else st.get("last_ts", 0),
               "last_run": now, "last_stats": s})
    save_state(home, st)
    if due:
        slow = sorted(rows, key=lambda r: -r[1])
        rep["card"] = file_card(home, a.host, datetime.fromtimestamp(now), s, a.limit_s, slow, runner or subprocess.run)
    if a.json:
        print(json.dumps(rep, indent=1))
    elif due and rep["card"]["action"] != "already_filed":
        print(f"pre-verify-latency-watch: p90 {s['p90_s']}s over {s['count']} turns -> {rep['card']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
