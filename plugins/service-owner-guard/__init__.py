"""service-owner-guard: an agent does not stop an owner-marked service with a raw command.

A service (launchd job, systemd unit, or process) can carry an owner card: ``HERMES_OWNER_CARD`` in its
environment, or an ``OWNER.md`` whose first line is ``card: t_<hex>[@host]`` in its working directory or the
script's directory (or an ancestor below $HOME). This ``pre_tool_call`` hook (plus ``claude_hook.py`` for Claude
Code Bash) refuses ``launchctl disable|bootout|unload|remove|stop``, ``systemctl stop|disable|mask|kill`` and
``kill|pkill|killall`` of such a service, at top level or inside ``ssh <host> '<cmd>'`` / ``bash -c``, and from
execute_code Python, unless the command carries ``owner-approved=<card id>``. Owner lookup reuses
machine-investigate's collect.py (``valid_owner``, ``read_owner_md``, ``owner``); nothing here parses markers.
Reloads (bootout then bootstrap of the same label) and kickstart/restart/start/enable/bootstrap are never refused.
Shell segmenting is vendored from backend-restart-guard. ``SERVICE_OWNER_GUARD=off`` disables it.
"""

from __future__ import annotations

import ast
import datetime
import importlib.util
import inspect
import json
import logging
import os
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
SHELL_TOOLS = {"terminal", "process_manage", "process"}
PY_TOOLS = {"execute_code"}
SHELLS = {"sh", "bash", "zsh", "dash"}
LAUNCHCTL_STOP = {"disable", "bootout", "unload", "remove", "stop"}
SYSTEMCTL_STOP = {"stop", "disable", "mask", "kill"}
LOAD_VERBS = {"bootstrap", "load"}
REMOTE_TIMEOUT = 8
APPROVED = re.compile(r"owner-approved=(t_[0-9a-f]+)")
CHECKED_NONE = re.compile(r"owner-checked=none\b")


def enabled() -> bool:
    return os.environ.get("SERVICE_OWNER_GUARD", "on").lower() not in {"off", "0", "false", "no"}


# ───────────── single-sourced owner parsing: machine-investigate/scripts/collect.py ─────────────
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_path() -> Path:
    rel = Path("skills/devops/machine-investigate/scripts/collect.py")
    for base in (HERE.parents[1], Path.home() / "code/hermes-mods"):
        if (base / rel).exists():
            return base / rel
    raise FileNotFoundError("machine-investigate collect.py not found")


_collect = _lib = None


def libs():
    global _collect, _lib
    if _lib is None:
        _collect = _load("sog_collect", _collect_path())
        lib = _load("sog_lookup_lib", HERE / "lookup_lib.py")
        lib.valid_owner, lib.read_owner_md = _collect.valid_owner, _collect.read_owner_md
        _lib = lib
    return _collect, _lib


def run_cmd(argv: list, timeout: float = 5) -> tuple:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def remote_script(spec: dict) -> str:
    collect, lib = libs()
    return "\n".join([
        "from __future__ import annotations\nimport json, os, plistlib, re, subprocess, sys\nfrom pathlib import Path",
        (HERE / "lookup_lib.py").read_text(),  # declares valid_owner = read_owner_md = None ...
        inspect.getsource(collect.valid_owner), inspect.getsource(collect.read_owner_md),  # ... defined here
        "def run(argv, timeout=5):\n"
        "    try:\n"
        "        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)\n"
        "        return r.returncode, r.stdout\n"
        "    except Exception:\n"
        "        return 127, ''\n"
        "print(json.dumps(find_owner(json.loads(sys.argv[1]), run)))",
    ])


