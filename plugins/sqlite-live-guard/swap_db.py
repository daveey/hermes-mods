#!/usr/bin/env python3
"""Put a recovered SQLite database in place of a live one, safely and by one owner at a time.

    python3 swap_db.py --db ~/.hermes/kanban.db --replacement /path/recovered.db [--stop-unit hermes-gateway.service]

Steps, each of which aborts the swap (exit 2) instead of guessing:
1. Take ``<db>.recovery.lock`` (non-blocking flock). A second recovery of the same file is refused, so two
   agents can no longer swap over each other.
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


def check_replacement(live: Path, replacement: Path) -> str | None:
    try:
        con = sqlite3.connect(f"file:{replacement}?mode=ro", uri=True)
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


def swap(db: Path, replacement: Path, units: list[str], wait_s: float = 10.0) -> int:
    db = db.expanduser().absolute()
    replacement = replacement.expanduser().absolute()
    lock_path = db.with_name(db.name + ".recovery.lock")
    with open(lock_path, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"ABORT: another recovery holds {lock_path}; one owner swaps at a time", file=sys.stderr)
            return 2
        why = check_replacement(db, replacement)
        if why:
            print(f"ABORT: {why}", file=sys.stderr)
            return 2
        stopped = []
        try:
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
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
            con.close()
            print(f"swapped: {db} (integrity {rows[0] if rows else '?'}); originals kept at {keep}*")
            return 0 if rows == ["ok"] else 3
        finally:
            for unit in reversed(stopped):
                systemctl("start", unit)
            with contextlib.suppress(OSError):
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--db", required=True, type=Path, help="live database path")
    ap.add_argument("--replacement", required=True, type=Path, help="recovered database to put in place")
    ap.add_argument("--stop-unit", action="append", default=[], help="systemd --user unit to stop during the swap")
    ap.add_argument("--wait", type=float, default=10.0, help="seconds to wait for holders to close")
    a = ap.parse_args(argv)
    return swap(a.db, a.replacement, a.stop_unit, a.wait)


if __name__ == "__main__":
    sys.exit(main())
