#!/usr/bin/env python3
"""Restart a systemd --user unit whose process writes into a deleted SQLite WAL (Linux).

A process that lost its POSIX locks on a database can have its ``-wal``/``-shm`` unlinked under it. While it only
reads, it serves stale data. Once it commits, the frames land in its private, deleted WAL; its next checkpoint copies
pages built on an old snapshot into the shared main file and corrupts the b-trees for every process (hub default
kanban.db, 2026-10-28 03:49Z). The deleted WAL holding frames (size > 0) is the signal that this is about to happen.

    stranded_restart.py --unit hermes-always-on-dashboard.service [--unit ...] [--home ~/.hermes] [--dry-run]

For each unit, every pid in its cgroup is checked. Any deleted ``*.db-wal`` under the Hermes home with frames is
copied to ``<home>/archive/stranded-wal/`` (forensics; never replayed: it is based on an old snapshot), then the unit
is restarted. Prints one JSON line. Run it every minute from cron until the in-process lock fix ships.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

DELETED = " (deleted)"


def unit_pids(unit: str, runner: Callable = subprocess.run) -> list[int]:
    out = runner(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit],
                 capture_output=True, text=True, timeout=30).stdout.strip()
    if not out:
        return []
    procs = Path("/sys/fs/cgroup") / out.lstrip("/") / "cgroup.procs"
    try:
        return [int(x) for x in procs.read_text().split()]
    except (OSError, ValueError):
        return []


def dirty_deleted_wals(pids: Iterable[int], home: str, proc: str = "/proc") -> list[dict]:
    """[{pid, fd, path, bytes}] for deleted ``*.db-wal`` files under ``home`` that still hold frames."""
    root = os.path.realpath(home).rstrip("/") + "/"
    out = []
    for pid in pids:
        fd_dir = Path(proc) / str(pid) / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if not target.endswith(DELETED):
                continue
            path = target[: -len(DELETED)]
            if not (path.startswith(root) and path.endswith(".db-wal")):
                continue
            try:
                size = os.stat(fd).st_size
            except OSError:
                continue
            if size > 0:
                out.append({"pid": pid, "fd": int(fd.name), "path": path, "bytes": size})
    return out


def run(units: list[str], home: str, dry_run: bool = False, runner: Callable = subprocess.run,
        pids_for: Optional[Callable[[str], list[int]]] = None, proc: str = "/proc") -> dict:
    pids_for = pids_for or (lambda u: unit_pids(u, runner))
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "dry_run": dry_run, "units": {}}
    archive = Path(home) / "archive" / "stranded-wal"
    for unit in units:
        pids = pids_for(unit)
        found = dirty_deleted_wals(pids, home, proc)
        entry = {"pids": len(pids), "dirty": found, "restarted": False, "archived": []}
        if found and not dry_run:
            archive.mkdir(parents=True, exist_ok=True)
            for f in found:
                dst = archive / f"{res['ts']}-{unit}-pid{f['pid']}-fd{f['fd']}-{Path(f['path']).name}"
                try:
                    shutil.copyfile(Path(proc) / str(f["pid"]) / "fd" / str(f["fd"]), dst)
                    entry["archived"].append(str(dst))
                except OSError as exc:
                    entry["archived"].append(f"copy failed: {exc}")
            r = runner(["systemctl", "--user", "restart", unit], capture_output=True, text=True, timeout=120)
            entry["restarted"] = r.returncode == 0
            entry["restart_rc"] = r.returncode
        res["units"][unit] = entry
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--unit", action="append", required=True, help="systemd --user unit to watch (repeatable)")
    ap.add_argument("--home", default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    # cron runs without the user session's bus address; systemctl --user needs it.
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    res = run(a.unit, a.home, a.dry_run)
    if any(u["dirty"] for u in res["units"].values()) or a.dry_run:
        print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
