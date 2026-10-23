"""Refuse direct `git push` of code to a guarded branch (a repo whose main lands only through a merge queue/train).

Config ``$HERMES_HOME/push-guards.json`` (no file = no-op)::

    {"guards": [{"remote": "owner/repo", "branch": "main", "allow": ["docs/*", "*.md"],
                 "use": "my-land-tool <branch> --wait"}]}

A push whose destination is ``branch`` on a remote whose URL contains ``remote`` is checked: if every file it changes
against ``<remote>/<branch>`` matches an ``allow`` glob (fnmatch; ``*`` crosses ``/``) it goes through, otherwise it is
blocked with a pointer to ``use``. ``PUSH_GUARD=off`` anywhere in the command skips the check (emergencies; say why).
Shared by the Hermes pre_tool_call hook (__init__.py).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Optional

SEPARATORS = re.compile(r"&&|\|\||;|\n|\|")
OVERRIDE = "PUSH_GUARD=off"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def load_guards() -> list[dict]:
    f = Path(os.environ.get("PUSH_GUARD_CONFIG") or hermes_home() / "push-guards.json")
    try:
        raw = json.loads(f.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for g in (raw.get("guards") if isinstance(raw, dict) else None) or []:
        if isinstance(g, dict) and g.get("remote") and g.get("branch"):
            out.append({"remote": str(g["remote"]), "branch": str(g["branch"]), "allow": list(g.get("allow") or []),
                        "use": str(g.get("use") or "the repo's merge queue")})
    return out


def _resolve(p: str, base: Optional[str] = None) -> str:
    p = os.path.expanduser(os.path.expandvars(p))
    if base and not os.path.isabs(p):
        p = os.path.join(base, p)
    return os.path.realpath(p)


def _git(repo: str, *args: str) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _git_calls(cmd: str, cwd: str):
    """Yield (repo dir, subcommand, args) for each git invocation, following `cd` and `git -C`."""
    for seg in SEPARATORS.split(cmd or ""):
        try:
            words = shlex.split(seg, comments=True)
        except ValueError:
            words = seg.split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        while words and words[0] in ("sudo", "env", "command", "exec", "time", "nice", "timeout"):
            words = words[1:]
            while words and re.match(r"^(-\S+|\d+[smh]?)$", words[0]):
                words = words[1:]
        if not words:
            continue
        if words[0] in ("cd", "pushd") and len(words) > 1:
            cwd = _resolve(words[1], cwd)
            continue
        if os.path.basename(words[0]) != "git":
            continue
        repo, i = cwd, 1
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                repo = _resolve(words[i + 1], repo)
                i += 2
            elif words[i] in ("-c", "--git-dir", "--work-tree") and i + 1 < len(words):
                i += 2
            else:
                i += 1
        if i < len(words):
            yield repo, words[i], words[i + 1:]


VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def _push_targets(repo: str, args: list[str]):
    """(remote, [(src, dst branch)]) for `git push <args>`."""
    pos, i = [], 0
    while i < len(args):
        a = args[i]
        if a in VALUE_OPTS:
            i += 2
            continue
        if not a.startswith("-"):
            pos.append(a)
        i += 1
    cur = _git(repo, "symbolic-ref", "-q", "--short", "HEAD") or "HEAD"
    remote = pos[0] if pos else (_git(repo, "config", f"branch.{cur}.remote") or "origin")
    specs = pos[1:]
    if not specs:  # bare push: the current branch to its upstream (push.default simple/upstream)
        up = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or ""
        return remote, [("HEAD", up.split("/", 1)[1] if "/" in up else cur)]
    out = []
    for s in specs:
        s = s.lstrip("+")
        src, _, dst = s.partition(":")
        if not _:
            dst = cur if src == "HEAD" else src
        dst = re.sub(r"^refs/heads/", "", dst)
        out.append((src, dst))
    return remote, out


def _allowed(files: list[str], globs: list[str]) -> bool:
    return bool(files) and all(any(fnmatch.fnmatch(f, g) for g in globs) for f in files)


def check_shell(cmd: str, cwd: Optional[str] = None, guards: Optional[list[dict]] = None) -> Optional[str]:
    guards = load_guards() if guards is None else guards
    if not guards or "push" not in (cmd or "") or OVERRIDE in (cmd or ""):
        return None
    cwd = _resolve(cwd or os.getcwd())
    for repo, sub, args in _git_calls(cmd, cwd):
        if sub != "push":
            continue
        remote, targets = _push_targets(repo, args)
        url = _git(repo, "remote", "get-url", remote) or remote
        for g in guards:
            if g["remote"] not in url.replace(":", "/"):
                continue
            for src, dst in targets:
                if dst != g["branch"]:
                    continue
                if not src:
                    return f"`git push {remote} :{dst}` deletes {g['remote']} {dst}"
                diff = _git(repo, "diff", "--name-only", f"{remote}/{dst}...{src}")
                files = [f for f in (diff or "").splitlines() if f]
                if diff is not None and not files:
                    continue  # nothing new to push
                if diff is not None and _allowed(files, g["allow"]):
                    continue
                what = ", ".join(files[:5]) + (f" (+{len(files) - 5} more)" if len(files) > 5 else "") \
                    if files else "files it could not list"
                return (f"`git push {' '.join(args)[:80]}` pushes code straight to {g['remote']} {dst} ({what})||"
                        + g["use"] + "||" + ", ".join(g["allow"]))
    return None


def check_python(code: str, cwd: Optional[str] = None, guards: Optional[list[dict]] = None) -> Optional[str]:
    """Shell strings inside Python (subprocess.run("...", shell=True), terminal("...")) and argv lists."""
    guards = load_guards() if guards is None else guards
    if not guards or "push" not in (code or ""):
        return None
    for lit in re.findall(r"""(?:"([^"\n]*\bgit\b[^"\n]*\bpush\b[^"\n]*)"|'([^'\n]*\bgit\b[^'\n]*\bpush\b[^'\n]*)')""",
                          code or ""):
        why = check_shell(lit[0] or lit[1], cwd, guards)
        if why:
            return why
    for m in re.finditer(r"\[\s*[\"']git[\"'](?:\s*,\s*[\"'][^\"']*[\"'])+\s*\]", code or ""):
        words = re.findall(r"[\"']([^\"']*)[\"']", m.group(0))
        if "push" in words:
            why = check_shell(shlex.join(words), cwd, guards)
            if why:
                return why
    return None


def message(why: str) -> str:
    what, use, allow = (why.split("||") + ["", ""])[:3]
    return (f"PUSH GUARD: blocked: {what}. This branch takes code only through its merge train: run `{use}` instead "
            f"(it pushes your branch, opens or reuses the PR and lands it in the express lane). Pushes that change "
            f"only {allow or 'allowed paths'} may still go direct. Emergency only: prefix the command with "
            f"`{OVERRIDE}` and say why in your report.")
