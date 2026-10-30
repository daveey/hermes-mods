#!/usr/bin/env python3
"""Evidence for "why is this machine saturated / out of disk", plus the few fixes an agent may apply alone.

Runs ON the machine being investigated (stdlib only; macOS and Linux).

  collect.py report [--hours 24] [--no-du]     JSON: load, memory, top processes with parent chain and the
                                               agent (card/session) that started each, disk hogs, recorder spikes
  collect.py clean [--apply]                   allowlisted cache cleanups; dry run unless --apply; before/after
  collect.py kill PID [--apply]                kill a process only if it belongs to a finished Kanban card
  collect.py install-recorder [--apply]        macOS: load mac-load-recorder's launchd job if the script is here
                                               but the job is not (threshold scaled to this machine's cores)

Anything outside these allowlists is reported with a size or owner and left for a human decision.
"""
from __future__ import annotations

import argparse
import ctypes
from concurrent.futures import ThreadPoolExecutor
import json
import os
import platform
import plistlib
import shlex
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
MAC = platform.system() == "Darwin"
TOP_N = 20
MIN_CPU = 3.0
TAG_KEYS = (
    "HERMES_OWNER_CARD", "XPC_SERVICE_NAME",
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_KANBAN_BOARD", "HERMES_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "BU_NAME",
)
TASK_KEYS = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK")
TAG_RE = re.compile(r"(?:^|\s)(" + "|".join(TAG_KEYS) + r")=(\S+)")
FINISHED = ("done", "archived", "blocked")
GUI_APP = re.compile(r"\.app/Contents/MacOS/")
GATEWAY = re.compile(r"\b(gateway|serve|dashboard)\b")


# ───────────────────────── processes ─────────────────────────
def ps_table() -> dict[int, dict]:
    out = subprocess.run(["ps", "-Ao", "pid=,ppid=,pcpu=,rss=,etime=,args="],
                         capture_output=True, text=True, check=True).stdout
    table = parse_ps(out)
    annotate_processes(table)
    return table


def annotate_processes(table: dict, proc_root=Path("/proc")) -> None:
    """Read cwd and supervisor identity once per snapshot; never stop or change a job."""
    if MAC:
        try:
            out = subprocess.run(["/usr/sbin/lsof", "-a", "-d", "cwd", "-Fpn"],
                                 capture_output=True, text=True, timeout=15).stdout
            pid = None
            for line in out.splitlines():
                if line.startswith("p") and line[1:].isdigit():
                    pid = int(line[1:])
                elif line.startswith("n") and pid in table:
                    table[pid]["cwd"] = line[1:]
        except (OSError, subprocess.SubprocessError):
            pass
        loaded = set()
        try:
            out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                fields = line.split()
                if len(fields) != 3 or fields[0] == "PID":
                    continue
                loaded.add(fields[2])
                if fields[0].isdigit() and int(fields[0]) in table:
                    table[int(fields[0])].update(service=fields[2], service_manager="launchd")
        except (OSError, subprocess.SubprocessError):
            pass
        # Jobs can be loaded without a PID in list. Match complete ProgramArguments, not script basenames.
        for root in (HOME / "Library/LaunchAgents", Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")):
            for path in root.glob("*.plist"):
                try:
                    with path.open("rb") as f:
                        job = plistlib.load(f)
                    label_ = job.get("Label")
                    argv = job.get("ProgramArguments") or [job.get("Program")]
                    if label_ not in loaded or not argv or not all(isinstance(a, str) for a in argv):
                        continue
                    for row in table.values():
                        if row["ppid"] == 1 and row["args"] in (" ".join(argv), shlex.join(argv)):
                            row.update(service=label_, service_manager="launchd")
                except (OSError, ValueError, plistlib.InvalidFileException):
                    continue
    else:
        for pid, row in table.items():
            try:
                row["cwd"] = str((proc_root / str(pid) / "cwd").resolve(strict=True))
            except OSError:
                pass
            try:
                groups = (proc_root / str(pid) / "cgroup").read_text()
                units = [u for u in re.findall(r"(?:^|/)([^/\n]+\.service)(?=/|$)", groups, re.M)
                         if not u.startswith("user@")]
                if units:
                    row.update(service=units[-1], service_manager="systemd")
            except OSError:
                pass


def service_info(table: dict, tags: dict, pid: int) -> dict:
    ancestors = chain(table, pid)
    gateways = {table[q].get("service") for q in ancestors[1:]
                if table[q]["name"].startswith("hermes") and GATEWAY.search(table[q]["name"])}
    for q in ancestors:
        # A worker is not its gateway's long-lived service. Ordinary service wrappers still protect children.
        if q != pid and table[q]["name"].startswith("hermes") and GATEWAY.search(table[q]["name"]):
            break
        service = table[q].get("service") or tags.get(q, {}).get("XPC_SERVICE_NAME")
        if service and service != "0" and not service.startswith("application.") and service not in gateways:
            return {"service": service, "service_manager": table[q].get(
                "service_manager", "launchd" if MAC else "systemd")}
    return {"service": "", "service_manager": ""}


def parse_ps(out: str) -> dict[int, dict]:
    table = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 5)
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        args = parts[5] if len(parts) > 5 else ""
        table[int(parts[0])] = {"ppid": int(parts[1]), "cpu": float(parts[2]), "rss_mb": int(parts[3]) // 1024,
                                "etime": parts[4], "args": args, "name": short_name(args)}
    return table


