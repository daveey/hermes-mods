"""hub-status: is this host's gateway up, restarting or down, since when, and its recent history.

Runs inside the always-on dashboard backend (a separate process from the gateway), so it keeps
answering while the gateway restarts. Sources, all already written by core:

- ``$HERMES_HOME/gateway_state.json``: ``gateway_state`` (starting | running | draining | degraded |
  stopped | startup_failed), ``pid``, ``start_time`` (process start in clock ticks since boot),
  ``restart_requested``, ``exit_reason``.
- the gateway pid itself (alive? same process as recorded?), and on systemd hosts
  ``systemctl --user is-active hermes-gateway.service`` (activating/deactivating = restarting).

History: one sampler per HERMES_HOME (an flock picks it among backends) records only state
*transitions* to ``$HERMES_HOME/hub-status/events.jsonl`` every SAMPLE_S, plus a heartbeat file.
A heartbeat older than OFFLINE_GAP_S when the sampler starts means nothing was watching (the hub
itself or its dashboard was down): that span is recorded as ``down``. Events older than
RETAIN_S are trimmed (the last one before the cutoff is kept so the window starts in a known state).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

UP, RESTARTING, DOWN = "up", "restarting", "down"
STATES = (UP, RESTARTING, DOWN)
SAMPLE_S = 10
RESTART_GRACE_S = 300          # a restart still unfinished after this is reported down
OFFLINE_GAP_S = 60             # heartbeat gap that counts as "nobody was watching"
RETAIN_S = 30 * 86400
UNIT = "hermes-gateway.service"
STUCK = "restart did not finish within 5 min"
OFFLINE = "hub offline (status monitor not running)"


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


# ── process facts ─────────────────────────────────────────────────────────────

def _linux_boot_time() -> Optional[float]:
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except OSError:
        return None
    return None


def _linux_start_ticks(pid: int) -> Optional[int]:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return int(raw.rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def process_start_epoch(pid: int, ticks: Optional[int] = None) -> Optional[float]:
    """Wall-clock start of ``pid``; ``ticks`` (from gateway_state.json) wins when given on Linux."""
    if sys.platform.startswith("linux"):
        boot = _linux_boot_time()
        t = ticks if isinstance(ticks, int) and ticks > 0 else _linux_start_ticks(pid)
        if boot is not None and t is not None:
            return boot + t / os.sysconf("SC_CLK_TCK")
        return None
    try:
        out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
        return time.time() - _parse_etime(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def _parse_etime(s: str) -> int:
    """ps etime: [[dd-]hh:]mm:ss."""
    days, _, rest = s.rpartition("-")
    parts = [int(p) for p in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts
    return (int(days) if days else 0) * 86400 + h * 3600 + m * 60 + sec


def pid_alive(pid: Any, ticks: Any = None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass
    except OSError:
        return False
    if sys.platform.startswith("linux") and isinstance(ticks, int) and ticks > 0:
        cur = _linux_start_ticks(pid)
        if cur is not None and abs(cur - ticks) > 2:  # pid reused by another process
            return False
    return True


def unit_state() -> Optional[str]:
    if not sys.platform.startswith("linux") or not shutil.which("systemctl"):
        return None
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True, timeout=2)
        return out.stdout.strip() or None
    except Exception:
        return None


def read_runtime(home: Path) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads((home / "gateway_state.json").read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ── pure classification ───────────────────────────────────────────────────────

def classify(runtime: Optional[Dict[str, Any]], alive: bool, unit: Optional[str] = None) -> Tuple[str, str]:
    """(state, reason) from the gateway's own record, whether its pid lives, and systemd's view."""
    if unit in ("activating", "deactivating", "reloading"):
        return RESTARTING, f"systemd {unit}"
    if not runtime:
        return DOWN, "no gateway state file"
    gs = str(runtime.get("gateway_state") or "")
    exit_reason = runtime.get("exit_reason")
    restart = bool(runtime.get("restart_requested")) or "restart" in str(exit_reason or "").lower()
    if gs in ("running", "degraded"):
        return (UP, gs) if alive else (DOWN, "gateway process is gone")
    if gs == "starting":
        return RESTARTING, "starting"
    if gs == "draining":
        return (RESTARTING, "draining") if alive or restart else (DOWN, "draining, process gone")
    if gs == "stopped" and restart:
        return RESTARTING, "stopped for restart"
    return DOWN, str(exit_reason or gs or "unknown")


def escalate(state: str, reason: str, last: Optional[Dict[str, Any]], now: float) -> Tuple[str, str]:
    """A restart still running after RESTART_GRACE_S is a down hub, not a slow restart."""
    if state != RESTARTING or not last:
        return state, reason
    if last.get("state") == RESTARTING and now - float(last["ts"]) > RESTART_GRACE_S:
        return DOWN, STUCK
    if last.get("state") == DOWN and last.get("reason") == STUCK:
        return DOWN, STUCK
    return state, reason


# ── event log ─────────────────────────────────────────────────────────────────

class EventLog:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "events.jsonl"
        self.heartbeat = root / "heartbeat"
        self._trimmed_at = 0.0

    def read(self) -> List[Dict[str, Any]]:
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("ts"), (int, float)) and e.get("state") in STATES:
                out.append(e)
        out.sort(key=lambda e: e["ts"])
        return out

    def last(self) -> Optional[Dict[str, Any]]:
        ev = self.read()
        return ev[-1] if ev else None

    def append(self, event: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")

    def beat(self, now: float) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.heartbeat.write_text(f"{now:.0f}\n")

    def last_beat(self) -> Optional[float]:
        try:
            return float(self.heartbeat.read_text().strip())
        except (OSError, ValueError):
            return None

    def trim(self, now: float, force: bool = False) -> None:
        if not force and now - self._trimmed_at < 3600:
            return
        self._trimmed_at = now
        ev = self.read()
        cutoff = now - RETAIN_S
        old = [e for e in ev if e["ts"] < cutoff]
        if not old:
            return
        keep = [old[-1]] + [e for e in ev if e["ts"] >= cutoff]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in keep))
        os.replace(tmp, self.path)


def observe(log: EventLog, now: float, state: str, reason: str, pid: Any, started: Optional[float],
            first: bool = False) -> List[Dict[str, Any]]:
    """Record one sample; returns the events it appended (only transitions are written)."""
    added: List[Dict[str, Any]] = []
    hb = log.last_beat()
    last = log.last()
    if first and hb is not None and now - hb > OFFLINE_GAP_S:
        e = {"ts": round(hb, 1), "state": DOWN, "reason": OFFLINE, "pid": None}
        log.append(e)
        added.append(e)
        last = e
    state, reason = escalate(state, reason, last, now)
    if state == UP and last and last.get("state") == UP and last.get("pid") not in (None, pid):
        # A restart quick enough to fall between two samples: still show it.
        ts = hb if hb is not None else (started or now)
        if started:
            ts = min(ts, started)
        e = {"ts": round(ts, 1), "state": RESTARTING, "reason": "restarted between samples", "pid": None}
        log.append(e)
        added.append(e)
        last = e
    if not last or last.get("state") != state or (state == UP and last.get("pid") != pid):
        ts = started if state == UP and started and (not last or started >= last["ts"]) else now
        e = {"ts": round(ts, 1), "state": state, "reason": reason, "pid": pid}
        log.append(e)
        added.append(e)
    log.beat(now)
    log.trim(now)
    return added


def window(events: List[Dict[str, Any]], start: float) -> List[Dict[str, Any]]:
    """Events from ``start`` on, led by the state in force at ``start`` (clipped to it)."""
    before = [e for e in events if e["ts"] < start]
    inside = [e for e in events if e["ts"] >= start]
    lead = [dict(before[-1], ts=start, clipped=True)] if before else []
    return lead + inside


# ── live state + sampler ──────────────────────────────────────────────────────

def sample(home: Path, now: Optional[float] = None,
           unit_fn: Callable[[], Optional[str]] = unit_state) -> Dict[str, Any]:
    now = time.time() if now is None else now
    rt = read_runtime(home)
    pid = rt.get("pid") if rt else None
    ticks = rt.get("start_time") if rt else None
    alive = pid_alive(pid, ticks)
    state, reason = classify(rt, alive, unit_fn())
    started = process_start_epoch(pid, ticks) if alive and isinstance(pid, int) else None
    return {"state": state, "reason": reason, "pid": pid if alive else None, "started": started,
            "gateway_state": rt.get("gateway_state") if rt else None, "now": now}


def status(home: Optional[Path] = None, now: Optional[float] = None,
           unit_fn: Callable[[], Optional[str]] = unit_state) -> Dict[str, Any]:
    home = home or hermes_home()
    s = sample(home, now, unit_fn)
    last = EventLog(home / "hub-status").last()
    state, reason = escalate(s["state"], s["reason"], last, s["now"])
    if state == UP and s["started"]:
        since = s["started"]
    elif last and last.get("state") == state:
        since = last["ts"]
    else:
        since = s["now"]
    return {"api": 1, "state": state, "reason": reason, "since": since, "now": s["now"], "pid": s["pid"],
            "gateway_state": s["gateway_state"], "sampler": _SAMPLER.running}


def events(range_s: int, home: Optional[Path] = None, now: Optional[float] = None) -> Dict[str, Any]:
    home = home or hermes_home()
    now = time.time() if now is None else now
    return {"api": 1, "now": now, "start": now - range_s,
            "events": window(EventLog(home / "hub-status").read(), now - range_s)}


class _Sampler:
    def __init__(self):
        self.running = False
        self._fd: Optional[int] = None
        self._mu = threading.Lock()

    def ensure(self, home: Optional[Path] = None) -> bool:
        with self._mu:
            if self.running:
                return True
            home = home or hermes_home()
            root = home / "hub-status"
            try:
                import fcntl
                root.mkdir(parents=True, exist_ok=True)
                fd = os.open(root / "sampler.lock", os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)
                    return False  # another backend on this home is the sampler
            except (OSError, ImportError):
                return False
            self._fd = fd
            self.running = True
            threading.Thread(target=self._loop, args=(home,), name="hub-status-sampler", daemon=True).start()
            return True

    def _loop(self, home: Path) -> None:
        log = EventLog(home / "hub-status")
        first = True
        while True:
            try:
                s = sample(home)
                observe(log, s["now"], s["state"], s["reason"], s["pid"], s["started"], first=first)
                first = False
            except Exception:
                pass
            time.sleep(SAMPLE_S)


_SAMPLER = _Sampler()
