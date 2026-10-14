#!/usr/bin/env python3
"""Edit the user crontab one line at a time, never as a whole-file rewrite.

Agents used to install the crontab with `(crontab -l | ...; echo LINE) | crontab -`. When two
sessions do that at once, the second write drops the first one's line. This helper is the only
sanctioned writer (the crontab-guard hooks block everything else). It:

- takes a host-wide lock (mkdir), then reads the live crontab inside the lock, so writes serialize;
- changes exactly the lines you name and keeps every other line byte-for-byte;
- saves the previous crontab to ~/.hermes/state/crontab-backups/ before each write (last 100 kept).

Usage:
  crontab_line.py list
  crontab_line.py add 'SCHEDULE COMMAND'          # no-op if the identical line exists
  crontab_line.py remove SUBSTRING [--all]        # must match exactly 1 line unless --all
  crontab_line.py replace SUBSTRING 'NEW LINE'    # swap the one line matching SUBSTRING
  crontab_line.py restore [BACKUP_FILE]           # re-add lines from a backup (default: newest);
                                                  # never removes lines that exist now
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

CRONTAB = os.environ.get("CRONTAB_GUARD_BIN", "/usr/bin/crontab")
STATE = Path(os.environ.get("CRONTAB_GUARD_STATE", Path.home() / ".hermes/state"))
LOCK = STATE / "crontab.lock"
BACKUPS = STATE / "crontab-backups"
KEEP = 100


class Locked:
    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + 30
        while True:
            try:
                LOCK.mkdir()
                return self
            except FileExistsError:
                # Break a lock left behind by a crashed writer (older than 2 min).
                try:
                    if time.time() - LOCK.stat().st_mtime > 120:
                        LOCK.rmdir()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise SystemExit("crontab_line: lock busy for 30s (another session is editing)")
                time.sleep(0.2)

    def __exit__(self, *exc):
        try:
            LOCK.rmdir()
        except FileNotFoundError:
            pass


def read() -> list[str]:
    out = subprocess.run([CRONTAB, "-l"], capture_output=True, text=True)
    if out.returncode != 0:
        if "no crontab" in out.stderr.lower():
            return []
        raise SystemExit(f"crontab_line: crontab -l failed: {out.stderr.strip()}")
    return out.stdout.splitlines()


def write(old: list[str], new: list[str]) -> None:
    if new == old:
        print("crontab unchanged")
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    (BACKUPS / f"crontab-{stamp}.txt").write_text("\n".join(old) + ("\n" if old else ""))
    for stale in sorted(BACKUPS.glob("crontab-*.txt"))[:-KEEP]:
        stale.unlink()
    text = "\n".join(new) + "\n" if new else ""
    res = subprocess.run([CRONTAB, "-"], input=text, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"crontab_line: install failed: {res.stderr.strip()}")
    if read() != new:
        raise SystemExit("crontab_line: readback mismatch after install")
    removed = [line for line in old if line not in new]
    added = [line for line in new if line not in old]
    for line in removed:
        print(f"- {line}")
    for line in added:
        print(f"+ {line}")


def matches(lines: list[str], sub: str) -> list[int]:
    return [i for i, line in enumerate(lines) if sub in line and not line.lstrip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("line")
    r = sub.add_parser("remove")
    r.add_argument("match")
    r.add_argument("--all", action="store_true")
    rp = sub.add_parser("replace")
    rp.add_argument("match")
    rp.add_argument("line")
    rs = sub.add_parser("restore")
    rs.add_argument("backup", nargs="?")
    args = p.parse_args(argv)

    if args.cmd == "list":
        print("\n".join(read()))
        return 0
    with Locked():
        old = read()
        new = list(old)
        if args.cmd == "add":
            line = args.line.strip()
            if "\n" in line:
                raise SystemExit("crontab_line: one line at a time")
            if line not in new:
                new.append(line)
        elif args.cmd == "remove":
            hits = matches(new, args.match)
            if not hits:
                raise SystemExit(f"crontab_line: no line contains {args.match!r}")
            if len(hits) > 1 and not args.all:
                raise SystemExit(f"crontab_line: {len(hits)} lines contain {args.match!r}; narrow it or pass --all")
            new = [line for i, line in enumerate(new) if i not in hits]
        elif args.cmd == "replace":
            hits = matches(new, args.match)
            if len(hits) != 1:
                raise SystemExit(f"crontab_line: replace needs exactly 1 match for {args.match!r}, found {len(hits)}")
            new[hits[0]] = args.line.strip()
        elif args.cmd == "restore":
            files = sorted(BACKUPS.glob("crontab-*.txt"))
            src = Path(args.backup) if args.backup else (files[-1] if files else None)
            if not src or not src.exists():
                raise SystemExit("crontab_line: no backup to restore")
            for line in src.read_text().splitlines():
                if line.strip() and line not in new:
                    new.append(line)
        write(old, new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
