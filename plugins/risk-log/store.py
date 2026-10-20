"""Append-only 0600 SQLite log of high-risk Hermes tool use, written by one background thread.

The hook only enqueues (tool name, args reference, ids, origin); this thread classifies and writes, so a tool call
never waits on SQLite. Path: $HERMES_RISK_LOG, else <root hermes home>/risk/tool-risk.sqlite (one file per host,
shared by every profile; the profile is a column).
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

try:
    from . import classify as _classify, redact as _redact
except ImportError:  # loaded without a parent package (tests, hyphenated dir)
    import importlib.util
    import sys

    def _load(name):
        key = "risk_log_" + name
        if key in sys.modules:
            return sys.modules[key]
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return sys.modules.setdefault(key, mod)

    _classify, _redact = _load("classify"), _load("redact")

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_events(
  id TEXT PRIMARY KEY, at REAL NOT NULL, host TEXT NOT NULL, profile TEXT, session_id TEXT, task_id TEXT,
  tool TEXT NOT NULL, risk_class TEXT NOT NULL, level TEXT NOT NULL, origin TEXT NOT NULL, platform TEXT,
  cmd_sha256 TEXT, cmd_prefix TEXT, detail TEXT NOT NULL DEFAULT '{}', status TEXT, duration_ms INTEGER);
CREATE INDEX IF NOT EXISTS risk_events_at ON risk_events(at);
"""
QUEUE_MAX = 2000
_HOST: list = []


def host_name() -> str:
    """Stable host label. gethostname() follows DHCP/VPN on macOS (a MacBook flips between its name and "Mac"),
    so prefer $HERMES_RISK_HOST, then the macOS LocalHostName, then gethostname()."""
    if not _HOST:
        name = os.environ.get("HERMES_RISK_HOST", "").strip()
        if not name and sys.platform == "darwin":
            try:
                import subprocess
                name = subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"], capture_output=True,
                                      text=True, timeout=2).stdout.strip()
            except Exception:  # noqa: BLE001
                name = ""
        _HOST.append(name or socket.gethostname().split(".")[0])
    return _HOST[0]


def default_path() -> Path:
    env = os.environ.get("HERMES_RISK_LOG")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    if home.parent.name == "profiles":  # a profile home -> the host's root home
        home = home.parent.parent
    return home / "risk" / "tool-risk.sqlite"


def connect(path: Optional[Path] = None, timeout: float = 2.0) -> sqlite3.Connection:
    path = Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o600)
    db = sqlite3.connect(str(path), timeout=timeout, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    for suffix in ("-wal", "-shm"):
        side = Path(str(path) + suffix)
        if side.exists() and side.stat().st_mode & 0o077:
            os.chmod(side, 0o600)
    return db


def rows_for(event: dict, detector=None) -> list[dict]:
    """Classify one queued event into stored rows (no raw command text survives)."""
    findings = _classify.classify(event["tool"], event.get("args"), detector)
    out = []
    for n, f in enumerate(findings):
        text = f.text or ""
        out.append({
            "id": "%s:%s:%d" % (event.get("call_id") or "%s-%f" % (event["tool"], event["at"]), f.risk_class, n),
            "at": event["at"], "host": event.get("host") or host_name(),
            "profile": event.get("profile"), "session_id": event.get("session_id"), "task_id": event.get("task_id"),
            "tool": event["tool"][:120], "risk_class": f.risk_class, "level": f.level,
            "origin": event.get("origin") or "unknown", "platform": event.get("platform"),
            "cmd_sha256": _redact.sha256(text) if text else None,
            "cmd_prefix": _redact.prefix(text) if text else None,
            "detail": json.dumps(f.detail, sort_keys=True)[:1000],
            "status": event.get("status"), "duration_ms": event.get("duration_ms"),
        })
    return out


def write_rows(db: sqlite3.Connection, rows: list[dict]) -> None:
    for r in rows:
        db.execute(
            """INSERT INTO risk_events(id,at,host,profile,session_id,task_id,tool,risk_class,level,origin,platform,
               cmd_sha256,cmd_prefix,detail,status,duration_ms) VALUES(:id,:at,:host,:profile,:session_id,:task_id,
               :tool,:risk_class,:level,:origin,:platform,:cmd_sha256,:cmd_prefix,:detail,:status,:duration_ms)
               ON CONFLICT(id) DO UPDATE SET status=COALESCE(excluded.status, risk_events.status),
               duration_ms=COALESCE(excluded.duration_ms, risk_events.duration_ms)""", r)


class Writer:
    """Bounded queue + one daemon thread. ``submit`` never blocks and never raises."""

    def __init__(self, path: Optional[Path] = None, detector=None):
        self.path, self.detector = path, detector
        self.queue: "queue.Queue[Optional[dict]]" = queue.Queue(QUEUE_MAX)
        self.dropped = 0
        self.errors = 0
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def submit(self, event: dict) -> None:
        try:
            if self._thread is None:
                self._start()
            self.queue.put_nowait(event)
        except Exception:  # noqa: BLE001 - full queue or thread start failure: drop, never affect the call
            self.dropped += 1

    def _start(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="risk-log-writer", daemon=True)
                self._thread.start()
                atexit.register(self.flush, 1.0)

    def _detector(self):
        if self.detector is None:
            try:
                from tools.approval_detection import detect_dangerous_command
                self.detector = detect_dangerous_command
            except Exception:  # noqa: BLE001 - outside Hermes (tests, CLI): no dangerous-pattern class
                self.detector = False
        return self.detector or None

    def _run(self) -> None:
        db = None
        while True:
            event = self.queue.get()
            try:
                if event is None:
                    continue
                rows = rows_for(event, self._detector())
                if rows:
                    if db is None:
                        db = connect(self.path)
                    write_rows(db, rows)
            except Exception:  # noqa: BLE001 - observability only
                self.errors += 1
                db = None
            finally:
                self.queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while self.queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self.queue.unfinished_tasks


# ── export / stats CLI ────────────────────────────────────────────────────

def export(since: float, path: Optional[Path] = None, prefix: bool = False) -> list[dict]:
    """Rows newer than ``since``. ``prefix=False`` (the default for shipping off-host) drops the command prefix."""
    with connect(path) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM risk_events WHERE at >= ? ORDER BY at", (since,))]
    if not prefix:
        for r in rows:
            r.pop("cmd_prefix", None)
    return rows


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Hermes high-risk tool-use log")
    parser.add_argument("--db", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="JSONL rows (no command prefix unless --prefix)")
    e.add_argument("--days", type=float, default=8)
    e.add_argument("--prefix", action="store_true")
    s = sub.add_parser("stats")
    s.add_argument("--days", type=float, default=7)
    sub.add_parser("path")
    args = parser.parse_args(argv)
    if args.cmd == "path":
        print(args.db or default_path())
        return 0
    rows = export(time.time() - args.days * 86400, args.db, prefix=getattr(args, "prefix", False))
    if args.cmd == "export":
        for r in rows:
            print(json.dumps(r, sort_keys=True))
        return 0
    counts: dict = {}
    for r in rows:
        key = (r["risk_class"], r["origin"].split(":", 1)[0])
        counts[key] = counts.get(key, 0) + 1
    print("high-risk tool use, last %g days: %d rows" % (args.days, len(rows)))
    for (cls, origin), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("  %-18s %-12s %d" % (cls, origin, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
