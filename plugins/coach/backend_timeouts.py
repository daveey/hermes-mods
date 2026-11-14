#!/usr/bin/env python3
"""Watch desktop.log for Desktop->backend timeouts and file a Kanban card per incident above a threshold.

The Desktop app logs every failed backend call as `<local stamp> [hermes] [hermes:api GET /api/...] Error: Timed out
connecting to Hermes backend after Nms`. A CPU-starved `hermes serve` produced ~1,200 of those an hour on 2026-10-27
and nothing read them until an archive preflight failed in front of David (self-heal desktop-64ac180d9e). This counts
them over the last --window minutes (live desktop.log plus rotated desktop.log.1..3), grouped by request path.
Raw count stays unchanged; only awake_count triggers incidents. On macOS, positively observed pmset sleep/darkwake
intervals classify sleep_count (including timers delivered in the full-wake second), with no post-wake grace.
A timeout that lands after a wake still counts as sleep when its own `after Nms` (plus any `, Ns ago` of a quoted
probe failure) proves the request started before that wake: wall time >= the monotonic timer, so the request's
latest possible start is stamp + 1 s - N, and a sleep interval ending after it means the request lived through sleep
(e.g. 2026-11-01 18:36:09: 40 60s-timeouts 30 s after a lid wake, all issued while the lid was shut).
Failed/unsupported power probes suppress nothing.
A down-gate `... is down (Timed out connecting ..., Ns ago); ... Failing fast instead of waiting for a timeout.` line
quotes the conn's last error and no request waited: it stays in the raw and awake counts and is reported under
`fail_fast`, but the threshold is judged on awake_count minus those lines.

    backend_timeouts.py [--dry-run] [--json] [--hours] [--threshold N] [--window MIN] [--home PATH] [--host NAME]

Above the threshold (default 30 in 30 min; config.yaml `coach.backend_timeouts.threshold` / `.window_minutes`, env
COACH_BACKEND_TIMEOUTS_THRESHOLD, or --threshold) it runs `hermes kanban create --idempotency-key
backend-timeouts:<host>:<date>`; later firings comment only while that card remains open (at most once an hour).
Closed or unreadable cards start a recurrence with a :2, :3, ... key suffix.
Each awake path with >= 20% of the awake count (or >= the threshold) also needs an owner: the incident card while it
is active and names the path, or, while it waits in todo/scheduled, an open parent that names it. A path with neither
gets its own card, key `backend-timeouts:<host>:<path>` (commented hourly while open; :2, ... once closed), so a
catch-all parked behind a fleet-only parent no longer hides an unowned hub-sync/status burst (agent-23f6a4d787).
Before that, each connection (`conn=`) is checked for link saturation: its timeouts spread over >= 3 paths that time
out together in shared 2-second buckets (>= 2 such buckets, >= half of the conn's timeouts in them). One saturated
tunnel makes every path on it time out at once, so such a conn gets ONE card, key `backend-timeouts:<host>:conn:<conn>`,
naming the top paths as candidates and asking for per-path byte/request attribution. Its burst timeouts no longer make
paths significant; a path keeps its own card only when its timeouts outside those bursts are significant by themselves.
Saturation is judged only on timeouts the link's own traffic can explain. Two kinds are set aside first:
- client_stall: in a 5-minute bucket, >= 2 distinct remote conns (not `local`/`primary`) each show a stall signal
  (a `[slow-link] ... is slow` line, mapped to its conn by port, a `[remote-timing] conn:<c>::... dispatch probe slow`
  line, or a shared burst of their own timeouts) within 60 s of each other. Independent links stalling together point
  at the Desktop host's uplink, not at one link's traffic (2026-11-13 09:28-09:46 PST: hub direct + mini via DERP).
  When such timeouts are most of an over-threshold window, ONE card per host per incident, key
  `backend-timeouts:<host>:uplink-stall`, asks for the host's network/Tailscale path, not per-path byte attribution.
- reconnect_cascade: timeouts on a conn within 30 s after `[ssh] teardown conn:<c>` or `[slow-link] ... treating it
  as down` for it: in-flight requests and the new tunnel's backlog failing together, not traffic.
- backend_down: timeouts on a conn while the Desktop's own down gate held it down, from
  `[remote-down] conn:<c>::... marked down (<reason>)` to `... answered again; gate open`. While the gate is closed
  every request fails fast with a line that quotes the first timeout ("is down (Timed out ..., 27s ago)"), so one
  outage logs the needle once per poll on every path at once: that is the far-end backend being away (a restart, a
  hang), not traffic filling the link (2026-11-13 12:31 PST: a core update restarted the mini's backend; 61 s down,
  19 quoted timeouts across 7 paths, filed as "link saturation"). The episodes are reported with their reason.
- backend_stall: a hung far-end backend. A `[ssh] teardown conn:<c>` followed by an `ssh dial +N ms` for that conn
  with N < 5000 and then, within 120 s of a fast dial, `always-on dashboard unavailable (pid P not answering on port
  X)` (attributed to the conn of the latest dial) or `[remote-down] conn:<c>::... trial exceeded`. A saturated link
  would make the re-dial slow too; a fast re-dial with the backend still silent on the fresh tunnel means the far-end
  process is stopped (2026-11-13 12:03-12:13 PST: a blocking py-spy held the hub serve for 17 min; 265 hub timeouts
  were filed as "link saturation"). Timeouts on the conn within +/-60 s of those lines are backend_stall. A conn whose
  shared-burst timeouts are more than half backend_stall is never a saturated conn, and when it is significant it gets
  ONE card, key `backend-timeouts:<host>:backend-stall:<conn>` ("hung backend on conn=X"), asking for the backend
  host's loop stalls and observer (ptrace/py-spy) commands, not byte attribution. Its leftover timeouts are the same
  hang, so they make no path card and no generic incident card.
- backend_cold_start: informational correlation of positively local timeouts (`conn=local`/`primary`, or an
  explicit `[fleet-roster] local:`/`primary:` entry) within 45 s after `[backend] Hermes gateway is ready`.
  READY proximity does not establish an API-mount cause or recovery. These failures remain in residual/path
  accounting. Raw connection counts are unchanged, including `?` for fleet-roster entries without `conn=`.
  A local conn has no tunnel to saturate; an unknown conn cannot identify a saturated link. Both remain eligible
  for generic incident and path cards. An unknown entry is never made local merely by proximity to READY.
--dry-run prints the report and files nothing. --hours prints a per-hour histogram of every timeout still in the
logs (for checking past bursts). No Inbox items. Exit 2 with count:null when
clock/coverage evidence is unresolved; this is not a healthy raw-zero receipt.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    from .placement_evidence import home_pin_argv, home_route, HomePinUnresolved, fence_evidence, home_pin_note
except ImportError:  # run as a script, or loaded by path: take the sibling file
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_coach_placement_evidence", Path(__file__).with_name("placement_evidence.py"))
    _pe = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_pe)
    home_pin_argv, HomePinUnresolved, fence_evidence, home_pin_note = _pe.home_pin_argv, _pe.HomePinUnresolved, _pe.fence_evidence, _pe.home_pin_note
    home_route = _pe.home_route

NEEDLE = "Timed out connecting to Hermes backend"
# The Desktop's down gate answers a request at once while a conn is treated as down: `... is down (<last error>, Ns
# ago); ... Failing fast instead of waiting for a timeout.` It quotes the conn's last error, so it carries NEEDLE when
# that error was a timeout, yet no request waited. Such a line stays in the raw and awake counts (and in
# backend_down), but it does not count toward the incident threshold: it is an echo of one earlier timeout
# (2026-11-13 20:31Z: 22 echoes of 2 gate-trial timeouts took a 9-timeout window to 31, over the threshold of 30).
FAIL_FAST = "Failing fast instead of waiting for a timeout"
STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d{3} (?:([+-]\d{2}:\d{2}) )?")
API_RE = re.compile(r"\[hermes:api (?:[A-Z]+ )?(\S+?)\]")
PATH_RE = re.compile(r"(/api/[^\s\]\)'\"?,:]+)")
CONN_RE = re.compile(r"\bconn=([\w.-]+)")
AFTER_RE = re.compile(r"Timed out connecting to Hermes backend after (\d+)ms(?: \([^)]*\))?(?:, (\d+)s ago)?")
TAG_RE = re.compile(r"\[([a-z][\w:.-]*)\]")
ID_SEG_RE = re.compile(r"^(?=.*\d)[\w.-]{8,}$|^\d+$|^t_[0-9a-f]+$")
SLOW_LINK_RE = re.compile(r"\[slow-link\] https?://127\.0\.0\.1:(\d+)(?: is slow| timed out .*treating it as down)")
FORWARD_RE = re.compile(r"\[ssh\] forwarding 127\.0\.0\.1:(\d+) ->")
CANCEL_RE = re.compile(r"\[ssh\] cancelled forward 127\.0\.0\.1:(\d+)")
TEARDOWN_RE = re.compile(r"\[ssh\] teardown conn:([\w.-]+?)(?:::|[\s(]|$)")
PROBE_SLOW_RE = re.compile(r"\[remote-timing\] conn:([\w.-]+?)::\S* dispatch probe slow")
GATE_RE = re.compile(r"\[remote-down\] conn:([\w.-]+?)::\S* (marked down(?: \((.*)\);)?|trial exceeded|answered again)")
GATE_KINDS = {"marked down": "gate_down", "trial exceeded": "gate_trial", "answered again": "gate_up"}
DIAL_RE = re.compile(r"\[remote-timing\] conn:([\w.-]+?)::\S* ssh dial \+(\d+) ms")
DASH_DOWN_RE = re.compile(r"\[ssh-lifecycle\] always-on dashboard unavailable \(pid (\d+) not answering on port (\d+)\)")
# A `marked down (<reason>)` naming a closed or refused connection: the far end went away (a restart, a quitting
# Desktop), it did not hang. Such an episode is backend_down, never backend_stall (2026-11-13 12:31 PST: a core update
# restarted the mini's serve, `read ECONNRESET`, then the same fast re-dial + `not answering` lines as a hang).
GONE_RE = re.compile(r"ECONNRESET|ECONNREFUSED|EPIPE|socket hang up|connect failed", re.I)
LINE_CONN_RE = re.compile(r"\bconn[:=]([\w.-]+?)(?:::|[\s)\],]|$)")
ROTATED = 3
DEFAULT_THRESHOLD = 30
DEFAULT_WINDOW_MIN = 30
COMMENT_EVERY_S = 3600
TOP_PATHS = 8
# A path with this share of the awake count (or >= the threshold) needs its own owner card (agent-23f6a4d787).
PATH_SHARE = 0.2
PATHS_KEY = "paths"
# Link/connection saturation: a conn whose timeouts land on several paths in the same seconds is one incident.
BURST_BUCKET_S = 2      # "the same seconds": timeouts bucketed by this many seconds
BURST_MIN_PATHS = 2     # a bucket where >= this many paths of one conn time out together is a shared burst
SAT_MIN_PATHS = 3       # the bursts span at least this many distinct paths
SAT_MIN_BURSTS = 2      # ... and recur: at least this many burst buckets
SAT_SHARE = 0.5         # ... and hold at least this share of the conn's awake timeouts
# Client-side stall: several independent remote links stalling together = the Desktop host's own uplink.
STALL_BUCKET_S = 300    # judged per 5-minute bucket
STALL_PAIR_S = 60       # two remote conns' stall signals start within this many seconds of each other
LOCAL_CONNS = {"local", "primary"}
# READY proximity is correlation only; it does not prove API mount state.
READY_MARK = "[backend] Hermes gateway is ready"
COLD_START_S = 45       # informational startup-correlation window; never suppresses failures
CASCADE_S = 30          # timeouts within this long after a teardown / treated-as-down are a reconnect cascade
DOWN_EDGE_S = 2         # a timeout this close to a down episode's edges belongs to it (the line that closed the gate)
DOWN_OPEN_S = 60        # an episode with no `answered again` ends this long after its last gate line
PROBE_PORT_S = 2        # a `[slow-link]` line and its conn's `dispatch probe slow` line are logged together
# Hung far-end backend: the conn is torn down, the SSH re-dial is fast (the link is fine), and then the backend still
# does not answer on the fresh tunnel. A saturated link would make the re-dial slow too.
STALL_DIAL_FAST_MS = 5000  # a re-dial faster than this says the link itself carries traffic
STALL_CONFIRM_S = 120      # a `not answering` / `trial exceeded` line this soon after a fast dial confirms the hang
BACKEND_STALL_S = 60       # timeouts within +/- this of an episode's lines are backend_stall
BACKEND_STALL_SHARE = 0.5  # a conn whose shared-burst timeouts are more than this share backend_stall is no saturation
CLOSED = {"done", "archived"}
ACTIVE = {"triage", "ready", "running", "review", "blocked"}  # works the card's own text (blocked: David acts)


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


def link_event(line: str, ts: datetime) -> Optional[tuple]:
    """(ts, kind, conn, port) for a Desktop link-state line, else None. kind: slow | down | probe_slow | teardown |
    forward | cancel | gate_down | gate_trial | gate_up | dial | dash_down | ready. `conn` is None when the line names only a
    local port. A gate_down carries the gate's reason in the port slot, a dial its milliseconds (int), a dash_down
    the always-on dashboard's `(pid, port)`."""
    if READY_MARK in line:  # the Desktop's own (local) backend answered `gateway ensure`
        return (ts, "ready", None, None)
    if "[remote-down]" in line:
        m = GATE_RE.search(line)
        if m:
            return (ts, GATE_KINDS[m.group(2).split(" (", 1)[0]], m.group(1), m.group(3))
        return None
    if "always-on dashboard unavailable (" in line:  # an `[ssh-lifecycle]` line
        m = DASH_DOWN_RE.search(line)
        return (ts, "dash_down", None, (m.group(1), m.group(2))) if m else None
    if " ssh dial +" in line:
        m = DIAL_RE.search(line)
        return (ts, "dial", m.group(1), int(m.group(2))) if m else None
    if "[slow-link]" in line:
        m = SLOW_LINK_RE.search(line)
        if m:
            return (ts, "down" if "treating it as down" in line else "slow", None, m.group(1))
    elif "[ssh] " in line:
        m = TEARDOWN_RE.search(line)
        if m:
            return (ts, "teardown", m.group(1), None)
        m = FORWARD_RE.search(line)
        if m:
            c = LINE_CONN_RE.search(line)
            return (ts, "forward", c.group(1) if c else None, m.group(1))
        m = CANCEL_RE.search(line)
        if m:
            return (ts, "cancel", None, m.group(1))
    elif "dispatch probe slow" in line:
        m = PROBE_SLOW_RE.search(line)
        if m:
            return (ts, "probe_slow", m.group(1), None)
    return None


