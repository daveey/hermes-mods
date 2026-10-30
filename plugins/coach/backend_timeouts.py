#!/usr/bin/env python3
"""Watch desktop.log for Desktop->backend timeouts and file ONE Kanban card per host per day above a threshold.

The Desktop app logs every failed backend call as `<local stamp> [hermes] [hermes:api GET /api/...] Error: Timed out
connecting to Hermes backend after Nms`. A CPU-starved `hermes serve` produced ~1,200 of those an hour on 2026-10-27
and nothing read them until an archive preflight failed in front of David (self-heal desktop-64ac180d9e). This counts
them over the last --window minutes (live desktop.log plus rotated desktop.log.1..3), grouped by request path.
Raw count stays unchanged; only awake_count triggers incidents. On macOS, positively observed pmset sleep/darkwake
intervals classify sleep_count (including timers delivered in the full-wake second), with no post-wake grace.
Failed/unsupported power probes suppress nothing.

    backend_timeouts.py [--dry-run] [--json] [--hours] [--threshold N] [--window MIN] [--home PATH] [--host NAME]

Above the threshold (default 30 in 30 min; config.yaml `coach.backend_timeouts.threshold` / `.window_minutes`, env
COACH_BACKEND_TIMEOUTS_THRESHOLD, or --threshold) it runs `hermes kanban create --idempotency-key
backend-timeouts:<host>:<date>`; a later firing the same day comments on that card (at most once an hour).
--dry-run prints the report and files nothing. --hours prints a per-hour histogram of every timeout still in the
logs (for checking past bursts). No Inbox items. Exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

NEEDLE = "Timed out connecting to Hermes backend"
STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d{3} ")
API_RE = re.compile(r"\[hermes:api (?:[A-Z]+ )?(\S+?)\]")
PATH_RE = re.compile(r"(/api/[^\s\]\)'\"?,:]+)")
CONN_RE = re.compile(r"\bconn=([\w.-]+)")
TAG_RE = re.compile(r"\[([a-z][\w:.-]*)\]")
ID_SEG_RE = re.compile(r"^(?=.*\d)[\w.-]{8,}$|^\d+$|^t_[0-9a-f]+$")
ROTATED = 3
DEFAULT_THRESHOLD = 30
DEFAULT_WINDOW_MIN = 30
COMMENT_EVERY_S = 3600
TOP_PATHS = 8


def hermes_home(home: Optional[str] = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def log_files(home: Path) -> list:
    """Newest first: desktop.log, desktop.log.1, ... (the Desktop's own rotation, log-rotation.ts)."""
    base = home / "logs" / "desktop.log"
    return [p for p in [base] + [Path(f"{base}.{i}") for i in range(1, ROTATED + 1)] if p.is_file()]


def normalize_path(path: str) -> str:
    """`/api/sessions/20261017_000830_2f28de?x=1` -> `/api/sessions/<id>`: one bucket per route, not per id."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    return "/".join("<id>" if seg and ID_SEG_RE.match(seg) else seg for seg in path.split("/"))


def route_of(line: str) -> str:
    m = API_RE.search(line)
    if m:
        return normalize_path(m.group(1))
    m = PATH_RE.search(line)
    if m:
        return normalize_path(m.group(1))
    body = line.split("[hermes] ", 1)[-1]
    for tag in TAG_RE.findall(body):
        if not tag.startswith("renderer console"):
            return f"[{tag}]"
    return "(no path)"


def iter_timeouts(files: list):
    """Yield (datetime, route) per log entry naming the timeout. Stack/continuation lines without a stamp count only
    if their entry's stamped line did not already (one entry = one timeout)."""
    for path in files:
        last_ts, counted = None, False
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                m = STAMP_RE.match(line)
                if m:
                    try:
                        last_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        last_ts = None
                    counted = False
                if NEEDLE not in line or last_ts is None or counted:
                    continue
                counted = True
                m = CONN_RE.search(line)
                yield last_ts, route_of(line), (m.group(1) if m else "?")


POWER_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ([+-]\d{4})\s+"
    r"(Sleep|DarkWake|Wake)\s+(Entering (?:Sleep|DarkWake) state|DarkWake (?:from|to FullWake)|Wake from)"
)