def short_name(args: str) -> str:
    m = re.search(r"sys\.argv = \[[^,\]]*,\s*'([a-z][\w-]*)'(?:,\s*'([a-z][\w-]*)')?", args)
    if m and "hermes" in args.lower():
        return "hermes " + " ".join(g for g in m.groups() if g)
    m = re.search(r"-m hermes_cli\.main (?:-p \S+ )?(\w+)", args)
    if m:
        return f"hermes {m.group(1)}"
    exe = args.split(" -", 1)[0].strip() or args
    app = re.search(r"([^/]+)\.app/Contents/MacOS/", exe)
    return app.group(1) if app else os.path.basename(exe.split(" ")[0]) or "?"


def chain(table: dict, pid: int) -> list[int]:
    seen, cur = [], pid
    while cur in table and cur not in seen and cur > 1:
        seen.append(cur)
        cur = table[cur]["ppid"]
    return seen


def env_tags(pids: list[int]) -> dict[int, dict]:
    """Agent tags from each process's environment (ids only, never secret values)."""
    if not pids:
        return {}
    if MAC:
        res = subprocess.run(["ps", "-E", "-o", "pid=,args=", "-p", ",".join(map(str, pids))],
                             capture_output=True, text=True)
        return parse_env_tags(res.stdout)
    tags = {}
    for p in pids:
        try:
            env = Path(f"/proc/{p}/environ").read_bytes().split(b"\0")
        except OSError:
            continue
        found = {k: v for k, _, v in (e.decode(errors="replace").partition("=") for e in env) if k in TAG_KEYS and v}
        if found:
            tags[p] = found
    return tags


def parse_env_tags(out: str) -> dict[int, dict]:
    tags = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            found = dict(TAG_RE.findall(parts[1]))
            if found:
                tags[int(parts[0])] = found
    return tags


def owner(table: dict, tags: dict, pid: int) -> dict:
    """Tags inherited from the nearest tagged ancestor. A GUI app started from a worker keeps the dead
    worker's card id for ever, so card ids count only inside a hermes process tree."""
    c = chain(table, pid)
    agent: dict = {}
    hermes_tree = any(table[q]["name"].startswith("hermes") for q in c)
    for q in reversed(c):
        t = dict(tags.get(q, {}))
        if not hermes_tree and table[q]["ppid"] != 1:
            for k in TASK_KEYS:
                t.pop(k, None)
        agent.update(t)
    # Explicit service ownership is independent of inherited worker tags.
    for q in c:
        explicit = tags.get(q, {}).get("HERMES_OWNER_CARD", "")
        if re.fullmatch(r"t_[0-9a-f]+(?:@[A-Za-z0-9._-]+)?", explicit):
            agent.update(HERMES_OWNER_CARD=explicit, owner_source="env")
            return agent
    for q in c:
        cwd = table[q].get("cwd")
        if not cwd:
            continue
        try:
            with (Path(cwd) / "OWNER.md").open() as f:
                first = f.readline(4096)
        except OSError:
            continue
        match = re.match(r"^card: (t_[0-9a-f]+)(@[A-Za-z0-9._-]+)?(?:\s|$)", first)
        if match:
            agent.update(HERMES_OWNER_CARD=match[1] + (match[2] or ""),
                         owner_source=str(Path(cwd) / "OWNER.md"))
            break
    if "owner_source" not in agent and agent:
        agent["owner_source"] = "env"
    return agent