def resolve_link_events(events: list, since: Optional[datetime] = None) -> tuple:
    """Map each `[slow-link]` port to its conn and return ([(ts, kind, conn)], unmapped_count).

    `[ssh] forwarding 127.0.0.1:<port>` maps a port to the conn it names; today's Desktop names none, so a port is
    mapped by the `dispatch probe slow` line its conn logs in the same instant (within PROBE_PORT_S), else by the
    most recent mapping learned for that port since it was last forwarded, else by `[ssh] cancelled forward <port>`
    right after a `teardown conn:<c>`. An unmappable slow-link line is counted, never guessed."""
    events = sorted(events, key=lambda e: e[0])
    probes = [(ts, conn) for ts, kind, conn, _ in events if kind == "probe_slow"]
    port_conn: dict = {}
    last_teardown = None
    out, unmapped = [], 0
    for ts, kind, conn, port in events:
        if kind in ("dial", "dash_down", "ready"):  # read by :func:`classify` and :func:`backend_stall_episodes`; they name no slow-link port
            continue
        if kind == "forward":
            port_conn.pop(port, None)  # a fresh forward: whatever this port meant before is stale
            if conn:
                port_conn[port] = conn
            continue
        if kind == "teardown":
            last_teardown = (ts, conn)
            out.append((ts, kind, conn))
            continue
        if kind == "cancel":
            if last_teardown and (ts - last_teardown[0]).total_seconds() <= CASCADE_S:
                port_conn.setdefault(port, last_teardown[1])
            continue
        if kind == "probe_slow" or kind.startswith("gate_"):
            out.append((ts, kind, conn))
            continue
        near = {c for t, c in probes if abs((t - ts).total_seconds()) <= PROBE_PORT_S}
        if len(near) == 1:
            port_conn[port] = next(iter(near))
        c = port_conn.get(port)
        if c is None:
            unmapped += 1 if since is None or ts >= since else 0
            continue
        out.append((ts, kind, c))
    return out, unmapped


def iter_timeouts(files: list, *, strict=False, evidence=None, events=None, fail_fast=None, local_evidence=None):
    """Yield (datetime, route, conn, lookback_s) per log entry naming the timeout. Stack/continuation lines without a stamp count only
    if their entry's stamped line did not already (one entry = one timeout). With `events` (a list), link-state
    lines (:func:`link_event`) are appended to it from the same pass. `local_evidence`, when supplied, receives
    one per-entry boolean before each yield; raw connection names remain unchanged."""
    for path in files:
        last_ts, counted = None, False
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            if strict:
                raise ValueError(f'log unreadable: {path}')
            continue
        with fh:
            for line in fh:
                m = STAMP_RE.match(line)
                if m:
                    try:
                        last_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                        if m.group(2):
                            last_ts = datetime.strptime(m.group(1) + " " + m.group(2),
                                                        "%Y-%m-%d %H:%M:%S %z").astimezone(timezone.utc)
                    except ValueError:
                        last_ts = None
                    if evidence is not None and last_ts is not None and last_ts.tzinfo:
                        evidence['coverage_start'] = min(evidence.get('coverage_start', last_ts), last_ts)
                    counted = False
                    if events is not None and last_ts is not None:
                        ev = link_event(line, last_ts)
                        if ev is not None:
                            events.append(ev)
                if strict and NEEDLE in line and last_ts is None:
                    raise ValueError('timeout has missing or invalid timestamp')
                if NEEDLE not in line or last_ts is None or counted:
                    continue
                counted = True
                m = CONN_RE.search(line)
                a = AFTER_RE.search(line)
                lookback = (int(a.group(1)) / 1000.0 + int(a.group(2) or 0)) if a else 0.0
                if fail_fast is not None and FAIL_FAST in line:  # still yielded; the caller sees the list grow
                    fail_fast.append(last_ts)
                if local_evidence is not None:
                    # Attribute this entry only, never every unknown conn near a local READY.
                    local_evidence.append(bool(m and m.group(1) in LOCAL_CONNS) or bool(
                        not m and re.search(r"\[fleet-roster\] (?:local|primary):", line)))
                yield last_ts, route_of(line), (m.group(1) if m else "?"), lookback


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
        ts = ts.astimezone(timezone.utc) if now.tzinfo else ts.astimezone().replace(tzinfo=None)
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


def spanned_sleep(ts: datetime, lookback_s: float, intervals) -> bool:
    """True when the timed-out request provably started before a sleep interval ended and that sleep began before
    the timeout. `ts` is floored to the second, so the latest possible start is ts + 1 s - lookback; a wake in the
    same second as that start is ambiguous and stays awake (positive evidence only)."""
    if lookback_s <= 0:
        return False
    latest_start = ts + timedelta(seconds=1) - timedelta(seconds=lookback_s)
    return any(lo <= ts and hi > latest_start for lo, hi in intervals)