def power_intervals(text: str, now: datetime) -> list:
    """Only positive power evidence suppresses alerts; DarkWake is NOT a full wake.

    Both logs have second-resolution event attribution. Timers delivered in the
    full-wake second belong to the suspended interval; no later grace is used.
    """
    intervals = []
    asleep = None
    for line in text.splitlines():
        m = POWER_RE.match(line)
        if not m:
            continue
        ts = datetime.strptime(m[1] + ' ' + m[2], '%Y-%m-%d %H:%M:%S %z')
        ts = ts.astimezone().replace(tzinfo=None)
        if ts > now:
            continue
        if m[3] in ('Sleep', 'DarkWake'):
            if asleep is None:
                asleep = ts
        elif asleep is not None:
            intervals.append((asleep, ts))
            asleep = None
    if asleep is not None:
        intervals.append((asleep, now))
    return intervals


def probe_power(now: datetime) -> dict:
    if sys.platform != 'darwin':
        return {'status': 'unsupported', 'intervals': []}
    try:
        r = subprocess.run(['/usr/bin/pmset', '-g', 'log'], capture_output=True, text=True, timeout=30)
        if r.returncode:
            return {'status': 'failed', 'intervals': [], 'error': f'pmset exit {r.returncode}'}
        intervals = power_intervals(r.stdout, now)
        return {'status': 'ok', 'intervals': intervals}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {'status': 'failed', 'intervals': [], 'error': type(exc).__name__}


def wake_bucket(ts: datetime, wake_ends: list) -> str:
    """Informational only (suppresses nothing): how long after the latest observed wake an awake timeout landed.
    Stragglers minutes after a wake point at a stale tunnel/route to one remote backend, not at CPU starvation."""
    prior = [w for w in wake_ends if w <= ts]
    if not prior:
        return "no_wake_seen"
    gap = (ts - prior[-1]).total_seconds()
    return "<1m" if gap < 60 else "<10m" if gap < 600 else ">=10m"


def scan(home: Path, now: datetime, window_min: int, intervals=()) -> dict:
    start = now - timedelta(minutes=window_min)
    by_path: Counter = Counter()
    awake_by_path: Counter = Counter()
    awake_by_conn: Counter = Counter()
    awake_since_wake: Counter = Counter()
    wake_ends = sorted(hi for _, hi in intervals)
    sleep_count = 0
    first = last = None
    files = log_files(home)
    for ts, route, conn in iter_timeouts(files):
        if start <= ts <= now:
            by_path[route] += 1
            if any(lo <= ts <= hi for lo, hi in intervals):
                sleep_count += 1
            else:
                awake_by_path[route] += 1
                awake_by_conn[conn] += 1
                awake_since_wake[wake_bucket(ts, wake_ends)] += 1
            first = ts if first is None or ts < first else first
            last = ts if last is None or ts > last else last
    return {"window_start": start.isoformat(sep=" "), "window_end": now.isoformat(sep=" "),
            "files": [str(p) for p in files], "count": sum(by_path.values()),
            "by_path": dict(by_path.most_common()),
            "sleep_count": sleep_count, "awake_count": sum(awake_by_path.values()),
            "awake_by_path": dict(awake_by_path.most_common()),
            "awake_by_conn": dict(awake_by_conn.most_common()),
            "awake_since_wake": dict(awake_since_wake.most_common()),
            "first": first.isoformat(sep=" ") if first else None, "last": last.isoformat(sep=" ") if last else None}


def hours(home: Path) -> dict:
    c: Counter = Counter(ts.strftime("%Y-%m-%d %H") for ts, _, _c in iter_timeouts(log_files(home)))
    return dict(sorted(c.items()))


def probe_host() -> dict:
    """Load average and the Hermes backend processes' CPU (`ps`), for the card's evidence."""
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except OSError:
        load = []
    rows = []
    try:
        r = subprocess.run(["ps", "-axo", "pid=,pcpu=,etime=,command="], capture_output=True, text=True, timeout=10)
        for row in r.stdout.splitlines():
            low = row.lower()
            if "hermes" in low and any(k in low for k in (" serve", "dashboard", "backend", "gateway")):
                rows.append(row.strip()[:200])
    except (OSError, subprocess.SubprocessError):
        pass
    rows.sort(key=lambda s: -_cpu(s))
    return {"loadavg": load, "backend_ps": rows[:6]}


