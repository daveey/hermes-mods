"""Plugin-owned stdlib owner/process parsing.

Vendored from machine-investigate/scripts/collect.py at
5e24f25a6a9aa0bc3e1d2892da785ccb7ab4d455; keep updates reviewed with this plugin.
No runtime dependency on skills or a home-directory checkout. The same functions
are sent to remote hosts by remote_script().
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path

MAC = platform.system() == "Darwin"


TAG_KEYS = (
    "HERMES_OWNER_CARD", "XPC_SERVICE_NAME",
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_KANBAN_BOARD", "HERMES_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "BU_NAME",
)


TASK_KEYS = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK")


TAG_RE = re.compile(r"(?:^|\s)(" + "|".join(TAG_KEYS) + r")=(\S+)")


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


def env_tags(pids: list[int], strict: bool = False) -> dict[int, dict]:
    """Agent tags from each process's environment (ids only, never secret values)."""
    if not pids:
        return {}
    if MAC:
        res = subprocess.run(["ps", "-E", "-o", "pid=,args=", "-p", ",".join(map(str, pids))],
                             capture_output=True, text=True)
        if strict and (res.returncode or getattr(res, "truncated", False) or
                       set(pids) - {int(line.split()[0]) for line in res.stdout.splitlines()
                                    if line.split() and line.split()[0].isdigit()}):
            raise OSError("incomplete process environment listing")
        return parse_env_tags(res.stdout)
    tags = {}
    for p in pids:
        try:
            env = Path(f"/proc/{p}/environ").read_bytes().split(b"\0")
        except OSError:
            if strict:
                raise
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


def valid_owner(value: str) -> str:
    """`t_<hex>[@host]` or ''. Validate an explicit service owner."""
    return value if re.fullmatch(r"t_[0-9a-f]+(?:@[A-Za-z0-9._-]+)?", value or "") else ""


def read_owner_md(directory, strict: bool = False) -> tuple[str, str] | None:
    """(card, path) from `<directory>/OWNER.md` whose first line is `card: t_<hex>[@host]`, else None.
    Only the first line supplies ownership."""
    path = Path(directory) / "OWNER.md"
    try:
        with path.open() as f:
            first = f.readline(4096)
    except FileNotFoundError:
        return None
    except OSError:
        if strict:
            raise
        return None
    match = re.match(r"^card: (t_[0-9a-f]+)(@[A-Za-z0-9._-]+)?(?:\s|$)", first)
    return (match[1] + (match[2] or ""), str(path)) if match else None


def owner(table: dict, tags: dict, pid: int, strict: bool = False) -> dict:
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
        explicit = valid_owner(tags.get(q, {}).get("HERMES_OWNER_CARD", ""))
        if explicit:
            agent.update(HERMES_OWNER_CARD=explicit, owner_source="env")
            return agent
    for q in c:
        cwd = table[q].get("cwd")
        if strict and not cwd:
            raise OSError(f"unreadable cwd for pid {q}")
        found = read_owner_md(cwd, strict=True) if cwd and strict else read_owner_md(cwd) if cwd else None
        if found:
            agent.update(HERMES_OWNER_CARD=found[0], owner_source=found[1])
            break
    if "owner_source" not in agent and agent:
        agent["owner_source"] = "env"
    return agent
