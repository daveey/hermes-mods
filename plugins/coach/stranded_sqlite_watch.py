#!/usr/bin/env python3
"""Detect processes stranded on deleted SQLite WAL/SHM sidecars (Linux).

A process that loses POSIX locks can retain a stale WAL generation, but deleted descriptors alone do not prove
that cause or any read failure. This scans /proc/*/fd for deleted kanban.db/state.db WAL/SHM under HERMES_HOME
and files ONE Kanban card per (host, pid, pid start time, db). Entire absent profile homes are excluded as
retired-reader cleanup, not live-database restart incidents.

    stranded_sqlite_watch.py [--dry-run] [--json] [--home PATH]

--dry-run prints the findings JSON and creates nothing. Exit 0 unless the scan itself crashes.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

SIDECARS = ("kanban.db-wal", "kanban.db-shm", "state.db-wal", "state.db-shm")
DELETED = " (deleted)"
CMDLINE_MAX = 200


def hermes_home(home: Optional[str] = None) -> Path:
    return Path(home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _starttime(pid: int, proc: Path) -> Optional[int]:
    """Field 22 of /proc/<pid>/stat (clock ticks since boot): with the pid it names one process lifetime."""
    try:
        raw = (proc / str(pid) / "stat").read_text()
        return int(raw[raw.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _cmdline(pid: int, proc: Path) -> str:
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    s = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    return s if len(s) <= CMDLINE_MAX else s[:CMDLINE_MAX - 1] + "…"


def scan(home: Optional[str] = None, proc: str = "/proc") -> list:
    """-> [{pid, starttime, cmdline, db, sidecars: [{path, fd, deleted_ctime}]}], one per (pid, db), sorted."""
    root = str(hermes_home(home).resolve()).rstrip("/") + "/"
    procp = Path(proc)
    found: dict = {}
    me = os.getpid()
    for entry in procp.iterdir() if procp.is_dir() else []:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        pid = int(entry.name)
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:  # gone, or not ours
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if not target.endswith(DELETED):
                continue
            path = target[:-len(DELETED)]
            if not path.startswith(root) or not path.endswith(SIDECARS):
                continue
            db = path.rsplit("-", 1)[0]
            relative = Path(db).relative_to(root).parts
            if len(relative) >= 3 and relative[0] == "profiles":
                # Entire absent profile (tombstoned or otherwise retired): retained readers
                # need lifecycle cleanup, not a live-db corruption/restart incident.
                # Do not suppress a missing DB inside a still-existing profile home.
                try:
                    (Path(root) / "profiles" / relative[1]).stat()
                except FileNotFoundError:
                    continue
                except OSError:
                    pass  # unknown ownership state: retain the finding
            try:
                ctime = os.stat(fd).st_ctime  # follows the fd to the unlinked inode: ctime ~ when it was deleted
            except OSError:
                ctime = None
            rec = found.get((pid, db))
            if rec is None:
                rec = found[(pid, db)] = {"pid": pid, "starttime": _starttime(pid, procp),
                                          "cmdline": _cmdline(pid, procp), "db": db, "sidecars": []}
            rec["sidecars"].append({"path": path, "fd": int(fd.name), "deleted_ctime": ctime})
    return [found[k] for k in sorted(found)]


def card_key(host: str, f: dict) -> str:
    return f"stranded-sqlite:{host}:{f['pid']}:{f['starttime']}:{Path(f['db']).name}"


def card_body(host: str, f: dict) -> str:
    side = "\n".join(
        f"- fd {s['fd']}: `{s['path']} (deleted)`, inode ctime "
        f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(s['deleted_ctime'])) if s['deleted_ctime'] else '?'}"
        for s in f["sidecars"])
    return f"""Detected by stranded_sqlite_watch.py on {host}.

## Evidence
- pid {f['pid']} (start time {f['starttime']} ticks since boot)
- cmdline: `{f['cmdline']}`
- db: `{f['db']}`
{side}

## What it means
This process still holds deleted WAL/SHM sidecars. This can indicate a stale generation for a live database,
but the descriptor evidence alone does not establish read failures, corruption, the deleting actor, or POSIX
lock loss. Entire absent profile homes are excluded as retired-reader cleanup; recheck ownership before acting.

## Remediation
1. Do NOT touch the live db or its sidecars (no delete, copy-over or checkpoint by hand).
2. Verify that the profile/database is still live and reproduce any read error safely. For an absent retired
   profile, close/evict cached readers rather than requesting a service restart.
3. If live reads are stranded, gracefully restart only the affected process when idle (no running turn/worker),
   then confirm it no longer holds deleted sidecars.
4. If POSIX lock loss is independently established for Kanban, inspect the kanban OFD lock guard
   (`hermes_cli/kanban_lockguard.py`) and raw db opens. Retired-reader lifecycle is a separate failure class.
"""


def create_argv(host: str, f: dict) -> list:
    return ["hermes", "kanban", "create", "--assignee", "default", "--idempotency-key", card_key(host, f),
            "--created-by", "stranded-sqlite-watch", "--body-file", "-", "--json",
            f"Process {f['pid']} stranded on deleted {Path(f['db']).name} WAL/SHM"]


def file_cards(findings: list, host: str, runner: Optional[Callable] = None) -> list:
    """One `hermes kanban create` per finding; the idempotency key returns the existing card for a repeat."""
    run = runner or subprocess.run
    out = []
    for f in findings:
        r = run(create_argv(host, f), input=card_body(host, f), capture_output=True, text=True, timeout=120)
        out.append({"key": card_key(host, f), "rc": r.returncode, "out": ((r.stdout or "") + (r.stderr or ""))[-300:]})
    return out


def main(argv=None, runner: Optional[Callable] = None, proc: str = "/proc") -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print findings JSON, create no card")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--home", help="HERMES_HOME to scan (default $HERMES_HOME or ~/.hermes)")
    a = ap.parse_args(argv)
    host = socket.gethostname().split(".")[0]
    findings = scan(a.home, proc)
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": host,
           "home": str(hermes_home(a.home)), "dry_run": a.dry_run, "findings": findings, "cards": []}
    if findings and not a.dry_run:
        res["cards"] = file_cards(findings, host, runner)
    if a.json or a.dry_run:
        print(json.dumps(res))
    else:
        print(f"{res['ts']} {host}: {len(findings)} stranded" +
              "".join(f"\n  pid {f['pid']} {f['db']} -> {c['out'].strip()[-80:]}"
                      for f, c in zip(findings, res["cards"] or [{"out": "(not filed)"}] * len(findings))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