def _cpu(row: str) -> float:
    try:
        return float(row.split()[1])
    except (IndexError, ValueError):
        return 0.0


def config_value(name: str, default: int) -> int:
    try:
        from hermes_cli.config import load_config  # type: ignore
        v = ((load_config().get("coach") or {}).get("backend_timeouts") or {}).get(name)
        return int(v) if v is not None else default
    except Exception:
        return default


def card_key(host: str, now: datetime) -> str:
    return f"backend-timeouts:{host}:{now.strftime('%Y-%m-%d')}"


def _paths_md(rep: dict) -> str:
    items = list(rep["by_path"].items())[:TOP_PATHS]
    return "\n".join(f"- `{p}`: {n}" for p, n in items) or "- (none)"


def _host_md(probe: dict) -> str:
    load = ", ".join(str(x) for x in probe.get("loadavg") or []) or "?"
    ps = "\n".join(f"    {r}" for r in probe.get("backend_ps") or []) or "    (no hermes backend process found)"
    return f"- load average (1/5/15 min): {load}\n- backend processes (pid %cpu elapsed command):\n{ps}"


def card_title(host: str, rep: dict, window_min: int) -> str:
    return f"Desktop backend timeouts on {host}: {rep['count']} in {window_min} min"


def card_body(host: str, rep: dict, probe: dict, threshold: int, window_min: int) -> str:
    return f"""Detected by hermes-mods plugins/coach/backend_timeouts.py (cron, every 10 min) on {host}.

The Desktop app on {host} logged {rep['count']} "{NEEDLE}" lines in the last {window_min} min
({rep['first']} .. {rep['last']}, local time; threshold {threshold}). Users see this as chats that won't load,
archive asking "task status unavailable", stale boards.
Of these, {rep.get("awake_count", rep["count"])} occurred while awake and
{rep.get("sleep_count", 0)} during positively observed sleep/darkwake intervals.
The threshold applies only to awake timeouts; raw count remains unchanged.
Awake timeouts by backend connection: {rep.get("awake_by_conn") or "(none)"}; by time since last observed wake:
{rep.get("awake_since_wake") or "(none)"}.

## Top request paths
{_paths_md(rep)}

## Host
{_host_md(probe)}

## Do this
1. Find what makes the backend slow (it is usually CPU-starved or a request is doing heavy work per call): time the
   top paths above against the local backend, `ps`/`sample` the busiest `hermes serve`, and check recent core
   commits for per-request work (e.g. 2026-10-27: a per-request kanban integrity_check, fixed in f2d0e23785).
2. Fix the cause (not just a restart). Use the five-whys skill for the chain.

## Acceptance
- After the fix, a fresh 30-minute window shows zero timeouts:
  `python3 ~/code/hermes-mods/plugins/coach/backend_timeouts.py --dry-run` prints `"count": 0` with `window_start`
  later than the fix. A restart alone, or a count taken right after a restart, does not satisfy this.
- Paste that output in a comment before completing.
"""


def _hermes_bin() -> str:
    """cron's PATH may lack the hermes launcher; fall back to its usual install paths."""
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


def state_path(home: Path) -> Path:
    return home / "coach" / "backend_timeouts" / "state.json"


def load_state(home: Path) -> dict:
    try:
        return json.loads(state_path(home).read_text())
    except (OSError, ValueError):
        return {}


def save_state(home: Path, st: dict) -> None:
    p = state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = dict(sorted(st.items())[-14:])  # two weeks of daily keys
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep, indent=1))
    os.replace(tmp, p)


