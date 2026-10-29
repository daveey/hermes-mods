"""cpu-budget: detect a service group burning CPU while the host is affirmatively idle.

Generic, stdlib-only (Python 3.9 safe: macOS ships /usr/bin/python3 3.9). A host scheduler calls
``tick()`` about every 30 s (one ``ps`` call). Per service it sums *cumulative CPU-time deltas*
(ps ``cputime``), never ``%CPU`` (a decaying average that hides bursts and idle floors).

Accounting
  * A process identity is ``(pid, start time)``. A reused pid has a different start time, so it is a
    new identity and is never diffed against the old one.
  * Identity seen before: delta = cputime - previous cputime (negative = anomaly, interval incomplete).
  * Identity not seen before that started inside this interval: its whole cputime is the delta
    (and the interval is marked ``restart``: the old process's last slice is unobservable).
  * Identity not seen before that started earlier (first run, missed samples): baseline only,
    interval incomplete (``unbaselined``).
  * An identity that vanished, or a required service with no process: interval incomplete.
Idleness
  * Probes of kind ``conversation`` and kind ``ci`` each return idle / active / unknown. An interval
    is idle only if at least one probe of EACH kind says idle and none says active or unknown.
    Unknown is never idle.
Windows
  * Tumbling windows of ``window_s`` (600 s). A window is *complete idle* only if every interval in it
    was complete, idle and no longer than ``max_gap_s``. CPU% is of one core (sum cpu_s / wall_s).
  * ``consecutive`` (2) adjacent complete-idle windows above ``budget_pct`` (40) report an incident to
    the sink: first one creates a deduplicated Kanban card, later ones append evidence. Anything else
    resets the streak. Samples closer than ``min_interval_s`` (30) are ignored.
No lease/takeover code is touched; the only side effects are the state file, the evidence ledger and
the sink (all injectable).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULTS = {
    "budget_pct": 40.0,
    "window_s": 600,
    "consecutive": 2,
    "min_interval_s": 30,
    "max_gap_s": 90,
    "repeat_min_s": 1800,
}
_SELF_RE = re.compile(r"mac-load-recorder\s+sample|cpu_budget\.py\s+--config")
_PS_RE = re.compile(r"^\s*(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+\d\d:\d\d:\d\d\s+\d{4})\s+(\S+)\s*(.*)$")

Identity = Tuple[int, float]  # (pid, start epoch)


# ---------------------------------------------------------------- ps parsing
def parse_cputime(text: str) -> float:
    """``[DD-][HH:]MM:SS[.cc]`` -> seconds."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = text.split(":")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return days * 86400 + secs


def parse_ps(out: str) -> List[dict]:
    rows = []
    for line in out.splitlines():
        m = _PS_RE.match(line)
        if not m:
            continue
        try:
            start = time.mktime(time.strptime(re.sub(r"\s+", " ", m.group(2)), "%a %b %d %H:%M:%S %Y"))
            rows.append({"pid": int(m.group(1)), "start": start, "cpu_s": parse_cputime(m.group(3)),
                         "args": m.group(4)})
        except ValueError:
            continue
    return rows