def label(agent: dict) -> str:
    task = agent.get("HERMES_OWNER_CARD") or agent.get("HERMES_KANBAN_TASK") or agent.get("HERMES_KANBAN_WORKER_TASK")
    for key, val in (("card", task), ("claude", agent.get("CLAUDE_CODE_HOST_SESSION_ID") or agent.get("CLAUDE_CODE_SESSION_ID")),
                     ("hermes-session", agent.get("HERMES_SESSION_ID")), ("codex", agent.get("CODEX_THREAD_ID")),
                     ("browser-use", agent.get("BU_NAME"))):
        if val:
            return f"{key}:{val}"
    return ""


def parse_runnable(out: str, exclude=frozenset(), top=10) -> dict:
    """Parse Darwin ps -M -ax, including USER-less continuation thread rows.

    PRI has scheduler flags (4T, 31R, 4N); STAT may have modifiers (R+).
    This is a point-in-time thread count, not the time-averaged load value.
    """
    by_priority, priority4 = {}, {}
    for line in out.splitlines():
        fields = line.split(None, 8)
        if not fields:
            continue
        offset = 1 if len(fields) > 1 and fields[1].isdigit() else 0
        try:
            pid = int(fields[offset])
            state = fields[offset + (2 if offset == 0 else 3)]
            pri = fields[offset + (3 if offset == 0 else 4)]
        except (ValueError, IndexError):
            continue
        if pid in exclude or not state.startswith("R") or not re.fullmatch(r"\d+[TRN]*", pri):
            continue
        priority = str(int(pri.rstrip("TRN")))
        by_priority[priority] = by_priority.get(priority, 0) + 1
        if priority == "4":
            priority4[pid] = priority4.get(pid, 0) + 1
    out = _finish(by_priority, priority4)
    if top is None:
        out["priority4"] = [{"pid": p, "threads": n} for p, n in priority4.items()]
    return out


class _ThreadInfo(ctypes.Structure):  # <sys/proc_info.h> struct proc_threadinfo
    _fields_ = [("user_time", ctypes.c_uint64), ("system_time", ctypes.c_uint64), ("cpu_usage", ctypes.c_int32),
                ("policy", ctypes.c_int32), ("run_state", ctypes.c_int32), ("flags", ctypes.c_int32),
                ("sleep_time", ctypes.c_int32), ("curpri", ctypes.c_int32), ("priority", ctypes.c_int32),
                ("maxpriority", ctypes.c_int32), ("name", ctypes.c_char * 64)]


PROC_PIDLISTTHREADS, PROC_PIDTHREADINFO, TH_STATE_RUNNING = 6, 5, 1


def _libproc():
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    return lib