def file_card(home: Path, host: str, now: datetime, rep: dict, probe: dict, threshold: int, window_min: int,
              runner: Callable) -> dict:
    """Create (or find, via the idempotency key) today's card; a repeat firing comments at most once an hour."""
    key = card_key(host, now)
    st = load_state(home)
    ent = st.get(key) or {}
    now_s = now.timestamp()
    tid = ent.get("task_id")
    existed = bool(tid)
    if not tid:
        r = _run(runner, ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", key,
                          "--created-by", "backend-timeouts-watch", "--body-file", "-", "--json",
                          card_title(host, rep, window_min)], card_body(host, rep, probe, threshold, window_min))
        if r.returncode != 0:
            return {"key": key, "action": "create_failed", "rc": r.returncode,
                    "out": ((r.stdout or "") + (r.stderr or ""))[-300:]}
        try:
            d = json.loads(r.stdout)
            tid = d.get("id")
            created = float(d.get("created_at") or now_s)
        except (ValueError, TypeError, AttributeError):
            return {"key": key, "action": "create_unparsed", "out": (r.stdout or "")[-300:]}
        existed = created < now_s - 120  # the key returned a card made by an earlier run (state file lost)
        ent = {"task_id": tid, "created": now_s if not existed else created, "last_comment": 0}
        if not existed:
            ent["last_comment"] = now_s
            st[key] = ent
            save_state(home, st)
            return {"key": key, "action": "created", "task_id": tid}
    if now_s - float(ent.get("last_comment") or 0) < COMMENT_EVERY_S:
        st[key] = ent
        save_state(home, st)
        return {"key": key, "action": "throttled", "task_id": tid}
    text = (f"Still firing at {now.strftime('%H:%M')} on {host}: {rep['count']} timeouts in {window_min} min "
            f"(threshold {threshold}).\nTop paths:\n{_paths_md(rep)}\n{_host_md(probe)}")
    r = _run(runner, ["hermes", "kanban", "comment", tid, "--author", "backend-timeouts-watch", text])
    ent["last_comment"] = now_s
    st[key] = ent
    save_state(home, st)
    return {"key": key, "action": "commented" if r.returncode == 0 else "comment_failed", "task_id": tid,
            "existed": existed}


def main(argv=None, runner: Optional[Callable] = None, now: Optional[datetime] = None,
         probe: Optional[Callable] = None, power_probe: Optional[Callable] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the report, file nothing")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--hours", action="store_true", help="per-hour histogram of every timeout still in the logs")
    ap.add_argument("--threshold", type=int, help=f"timeouts per window that file a card (default {DEFAULT_THRESHOLD})")
    ap.add_argument("--window", type=int, help=f"window in minutes (default {DEFAULT_WINDOW_MIN})")
    ap.add_argument("--home", help="HERMES_HOME (default $HERMES_HOME or ~/.hermes)")
    ap.add_argument("--host", help="host name for the card key (default: short hostname)")
    a = ap.parse_args(argv)
    home = hermes_home(a.home)
    host = a.host or socket.gethostname().split(".")[0]
    now = now or datetime.now()
    if a.hours:
        h = hours(home)
        if a.json:
            print(json.dumps({"host": host, "files": [str(p) for p in log_files(home)], "hours": h}))
        else:
            print("\n".join(f"{k}h  {v}" for k, v in h.items()) or "no timeouts in the logs")
        return 0
    env_t = os.environ.get("COACH_BACKEND_TIMEOUTS_THRESHOLD")
    threshold = a.threshold if a.threshold is not None else (
        int(env_t) if env_t and env_t.isdigit() else config_value("threshold", DEFAULT_THRESHOLD))
    window = a.window if a.window is not None else config_value("window_minutes", DEFAULT_WINDOW_MIN)
    power = (power_probe or probe_power)(now)
    intervals = power.get("intervals", [])
    rep = scan(home, now, window, intervals)
    power_report = {k: v for k, v in power.items() if k != "intervals"}
    power_report["intervals"] = [[lo.isoformat(sep=" "), hi.isoformat(sep=" ")]
                                 for lo, hi in intervals if hi >= now - timedelta(minutes=window)]
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host, "home": str(home),
           "dry_run": a.dry_run, "threshold": threshold, "window_min": window, **rep,
           "power": power_report,
           "over_threshold": rep["awake_count"] >= threshold, "cards": []}
    if res["over_threshold"]:
        res["host_probe"] = (probe or probe_host)()
        if not a.dry_run:
            res["cards"].append(file_card(home, host, now, rep, res["host_probe"], threshold, window,
                                          runner or subprocess.run))
    if a.json or a.dry_run:
        print(json.dumps(res))
    elif res["over_threshold"]:  # cron no-agent: stdout is delivered, so stay silent when healthy
        c = res["cards"][0] if res["cards"] else {}
        top = ", ".join(f"{p} {n}" for p, n in list(rep["by_path"].items())[:3])
        print(f"backend timeouts on {host}: {rep['count']} in {window} min (threshold {threshold}); top: {top}; "
              f"card {c.get('task_id', '?')} {c.get('action', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
