#!/usr/bin/env python3
"""Put a recovered SQLite database in place of a live one, safely and by one owner at a time.

    python3 swap_db.py --db ~/.hermes/kanban.db --replacement /path/recovered.db [--stop-unit hermes-gateway.service]

Steps, each of which aborts the swap (exit 2) instead of guessing:
0. A Kanban board (``kanban.db``) is refused: ``hermes kanban [--board <slug>] recover`` rebuilds it in place
   through the SQLite backup API without renaming the file under open WAL connections. ``--allow-kanban`` is
   for an operator who has stopped every Kanban process and decided a rename is required.
1. Take ``<db>.recovery.lock`` (non-blocking flock; the same lock ``hermes kanban recover`` takes) and record
   the holder (pid, session, card) in ``<db>.recover.pid`` (removed on release). A second recovery of the
   same file is refused, so two agents can no longer swap over each other.
2. Check the replacement: ``PRAGMA integrity_check`` must return exactly ``ok`` and it must hold every table
   the live file declares.
3. Stop the listed systemd user units (restarted at the end, also on abort).
4. Refuse while any process has the live file or its ``-wal``/``-shm`` open (listed with pid and command line;
   a process holding *deleted* sidecars is stranded and must be restarted first).
5. Move the live file and sidecars to ``<db>.pre-swap-<UTC>{,-wal,-shm}`` (kept), then rename the replacement
   in (same filesystem: copied next to the target first), and re-check integrity of the result.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SIDECARS = ("-wal", "-shm", "-journal")


def holders(db: Path) -> list[tuple[int, str, str]]:
    """(pid, path-as-linked, cmdline) for every process with ``db`` or a sidecar open (Linux /proc)."""
    names = {str(db) + s for s in ("",) + SIDECARS}
    out = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            fds = list((proc / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if link.removesuffix(" (deleted)") in names:
                try:
                    cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")[:200]
                except OSError:
                    cmd = "?"
                out.append((int(proc.name), link, cmd))
                break
    return out


def _ro_connect(path, timeout=5):
    """Read-only open of a live WAL db. mode=ro fails (Apple SQLite) once writers deleted -wal/-shm, so fall back
    to mode=rw + PRAGMA query_only: same data, never writes, and mode=rw never creates a missing db."""
    uri = Path(path).resolve().as_posix()
    con = None
    try:
        con = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=timeout)
        con.execute("select 1 from sqlite_master limit 1")
        return con
    except sqlite3.OperationalError:
        if con is not None:
            con.close()
        con = sqlite3.connect(f"file:{uri}?mode=rw", uri=True, timeout=timeout)
        con.execute("PRAGMA query_only=ON")
        return con


def check_replacement(live: Path, replacement: Path) -> str | None:
    try:
        con = _ro_connect(replacement, timeout=5)
        try:
            rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
            if rows != ["ok"]:
                return f"replacement integrity_check: {rows[:3]}"
            have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return f"replacement is not a readable database: {exc}"
    if live.exists():
        try:
            lc = sqlite3.connect(f"file:{live}?immutable=1", uri=True)
            want = {r[0] for r in lc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            lc.close()
        except sqlite3.DatabaseError:
            want = set()  # live schema unreadable: nothing to compare against
        missing = sorted(want - have - {"lost_and_found"})
        if missing:
            return f"replacement lacks tables the live file has: {missing}"
    return None


def systemctl(action: str, unit: str) -> None:
    subprocess.run(["systemctl", "--user", action, unit], check=False, timeout=120)


def _proc_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _holder_record(db: Path) -> Path:
    return db.with_name(db.name + ".recover.pid")  # same record hermes_cli.kanban_db_recover writes


def _describe_holder(db: Path) -> str:
    try:
        info = json.loads(_holder_record(db).read_text())
    except (OSError, ValueError):
        return "holder unknown"
    if not isinstance(info, dict):
        return f"pid {info}"
    return ", ".join(f"{k} {info[v]}" for k, v in (("pid", "pid"), ("session", "session_id"), ("card", "task_id"))
                     if info.get(v))


def swap(db: Path, replacement: Path, units: list[str], wait_s: float = 10.0, allow_kanban: bool = False) -> int:
    db = db.expanduser().absolute()
    replacement = replacement.expanduser().absolute()
    if db.name == "kanban.db" and not allow_kanban:
        board = db.parent.name if db.parent.parent.name == "boards" else "default"
        cmd = "hermes kanban recover" if board == "default" else f"hermes kanban --board {board} recover"
        print(f"ABORT: {db} is a Kanban board; rebuild it in place with `{cmd}` (single-owner lock, SQLite "
              "backup-API install, loss report). --allow-kanban is for an operator who stopped every Kanban "
              "process first.", file=sys.stderr)
        return 2
    lock_path = db.with_name(db.name + ".recovery.lock")
    with open(lock_path, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"ABORT: another recovery holds {lock_path} ({_describe_holder(db)}); one owner swaps at a time",
                  file=sys.stderr)
            return 2
        with contextlib.suppress(OSError):
            _holder_record(db).write_text(json.dumps({
                "pid": os.getpid(), "proc_start": _proc_start(os.getpid()), "started_at": time.time(),
                "tool": "swap_db.py", "session_id": os.environ.get("HERMES_SESSION_ID") or None,
                "task_id": os.environ.get("HERMES_RECOVERY_HOLDER_TASK") or os.environ.get("HERMES_KANBAN_TASK")
                or None}) + "\n")
        stopped = []
        try:
            why = check_replacement(db, replacement)
            if why:
                print(f"ABORT: {why}", file=sys.stderr)
                return 2
            for unit in units:
                systemctl("stop", unit)
                stopped.append(unit)
            deadline = time.monotonic() + wait_s
            held = holders(db)
            while held and time.monotonic() < deadline:
                time.sleep(0.25)
                held = holders(db)
            if held:
                print("ABORT: the live file is still open; stop or restart these first:", file=sys.stderr)
                for pid, link, cmd in held:
                    print(f"  pid {pid} {link}  {cmd}", file=sys.stderr)
                return 2
            staged = db.with_name(f".{db.name}.swap-{os.getpid()}")
            shutil.copy2(replacement, staged)
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            keep = db.with_name(f"{db.name}.pre-swap-{stamp}")
            for s in SIDECARS:
                side = Path(str(db) + s)
                if side.exists():
                    os.replace(side, Path(str(keep) + s))
            if db.exists():
                os.replace(db, keep)
            os.replace(staged, db)
            con = _ro_connect(db, timeout=5)
            rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
            con.close()
            print(f"swapped: {db} (integrity {rows[0] if rows else '?'}); originals kept at {keep}*")
            return 0 if rows == ["ok"] else 3
        finally:
            for unit in reversed(stopped):
                systemctl("start", unit)
            with contextlib.suppress(OSError):
                _holder_record(db).unlink()
            with contextlib.suppress(OSError):
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--db", required=True, type=Path, help="live database path")
    ap.add_argument("--replacement", required=True, type=Path, help="recovered database to put in place")
    ap.add_argument("--stop-unit", action="append", default=[], help="systemd --user unit to stop during the swap")
    ap.add_argument("--wait", type=float, default=10.0, help="seconds to wait for holders to close")
    ap.add_argument("--allow-kanban", action="store_true",
                    help="swap a kanban.db anyway (operator only, every Kanban process stopped; prefer "
                         "`hermes kanban recover`)")
    a = ap.parse_args(argv)
    return swap(a.db, a.replacement, a.stop_unit, a.wait, a.allow_kanban)


if __name__ == "__main__":
    sys.exit(main())