def libproc_runnable(lib, exclude=frozenset()):
    """Runnable threads by current priority for every pid libproc lets us read, in-process (~25 ms for 10k
    threads, vs 0.1-0.9 s for setuid ps -M). Returns (by_priority, per-pid priority-4 counts, unreadable pids):
    other users' processes (root daemons) are unreadable without root and go to ps."""
    n = lib.proc_listallpids(None, 0)
    pids = (ctypes.c_int * (n + 256))()
    n = lib.proc_listallpids(pids, ctypes.sizeof(pids))
    handles = (ctypes.c_uint64 * 4096)()
    info = ctypes.sizeof(_ThreadInfo)
    ti = _ThreadInfo()
    by_priority, priority4, denied = {}, {}, []
    for pid in pids[:max(n, 0)]:
        if pid in exclude or pid <= 0:
            continue
        got = lib.proc_pidinfo(pid, PROC_PIDLISTTHREADS, 0, handles, ctypes.sizeof(handles))
        while got >= ctypes.sizeof(handles):  # more threads than the buffer holds: grow and retry
            handles = (ctypes.c_uint64 * (len(handles) * 2))()
            got = lib.proc_pidinfo(pid, PROC_PIDLISTTHREADS, 0, handles, ctypes.sizeof(handles))
        if got <= 0:
            if ctypes.get_errno() != 3:  # ESRCH = exited since the listing; anything else = no access
                denied.append(pid)
            continue
        for h in handles[:got // 8]:
            if lib.proc_pidinfo(pid, PROC_PIDTHREADINFO, h, ctypes.byref(ti), info) != info:
                continue  # thread exited
            if ti.run_state == TH_STATE_RUNNING:
                pri = str(ti.curpri)
                by_priority[pri] = by_priority.get(pri, 0) + 1
                if ti.curpri == 4:
                    priority4[pid] = priority4.get(pid, 0) + 1
    return by_priority, priority4, denied


def _ps_threads(pids, timeout=2):
    """ps -M for the given pids (all when None); returns (stdout, ps pid). -c skips argv (the process table
    already has names/ancestry). The timeout is a safety net, not a performance claim."""
    cmd = ["/bin/ps", "-M", "-axc"] if pids is None else ["/bin/ps", "-M", "-c", "-p", ",".join(map(str, pids))]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, _err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    # ps -p exits 1 when some listed pid is gone; output for the rest is still valid.
    if proc.returncode and (pids is None or not out):
        raise subprocess.CalledProcessError(proc.returncode, proc.args)
    return out, proc.pid


def _finish(by_priority: dict, priority4: dict) -> dict:
    return {"total": sum(by_priority.values()),
            "by_priority": dict(sorted(by_priority.items(), key=lambda kv: int(kv[0]))),
            "priority4": [{"pid": p, "threads": n} for p, n in
                          sorted(priority4.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]}


def runnable_snapshot(lib_fn=None) -> dict:
    """Runnable threads now: libproc for our own processes, setuid ps -M only for the ones it can't read.
    The probe's own process and its ps are excluded. Run it outside Background QoS: a background-clamped probe
    waits on the saturated E-cores it is counting (see install-agent in mac-load-recorder)."""
    t0 = time.perf_counter()
    me = {os.getpid()}
    try:
        try:
            lib = (lib_fn or _libproc)()
        except OSError:
            lib = None
        if lib is None:
            out, ps_pid = _ps_threads(None)
            snap = parse_runnable(out, exclude=me | {ps_pid})
            snap["method"] = "ps"
        else:
            by_priority, priority4, denied = libproc_runnable(lib, exclude=me)
            if denied:
                out, ps_pid = _ps_threads(denied)
                extra = parse_runnable(out, exclude=me | {ps_pid}, top=None)
                for k, v in extra["by_priority"].items():
                    by_priority[k] = by_priority.get(k, 0) + v
                for row in extra["priority4"]:
                    priority4[row["pid"]] = priority4.get(row["pid"], 0) + row["threads"]
            snap = _finish(by_priority, priority4)
            snap["method"] = "libproc+ps"
            snap["ps_pids"] = len(denied)
    except (OSError, subprocess.SubprocessError) as exc:
        # Never report a failed measurement as a zero queue, or drop the spike.
        snap = {"total": None, "by_priority": {}, "priority4": [], "error": type(exc).__name__}
    snap["collect_s"] = round(time.perf_counter() - t0, 3)
    return snap


def attribute_runnable(snapshot: dict, table: dict, tags: dict) -> dict:
    rows = []
    for row in snapshot["priority4"]:
        p = row["pid"]
        c = chain(table, p)
        rows.append(dict(row, name=table.get(p, {}).get("name", "?"),
                         chain=[f"{table[q]['name']}[{q}]" for q in c[1:]],
                         owner=label(owner(table, tags, p))))
    return dict(snapshot, priority4=rows)


def processes(table: dict, tags_fn=env_tags, extra_pids=()) -> dict:
    top = sorted((p for p in table if table[p]["cpu"] >= MIN_CPU), key=lambda p: -table[p]["cpu"])[:TOP_N]
    young_pids = [p for p in table if re.fullmatch(r"0?0:0\d", table[p]["etime"])]
    service_pids = [p for p in table if service_info(table, {}, p)["service"]]
    tags = tags_fn(sorted({q for p in [*top, *young_pids, *service_pids, *extra_pids] for q in chain(table, p)}))
    def ownership(p):
        agent = owner(table, tags, p)
        return dict(owner=label(agent), owner_source=agent.get("owner_source", ""),
                    **service_info(table, tags, p))
    rows = []
    for p in top:
        c = chain(table, p)
        rows.append({"pid": p, "cpu": table[p]["cpu"], "rss_mb": table[p]["rss_mb"], "etime": table[p]["etime"],
                     "name": table[p]["name"], "args": re.sub(r"\s+", " ", table[p]["args"])[:200],
                     "chain": [f"{table[q]['name']}[{q}]" for q in c[1:]],
                     "root": table[c[-1]]["name"] if c else table[p]["name"], **ownership(p)})
    apps: dict[str, list] = {}
    for p, row in table.items():
        if row["cpu"] > 0:
            c = chain(table, p)
            a = apps.setdefault(table[c[-1]]["name"] if c else row["name"], [0.0, 0])
            a[0] += row["cpu"]
            a[1] += 1
    # Short-lived churn: how many processes are under 10 s old, by name (cron/sync loops show up here).
    young: dict[tuple, dict] = {}
    for p in young_pids:
        info = ownership(p)
        key = (table[p]["name"], info["owner"], info["service"])
        row = young.setdefault(key, dict(name=table[p]["name"], count=0, **info))
        row["count"] += 1
    # Include quiet owned services too: age and low CPU are not evidence of abandonment.
    owned_services = []
    for p in service_pids:
        info = ownership(p)
        if info["owner"]:
            owned_services.append(dict(pid=p, name=table[p]["name"], cpu=table[p]["cpu"], **info))
    return {"top": rows, "owned_services": owned_services,
            "by_app": sorted(([k, round(v[0], 1), v[1]] for k, v in apps.items()), key=lambda r: -r[1])[:15],
            "young_by_name": sorted(young.values(), key=lambda r: -r["count"])[:10]}


# ───────────────────────── machine ─────────────────────────
def memory() -> dict:
    if MAC:
        try:
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
            page = int(re.search(r"page size of (\d+)", vm).group(1))
            pages = {k.strip(): int(v.strip(" .")) for k, v in re.findall(r"^(.+?):\s+(\d+)\.", vm, re.M)}
            free = (pages.get("Pages free", 0) + pages.get("Pages inactive", 0) + pages.get("Pages speculative", 0)) * page
            swap = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout.strip()
            return {"total_gb": round(total / 2**30, 1), "used_pct": round(100 * (1 - free / total), 1), "swap": swap}
        except (OSError, ValueError, AttributeError):
            return {}
    try:
        info = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines())}
        return {"total_gb": round(info["MemTotal"] / 2**20, 1),
                "used_pct": round(100 * (1 - info["MemAvailable"] / info["MemTotal"]), 1),
                "swap": f"used {round((info['SwapTotal'] - info['SwapFree']) / 2**20, 1)} GB of {round(info['SwapTotal'] / 2**20, 1)}"}
    except (OSError, KeyError, ValueError):
        return {}