def remote_lookup(ssh_prefix: list, spec: dict) -> dict:
    """One bounded ssh call (<= 8 s) running the shared lookup on the target host."""
    try:
        script = remote_script(spec)
        res = subprocess.run(ssh_prefix + ["python3 - " + shlex.quote(json.dumps(spec))], input=script,
                             capture_output=True, text=True, timeout=REMOTE_TIMEOUT)
        if res.returncode != 0:
            return {"status": "failed", "error": f"ssh rc={res.returncode}: {res.stderr.strip()[:120]}"}
        return json.loads(res.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": "timeout"}
    except Exception as exc:
        return {"status": "failed", "error": repr(exc)[:160]}


def local_lookup(spec: dict) -> dict:
    return libs()[1].find_owner(spec, run_cmd)


# ───────────── shell parsing (vendored from backend-restart-guard) ─────────────
def _segments(text: str) -> list:
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError:
        tokens = text.split()
    out, cur, skipping = [], [], False
    for tok in tokens:
        if tok and set(tok) <= set(";&|()\n"):
            if cur:
                out.append(cur)
            cur, skipping = [], False
        elif tok.startswith("#"):
            skipping = True  # a comment runs to the end of the segment
        elif not skipping:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _strip(argv: list) -> list:
    i = 0
    while i < len(argv):
        tok = argv[i]
        if re.match(r"^[A-Za-z_]\w*=", tok) or os.path.basename(tok) in {
                "sudo", "env", "nice", "timeout", "command", "nohup", "exec", "setsid"}:
            i += 1
            if os.path.basename(tok) == "timeout" and i < len(argv):
                i += 1
            continue
        if tok.startswith("-") and i > 0 and os.path.basename(argv[i - 1]) in {"sudo", "env", "nice"}:
            i += 1
            continue
        break
    return argv[i:]


def _ssh_parts(args: list) -> tuple:
    """(option args, destination, remote command) of an ssh invocation."""
    takes_value = set("BbcDEeFIiJLlmOopQRSWw")
    i, opts = 0, []
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if not arg.startswith("-") or arg == "-":
            break
        opts.append(arg)
        i += 1
        if len(arg) == 2 and arg[1] in takes_value and i < len(args):
            opts.append(args[i])
            i += 1
    dest = args[i] if i < len(args) else ""
    return opts, dest, " ".join(args[i + 1:])


# ───────────── actions ─────────────
def _domain_label(target: str) -> tuple:
    m = re.match(r"^((?:gui|user|login|session|pid)/[^/]+|system)/(.+)$", target)
    return (m[1], m[2]) if m else ("", target)


def _launchctl(args: list, host) -> list:
    pos = [a for a in args if not a.startswith("-")]
    if not pos:
        return []
    verb, rest = pos[0], pos[1:]
    act = {"tool": "launchctl", "verb": verb, "host": host, "plist": None, "label": "", "domain": ""}
    if verb in LOAD_VERBS:
        plist = rest[-1] if rest else ""
        return [dict(act, key=Path(plist).stem, load=True)]
    if verb not in LAUNCHCTL_STOP or not rest:
        return []
    if verb == "unload":
        plist = rest[0]
        return [dict(act, plist=plist, label=Path(plist).stem, key=Path(plist).stem)]
    domain, label = _domain_label(rest[0])
    if verb == "bootout" and len(rest) > 1 and rest[1].endswith(".plist"):  # bootout <domain> <plist>
        plist = rest[1]
        return [dict(act, domain=rest[0], plist=plist, label=Path(plist).stem, key=Path(plist).stem)]
    return [dict(act, domain=domain, label=label, key=label)]


def _systemctl(args: list, host) -> list:
    pos = [a for a in args if not a.startswith("-")]
    if len(pos) < 2 or pos[0] not in SYSTEMCTL_STOP:
        return []
    user = "--user" in args
    return [{"tool": "systemctl", "verb": pos[0], "host": host, "unit": u, "user": user, "label": u, "key": u}
            for u in pos[1:]]


def _kill(prog: str, args: list, host) -> list:
    if host:  # local targets only
        return []
    valued = {"-s", "-n"} if prog == "kill" else {"-u", "-U", "-g", "-G", "-P", "-t", "-s", "-n", "-o"}
    pos, full, i = [], False, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a in valued:
            if prog == "kill" and a == "-s" and i < len(args) and args[i] in {"0", "EXIT"}:
                return []
            i += 1
        elif a in {"-l", "-L", "-0"}:
            return []  # list signals / existence probe
        elif a.startswith("-") and len(a) > 1:
            if prog != "kill" and not a.startswith("--") and "f" in a[1:]:
                full = True
        else:
            pos.append(a)
    if prog == "kill":
        return [{"tool": "kill", "verb": "kill", "host": None, "pid": int(p), "label": p, "key": p}
                for p in pos if p.isdigit() and int(p) > 1]
    if not pos:
        return []
    return [{"tool": "kill", "verb": prog, "host": None, "pattern": pos[0], "full": full,
             "label": pos[0], "key": pos[0]}]


def _extract(argv: list, host, depth: int, out: list) -> None:
    argv = _strip(argv)
    if not argv:
        return
    prog, args = os.path.basename(argv[0]), argv[1:]
    if prog in SHELLS and depth < 3:
        for i, arg in enumerate(args[:-1]):
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", arg):
                _extract_text(args[i + 1], host, depth + 1, out)
                return
    elif prog == "ssh" and depth < 3:
        opts, dest, cmd = _ssh_parts(args)
        _extract_text(cmd, {"dest": dest, "opts": opts}, depth + 1, out)
    elif prog == "launchctl":
        out.extend(_launchctl(args, host))
    elif prog == "systemctl":
        out.extend(_systemctl(args, host))
    elif prog in {"kill", "pkill", "killall"}:
        out.extend(_kill(prog, args, host))


def _extract_text(text: str, host, depth: int, out: list) -> None:
    for argv in _segments(text):
        _extract(argv, host, depth, out)


def _py_commands(code: str) -> list:
    """Shell-like command strings found in Python source: argv lists and string literals."""
    cmds = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return re.findall(r"""['"]((?:launchctl|systemctl|kill|pkill|killall|ssh)\b[^'"]*)['"]""", code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            parts = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else
                     str(e.value) if isinstance(e, ast.Constant) else "?" for e in node.elts]
            if os.path.basename(parts[0]) in {"launchctl", "systemctl", "kill", "pkill", "killall", "ssh"}:
                cmds.append(shlex.join(parts))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and " " in node.value:
            cmds.append(node.value)
    return cmds


def extract_actions(text: str, python: bool = False) -> list:
    out: list = []
    if python:
        for cmd in _py_commands(text):
            _extract_text(cmd, None, 0, out)
    else:
        _extract_text(text, None, 0, out)
    return out


# ───────────── verdicts ─────────────
def _log(host: str, act: dict, owner: str, decision: str) -> None:
    path = Path(os.environ.get("SERVICE_OWNER_GUARD_LOG") or Path.home() / ".hermes/logs/service-owner-guard.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a") as f:
            f.write("\t".join([stamp, host, act["verb"], act["label"], owner or "-", decision]) + "\n")
    except OSError:
        logger.debug("service-owner-guard log write failed", exc_info=True)


def _host_name(host) -> str:
    if host:
        return host["dest"].split("@")[-1] or "remote"
    return platform.node().split(".")[0] or "local"


def _lookup(act: dict) -> dict:
    host = act["host"]
    if act["tool"] == "kill":
        return _lookup_pids(act)
    if act["tool"] == "launchctl":
        spec = {"kind": "launchd", "label": act["label"], "domain": act["domain"], "plist": act["plist"]}
    else:
        spec = {"kind": "systemd", "unit": act["unit"], "user": act["user"]}
    if not host:
        return local_lookup(spec)
    opts = [o for o in host["opts"] if o not in {"-t", "-tt", "-T"}]
    prefix = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"] + opts + [host["dest"]]
    return remote_lookup(prefix, spec)


def _lookup_pids(act: dict) -> dict:
    collect = libs()[0]
    try:
        res = subprocess.run(["ps", "-Ao", "pid=,ppid=,pcpu=,rss=,etime=,args="], capture_output=True, text=True,
                             timeout=5)
        table = collect.parse_ps(res.stdout)
        if "pid" in act:
            pids = [act["pid"]] if act["pid"] in table else []
        else:
            rx = re.compile(act["pattern"])
            pids = [p for p, r in table.items() if p != os.getpid() and p > 1 and
                    rx.search(r["args"] if act.get("full") else os.path.basename(r["args"].split(" ")[0]))]
        chains = {p: collect.chain(table, p) for p in pids}
        every = sorted({q for c in chains.values() for q in c})
        for q in every:
            if "cwd" not in table[q]:
                cwd = libs()[1]._cwd_of(q, run_cmd)
                if cwd:
                    table[q]["cwd"] = cwd
        tags = collect.env_tags(every)
        for p in pids:
            agent = collect.owner(table, tags, p)
            if agent.get("HERMES_OWNER_CARD"):
                return {"status": "found", "owner": agent["HERMES_OWNER_CARD"],
                        "source": f"pid {p}: {agent.get('owner_source', 'env')}", "label": str(p)}
        return {"status": "none"}
    except Exception as exc:
        return {"status": "failed", "error": repr(exc)[:160]}


def classify(tool_name: str, args: Any) -> Optional[str]:
    """The refusal message for a tool call, or None."""
    args = args if isinstance(args, dict) else {}
    if tool_name in SHELL_TOOLS:
        text = " ".join(str(args.get(k) or "") for k in ("command", "input", "data"))
        return _evaluate(text, extract_actions(text))
    if tool_name in PY_TOOLS:
        text = str(args.get("code") or "")
        return _evaluate(text, extract_actions(text, python=True))
    if tool_name in {"tool_call", "mcp__tool_call"}:
        for call in args.get("calls") or []:
            if isinstance(call, dict):
                why = classify(str(call.get("name") or ""), call.get("arguments"))
                if why:
                    return why
    return None


def _evaluate(text: str, actions: list) -> Optional[str]:
    stops = [a for a in actions if not a.get("load")]
    if not stops:
        return None
    approved = set(APPROVED.findall(text))
    checked_none = bool(CHECKED_NONE.search(text))
    mine = os.environ.get("HERMES_KANBAN_TASK", "")
    for idx, act in enumerate(actions):
        if act.get("load"):
            continue
        if act["tool"] == "launchctl" and act["verb"] in {"bootout", "unload"} and any(
                b.get("load") and b["key"] == act["key"] and j > idx for j, b in enumerate(actions)):
            continue  # reload: the same label is loaded again in this command
        persistent = (act["tool"] == "launchctl" and act["verb"] == "disable") or (
            act["tool"] == "systemctl" and act["verb"] in {"disable", "mask"})
        res = _lookup(act)
        host = _host_name(act["host"])
        target = act["label"] if act["tool"] != "kill" or "pid" not in act else f"pid {act['pid']}"
        status = res.get("status")
        if status == "found":
            owner = res["owner"]
            card = owner.split("@")[0]
            if mine and mine == card:
                continue
            if card in approved:
                _log(host, act, owner, "override owner-approved")
                continue
            _log(host, act, owner, "refused")
            what = f"{target} (pid {res['label']})" if act["tool"] == "kill" and "pattern" in act else target
            return (f"Refused: {what} is owned by card {owner} ({res.get('source', '?')}). Ask on that card "
                    f"with the measured cost (CPU-time delta over >= 60 s). Stop it only after the owner agrees, "
                    f"then add owner-approved={card} to the command.")
        if status == "failed":
            _log(host, act, "-", f"lookup failed ({res.get('error', '?')}); " +
                 ("override owner-checked=none" if persistent and checked_none else
                  "refused" if persistent else "allowed"))
            if persistent and not checked_none:
                return (f"Refused: could not check the owner of {target} on {host} ({res.get('error', '?')}), and "
                        f"{act['verb']} persists across reboots. Check it another way (OWNER.md, plist "
                        f"EnvironmentVariables), then add owner-checked=none if there is no owner.")
            continue
        if persistent:  # no owner found
            if checked_none:
                _log(host, act, "-", "override owner-checked=none")
                continue
            _log(host, act, "-", "refused (no owner found, persistent)")
            return (f"Refused: {act['verb']} of {target} on {host} persists across reboots, and no owner marker "
                    f"was found. Confirm nobody owns it, then add owner-checked=none to the command.")
    return None


def _pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> Optional[dict]:
    try:
        if not enabled():
            return None
        why = classify(tool_name, args)
    except Exception:  # never let this plugin break a tool call
        logger.debug("service-owner-guard classify failed", exc_info=True)
        return None
    if not why:
        return None
    logger.info("service-owner-guard blocked %s: %s", tool_name, why)
    return {"action": "block", "message": why}


def check(command: str) -> Optional[str]:
    """Dry run: the refusal message for a shell command, or None. Never executes the command."""
    return classify("terminal", {"command": command})


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _pre_tool_call)