def run_ps() -> Optional[str]:
    try:
        env = dict(os.environ, LC_ALL="C")
        res = subprocess.run(["/bin/ps", "-Aww", "-o", "pid=,lstart=,cputime=,args="], capture_output=True, text=True,
                             timeout=20, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 and res.stdout.strip() else None


def snapshot(rows: List[dict], services: List[dict], self_pid: int) -> Dict[str, Dict[Identity, float]]:
    """service name -> {identity: cumulative cpu seconds}. A pid is counted in the first matching service."""
    out: Dict[str, Dict[Identity, float]] = {s["name"]: {} for s in services}
    pats = [(s["name"], re.compile(s["match"])) for s in services]
    for r in rows:
        if r["pid"] == self_pid or _SELF_RE.search(r["args"]):
            continue
        for name, pat in pats:
            if pat.search(r["args"]):
                out[name][(r["pid"], r["start"])] = r["cpu_s"]
                break
    return out


# ---------------------------------------------------------------- idle probes
def probe_process_absent(spec: dict, rows: List[dict], now: float, since: float) -> Tuple[str, str]:
    """active if any process args match a pattern (CI runners, builds); unknown without a ps table."""
    if rows is None or not spec.get("patterns"):
        return "unknown", "no ps table" if rows is None else "no patterns configured"
    for pat in spec["patterns"]:
        rx = re.compile(pat)
        for r in rows:
            if _SELF_RE.search(r["args"]):
                continue
            if rx.search(r["args"]):
                return "active", "process %d matches %s" % (r["pid"], pat)
    return "idle", "no process matches %d pattern(s)" % len(spec["patterns"])


def probe_files_quiet(spec: dict, rows: Any, now: float, since: float) -> Tuple[str, str]:
    """active if any file under the globs changed since ``since - quiet_s``; unknown if a directory is missing."""
    import glob as _glob
    quiet = float(spec.get("quiet_s", 120))
    if not spec.get("globs"):
        return "unknown", "no globs configured"
    newest = 0.0
    for pattern in spec["globs"]:
        pattern = os.path.expanduser(pattern)
        parent = os.path.dirname(pattern)
        if not os.path.isdir(parent):
            return "unknown", "directory missing: %s" % parent
        try:
            for f in _glob.glob(pattern):
                newest = max(newest, os.stat(f).st_mtime)
            newest = max(newest, os.stat(parent).st_mtime)
        except OSError as exc:
            return "unknown", "stat failed: %s" % exc
    if newest >= since - quiet:
        return "active", "activity %.0fs ago" % (now - newest)
    return "idle", "quiet for %.0fs" % (now - newest)


PROBES = {"process_absent": probe_process_absent, "files_quiet": probe_files_quiet}


def probe_key(spec: dict) -> str:
    return "%s/%s" % (spec.get("kind"), spec.get("name", spec.get("type")))


def evaluate_idle(probes: List[dict], rows: Any, now: float, since: float, registry: Optional[dict] = None,
                  prev: Optional[dict] = None, seen: Optional[dict] = None) -> Tuple[str, List[str]]:
    """``prev`` = each probe's status at the previous sample, ``seen`` is filled with this sample's raw status.

    A point-in-time probe (ps) cannot see the interval between samples, so an interval is idle only if the
    probe was idle at BOTH ends: active at the previous sample means the interval is active, no previous
    reading means unknown.
    """
    reg = PROBES if registry is None else registry
    detail, idle_kinds, worst = [], set(), "idle"
    for spec in probes:
        fn = reg.get(spec.get("type"))
        try:
            status, why = fn(spec, rows, now, since) if fn else ("unknown", "unknown probe type")
        except Exception as exc:  # a probe bug must read as unknown, never idle
            status, why = "unknown", "probe error: %s" % exc
        if seen is not None:
            seen[probe_key(spec)] = status
        if prev is not None and status == "idle":
            before = prev.get(probe_key(spec), "unknown")
            if before != "idle":
                status, why = ("active" if before == "active" else "unknown"), "%s at previous sample; %s" % (before, why)
        detail.append("%s=%s (%s)" % (probe_key(spec), status, why))
        if status == "idle":
            idle_kinds.add(spec.get("kind"))
        elif status == "active":
            worst = "active"
        elif worst != "active":
            worst = "unknown"
    if worst == "idle" and not {"conversation", "ci"} <= idle_kinds:
        worst = "unknown"
        detail.append("missing affirmative evidence kind(s): %s" % sorted({"conversation", "ci"} - idle_kinds))
    return worst, detail


# ---------------------------------------------------------------- interval accounting
def account_interval(prev: dict, cur: Dict[str, Dict[Identity, float]], services: List[dict],
                     prev_ts: float, now: float, max_gap_s: float) -> dict:
    """Return {cpu: {svc: seconds}, reasons: [...]} ; reasons non-empty = interval incomplete."""
    cpu: Dict[str, float] = {}
    reasons: List[str] = []
    if now < prev_ts:
        reasons.append("clock_back")
    if now - prev_ts > max_gap_s:
        reasons.append("gap:%ds" % (now - prev_ts))
    for s in services:
        name = s["name"]
        before = prev.get(name, {})
        after = cur.get(name, {})
        total = 0.0
        if s.get("required", True) and not after:
            reasons.append("missing:%s" % name)
        for ident, cpu_s in after.items():
            if ident in before:
                d = cpu_s - before[ident]
                if d < -0.01:
                    reasons.append("cpu_went_back:%s" % name)
                    d = 0.0
                total += max(d, 0.0)
            elif ident[1] >= prev_ts - 2:
                total += cpu_s
                reasons.append("restart:%s" % name)
            else:
                reasons.append("unbaselined:%s" % name)
        for ident in before:
            if ident not in after:
                reasons.append("exited:%s" % name)
        cpu[name] = total
    return {"cpu": cpu, "reasons": sorted(set(reasons))}


def _jsonable_snap(snap: Dict[str, Dict[Identity, float]]) -> dict:
    return {n: [[p, st, c] for (p, st), c in m.items()] for n, m in snap.items()}


def _load_snap(raw: dict) -> Dict[str, Dict[Identity, float]]:
    return {n: {(int(p), float(st)): float(c) for p, st, c in rows} for n, rows in raw.items()}


# ---------------------------------------------------------------- sink (Kanban)
class KanbanSink:
    """Agent-owned incident card through the hermes CLI. Never Inbox, never needs-human."""

    def __init__(self, hermes: List[str], board: str, assignee: str, runner: Optional[Callable] = None,
                 host: str = ""):
        self.hermes, self.board, self.assignee, self.host = hermes, board, assignee, host
        self.run = runner or self._run

    @staticmethod
    def _run(argv: List[str], stdin: Optional[str] = None) -> Tuple[int, str]:
        res = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=60)
        return res.returncode, res.stdout + ("\n" + res.stderr if res.stderr else "")

    def _base(self) -> List[str]:
        return list(self.hermes) + ["kanban", "--board", self.board]

    def is_open(self, task_id: str) -> Optional[bool]:
        code, out = self.run(self._base() + ["show", "--json", task_id], None)
        if code != 0:
            return None
        data = self._json(out)
        try:
            status = data["task"]["status"]  # type: ignore[index]
        except (KeyError, TypeError):
            return None
        return status not in ("done", "archived")

    @staticmethod
    def _json(out: str) -> Optional[dict]:
        dec = json.JSONDecoder()
        for i, ch in enumerate(out):
            if ch == "{":
                try:
                    val, _ = dec.raw_decode(out[i:])
                except ValueError:
                    continue
                if isinstance(val, dict):
                    return val
        return None

    def create(self, key: str, title: str, body: str) -> Optional[str]:
        argv = self._base() + ["create", title, "--body-file", "-", "--assignee", self.assignee,
                               "--idempotency-key", key, "--no-requires", "--json"]
        code, out = self.run(argv, body)
        data = self._json(out)
        if data and data.get("code") == "create_overlap":
            # Another open card looks similar (e.g. an earlier incident or an owner card): the detector's card
            # is separate work (a fresh measured episode), so say so instead of being refused.
            ids = [str(m["id"]) for m in data.get("matches", []) if m.get("id")]
            if not ids:
                return None
            extra: List[str] = []
            for i in ids:
                extra += ["--distinct-from", i]
            extra += ["--distinct-reason", "cpu-budget detector: new measured idle-CPU episode, key " + key]
            code, out = self.run(argv + extra, body)
            data = self._json(out)
        if code != 0 or not data:
            return None
        try:
            return str(data.get("id") or data.get("task_id") or data["task"]["id"])
        except (KeyError, TypeError):
            return None

    def comment(self, task_id: str, body: str) -> bool:
        code, _ = self.run(self._base() + ["comment", task_id, body], None)
        return code == 0