def du_kb(path: Path, timeout: int = 240) -> int | None:
    try:
        out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=timeout).stdout
        return int(out.split()[0]) if out.strip() else None
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def du_children(path: Path, depth: int = 2, limit: int = 25, timeout: int = 600) -> list:
    """Largest directories under path, depth levels down (sizes in GB)."""
    flag = ["-d", str(depth)] if MAC else [f"--max-depth={depth}"]
    try:
        out = subprocess.run(["du", "-k", *flag, str(path)], capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    rows = []
    for line in out.splitlines():
        size, _, p = line.partition("\t")
        if size.isdigit() and p != str(path):
            rows.append([round(int(size) / 2**20, 2), p])
    return sorted(rows, reverse=True)[:limit]


def disk(with_du: bool = True) -> dict:
    total, used, free = shutil.disk_usage(HOME)
    out: dict = {"home_volume": {"total_gb": round(total / 2**30, 1), "free_gb": round(free / 2**30, 1),
                                 "used_pct": round(100 * used / total, 1)}}
    if MAC:
        snaps = subprocess.run(["tmutil", "listlocalsnapshots", "/"], capture_output=True, text=True).stdout
        out["apfs_local_snapshots"] = [l for l in snaps.splitlines() if "com.apple" in l]
    # With --no-du (CPU-only question) never walk the caches: at load > 40 each `du` of a big cache ran its
    # full 240 s timeout and the report printed nothing for minutes. Sizes are skipped (gb null), not guessed.
    out["cleanable"] = [dict(c, gb=round((du_kb(c["path"], 15) or 0) / 2**20, 2) if with_du else None, path=str(c["path"]))
                        for c in cache_targets() if c["path"].exists()]
    if with_du:
        out["largest_in_home"] = du_children(HOME, 2)
        others = [p for p in Path("/Users" if MAC else "/home").glob("*") if p != HOME and p.name != "Shared"]
        out["other_homes_gb"] = {str(p): round((du_kb(p, 60) or 0) / 2**20, 2) for p in others}
    return out


# ───────────────────────── recorder spikes ─────────────────────────
RECORDER_DIR = Path(os.environ.get("MAC_LOAD_RECORDER_DIR", HOME / "Library/Logs/mac-load-recorder"))


def spikes(hours: float, rdir: Path = RECORDER_DIR) -> dict:
    since = time.time() - hours * 3600
    loads, recs = [], []
    for f in sorted(rdir.glob("load-20*.tsv")):
        for line in f.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and int(parts[0]) >= since:
                loads.append(float(parts[1]))
    for f in sorted(rdir.glob("spikes-20*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                recs.append(json.loads(line))
            except ValueError:
                pass
    recs = [r for r in recs if _ts(r.get("ts")) >= since]
    agents: dict[str, float] = {}
    for r in recs:
        for k, v in r.get("by_agent", []):
            agents[k] = agents.get(k, 0) + v
    s = sorted(loads)
    return {"recorder_dir": str(rdir), "samples": len(s),
            "load1": {"median": s[len(s) // 2], "p90": s[int(len(s) * 0.9)], "max": s[-1]} if s else None,
            "spike_records": len(recs),
            "mean_cpu_by_agent_in_spikes": sorted(([k, round(v / max(len(recs), 1), 1)] for k, v in agents.items()),
                                                 key=lambda r: -r[1])[:12]}


def _ts(value) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def report(hours: float, with_du: bool) -> dict:
    load = os.getloadavg()
    ncpu = os.cpu_count() or 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(runnable_snapshot) if MAC else None
        table = ps_table()
        runnable = pending.result() if pending else {"supported": False, "total": None, "by_priority": {}, "priority4": []}
    tags = {}
    def read_tags(pids):
        tags.update(env_tags(pids))
        return tags
    proc = processes(table, tags_fn=read_tags, extra_pids=[row["pid"] for row in runnable["priority4"]])
    return {"host": platform.node(), "os": platform.platform(), "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "load": [round(x, 2) for x in load], "cpus": ncpu, "cpu_pct": round(100 * load[0] / ncpu),
            "memory": memory(), "processes": proc, "disk": disk(with_du),
            "runnable": attribute_runnable(runnable, table, tags),
            "recorder": spikes(hours) if RECORDER_DIR.exists() else {"installed": False}}


# ───────────────────────── safe fixes ─────────────────────────
def cache_targets() -> list[dict]:
    """Regenerable package/build caches. Deleting one costs a re-download or rebuild, never data."""
    lib = HOME / "Library/Caches"
    t = [
        ("uv cache", HOME / ".cache/uv", ["uv", "cache", "clean"]),
        ("pip cache", HOME / ".cache/pip", ["python3", "-m", "pip", "cache", "purge"]),
        ("pip cache", lib / "pip", ["python3", "-m", "pip", "cache", "purge"]),
        ("npm cache", HOME / ".npm/_cacache", ["npm", "cache", "clean", "--force"]),
        ("npx cache", HOME / ".npm/_npx", None),
        ("go build cache", lib / "go-build" if MAC else HOME / ".cache/go-build", ["go", "clean", "-cache"]),
        ("cargo registry cache", HOME / ".cargo/registry/cache", None),
        ("Homebrew cache", lib / "Homebrew", ["brew", "cleanup", "-s", "--prune=all"]),
        ("Xcode DerivedData", HOME / "Library/Developer/Xcode/DerivedData", None),
        ("pnpm store", HOME / "Library/pnpm/store" if MAC else HOME / ".local/share/pnpm/store", ["pnpm", "store", "prune"]),
    ]
    return [{"name": n, "path": p, "cmd": c} for n, p, c in t]


def _safe_target(path: Path) -> bool:
    allowed = {str(c["path"].resolve()) for c in cache_targets()}
    try:
        return str(path.resolve()) in allowed and path.resolve() != HOME.resolve()
    except OSError:
        return False


def clean(apply: bool) -> dict:
    total, _, free_before = shutil.disk_usage(HOME)
    actions = []
    for c in cache_targets():
        p = c["path"]
        if not p.exists():
            continue
        before = du_kb(p) or 0
        if before < 50 * 1024:  # under 50 MB: not worth a rebuild
            continue
        act = {"name": c["name"], "path": str(p), "before_gb": round(before / 2**20, 2)}
        if apply:
            tool = c["cmd"] and shutil.which(c["cmd"][0])
            if tool:
                act["ran"] = " ".join(c["cmd"])
                try:
                    act["exit"] = subprocess.run(c["cmd"], capture_output=True, text=True, timeout=180).returncode
                except subprocess.TimeoutExpired:
                    act["exit"] = "timeout after 180 s"  # falls back to removing the directory below
            if not tool or (p.exists() and (du_kb(p) or 0) > before * 0.5):
                if _safe_target(p):
                    shutil.rmtree(p, ignore_errors=True)
                    act["removed"] = True
            act["after_gb"] = round((du_kb(p) or 0) / 2**20, 2) if p.exists() else 0.0
        actions.append(act)
    if MAC and apply and shutil.which("xcrun"):
        r = subprocess.run(["xcrun", "simctl", "delete", "unavailable"], capture_output=True, text=True, timeout=300)
        actions.append({"name": "unavailable simulators", "ran": "xcrun simctl delete unavailable", "exit": r.returncode})
    free_after = shutil.disk_usage(HOME)[2]
    return {"applied": apply, "free_gb_before": round(free_before / 2**30, 1),
            "free_gb_after": round(free_after / 2**30, 1), "actions": actions}


def _ro_connect(path: Path, timeout: float = 5):
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


def card_status(task_id: str, homes: list[Path] | None = None) -> str | None:
    """Status of a Kanban card from this machine's board DBs and sync-shadow replicas."""
    if not re.fullmatch(r"t_[0-9A-Za-z]{4,40}", task_id or ""):
        return None
    hermes_home = Path(os.environ.get("HERMES_HOME") or HOME / ".hermes")
    for root in homes or [hermes_home / "kanban"]:
        # Known board locations only (kanban.db, sync-shadow/*.db, boards/<slug>/kanban.db). rglob also walked workspaces/ and
        # evidence/ (millions of files, ~2 min per call), which made `kill` hang once per candidate process.
        for db in sorted({*root.glob("*.db"), *root.glob("*/*.db"), *root.glob("boards/*/*.db")}):
            try:
                con = _ro_connect(db, timeout=1)
                row = con.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
                con.close()
            except sqlite3.Error:
                continue
            if row:
                return row[0]
    return None


def kill_verdict(table: dict, tags: dict, pid: int, status_fn=card_status) -> tuple[bool, str]:
    """May an agent kill pid without asking? Only a non-gateway, non-GUI process whose card is finished."""
    if pid not in table or pid <= 1 or pid == os.getpid():
        return False, "no such process"
    row = table[pid]
    if row["name"].startswith("hermes") and GATEWAY.search(row["name"]):
        return False, "a Hermes gateway/serve process: ask the user"
    info = service_info(table, tags, pid)
    if info["service"]:
        return False, f"a {info['service_manager']} service {info['service']}: ask on its owner card"
    exe = row["args"].split(" -", 1)[0]
    if GUI_APP.search(exe) and not re.search(r"/python[\d.]*$", exe.strip()):
        return False, "a GUI app: ask the user"
    agent = owner(table, tags, pid)
    task = agent.get("HERMES_OWNER_CARD") or agent.get("HERMES_KANBAN_TASK") or agent.get("HERMES_KANBAN_WORKER_TASK")
    if not task:
        return False, "no Kanban card owns it: ask the user"
    card, _, host = task.partition("@")
    local_hosts = {platform.node(), platform.node().split(".")[0]}
    # A sync-shadow's terminal status is not authority to stop a remote-owned process.
    status = None if host and host not in local_hosts else status_fn(card)
    if status in FINISHED:
        return True, f"card {task} is {status}; its process outlived it"
    return False, f"card {task} is {status or 'unknown here'}: still owned"


def kill(pid: int, apply: bool) -> dict:
    table = ps_table()
    tags = env_tags(chain(table, pid))
    ok, why = kill_verdict(table, tags, pid)
    out = {"pid": pid, "name": table.get(pid, {}).get("name"), "allowed": ok, "why": why, "applied": False}
    if ok and apply:
        try:
            os.kill(pid, signal.SIGTERM)
            out["applied"] = True
        except OSError as exc:
            out["error"] = str(exc)
    return out


# ───────────────────────── recorder install ─────────────────────────
RECORDER = HOME / ".hermes/bin/mac-load-recorder"
RECORDER_LABEL = "com.daveey.mac-load-recorder"


def recorder_plist(ncpu: int) -> str:
    threshold = max(8, int(ncpu * 2.5))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{RECORDER_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>{RECORDER}</string>
    <string>sample</string>
    <string>--threshold</string>
    <string>{threshold}</string>
  </array>
  <key>StartInterval</key><integer>30</integer>
  <key>RunAtLoad</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardErrorPath</key><string>{HOME}/Library/Logs/mac-load-recorder/launchd.err</string>
</dict>
</plist>
"""


def install_recorder(apply: bool) -> dict:
    if not MAC:
        return {"installed": False, "why": "not macOS (the recorder is macOS-only)"}
    if not RECORDER.exists():
        return {"installed": False, "why": f"{RECORDER} missing (hermes-sync ships it)"}
    loaded = subprocess.run(["launchctl", "list", RECORDER_LABEL], capture_output=True).returncode == 0
    if loaded:
        return {"installed": True, "why": "already loaded"}
    plist = HOME / f"Library/LaunchAgents/{RECORDER_LABEL}.plist"
    if not apply:
        return {"installed": False, "why": "dry run", "would_write": str(plist)}
    (HOME / "Library/Logs/mac-load-recorder").mkdir(parents=True, exist_ok=True)
    plist.write_text(recorder_plist(os.cpu_count() or 1))
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
    return {"installed": r.returncode == 0, "plist": str(plist), "exit": r.returncode, "stderr": r.stderr.strip()[:300]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--hours", type=float, default=24)
    r.add_argument("--no-du", action="store_true", help="skip the slow home-directory du")
    c = sub.add_parser("clean")
    c.add_argument("--apply", action="store_true")
    k = sub.add_parser("kill")
    k.add_argument("pid", type=int)
    k.add_argument("--apply", action="store_true")
    i = sub.add_parser("install-recorder")
    i.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        out = report(a.hours, not a.no_du)
    elif a.cmd == "clean":
        out = clean(a.apply)
    elif a.cmd == "kill":
        out = kill(a.pid, a.apply)
    else:
        out = install_recorder(a.apply)
    json.dump(out, sys.stdout, indent=1, default=str)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
