"""Owner lookup for one service. Stdlib only: this file's text is also sent to a remote host over ssh.

``valid_owner`` and ``read_owner_md`` are NOT defined here: they are machine-investigate's collect.py
functions (the single OWNER.md / HERMES_OWNER_CARD parser). The caller injects them (locally by setattr,
remotely by appending their source to the script).
"""
import json
import os
import plistlib
import re
import sys
from pathlib import Path

valid_owner = read_owner_md = None  # injected from collect.py


def _dirs_up(d):
    """d, then its ancestors while they stay below $HOME (an OWNER.md in $HOME itself would own everything)."""
    home = Path(os.path.expanduser("~"))
    out, cur = [], Path(d)
    while True:
        out.append(cur)
        if cur == home or cur.parent == cur or home not in cur.parents:
            break
        cur = cur.parent
        if cur == home:
            break
    return out


def _marker_in(dirs):
    seen = set()
    for d in dirs:
        for dd in _dirs_up(d):
            if str(dd) in seen:
                continue
            seen.add(str(dd))
            found = read_owner_md(dd)
            if found:
                return found
    return None


def _expand(p):
    return os.path.expandvars(os.path.expanduser(p))


def _cwd_of(pid, run):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        pass
    rc, out = run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"], 5)
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def _plist_dirs(job):
    dirs = []
    if job.get("WorkingDirectory"):
        dirs.append(_expand(job["WorkingDirectory"]))
    argv = list(job.get("ProgramArguments") or [])
    if job.get("Program"):
        argv.insert(0, job["Program"])
    for arg in argv:
        if not isinstance(arg, str):
            continue
        path = _expand(arg)
        if os.path.isabs(path) and os.path.exists(path):
            dirs.append(path if os.path.isdir(path) else os.path.dirname(path))
        for m in re.finditer(r"""\bcd\s+(?:--\s+)?("[^"]+"|'[^']+'|[^\s;&|]+)""", arg):
            target = _expand(m[1].strip("\"'"))
            if os.path.isabs(target):
                dirs.append(target)
    return dirs


def _launchd(spec, run):
    label = spec.get("label") or ""
    domain = spec.get("domain") or "gui/%d" % os.getuid()
    plist, pids = spec.get("plist"), []
    if label:
        rc, out = run(["launchctl", "print", f"{domain}/{label}"], 5)
        if rc == 0:
            m = re.search(r"^\s*path = (.+\.plist)\s*$", out, re.M)
            if m and not plist:
                plist = m[1]
            pids = [int(x) for x in re.findall(r"^\s*pid = (\d+)\s*$", out, re.M)]
    if not plist and label:
        for root in (Path(os.path.expanduser("~/Library/LaunchAgents")), Path("/Library/LaunchAgents"),
                     Path("/Library/LaunchDaemons")):
            if (root / f"{label}.plist").exists():
                plist = str(root / f"{label}.plist")
                break
    dirs = []
    if plist:
        plist = _expand(plist)
        try:
            with open(plist, "rb") as f:
                job = plistlib.load(f)
        except (OSError, ValueError, plistlib.InvalidFileException):
            job = {}
        env = job.get("EnvironmentVariables") or {}
        card = valid_owner(env.get("HERMES_OWNER_CARD", "") if isinstance(env, dict) else "")
        if card:
            return {"status": "found", "owner": card, "source": f"{plist} EnvironmentVariables"}
        dirs = _plist_dirs(job)
    found = _marker_in(dirs)
    if found:
        return {"status": "found", "owner": found[0], "source": found[1]}
    for pid in pids:  # the running job's own working directory
        cwd = _cwd_of(pid, run)
        found = _marker_in([cwd]) if cwd else None
        if found:
            return {"status": "found", "owner": found[0], "source": found[1]}
    return {"status": "none"}


def _systemd(spec, run):
    argv = ["systemctl"] + (["--user"] if spec.get("user") else []) + [
        "show", "-p", "Environment,WorkingDirectory,ExecStart", spec["unit"]]
    rc, out = run(argv, 5)
    if rc != 0:
        return {"status": "failed", "error": f"systemctl show rc={rc}"}
    dirs = []
    for line in out.splitlines():
        key, _, val = line.partition("=")
        if key == "Environment":
            m = re.search(r"""HERMES_OWNER_CARD=["']?([^\s"']+)""", val)
            card = valid_owner(m[1]) if m else ""
            if card:
                return {"status": "found", "owner": card, "source": f"{spec['unit']} Environment"}
        elif key == "WorkingDirectory" and val.startswith("/"):
            dirs.append(val)
        elif key == "ExecStart":
            for p in re.findall(r"(?:path|argv\[\])=(/[^\s;]+)", val):
                dirs.append(p if os.path.isdir(p) else os.path.dirname(p))
    found = _marker_in(dirs)
    if found:
        return {"status": "found", "owner": found[0], "source": found[1]}
    return {"status": "none"}


def _pids(spec, run):
    rc, out = run(["ps", "-Ao", "pid=,ppid=,pcpu=,rss=,etime=,args="], 5)
    if rc != 0:
        return {"status": "failed", "error": "ps failed"}
    table = parse_ps(out)
    if "pid" in spec or "pids" in spec:
        pids = [p for p in spec.get("pids", [spec.get("pid")]) if p in table]
    else:
        argv = ["pgrep"] + spec["pgrep_args"]
        rc, out = run(argv, 5)
        if rc not in (0, 1):
            return {"status": "failed", "error": "pgrep failed"}
        matches = [line.split()[0] for line in out.splitlines() if line.split()]
        pids = [int(p) for p in matches if p.isdigit() and int(p) in table and int(p) > 1]
    every = sorted({q for p in pids for q in chain(table, p)})
    for q in every:
        cwd = _cwd_of(q, run)
        if cwd:
            table[q]["cwd"] = cwd
    tags = env_tags(every)
    owners = []
    for p in pids:
        agent = owner(table, tags, p)
        if agent.get("HERMES_OWNER_CARD"):
            owners.append({"status": "found", "owner": agent["HERMES_OWNER_CARD"],
                           "source": "pid %s: %s" % (p, agent.get("owner_source", "env")), "label": str(p)})
    return dict(owners[0], owners=owners) if owners else {"status": "none"}


def find_owner(spec, run):
    try:
        if spec.get("kind") == "pids":
            return _pids(spec, run)
        return _launchd(spec, run) if spec.get("kind") == "launchd" else _systemd(spec, run)
    except Exception as exc:  # a lookup bug must read as "failed", never as "no owner"
        return {"status": "failed", "error": repr(exc)[:200]}