def format_evidence(win: dict, services: List[str], budget: float, host: str) -> str:
    per = ", ".join("%s %.1f%%" % (n, 100.0 * win["cpu"].get(n, 0.0) / win["wall_s"]) for n in services)
    return ("cpu-budget evidence (%s): window %s..%s UTC, %.0fs wall, combined %.1f%% of one core "
            "(budget %.0f%%), %d intervals all complete and idle-evidenced.\nper service: %s\nidle evidence: %s"
            % (host or "host", _iso(win["start"]), _iso(win["end"]), win["wall_s"], win["pct"], budget,
               win["intervals"], per, "; ".join(win.get("evidence", [])[:6])))


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- detector
class Detector:
    def __init__(self, config: dict, state_dir: Path, sink: Optional[KanbanSink] = None,
                 ps_fn: Callable[[], Optional[str]] = run_ps, clock: Callable[[], float] = time.time,
                 probe_registry: Optional[dict] = None, self_pid: Optional[int] = None):
        self.cfg = dict(DEFAULTS)
        self.cfg.update({k: v for k, v in config.items() if k in DEFAULTS})
        self.services = config["services"]
        self.probes = config.get("probes", [])
        self.host = config.get("host", "")
        self.key_prefix = config.get("incident_key_prefix", "cpu-budget:" + (self.host or "host"))
        self.state_dir = Path(state_dir)
        self.sink, self.ps_fn, self.clock = sink, ps_fn, clock
        self.registry, self.self_pid = probe_registry, self_pid if self_pid is not None else os.getpid()
        self.state_path = self.state_dir / "cpu-budget-state.json"

    # state ---------------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_dir), prefix=".cpu-budget-")
        with os.fdopen(fd, "w") as fh:
            json.dump(st, fh, separators=(",", ":"))
        os.replace(tmp, self.state_path)

    def _ledger(self, rec: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        day = _dt.datetime.fromtimestamp(rec["end"], _dt.timezone.utc).strftime("%Y-%m-%d")
        with open(self.state_dir / ("cpu-budget-windows-%s.jsonl" % day), "a") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    # one scheduler tick ------------------------------------------------
    def tick(self) -> dict:
        now = self.clock()
        st = self._load()
        last_ts = st.get("last_ts")
        if last_ts is not None and 0 <= now - last_ts < max(30, self.cfg["min_interval_s"]):
            return {"action": "skipped_too_soon"}
        out = self.ps_fn()
        rows = (parse_ps(out) if out else None) or None  # unparseable/empty table = no sample
        if rows is None:
            # Keep the baseline for CPU accounting, but invalidate evidence across the failed sample.
            st["last_failure"] = now
            self._save(st)
            return {"action": "ps_failed"}
        cur = snapshot(rows, self.services, self.self_pid)
        result: Dict[str, Any] = {"action": "sampled"}
        seen: Dict[str, str] = {}
        if last_ts is None or "snap" not in st or now < last_ts:
            # first run, lost state, or the wall clock stepped back: re-baseline, drop the window and streak
            evaluate_idle(self.probes, rows, now, now, self.registry, None, seen)
            st.update(last_ts=now, snap=_jsonable_snap(cur), window=None, streak=[], probe_prev=seen)
            st.pop("last_failure", None)  # failures before this fresh baseline cannot spoil its window
            self._save(st)
            return {"action": "baseline" if last_ts is None or "snap" not in st else "clock_back"}
        prev = _load_snap(st["snap"])
        acc = account_interval(prev, cur, self.services, last_ts, now, self.cfg["max_gap_s"])
        if st.pop("last_failure", None) is not None:
            acc["reasons"].append("ps_failed")
        idle, evidence = evaluate_idle(self.probes, rows, now, last_ts, self.registry, st.get("probe_prev", {}), seen)
        win = st.get("window") or {"start": last_ts, "cpu": {}, "wall_s": 0.0, "intervals": 0,
                                    "bad": [], "evidence": []}
        dt = now - last_ts
        win["wall_s"] += dt
        win["intervals"] += 1
        for name, v in acc["cpu"].items():
            win["cpu"][name] = win["cpu"].get(name, 0.0) + v
        for r in acc["reasons"]:
            if r not in win["bad"]:
                win["bad"].append(r)
        if idle != "idle" and "idle:" + idle not in win["bad"]:
            win["bad"].append("idle:" + idle)
        win["evidence"] = evidence
        st.update(last_ts=now, snap=_jsonable_snap(cur), window=win, probe_prev=seen)
        if now - win["start"] >= self.cfg["window_s"]:
            result.update(self._close_window(st, win, now))
            st["window"] = None
        self._save(st)
        return result

    def _close_window(self, st: dict, win: dict, now: float) -> dict:
        total = sum(win["cpu"].values())
        pct = 100.0 * total / win["wall_s"] if win["wall_s"] > 0 else 0.0
        complete_idle = not win["bad"] and win["wall_s"] >= self.cfg["window_s"]
        over = complete_idle and pct > self.cfg["budget_pct"]
        kind = ("idle_over" if over else "idle_ok") if complete_idle else (
            "active" if any(b == "idle:active" for b in win["bad"]) else "incomplete")
        rec = {"start": win["start"], "end": now, "wall_s": win["wall_s"], "pct": round(pct, 2), "kind": kind,
               "cpu": {k: round(v, 2) for k, v in win["cpu"].items()}, "intervals": win["intervals"],
               "bad": win["bad"], "evidence": win["evidence"]}
        self._ledger(rec)
        streak = st.get("streak", []) if over else []
        if over:
            if streak and abs(streak[-1]["end"] - win["start"]) > 2:
                streak = []
            streak.append({"start": win["start"], "end": now, "pct": round(pct, 2)})
            streak = streak[-self.cfg["consecutive"]:]
        st["streak"] = streak
        out = {"window": kind, "pct": round(pct, 2)}
        if over and len(streak) >= self.cfg["consecutive"]:
            out["incident"] = self._report(st, rec, now)
        return out

    def _report(self, st: dict, rec: dict, now: float) -> str:
        if self.sink is None:
            return "no_sink"
        inc = st.get("incident") or {}
        body = format_evidence(rec, [s["name"] for s in self.services], self.cfg["budget_pct"], self.host)
        if inc.get("id"):
            if now - inc.get("last_report", 0) < self.cfg["repeat_min_s"]:
                return "repeat_throttled"
            open_ = self.sink.is_open(inc["id"])
            if open_ is None:
                return "sink_unreachable"
            if open_:
                ok = self.sink.comment(inc["id"], "repeat: " + body)
                if ok:
                    inc["last_report"] = now
                    inc["repeats"] = inc.get("repeats", 0) + 1
                    st["incident"] = inc
                return "commented" if ok else "comment_failed"
            seq = inc.get("seq", 1) + 1  # prior card was closed: this is a new episode
        else:
            seq = 1
        key = "%s:%d" % (self.key_prefix, seq)
        title = "BabyBear %s idle CPU over budget: combined %.0f%% of one core for 2 idle ten-minute windows" % (
            self.host or "host", rec["pct"])
        task_id = self.sink.create(key, title, "Auto-detected by cpu-budget (agent-owned; no human action "
                                   "needed).\n\n" + body)
        if not task_id:
            return "create_failed"
        st["incident"] = {"id": task_id, "seq": seq, "opened": now, "last_report": now, "repeats": 0}
        return "created"


# ---------------------------------------------------------------- config + CLI
def load_config(path: str) -> dict:
    cfg = json.loads(Path(os.path.expanduser(path)).read_text())
    if not cfg.get("services"):
        raise ValueError("config needs services")
    return cfg


def build_sink(cfg: dict) -> Optional[KanbanSink]:
    s = cfg.get("sink")
    if not s:
        return None
    found: Optional[str] = None
    for cand in s.get("hermes_candidates", []):
        p = os.path.expanduser(cand)
        if os.access(p, os.X_OK):
            found = p
            break
    found = found or shutil.which("hermes")
    if not found:
        return None
    return KanbanSink([found], s.get("board", "default"), s["assignee"], host=cfg.get("host", ""))


def run_from_config(config_path: str, state_dir: str) -> dict:
    cfg = load_config(config_path)
    return Detector(cfg, Path(os.path.expanduser(state_dir)), build_sink(cfg)).tick()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="CPU budget detector tick")
    ap.add_argument("--config", required=True)
    ap.add_argument("--state-dir", required=True)
    a = ap.parse_args(argv)
    print(json.dumps(run_from_config(a.config, a.state_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