def scan(home: Path, now: datetime, window_min: int, intervals=()) -> dict:
    # Production scans use UTC instants, never the caller's local wall clock.
    if now.tzinfo:
        now = now.astimezone(timezone.utc)
    start = now - timedelta(minutes=window_min)
    by_path: Counter = Counter()
    by_conn: Counter = Counter()
    awake_by_path: Counter = Counter()
    awake_by_conn: Counter = Counter()
    awake_since_wake: Counter = Counter()
    awake: list = []  # (ts, route, conn) of awake timeouts, classified after the pass
    link_events: list = []
    echoes: list = []  # grows by one just before a down-gate fail-fast entry is yielded
    seen_echoes = 0
    local_evidence, local_ids = [], set()
    ff_path: Counter = Counter()
    ff_conn: Counter = Counter()
    ff_awake = 0
    wake_ends = sorted(hi for _, hi in intervals)
    sleep_count = 0
    spanning_count = 0

    first = last = None
    files = log_files(home)
    if now.tzinfo and not files:
        raise ValueError('no Desktop logs found; bounded count unproven')
    evidence = {}
    for ts, route, conn, lookback in iter_timeouts(files, strict=bool(now.tzinfo), evidence=evidence,
                                                   events=link_events, fail_fast=echoes, local_evidence=local_evidence):
        is_echo, seen_echoes = len(echoes) > seen_echoes, len(echoes)
        if now.tzinfo and ts.tzinfo is None:
            # Any legal datetime UTC offset is strictly within +/-24 hours.
            # Exclude only when ALL possible instants are outside this window;
            # this is not a guess about the writer's historical timezone.
            wall = ts.replace(tzinfo=timezone.utc)
            if wall + timedelta(hours=24) < start or wall - timedelta(hours=24) > now:
                continue
            raise ValueError('legacy timeout lacks immutable UTC offset; bounded count unproven')
        if start <= ts <= now:
            by_path[route] += 1
            by_conn[conn] += 1
            if any(lo <= ts <= hi for lo, hi in intervals):
                sleep_count += 1
            elif spanned_sleep(ts, lookback, intervals):
                sleep_count += 1
                spanning_count += 1
            else:
                awake_by_path[route] += 1
                awake_by_conn[conn] += 1
                awake_since_wake[wake_bucket(ts, wake_ends)] += 1
                awake.append((ts, route, conn))
                if local_evidence[-1]:
                    local_ids.add(id(awake[-1]))
                ff_awake += is_echo
            if is_echo:
                ff_path[route] += 1
                ff_conn[conn] += 1
            first = ts if first is None or ts < first else first
            last = ts if last is None or ts > last else last
    coverage_start = evidence.get('coverage_start')
    cls = classify(awake, [e for e in link_events if (e[0].tzinfo is None) == (now.tzinfo is None)], start, now,
                   local_ids=local_ids)
    if now.tzinfo and not by_path and (coverage_start is None or coverage_start > start):
        raise ValueError('raw zero unproven: retained offset-stamped logs do not cover window start')
    return {"window_start": start.isoformat(sep=" "), "window_end": now.isoformat(sep=" "),
            "coverage_start": coverage_start.isoformat() if coverage_start else None,
            "files": [str(p) for p in files], "count": sum(by_path.values()),
            "by_path": dict(by_path.most_common()), "by_conn": dict(by_conn.most_common()),

            "sleep_count": sleep_count, "sleep_spanning_count": spanning_count, "awake_count": sum(awake_by_path.values()),
            "fail_fast": {"count": sum(ff_path.values()), "awake_count": ff_awake,
                          "by_conn": dict(ff_conn.most_common()), "by_path": dict(ff_path.most_common())},
            "awake_by_path": dict(awake_by_path.most_common()),
            "awake_by_conn": dict(awake_by_conn.most_common()),
            "awake_since_wake": dict(awake_since_wake.most_common()),
            "awake_conn_bursts": cls["awake_conn_bursts"],
            "client_stall": cls["client_stall"], "reconnect_cascade": cls["reconnect_cascade"],
            "backend_down": cls["backend_down"], "backend_stall": cls["backend_stall"],
            "backend_cold_start": cls["backend_cold_start"],
            "awake_explained_by_path": cls["explained_by_path"],
            "first": first.isoformat(sep=" ") if first else None, "last": last.isoformat(sep=" ") if last else None}


