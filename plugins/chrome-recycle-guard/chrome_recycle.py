#!/usr/bin/env python3
"""Restart the shared agent Chrome (systemd --user unit) when it has grown too large while idle (Linux).

Long-lived agent Chrome keeps renderers for days (one hub had 5-day renderers at 1-2 GB each, ~24 GB PSS in total),
which pushes the host into "memory pressure elevated" and makes the Kanban dispatcher admit one worker per tick.
Cookies live in the profile directory, so a restart keeps logins; only open tabs are lost.

    chrome_recycle.py --unit hub-chrome.service --user-data-dir ~/.hermes/chrome-agent [--dry-run]

Decision, in order (first match wins, one JSON line printed):
  hold      <home>/chrome-recycle-hold exists (a session asked for Chrome to be left alone)
  cooldown  last recycle was less than --cooldown-hours ago
  below     summed PSS of the Chrome process tree is under the threshold
            (--threshold-mb, or --pressure-threshold-mb when MemAvailable < --pressure-avail-mb)
  busy      (only when NOT under memory pressure) Chrome used more than --idle-cpu-pct of one core over
            --sample-seconds, so a page is doing work. Under pressure the idle check is skipped.
  recycle   systemctl --user restart <unit>
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

CLK = os.sysconf("SC_CLK_TCK")


def chrome_pids(user_data_dir: str, proc: Path = Path("/proc")) -> list[int]:
    needle = f"--user-data-dir={user_data_dir}".encode()
    out = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            # Chrome rewrites argv into one space-joined string, so split on NUL and spaces.
            if needle in (d / "cmdline").read_bytes().replace(b"\0", b" ").split(b" "):
                out.append(int(d.name))
        except OSError:
            pass
    return out


def pss_mb(pid: int, proc: Path = Path("/proc")) -> float:
    try:
        for line in (proc / str(pid) / "smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def cpu_ticks(pid: int, proc: Path = Path("/proc")) -> int:
    try:
        rest = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return int(rest[11]) + int(rest[12])  # utime + stime
    except (OSError, IndexError, ValueError):
        return 0


def mem_available_mb(meminfo: Path = Path("/proc/meminfo")) -> float:
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024
    return float("inf")


def decide(args, *, now: float, pids: list[int], total_pss: float, avail_mb: float,
           cpu_pct: Optional[float], last_recycle: float, held: bool) -> tuple[str, dict]:
    pressure = avail_mb < args.pressure_avail_mb
    threshold = args.pressure_threshold_mb if pressure else args.threshold_mb
    info = {"pss_mb": round(total_pss), "procs": len(pids), "avail_mb": round(avail_mb), "threshold_mb": threshold,
            "pressure": pressure}
    if held:
        return "hold", info
    if now - last_recycle < args.cooldown_hours * 3600:
        return "cooldown", info
    if not pids or total_pss < threshold:
        return "below", info
    info["cpu_pct"] = None if cpu_pct is None else round(cpu_pct, 1)
    if pressure:  # the host is starving the dispatcher: lost tabs cost less than blocked workers
        return "recycle", info
    if cpu_pct is None or cpu_pct > args.idle_cpu_pct:
        return "busy", info
    return "recycle", info


def main(argv=None, *, sleep: Callable[[float], None] = time.sleep,
         runner: Callable[..., object] = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", default="hub-chrome.service")
    ap.add_argument("--user-data-dir", default=str(Path.home() / ".hermes" / "chrome-agent"))
    ap.add_argument("--home", default=str(Path.home() / ".hermes"))
    ap.add_argument("--threshold-mb", type=float, default=12000)
    ap.add_argument("--pressure-threshold-mb", type=float, default=3000)
    ap.add_argument("--pressure-avail-mb", type=float, default=8000)
    ap.add_argument("--idle-cpu-pct", type=float, default=25)
    ap.add_argument("--sample-seconds", type=float, default=20)
    ap.add_argument("--cooldown-hours", type=float, default=6)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    home = Path(args.home)
    state = home / "state" / "chrome-recycle.json"
    try:
        last = float(json.loads(state.read_text()).get("last_recycle", 0))
    except (OSError, ValueError):
        last = 0.0

    pids = chrome_pids(args.user_data_dir)
    total = sum(pss_mb(p) for p in pids)
    avail = mem_available_mb()
    held = (home / "chrome-recycle-hold").exists()
    cpu_pct = None
    verdict, info = decide(args, now=time.time(), pids=pids, total_pss=total, avail_mb=avail, cpu_pct=0.0,
                           last_recycle=last, held=held)
    if verdict == "recycle":  # only now pay for the CPU sample
        before = {p: cpu_ticks(p) for p in pids}
        sleep(args.sample_seconds)
        after = {p: cpu_ticks(p) for p in chrome_pids(args.user_data_dir)}
        used = sum(max(0, after[p] - before.get(p, after[p])) for p in after) / CLK
        cpu_pct = 100 * used / args.sample_seconds
        verdict, info = decide(args, now=time.time(), pids=pids, total_pss=total, avail_mb=mem_available_mb(),
                               cpu_pct=cpu_pct, last_recycle=last, held=held)
    result = {"verdict": verdict, **info, "unit": args.unit, "dry_run": args.dry_run}
    if verdict == "recycle" and not args.dry_run:
        r = runner(["systemctl", "--user", "restart", args.unit], capture_output=True, text=True)
        result["rc"] = getattr(r, "returncode", 0)
        if result["rc"] == 0:
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"last_recycle": time.time(), "pss_mb": round(total)}))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