def _burst_buckets(timeouts: list) -> dict:
    """conn -> 2-second bucket -> Counter(route): which paths of a conn timed out together."""
    out: dict = {}
    for ts, route, conn in timeouts:
        out.setdefault(conn, {}).setdefault(int(ts.timestamp()) // BURST_BUCKET_S, Counter())[route] += 1
    return out


def down_episodes(resolved: list, reasons: dict, lo: datetime, now: datetime) -> list:
    """[{conn, start, end, open, reason}] from the Desktop's down-gate lines, oldest first. An episode runs from
    `marked down` (or, when that line is older than the retained log, the first gate line seen) to `answered again`.
    One that never answers ends DOWN_OPEN_S after its last gate line: a Desktop that quit while a conn was down
    logs no `answered again`, and its next launch starts with the gate open."""
    open_eps: dict = {}
    out = []

    def close(conn, end, still_open):
        ep = open_eps.pop(conn)
        out.append({"conn": conn, "start": ep["start"], "end": min(end, now), "open": still_open,
                    "reason": ep["reason"]})

    for ts, kind, conn in resolved:
        if not kind.startswith("gate_"):
            continue
        ep = open_eps.get(conn)
        if ep and (ts - ep["last"]).total_seconds() > DOWN_OPEN_S and kind != "gate_up":
            close(conn, ep["last"] + timedelta(seconds=DOWN_OPEN_S), True)
            ep = None
        if kind == "gate_up":
            if ep is None:  # went down before the retained lines
                open_eps[conn] = {"start": lo, "last": ts, "reason": None}
            close(conn, ts, False)
        elif ep is None:
            open_eps[conn] = {"start": ts, "last": ts, "reason": reasons.get((ts, conn))}
        else:
            ep["last"] = ts
    for conn in list(open_eps):
        close(conn, open_eps[conn]["last"] + timedelta(seconds=DOWN_OPEN_S), True)
    return sorted((e for e in out if e["end"] >= lo), key=lambda e: e["start"])


def backend_stall_episodes(link_events: list, resolved: list, lo: datetime, now: datetime) -> list:
    """[{conn, teardown, dials, confirms, events}] where a conn's far-end backend hung while its link stayed fine.

    An episode starts at `[ssh] teardown conn:<c>`; before the conn's next teardown, an `ssh dial +N ms` for it with
    N < STALL_DIAL_FAST_MS must be followed within STALL_CONFIRM_S by `always-on dashboard unavailable (pid P not
    answering on port X)` or `[remote-down] conn:<c>::... trial exceeded`. The dashboard line names no conn: it
    belongs to the conn of the latest dial at most STALL_CONFIRM_S before it. A slow re-dial is never a stall: a
    saturated link slows the SSH handshake as well (2026-11-13 12:04 PST: re-dial +724 ms, then the hub's always-on
    dashboard did not answer on the fresh tunnel for 20 s, while a blocking py-spy held the hub serve)."""
    dials = sorted((e[0], e[2], e[3]) for e in link_events if e[1] == "dial" and lo <= e[0] <= now)
    confirms: dict = {}  # conn -> [(ts, kind, detail)]
    for ts, _kind, _c, pidport in (e for e in link_events if e[1] == "dash_down" and lo <= e[0] <= now):
        prior = [d for d in dials if 0 <= (ts - d[0]).total_seconds() <= STALL_CONFIRM_S]
        if prior:
            confirms.setdefault(prior[-1][1], []).append((ts, "dashboard_not_answering", f"pid {pidport[0]} port {pidport[1]}"))
    for ts, kind, conn in resolved:
        if kind == "gate_trial" and lo <= ts <= now:
            confirms.setdefault(conn, []).append((ts, "trial_exceeded", None))
    teardowns: dict = {}
    for ts, kind, conn in resolved:
        if kind == "teardown" and lo <= ts <= now:
            teardowns.setdefault(conn, []).append(ts)
    gone: dict = {}  # conn -> [ts] of `marked down (<connection closed/refused>)`
    for ts, kind, conn, reason in link_events:
        if kind == "gate_down" and reason and GONE_RE.search(reason):
            gone.setdefault(conn, []).append(ts)
    out = []
    for conn, tds in teardowns.items():
        tds.sort()
        for i, t0 in enumerate(tds):
            t1 = tds[i + 1] if i + 1 < len(tds) else now
            if any(abs((g - t0).total_seconds()) <= STALL_CONFIRM_S and g < t1 for g in gone.get(conn, ())):
                continue  # the far end closed or refused the connection: gone (backend_down), not hung
            # Walk the conn's dials and confirmations after the teardown, each within STALL_CONFIRM_S of the last.
            seq = sorted([(ts, 0, ms) for ts, c, ms in dials if c == conn and t0 <= ts < t1]
                         + [(c[0], 1, c) for c in confirms.get(conn, ()) if t0 <= c[0] < t1], key=lambda x: x[:2])
            fast, conf, last = [], [], t0
            for ts, is_conf, data in seq:
                if (ts - last).total_seconds() > STALL_CONFIRM_S:
                    break
                if not is_conf:
                    if data >= STALL_DIAL_FAST_MS:
                        if not fast:
                            break  # the first re-dial is slow: the link may be the problem
                        continue
                    fast.append((ts, data))
                    last = ts
                elif fast:
                    conf.append(data)
                    last = ts
            if not fast or not conf:
                continue
            out.append({"conn": conn, "teardown": t0, "dials": fast, "confirms": conf,
                        "events": sorted([t0] + [d[0] for d in fast] + [c[0] for c in conf])})
    return sorted(out, key=lambda e: e["teardown"])


def classify(awake: list, link_events: list, start: datetime, now: datetime, *, local_ids=None) -> dict:
    """Split awake timeouts into backend_down, reconnect_cascade, client_stall and the rest; only the rest feeds
    awake_conn_bursts (and so :func:`saturated_conns`). A timeout can be in several: it is reported in each and set
    aside once."""
    lo = start - timedelta(seconds=STALL_BUCKET_S)
    # Ports are mapped over every retained line (a mapping learned before the window still holds until the port is
    # forwarded again); only the window's events count.
    resolved, unmapped = resolve_link_events([e for e in link_events if e[0] <= now], since=lo)
    downs: dict = {}    # conn -> [ts] of teardown / treated-as-down
    signals: dict = {}  # conn -> [epoch s] stall signals
    for ts, kind, conn in resolved:
        if ts < lo:
            continue
        if kind in ("teardown", "down"):
            downs.setdefault(conn, []).append(ts)
        if kind in ("slow", "probe_slow"):
            signals.setdefault(conn, []).append(ts.timestamp())

    episodes = down_episodes(resolved, {(e[0], e[2]): e[3] for e in link_events if e[1] == "gate_down"}, lo, now)
    edge = timedelta(seconds=DOWN_EDGE_S)

    def in_cascade(ts, conn):
        return any(0 <= (ts - d).total_seconds() <= CASCADE_S for d in downs.get(conn, ()))

    def in_down(ts, conn):
        return any(e["conn"] == conn and e["start"] - edge <= ts <= e["end"] + edge for e in episodes)

    cascade_ids = {id(t) for t in awake if in_cascade(t[0], t[2])}
    down_ids = {id(t) for t in awake if in_down(t[0], t[2])}
    stalls = backend_stall_episodes(link_events, resolved, lo, now)
    near = timedelta(seconds=BACKEND_STALL_S)

    def in_stall(ts, conn):
        return any(e["conn"] == conn and any(abs(ts - x) <= near for x in e["events"]) for e in stalls)

    readies = sorted(e[0] for e in link_events if e[1] == "ready" and lo <= e[0] <= now)

    # Informational only: these failures still need incident/path owners.
    cold_ids = {id(t) for t in awake
                if (t[2] in LOCAL_CONNS or id(t) in (local_ids or ()))
                and any(0 <= (t[0] - r).total_seconds() <= COLD_START_S for r in readies)}
    rest = [t for t in awake if id(t) not in cascade_ids and id(t) not in down_ids]
    for conn, buckets in _burst_buckets(rest).items():
        for b, c in buckets.items():
            if len(c) >= BURST_MIN_PATHS:
                signals.setdefault(conn, []).append(float(b * BURST_BUCKET_S))
    remote = sorted(c for c in signals if c not in LOCAL_CONNS and c != "?")
    stall_buckets: dict = {}  # 5-min bucket -> set(conns)
    for i, a in enumerate(remote):
        for b in remote[i + 1:]:
            for ta in signals[a]:
                for tb in signals[b]:
                    if abs(ta - tb) <= STALL_PAIR_S:
                        for t in (ta, tb):
                            stall_buckets.setdefault(int(t) // STALL_BUCKET_S, set()).update((a, b))
    stalled_ids = {id(t) for t in awake if int(t[0].timestamp()) // STALL_BUCKET_S in stall_buckets}
    # A hung far-end backend, judged after client_stall: independent links stalling together point at the Desktop
    # host's uplink, and a fast re-dial between two uplink stalls says nothing about the far end (09:42 PST).
    stall_ids = {id(t) for t in awake if id(t) not in stalled_ids and in_stall(t[0], t[2])}
    # A conn whose shared bursts (over all its awake timeouts) are mostly backend_stall is a hung backend, not
    # saturation: its leftover bursts belong to the same hang and are not judged as link traffic.
    stall_share: dict = {}
    for conn, buckets in _burst_buckets(awake).items():
        mine = [t for t in awake if t[2] == conn]
        # judged on its shared-burst timeouts; a conn with no bursts on all of its timeouts
        burst = [t for t in mine if len(buckets[int(t[0].timestamp()) // BURST_BUCKET_S]) >= BURST_MIN_PATHS] or mine
        n_stall = sum(1 for t in burst if id(t) in stall_ids)
        if n_stall:
            stall_share[conn] = round(n_stall / len(burst), 3)
    stall_conns = sorted(c for c, s in stall_share.items() if s > BACKEND_STALL_SHARE)
    kept = [t for t in rest if id(t) not in stalled_ids and id(t) not in stall_ids]
    explained = Counter(t[1] for t in awake if id(t) in cascade_ids or id(t) in stalled_ids or id(t) in down_ids
                        or id(t) in stall_ids)
    tz = now.tzinfo

    def bucket_iso(b):
        return datetime.fromtimestamp(b * STALL_BUCKET_S, tz).isoformat(sep=" ")

    return {
        "awake_conn_bursts": conn_bursts(_burst_buckets(kept)),
        "client_stall": {"count": len(stalled_ids), "buckets": [bucket_iso(b) for b in sorted(stall_buckets)],
                         "conns": sorted(set().union(*stall_buckets.values())) if stall_buckets else [],
                         "by_conn": dict(Counter(t[2] for t in awake if id(t) in stalled_ids).most_common()),
                         "unmapped_slow_links": unmapped},
        "reconnect_cascade": {"count": len(cascade_ids),
                              "by_conn": dict(Counter(t[2] for t in awake if id(t) in cascade_ids).most_common()),
                              "teardowns": sum(len(v) for v in downs.values())},
        "backend_down": {"count": len(down_ids),
                         "by_conn": dict(Counter(t[2] for t in awake if id(t) in down_ids).most_common()),
                         "episodes": [{"conn": e["conn"], "start": e["start"].isoformat(sep=" "),
                                       "end": e["end"].isoformat(sep=" "),
                                       "seconds": round((e["end"] - e["start"]).total_seconds(), 1),
                                       "answered": not e["open"], "reason": e["reason"]}
                                      for e in episodes if e["end"] >= start]},
        "backend_stall": {"count": len(stall_ids),
                          "by_conn": dict(Counter(t[2] for t in awake if id(t) in stall_ids).most_common()),
                          "conns": stall_conns, "burst_share": stall_share,
                          "episodes": [{"conn": e["conn"], "teardown": e["teardown"].isoformat(sep=" "),
                                        "dials": [{"at": d[0].isoformat(sep=" "), "ms": d[1]} for d in e["dials"]],
                                        "confirms": [{"at": c[0].isoformat(sep=" "), "kind": c[1], "detail": c[2]}
                                                     for c in e["confirms"]]}
                                       for e in stalls if e["events"][-1] >= start - near]},
        "backend_cold_start": {"count": len(cold_ids),
                               "by_conn": dict(Counter(t[2] for t in awake if id(t) in cold_ids).most_common()),
                               "by_path": dict(Counter(t[1] for t in awake if id(t) in cold_ids).most_common()),
                               "readies": [r.isoformat(sep=" ") for r in readies if any(
                                   id(t) in cold_ids and 0 <= (t[0] - r).total_seconds() <= COLD_START_S
                                   for t in awake)]},
        "explained_by_path": dict(explained.most_common()),
    }


def stall_dominant(rep: dict) -> bool:
    """Client-stall timeouts are most (> half) of the awake count."""
    total = rep.get("awake_count") or 0
    return bool(total) and ((rep.get("client_stall") or {}).get("count") or 0) * 2 > total


def unexplained_count(rep: dict) -> int:
    """Awake timeouts that are neither client_stall, reconnect_cascade, backend_down nor backend_stall.
    Startup correlation remains eligible for incident/path ownership."""
    return (rep.get("awake_count") or 0) - sum((rep.get("awake_explained_by_path") or {}).values())


def stall_card_conns(rep: dict, threshold: int) -> list:
    """Hung-backend conns (backend_stall `conns`) big enough for their own card: >= PATH_SHARE of the awake count,
    or >= the threshold. Biggest first."""
    total = rep.get("awake_count") or 0
    by_conn = rep.get("awake_by_conn") or {}
    found = [(-by_conn.get(c, 0), c) for c in (rep.get("backend_stall") or {}).get("conns") or ()
             if total and (by_conn.get(c, 0) >= threshold or by_conn.get(c, 0) >= PATH_SHARE * total)]
    return [c for _, c in sorted(found)]


def _hung_leftover(rep: dict) -> Counter:
    """By path: a hung-backend conn's awake timeouts outside +/- BACKEND_STALL_S of its episode lines. The same hang
    (it lasts longer than the lines that prove it), so its conn's card owns them."""
    out: Counter = Counter()
    for conn in (rep.get("backend_stall") or {}).get("conns") or ():
        out.update(((rep.get("awake_conn_bursts") or {}).get(conn) or {}).get("paths") or {})
    return out


def residual_count(rep: dict) -> int:
    """Unexplained awake timeouts that no hung-backend conn owns: what the generic incident card is judged on."""
    return unexplained_count(rep) - sum(_hung_leftover(rep).values())


def conn_bursts(conn_buckets: dict) -> dict:
    """Per conn: its awake timeouts by path, and how many fell in shared bursts (a time bucket in which
    >= BURST_MIN_PATHS of its paths timed out together), by path. Threshold-free; :func:`saturated_conns` judges."""
    out = {}
    for conn, buckets in conn_buckets.items():
        paths: Counter = Counter()
        in_bursts: Counter = Counter()
        n_bursts = 0
        for c in buckets.values():
            paths.update(c)
            if len(c) >= BURST_MIN_PATHS:
                n_bursts += 1
                in_bursts.update(c)
        out[conn] = {"count": sum(paths.values()), "paths": dict(paths.most_common()), "burst_buckets": n_bursts,
                     "in_bursts": sum(in_bursts.values()), "burst_by_path": dict(in_bursts.most_common())}
    return out


def saturated_conns(rep: dict, threshold: int) -> list:
    """Conns classified as link/connection saturation, biggest first: significant by count (>= PATH_SHARE of the
    awake count, or >= the threshold), with >= SAT_SHARE of their timeouts in >= SAT_MIN_BURSTS shared bursts that
    span >= SAT_MIN_PATHS paths. A single slow path never qualifies: its timeouts do not co-occur with other paths."""
    total = rep.get("awake_count") or 0
    hung = set((rep.get("backend_stall") or {}).get("conns") or ())  # a hung backend, not its link: no saturation
    found = []
    for conn, b in (rep.get("awake_conn_bursts") or {}).items():
        n = b.get("count") or 0
        if conn in hung or conn in LOCAL_CONNS or conn == "?" or not total or not (n >= threshold or n >= PATH_SHARE * total):
            continue  # local has no tunnel; unknown cannot establish a particular saturated link
        if (len(b.get("burst_by_path") or {}) >= SAT_MIN_PATHS and (b.get("burst_buckets") or 0) >= SAT_MIN_BURSTS
                and (b.get("in_bursts") or 0) >= SAT_SHARE * n):
            found.append((-n, conn))
    return [conn for _, conn in sorted(found)]


def _saturation_md(rep: dict, threshold: int) -> str:
    lines = []
    for conn in saturated_conns(rep, threshold):
        b = rep["awake_conn_bursts"][conn]
        lines.append(f"Link/connection saturation suspected on conn={conn}: {b['in_bursts']} of its {b['count']} "
                     f"awake timeouts fell in {b['burst_buckets']} shared {BURST_BUCKET_S}-second bursts across "
                     f"{len(b['burst_by_path'])} paths.")
    return "\n".join(lines)


def hours(home: Path) -> dict:
    c: Counter = Counter(ts.strftime("%Y-%m-%d %H") + ('+00:00' if ts.tzinfo else '')
                         for ts, *_ in iter_timeouts(log_files(home)))
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
    if now.tzinfo:
        now = now.astimezone()  # Preserve the existing host-local daily lifecycle.
    return f"backend-timeouts:{host}:{now.strftime('%Y-%m-%d')}"


def _paths_md(rep: dict) -> str:
    items = list(rep["by_path"].items())[:TOP_PATHS]
    return "\n".join(f"- `{p}`: {n}" for p, n in items) or "- (none)"


def _host_md(probe: dict) -> str:
    load = ", ".join(str(x) for x in probe.get("loadavg") or []) or "?"
    ps = "\n".join(f"    {r}" for r in probe.get("backend_ps") or []) or "    (no hermes backend process found)"
    return f"- load average (1/5/15 min): {load}\n- backend processes (pid %cpu elapsed command):\n{ps}"


def card_title(host: str, rep: dict, window_min: int) -> str:
    return f"Desktop backend timeouts: {rep['count']} in {window_min} min"


ATTRIBUTE_MD = """Attribute bytes and requests per path on the far end of the connection, not timeouts: the paths that time out
   are mostly victims queued behind whatever fills the link. On the backend host, capture its loopback port for 30 s
   (`sudo timeout 30 tcpdump -i lo -w /tmp/bt.pcap 'tcp port <backend port>'`), then sum requests and response bytes
   per request path (e.g. `tshark -r /tmp/bt.pcap -Y http.response -T fields -e http.response_for.uri -e
   http.content_length_header`, or a small dpkt/scapy parser when responses are chunked). The path with the most
   bytes or requests is the suspect even if its timeout count is not the highest."""


def _saturation_step(rep: dict, threshold: int) -> str:
    conns = saturated_conns(rep, threshold)
    if not conns:
        return ""
    names = ", ".join(f"conn={c}" for c in conns)
    return f"3. The evidence names link/connection saturation on {names}. {ATTRIBUTE_MD}\n"


def _evidence(host: str, rep: dict, probe: dict, threshold: int, window_min: int) -> str:
    sat = "\n".join(x for x in (_saturation_md(rep, threshold), _stall_md(rep)) if x)
    return fence_evidence(f"""Detected by hermes-mods plugins/coach/backend_timeouts.py (cron, every 10 min) on {host}.

The Desktop app on {host} logged {rep['count']} "{NEEDLE}" lines in the last {window_min} min
({rep['first']} .. {rep['last']}, explicit UTC offsets; threshold {threshold}).
Of these, {rep.get("awake_count", rep["count"])} occurred while awake and
{rep.get("sleep_count", 0)} during positively observed sleep/darkwake intervals (of which {rep.get("sleep_spanning_count", 0)} landed
after a wake but were issued before it).
The threshold applies only to awake timeouts; raw count remains unchanged.
{(rep.get("fail_fast") or {}).get("awake_count", 0)} of the awake lines are down-gate "Failing fast" lines that quote an earlier timeout (no request
waited; by conn {(rep.get("fail_fast") or {}).get("by_conn") or "(none)"}): they stay in the counts but not in the threshold.
Awake timeouts by backend connection: {rep.get("awake_by_conn") or "(none)"}; by time since last observed wake:
{rep.get("awake_since_wake") or "(none)"}.
{(chr(10) + sat + chr(10)) if sat else ""}
## Top request paths
{_paths_md(rep)}

## Host
{_host_md(probe)}""")


def card_body(host: str, rep: dict, probe: dict, threshold: int, window_min: int) -> str:
    seen = _evidence(host, rep, probe, threshold, window_min)
    return f"""The Desktop backend timed out on requests. Users see this as chats that won't load,
archive asking "task status unavailable", stale boards.

{home_pin_note("the diagnosis times the local backend and samples its busiest process, which only the gateway that logged the timeouts has")}

## Evidence
{seen}

## Do this
1. Find what makes the backend slow (it is usually CPU-starved or a request is doing heavy work per call): time the
   top paths above against the local backend, `ps`/`sample` the busiest `hermes serve`, and check recent core
   commits for per-request work (e.g. 2026-10-27: a per-request kanban integrity_check, fixed in f2d0e23785).
2. Fix the cause (not just a restart). Use the five-whys skill for the chain.
{_saturation_step(rep, threshold)}
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
    daily = {k: v for k, v in st.items() if k != PATHS_KEY}
    keep = dict(sorted(daily.items())[-14:])  # two weeks of daily keys
    paths = st.get(PATHS_KEY) or {}
    if paths:  # per-path owner cards: the 50 most recently created
        keep[PATHS_KEY] = dict(sorted(paths.items(), key=lambda kv: -float((kv[1] or {}).get("created") or 0))[:50])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep, indent=1))
    os.replace(tmp, p)


def _show(runner: Callable, tid: str) -> Optional[dict]:
    """`hermes kanban show --json` with a non-empty task status, else None (failed, malformed, unknown id)."""
    try:
        r = _run(runner, ["hermes", "kanban", "show", tid, "--json"])
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        status = data["task"]["status"]
        return data if isinstance(status, str) and status else None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return None


def _card_status(runner: Callable, tid: str) -> Optional[str]:
    """A failed or malformed read must not hide a recurrence behind a stale ID."""
    data = _show(runner, tid)
    return data["task"]["status"] if data else None


def _open_earlier_card(st: dict, host: str, daily_key: str, runner: Callable) -> Optional[tuple[str, dict]]:
    """Newest earlier-day state entry for this host whose card is still open (readable, not done/archived)."""
    prefix = f"backend-timeouts:{host}:"
    for key in sorted((k for k in st if k.startswith(prefix) and k < daily_key), reverse=True):
        tid = (st.get(key) or {}).get("task_id")
        if not tid:
            continue
        status = _card_status(runner, tid)
        if status is not None and status not in {"done", "archived"}:
            return key, st[key]
    return None


def file_card(home: Path, host: str, now: datetime, rep: dict, probe: dict, threshold: int, window_min: int,
              runner: Callable) -> dict:
    """Reuse only an open destination; closed/unreadable cards start a numbered recurrence."""
    daily_key = card_key(host, now)
    st = load_state(home)
    ent = st.get(daily_key) or {}
    if not ent:
        # One card per incident, not per calendar day: a burst after midnight while an earlier
        # day's card is still open is that incident recurring, so it comments there.
        carried = _open_earlier_card(st, host, daily_key, runner)
        if carried is not None:
            daily_key, ent = carried
    sequence = int(ent.get("sequence") or 1)
    key = daily_key if sequence == 1 else f"{daily_key}:{sequence}"
    now_s = now.timestamp()
    tid = ent.get("task_id")
    existed = bool(tid)
    previous = ""
    while True:
        if tid:
            status = _card_status(runner, tid)
            if status is not None and status not in {"done", "archived"}:
                break
            previous = (f"\nEarlier card `{tid}` closed ({status}).\n" if status else
                        f"\nEarlier card `{tid}` has unreadable status; treating it as closed.\n")
            sequence += 1
            key = f"{daily_key}:{sequence}"
        try:
            # the hub's card goes to the pool (hub admission refuses a machine:hub pin); elsewhere the home pin
            pin, body = home_route(card_body(host, rep, probe, threshold, window_min) + previous)
        except HomePinUnresolved as exc:  # a synced board with no readable identity: never file it portable
            return {"key": key, "action": "create_failed", "rc": None, "out": str(exc)[-300:]}
        r = _run(runner, ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", key,
                          "--created-by", "backend-timeouts-watch", "--body-file", "-", "--json",
                          *pin, card_title(host, rep, window_min)], body)
        if r.returncode != 0:
            return {"key": key, "action": "create_failed", "rc": r.returncode,
                    "out": ((r.stdout or "") + (r.stderr or ""))[-300:]}
        try:
            d = json.loads(r.stdout)
            tid = d.get("id")
            if not isinstance(tid, str) or not tid:
                raise ValueError("missing task id")
            created = float(d.get("created_at") or now_s)
        except (ValueError, TypeError, AttributeError):
            return {"key": key, "action": "create_unparsed", "out": (r.stdout or "")[-300:]}
        existed = created < now_s - 120  # the key returned a card made by an earlier run (state file lost)
        ent = {"task_id": tid, "sequence": sequence,
               "created": now_s if not existed else created, "last_comment": 0}
        if not existed:
            ent["last_comment"] = now_s
            ent["paths"] = list(rep["by_path"])[:TOP_PATHS]  # the paths its body names: it owns them
            st[daily_key] = ent
            save_state(home, st)
            return {"key": key, "action": "created", "task_id": tid}
        # Recovered idempotent destinations need the same lifecycle check as cached ones.
    if now_s - float(ent.get("last_comment") or 0) < COMMENT_EVERY_S:
        st[daily_key] = ent
        save_state(home, st)
        return {"key": key, "action": "throttled", "task_id": tid}
    text = (f"Still firing at {now.isoformat()} on {host}: {rep['count']} timeouts in {window_min} min "
            f"(threshold {threshold}).\nTop paths:\n{_paths_md(rep)}\n{_host_md(probe)}")
    r = _run(runner, ["hermes", "kanban", "comment", tid, "--author", "backend-timeouts-watch", text])
    ent["last_comment"] = now_s
    st[daily_key] = ent
    save_state(home, st)
    return {"key": key, "action": "commented" if r.returncode == 0 else "comment_failed", "task_id": tid,
            "existed": existed}


def significant_paths(rep: dict, threshold: int) -> list:
    """Awake paths big enough to need their own owner: >= PATH_SHARE of the awake count, or >= the threshold,
    counting only timeouts that stand apart from their conn: a saturated conn's shared-burst timeouts belong to its
    conn card (:func:`saturated_conns`), so a victim path gets no card of its own."""
    total = rep.get("awake_count") or 0
    absorbed: Counter = Counter(rep.get("awake_explained_by_path") or {})  # client stall / reconnect cascade
    absorbed.update(_hung_leftover(rep))  # a hung backend's conn card owns the rest of that conn's timeouts
    for conn in saturated_conns(rep, threshold):
        absorbed.update(rep["awake_conn_bursts"][conn].get("burst_by_path") or {})
    out = []
    for p, n in (rep.get("awake_by_path") or {}).items():
        apart = n - absorbed[p]
        if total and (apart >= threshold or apart >= PATH_SHARE * total):
            out.append(p)
    return out


def path_token(path: str) -> str:
    """`/api/plugins/hub-sync/status` -> `hub-sync/status`: how cards and people name a route."""
    tok = path
    for prefix in ("/api/", "plugins/"):
        tok = tok[len(prefix):] if tok.startswith(prefix) else tok
    tok = tok.split("/<id>", 1)[0].strip("/")
    return tok or path


def _names(data: dict, path: str) -> bool:
    t = data.get("task") or {}
    text = f"{t.get('title') or ''}\n{t.get('body') or ''}"
    return path in text or path_token(path) in text


def path_owner(runner: Callable, host_ent: dict, path: str) -> Optional[str]:
    """The open card that owns `path`: the host incident card when it is active and names the path (its recorded
    top paths or its text), else, while it waits (todo/scheduled), an open parent that names it. A catch-all
    waiting behind a parent about another path owns nothing for this one (agent-23f6a4d787)."""
    tid = host_ent.get("task_id")
    data = _show(runner, tid) if tid else None
    if data is None:
        return None
    t = data["task"]
    status = t["status"]
    if status in CLOSED:
        return None
    if status in ACTIVE:
        recorded = host_ent.get("paths")
        if path in (recorded or ()) or _names(data, path):
            return tid
        if recorded is None and "body" not in t and "title" not in t:
            return tid  # a legacy card with no naming evidence at all: keep the old per-host behaviour
        return None
    for pid in data.get("parents") or []:
        if not isinstance(pid, str):
            continue
        p = _show(runner, pid)
        if p is not None and p["task"]["status"] not in CLOSED and _names(p, path):
            return pid
    return None


def path_card_title(path: str, n: int, window_min: int) -> str:
    return f"Desktop backend timeouts on {path_token(path)}: {n} in {window_min} min (no owner card)"


def conn_card_title(conn: str, info: dict, window_min: int) -> str:
    return (f"Desktop link saturation on conn={conn}: {info['count']} timeouts across "
            f"{len(info['paths'])} paths in {window_min} min (one card for the conn)")


def conn_token(conn: str) -> str:
    """How a card names a saturated connection; the host card's evidence and the conn card both carry it."""
    return f"conn={conn}"


def conn_card_body(host: str, conn: str, rep: dict, probe: dict, threshold: int, window_min: int,
                   previous: str) -> str:
    info = rep["awake_conn_bursts"][conn]
    burst_by = info.get("burst_by_path") or {}
    cands = "\n".join(f"- `{p}`: {n} ({burst_by.get(p, 0)} in shared bursts)"
                      for p, n in list(info["paths"].items())[:TOP_PATHS]) or "- (none)"
    pct = round(100.0 * info["in_bursts"] / info["count"]) if info["count"] else 0
    return f"""Link/connection saturation on `{conn_token(conn)}`, not one slow handler: {info['count']} of
{rep.get('awake_count')} awake timeouts went over this connection, and {info['in_bursts']} of them ({pct}%) fell in
{info['burst_buckets']} shared {BURST_BUCKET_S}-second bursts in which several paths timed out together
({len(burst_by)} paths in all). One saturated tunnel or link makes every path on it time out at once, so this one card
stands for the whole connection; the detector files no per-path card for these victims.
{previous}
{home_pin_note("confirming the tunnel is the bottleneck needs the Desktop host that logged the timeouts and its tunnel")}

## Candidate paths on {conn_token(conn)}
{cands}

Most of these are probably victims. The cause is whichever path sends the most bytes or requests over the link,
and that may not be the one with the most timeouts.

## Evidence
{_evidence(host, rep, probe, threshold, window_min)}

## Do this
1. Confirm the link is the bottleneck. Time `/api/health` through the Desktop's tunnel for this conn, then through a
   fresh tunnel to the same port (`ssh -o ControlPath=none -L ...`), then on the backend host's loopback. Read
   `ss -tnoi` on the backend host for this peer's Send-Q / notsent.
2. {ATTRIBUTE_MD}
3. Fix the path that floods the link (request rate, refetch storms, response size), not its victims. Use the
   five-whys skill for the chain.

## Acceptance
- After the fix, a fresh 30-minute window shows zero timeouts on this conn:
  `python3 ~/code/hermes-mods/plugins/coach/backend_timeouts.py --dry-run` shows no `{conn}` entry in
  `awake_by_conn`, with `window_start` later than the fix. A restart alone does not satisfy this.
- Paste that output and the per-path byte/request attribution in a comment before completing.
"""


def uplink_card_title(host: str) -> str:
    return f"Desktop uplink stall on {host}"


def _episodes_md(rep: dict, conn: Optional[str] = None) -> str:
    out = []
    for e in (rep.get("backend_stall") or {}).get("episodes") or []:
        if conn is not None and e["conn"] != conn:
            continue
        dials = ", ".join(f"{d['at']} +{d['ms']} ms" for d in e["dials"])
        conf = ", ".join(f"{c['at']} {c['kind']}" + (f" ({c['detail']})" if c.get("detail") else "")
                         for c in e["confirms"])
        out.append(f"- conn={e['conn']}: teardown {e['teardown']}; re-dial {dials}; then {conf}")
    return "\n".join(out)


def _hung_md(rep: dict) -> str:
    bs = rep.get("backend_stall") or {}
    if not bs.get("count"):
        return ""
    return (f"Backend stall (hung far-end backend): {bs['count']} timeouts within {BACKEND_STALL_S} s of a teardown, "
            f"a fast SSH re-dial (< {STALL_DIAL_FAST_MS} ms) and the backend still not answering on the fresh tunnel "
            f"(by conn {bs.get('by_conn') or {}}; share of each conn's burst timeouts {bs.get('burst_share') or {}}; "
            f"hung conns {', '.join(bs.get('conns') or []) or 'none'}).\n{_episodes_md(rep)}")


def stall_card_title(conn: str, rep: dict, window_min: int) -> str:
    n = (rep.get("awake_by_conn") or {}).get(conn, 0)
    return f"Desktop hung backend on conn={conn}: {n} timeouts in {window_min} min (one card for the conn)"


def stall_card_body(host: str, conn: str, rep: dict, probe: dict, threshold: int, window_min: int,
                    previous: str) -> str:
    bs = rep.get("backend_stall") or {}
    n = (rep.get("awake_by_conn") or {}).get(conn, 0)
    eps = [e for e in bs.get("episodes") or [] if e["conn"] == conn]
    pids = sorted({c["detail"].split()[1] for e in eps for c in e["confirms"]
                   if c["kind"] == "dashboard_not_answering" and c.get("detail")})
    times = [e["teardown"] for e in eps] + [c["at"] for e in eps for c in e["confirms"]]
    span = f"{min(times)} .. {max(times)} (UTC)" if times else f"{rep.get('first')} .. {rep.get('last')}"
    return f"""The backend at the far end of `{conn_token(conn)}` hung; the link to it was fine. {n} of
{rep.get('awake_count')} awake timeouts went over this conn, {bs.get('by_conn', {}).get(conn, 0)} of them within
{BACKEND_STALL_S} s of a hang episode: the Desktop tore the conn down, re-dialled SSH in a few seconds (a saturated
link slows the handshake too), and the backend still did not answer on the fresh tunnel. Per-path byte attribution
will not find this: no path floods the link while the far-end process is stopped or its event loop is blocked.
{previous}
{home_pin_note("the hang episodes are read from the Desktop log of the host that logged the timeouts")}

## Hang episodes on {conn_token(conn)}
{_episodes_md(rep, conn) or "- (none in the window)"}
Not-answering dashboard pid(s): {", ".join(pids) or "(not logged)"}. Window to search: {span}.

## Evidence
{_evidence(host, rep, probe, threshold, window_min)}

## Do this
1. On the backend host for `{conn}`, grep `~/.hermes/logs/agent.log*` for `event loop stalled` and
   `loop-watch: event loop stalled` in the window above, and note which process (pid) stalled and for how long.
2. Check `sudo journalctl --since <window start> --until <window end>` for `COMMAND=.*py-spy` or other ptrace /
   observer commands (gdb, strace, perf, a blocking `py-spy record`/`dump` without `--nonblocking`) in the window:
   an attached observer stops the whole process.
3. Read the loop-watch.jsonl stall records (`loop_stacks`) for the pid ({", ".join(pids) or "the hung serve"}) to see
   where the loop was blocked.
4. Fix the cause (the observer, or the blocking call in the loop), not just a restart. Use the five-whys skill for
   the chain.

## Acceptance
- After the fix, a fresh 30-minute window shows no backend stall on this conn:
  `python3 ~/code/hermes-mods/plugins/coach/backend_timeouts.py --dry-run` shows no `{conn}` in
  `backend_stall.by_conn`, with `window_start` later than the fix.
- Paste that output and the stall/observer evidence in a comment before completing.
"""


def file_stall_card(home: Path, host: str, conn: str, now: datetime, rep: dict, probe: dict, threshold: int,
                    window_min: int, runner: Callable) -> dict:
    """At most one open card per hung-backend conn: reuse it (comment hourly) while open, else
    `backend-timeouts:<host>:backend-stall:<conn>` (:2, :3, ... once the earlier one closed)."""
    st = load_state(home)
    bs = rep.get("backend_stall") or {}
    still = (f"Still firing at {now.isoformat()} on {host}: hung backend on {conn_token(conn)}, "
             f"{(bs.get('by_conn') or {}).get(conn, 0)} backend_stall of "
             f"{(rep.get('awake_by_conn') or {}).get(conn, 0)} awake timeouts on the conn in {window_min} min.\n"
             f"{_episodes_md(rep, conn)}")
    out = _ensure_owner(home, st, f"{host}:backend-stall:{conn}", f"backend-timeouts:{host}:backend-stall:{conn}",
                        now, runner, "hung backend", lambda: None,
                        lambda previous: stall_card_body(host, conn, rep, probe, threshold, window_min, previous),
                        stall_card_title(conn, rep, window_min), still)
    if out.get("owner"):
        out["task_id"] = out["owner"]
    out["kind"] = "backend_stall"
    out["conn"] = conn
    return out


def _stall_md(rep: dict) -> str:
    cs = rep.get("client_stall") or {}
    rc = rep.get("reconnect_cascade") or {}
    bd = rep.get("backend_down") or {}
    down = ""
    if bd.get("count"):
        eps = "; ".join(f"conn={e['conn']} {e['start']} for {e['seconds']} s"
                        f"{'' if e['answered'] else ' (no answer logged)'} ({e['reason'] or 'reason not logged'})"
                        for e in bd.get("episodes") or [])
        down = (f"Backend down: {bd['count']} timeouts were logged while the Desktop's down gate held their conn down "
                f"(by conn {bd.get('by_conn') or {}}); most are fail-fast lines quoting one timeout, not new ones. "
                f"The far-end backend was away (restart or hang), which is not link traffic. Episodes: {eps}.")
    cold = ""
    bc = rep.get("backend_cold_start") or {}
    if bc.get("count"):
        cold = (f"Backend cold start: {bc['count']} timeouts on the Desktop's own backend (by conn "
                f"{bc.get('by_conn') or {}}) came within {COLD_START_S} s after `{READY_MARK}` "
                f"({', '.join(bc.get('readies') or [])}). Startup correlation only: READY proximity does not "
                f"prove the API was not mounted or that these failures are fixed. Local failures remain "
                f"eligible for incident/path ownership; investigate backend readiness and latency.")
    head = "\n".join(x for x in (down, cold, _hung_md(rep)) if x)
    if not cs.get("count") and not rc.get("count"):
        return head
    return (head + "\n" if head else "") + (f"Client-side stall: {cs.get('count', 0)} awake timeouts fell in 5-minute buckets where several remote "
            f"conns ({', '.join(cs.get('conns') or []) or 'none'}) stalled within {STALL_PAIR_S} s of each other "
            f"(buckets starting {', '.join(cs.get('buckets') or []) or 'none'}; by conn {cs.get('by_conn') or {}}).\n"
            f"Reconnect cascade: {rc.get('count', 0)} timeouts within {CASCADE_S} s after a teardown / treated-as-down "
            f"of their conn ({rc.get('teardowns', 0)} teardowns; by conn {rc.get('by_conn') or {}}).")


def uplink_card_body(host: str, rep: dict, probe: dict, threshold: int, window_min: int, previous: str) -> str:
    cs = rep.get("client_stall") or {}
    conns = ", ".join(f"conn={c}" for c in cs.get("conns") or []) or "several remote conns"
    return f"""Several independent remote links from the Desktop on {host} stalled at the same time ({conns}), so the
shared segment is most likely {host}'s own network uplink, not any one link's traffic. {cs.get('count', 0)} of
{rep.get('awake_count')} awake timeouts fell in those stall buckets. Per-path byte attribution on a backend will not
find a flooding path here: each far end sees a healthy link.
{previous}
{home_pin_note("the network and Tailscale path to check are the Desktop host's own")}

## Evidence
{_evidence(host, rep, probe, threshold, window_min)}

## Do this
1. On {host}, read its network path while (or soon after) it stalls: `tailscale status` (each stalled peer: direct
   or relayed through DERP, and which relay), `tailscale netcheck` (UDP, NAT type, nearest DERP latency), and
   `tailscale ping <peer>` for each stalled conn's host. Note the Wi-Fi/Ethernet network, captive or hard-NAT
   networks, VPNs and any large upload running on {host} at that time.
2. Compare the timestamps in `[slow-link]` and `[remote-timing] ... dispatch probe slow` lines of
   `~/.hermes/logs/desktop.log` across conns. Stalls that start together on links with different routes (direct vs
   DERP) point to the shared uplink. If only one conn stalls in a later window, that is a different incident.
3. Fix or work around the uplink cause where the agent can (e.g. a stuck Tailscale path: `tailscale down/up`, or a
   process saturating the uplink). For a network only the user controls, report the evidence. Use the five-whys
   skill for the chain.

## Acceptance
- A fresh 30-minute window on {host} shows no client stall:
  `python3 ~/code/hermes-mods/plugins/coach/backend_timeouts.py --dry-run` prints `"client_stall": {{"count": 0`,
  with `window_start` later than the fix. If the cause is a network outside the agents' control, paste the
  `tailscale netcheck` output and the stall timestamps instead.
- Paste that output in a comment before completing.
"""


def file_uplink_card(home: Path, host: str, now: datetime, rep: dict, probe: dict, threshold: int,
                     window_min: int, runner: Callable) -> dict:
    """At most one open card per host per uplink-stall incident: reuse it (comment hourly) while open, else
    `backend-timeouts:<host>:uplink-stall` (:2, :3, ... once the earlier one closed)."""
    st = load_state(home)
    cs = rep.get("client_stall") or {}
    still = (f"Still firing at {now.isoformat()} on {host}: client stall, {cs.get('count', 0)} of "
             f"{rep.get('awake_count')} awake timeouts in {window_min} min, conns {', '.join(cs.get('conns') or [])}, "
             f"buckets {', '.join(cs.get('buckets') or [])}; reconnect cascade "
             f"{(rep.get('reconnect_cascade') or {}).get('count', 0)}.")
    out = _ensure_owner(home, st, f"{host}:uplink-stall", f"backend-timeouts:{host}:uplink-stall", now, runner,
                        "uplink stall", lambda: None,
                        lambda previous: uplink_card_body(host, rep, probe, threshold, window_min, previous),
                        uplink_card_title(host), still)
    if out.get("owner"):
        out["task_id"] = out["owner"]
    out["kind"] = "uplink_stall"
    return out


def _ensure_owner(home: Path, st: dict, skey: str, base: str, now: datetime, runner: Callable, what: str,
                  find_owner: Callable, make_body: Callable, title: str, still: str) -> dict:
    """One owner for `skey`: reuse our open card (comment hourly), accept another owner (`find_owner()`), else file
    `base` (:2, :3, ... once the earlier one closed)."""
    paths_st = st.setdefault(PATHS_KEY, {})
    now_s = now.timestamp()
    ent = paths_st.get(skey) or {}
    tid = ent.get("task_id")
    previous = ""
    if tid:
        status = _card_status(runner, tid)
        if status is not None and status not in CLOSED:
            if now_s - float(ent.get("last_comment") or 0) < COMMENT_EVERY_S:
                return {"owner": tid, "action": "throttled"}
            r = _run(runner, ["hermes", "kanban", "comment", tid, "--author", "backend-timeouts-watch", still])
            ent["last_comment"] = now_s
            save_state(home, st)
            return {"owner": tid, "action": "commented" if r.returncode == 0 else "comment_failed"}
        previous = (f"\nEarlier card for this {what} `{tid}` closed ({status}).\n" if status else
                    f"\nEarlier card for this {what} `{tid}` has unreadable status; treating it as closed.\n")
    owner = find_owner()
    if owner is not None:
        return {"owner": owner, "action": "owned"}
    sequence = int(ent.get("sequence") or 0) + 1 if tid else 1
    key = base if sequence == 1 else f"{base}:{sequence}"
    try:
        pin, body = home_route(make_body(previous))
    except HomePinUnresolved as exc:
        return {"action": "create_failed", "out": str(exc)[-300:]}
    r = _run(runner, ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", key,
                      "--created-by", "backend-timeouts-watch", "--body-file", "-", "--json", *pin, title], body)
    try:
        new_tid = json.loads(r.stdout).get("id") if r.returncode == 0 else None
    except (ValueError, TypeError, AttributeError):
        new_tid = None
    if not isinstance(new_tid, str) or not new_tid:
        return {"action": "create_failed", "key": key, "rc": r.returncode}
    paths_st[skey] = {"task_id": new_tid, "sequence": sequence, "created": now_s, "last_comment": now_s}
    save_state(home, st)
    return {"owner": new_tid, "action": "created", "key": key}


def file_path_cards(home: Path, host: str, now: datetime, rep: dict, probe: dict, threshold: int,
                    window_min: int, runner: Callable, host_result: dict) -> dict:
    """Every saturated conn, then every significant path, gets an owner: reuse our open card (comment hourly),
    accept an owning host/parent card, else file `backend-timeouts:<host>:conn:<conn>` (at most one per conn) or
    `backend-timeouts:<host>:<path>` (:2, :3, ... once the earlier one closed). Output keys: the path, or
    `conn=<conn>`."""
    if host_result.get("action") not in {"commented", "comment_failed", "throttled"}:
        return {}  # a fresh host card lists every top path; a failed create means the board is unusable now
    st = load_state(home)
    host_ent = next((v for k, v in st.items() if k != PATHS_KEY and isinstance(v, dict)
                     and v.get("task_id") == host_result.get("task_id")), {"task_id": host_result.get("task_id")})
    n_by = rep.get("awake_by_path") or {}
    host_tid = host_result.get("task_id")
    out = {}
    for conn in saturated_conns(rep, threshold):
        info = rep["awake_conn_bursts"][conn]
        tok = conn_token(conn)

        def find_conn_owner(info=info, tok=tok):
            owner = path_owner(runner, host_ent, tok)
            if owner is not None:
                return owner
            # an active host card already working on the conn's top paths owns them, as before this check existed
            top = list(info.get("burst_by_path") or {})[:3]
            owners = {path_owner(runner, host_ent, p) for p in top}
            return owners.pop() if top and len(owners) == 1 and None not in owners else None

        def conn_body(previous, conn=conn, tok=tok):
            why = (f"No open card names `{tok}`: the incident card `{host_tid}` is not working on it (it is closed, "
                   f"or waits behind parents about other paths).\n")
            return why + conn_card_body(host, conn, rep, probe, threshold, window_min, previous)

        still = (f"Still firing at {now.isoformat()} on {host}: {tok} {info['count']} of {rep.get('awake_count')} "
                 f"awake timeouts in {window_min} min, {info['in_bursts']} in {info['burst_buckets']} shared "
                 f"bursts across {len(info.get('burst_by_path') or {})} paths.\nTop paths:\n"
                 + "\n".join(f"- `{p}`: {n}" for p, n in list(info["paths"].items())[:TOP_PATHS]))
        out[tok] = _ensure_owner(home, st, f"{host}:conn:{conn}", f"backend-timeouts:{host}:conn:{conn}", now,
                                 runner, "connection", find_conn_owner, conn_body,
                                 conn_card_title(conn, info, window_min), still)
    for path in significant_paths(rep, threshold):
        def path_body(previous, path=path):
            why = (f"No open card names `{path}`: the incident card `{host_tid}` is not working on it "
                   f"(it is closed, or waits behind parents about other paths). This card owns `{path_token(path)}` "
                   f"({n_by.get(path)} of {rep.get('awake_count')} awake timeouts).\n{previous}\n")
            return why + card_body(host, rep, probe, threshold, window_min)

        still = (f"Still firing at {now.isoformat()} on {host}: {path} {n_by.get(path)} of "
                 f"{rep.get('awake_count')} awake timeouts in {window_min} min.")
        out[path] = _ensure_owner(home, st, f"{host}:{path}", f"backend-timeouts:{host}:{path}", now, runner,
                                  "path", lambda path=path: path_owner(runner, host_ent, path), path_body,
                                  path_card_title(path, n_by.get(path) or 0, window_min), still)
    return out


# One active file plus three backups, each <= 64 KiB. No log text, paths,
# hostnames, probe output or owner IDs are copied into this diagnostic sink.
SCAN_RECEIPT_BYTES = 64 * 1024
SCAN_RECEIPT_BACKUPS = 3
SCAN_RECEIPT_READY_LIMIT = 128


def persist_scan_receipt(home: Path, res: dict) -> None:
    """Bounded, private, cross-process serialized JSONL; not a health receipt.

    Filing has already finished. A sink failure is reported on stderr by main,
    never retried by filing again or substituted with a healthy receipt.
    """
    import fcntl
    from logging.handlers import RotatingFileHandler
    import logging

    class PrivateRotatingFileHandler(RotatingFileHandler):
        def _open(self):
            # Also used by doRollover: never expose a newly created 0644 inode.
            fd = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.fchmod(fd, 0o600)
                return os.fdopen(fd, "a", encoding="utf-8")
            except BaseException:
                os.close(fd)
                raise

    cold = res.get("backend_cold_start") or {}
    by_conn = cold.get("by_conn") or {}
    readies = cold.get("readies") or []
    actions = Counter()
    allowed = {"created", "commented", "comment_failed", "throttled", "owned", "create_failed"}
    for card in res["cards"]:
        action = card.get("action")
        actions[action if action in allowed else "other"] += 1
    row = {
        "schema": "coach.backend-timeouts.scan.v1",
        "scope": "retained-log-scan; correlation-only; not API readiness or recovery",
        "window_start": res["window_start"], "window_end": res["window_end"],
        "coverage_start": res["clock"]["coverage_start"],
        "raw_count": res["count"], "awake_count": res["awake_count"],
        "fail_fast_awake_count": res["fail_fast"]["awake_count"],
        "residual_count": residual_count(res), "over_threshold": res["over_threshold"],
        "startup_correlation": {
            "count": cold.get("count", 0), "local_count": by_conn.get("local", 0),
            "primary_count": by_conn.get("primary", 0),
            "explicit_local_without_conn_count": by_conn.get("?", 0),
            "ready_times": readies[:SCAN_RECEIPT_READY_LIMIT],
            "ready_times_omitted": max(0, len(readies) - SCAN_RECEIPT_READY_LIMIT)},
        "owner_actions": dict(actions),
    }
    line = json.dumps(row, separators=(",", ":"), ensure_ascii=True)
    if len(line.encode("utf-8")) + 1 > SCAN_RECEIPT_BYTES:
        raise ValueError("scan receipt exceeds size bound")
    directory = home / "coach"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "backend-timeouts-scans.jsonl"
    # Lock a stable inode, not the rotating data file. Never truncate a peer's lock.
    with os.fdopen(os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600), "a") as lock:
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OSError("scan receipt lock busy") from None
                time.sleep(0.02)
        handler = PrivateRotatingFileHandler(path, maxBytes=SCAN_RECEIPT_BYTES,
                                             backupCount=SCAN_RECEIPT_BACKUPS, encoding="utf-8")
        try:
            record = logging.LogRecord("coach.scan", logging.INFO, "", 0, line, (), None)
            if handler.shouldRollover(record):
                handler.doRollover()
                os.chmod(path, 0o600)
            # Write directly so logging's handleError cannot swallow I/O failure.
            assert handler.stream is not None
            handler.stream.write(line + "\n")
            handler.flush()
        finally:
            handler.close()


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
    now = now or datetime.now(timezone.utc)
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
    clock = {"source": "per-entry-offset" if now.tzinfo else "injected-naive-compatibility",
             "bounds_timezone": "UTC" if now.tzinfo else "unspecified",
             "coverage": "retained log entries only; not backend health or continuous availability"}
    if now.tzinfo:
        now = now.astimezone(timezone.utc)
    power = (power_probe or probe_power)(now)
    intervals = power.get("intervals", [])
    try:
        rep = scan(home, now, window, intervals)
    except ValueError as exc:
        print(json.dumps({"status": "clock_unresolved", "count": None, "cards": [],
                          "error": str(exc), "clock": clock,
                          "window_end": now.isoformat(),
                          "window_start": (now - timedelta(minutes=window)).isoformat()}))
        return 2
    clock['coverage_start'] = rep.pop('coverage_start')
    power_report = {k: v for k, v in power.items() if k != "intervals"}
    power_report["intervals"] = [[lo.isoformat(sep=" "), hi.isoformat(sep=" ")]
                                 for lo, hi in intervals if hi >= now - timedelta(minutes=window)]
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host, "home": str(home),
           "dry_run": a.dry_run, "threshold": threshold, "window_min": window, **rep,
           "power": power_report, "clock": clock,
           "over_threshold": rep["awake_count"] - rep["fail_fast"]["awake_count"] >= threshold, "saturated_conns": saturated_conns(rep, threshold),
           "cards": []}
    # Most of the window is a client-side stall: one uplink card. A hung far-end backend: one card per hung conn.
    # The generic incident (and its conn/path owners) only when what remains unexplained, and not owned by a hung
    # conn's card, is over the threshold by itself.
    res["uplink_stall"] = res["over_threshold"] and stall_dominant(rep)
    res["backend_stall_conns"] = stall_card_conns(rep, threshold) if res["over_threshold"] else []
    special = res["uplink_stall"] or bool(res["backend_stall_conns"])
    generic = res["over_threshold"] and (not special or residual_count(rep) >= threshold)
    if res["over_threshold"]:
        res["host_probe"] = (probe or probe_host)()
        if not a.dry_run:
            runner = runner or subprocess.run
            if res["uplink_stall"]:
                res["cards"].append(file_uplink_card(home, host, now, rep, res["host_probe"], threshold, window,
                                                     runner))
            for conn in res["backend_stall_conns"]:
                res["cards"].append(file_stall_card(home, host, conn, now, rep, res["host_probe"], threshold,
                                                    window, runner))
            if generic:
                host_card = file_card(home, host, now, rep, res["host_probe"], threshold, window, runner)
                owners = file_path_cards(home, host, now, rep, res["host_probe"], threshold, window, runner,
                                         host_card)
                if owners:
                    host_card["path_owners"] = owners
                res["cards"].append(host_card)
    receipt_failed = False
    if not a.dry_run:
        try:
            persist_scan_receipt(home, res)
        except (OSError, ValueError):
            # Do not include exception text: filesystem paths may contain private data.
            print("backend timeout scan receipt persistence failed", file=sys.stderr)
            receipt_failed = True
    if a.json or a.dry_run:
        print(json.dumps(res))
    elif res["over_threshold"]:  # cron no-agent: stdout is delivered, so stay silent when healthy
        c = res["cards"][0] if res["cards"] else {}
        top = ", ".join(f"{p} {n}" for p, n in list(rep["by_path"].items())[:3])
        print(f"backend timeouts on {host}: {rep['count']} in {window} min (threshold {threshold}); top: {top}; "
              f"card {c.get('task_id', '?')} {c.get('action', '')}")
    return 1 if receipt_failed else 0


if __name__ == "__main__":
    sys.exit(main())
