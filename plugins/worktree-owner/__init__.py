"""worktree-owner: whoever creates a git worktree owns removing it.

Two hooks, both ``post_tool_call`` (never block or change a tool call):

1. Registration. After a ``terminal`` / ``execute_code`` call whose text contains ``worktree add``, every
   ``<repo>/.git/worktrees/<n>`` gitdir born during that call (any repo under ~/code at depth 1-2, plus
   ~/.hermes/hermes-agent and the call's own repo) gets ``hermes-owner.json``: the Kanban card
   (``HERMES_KANBAN_TASK``), session id, profile and time. The marker lives in git's own per-worktree dir, so
   it disappears with the worktree and is never committed. Works for for-loop fan-outs and nested trees inside a
   card's scratch workspace, because it reads git's state instead of parsing the shell command.

2. Teardown. When a Kanban worker calls ``kanban_complete`` for its own card, every worktree registered to that
   card, plus the card's own ``dir:`` workspace if that is a linked worktree no other open card uses, is removed
   with ``git worktree remove`` when it is clean and its HEAD is on a remote (nothing can be lost; the branch is
   kept). Dirty or unpushed trees are left for mac-worktree-gc, which archives then deletes them after 7 idle days.

Log: ~/.hermes/logs/worktree-owner.log.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

H = Path.home()
MARKER = "hermes-owner.json"
LOG = H / ".hermes" / "logs" / "worktree-owner.log"
REPO_GLOBS = [str(H / "code" / "*" / ".git"), str(H / "code" / "*" / "*" / ".git"), str(H / ".hermes" / "hermes-agent" / ".git")]
CREATE_TOOLS = {"terminal", "execute_code"}
SLACK_S = 5.0  # clock slack around the tool call window


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _git(cwd: str, *a: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True, timeout=timeout)


def _birth(p: str) -> float:
    st = os.stat(p)
    return getattr(st, "st_birthtime", st.st_ctime)


def _common_dirs(extra: list[str]) -> list[str]:
    out = []
    for g in REPO_GLOBS:
        out += [p for p in glob.glob(g) if os.path.isdir(p)]
    for d in extra:
        if d and os.path.isdir(d):
            r = _git(d, "rev-parse", "--path-format=absolute", "--git-common-dir", timeout=20)
            if r.returncode == 0 and r.stdout.strip():
                out.append(r.stdout.strip())
    seen, res = set(), []
    for p in out:
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            res.append(rp)
    return res


def new_gitdirs(since: float, extra_dirs: list[str] | None = None) -> list[str]:
    """Per-worktree gitdirs (<common>/worktrees/<n>) born at/after *since* that carry no owner marker yet."""
    found = []
    for common in _common_dirs(extra_dirs or []):
        wdir = os.path.join(common, "worktrees")
        try:
            names = os.listdir(wdir)
        except OSError:
            continue
        for n in names:
            gd = os.path.join(wdir, n)
            try:
                if _birth(gd) >= since and not os.path.exists(os.path.join(gd, MARKER)):
                    found.append(gd)
            except OSError:
                continue
    return found


def _wt_path(gd: str) -> str | None:
    try:
        return os.path.dirname(Path(gd, "gitdir").read_text().strip())
    except OSError:
        return None


def owner_from_env(env) -> dict:
    return {k: v for k, v in dict(task_id=env.get("HERMES_KANBAN_TASK") or None,
                                    session_id=env.get("HERMES_SESSION_ID") or None,
                                    profile=env.get("HERMES_PROFILE") or None).items() if v}


def _ours(path: str | None, text: str) -> bool:
    """Attribution guard: the new tree's dir name is in the command, or the command builds paths from variables
    (for-loops, $W/$b). A tree born in the same seconds by another agent and not named here is not claimed."""
    if not path:
        return False
    return os.path.basename(path.rstrip("/")) in text or "$" in text or "{" in text


def register_markers(since: float, env, extra_dirs: list[str] | None = None, text: str | None = None) -> list[dict]:
    owner = owner_from_env(env)
    out = []
    for gd in new_gitdirs(since, extra_dirs):
        path = _wt_path(gd)
        if text is not None and not _ours(path, text):
            _log(f"SKIP {path}: born during a 'worktree add' call but not named by it")
            continue
        rec = dict(owner, path=path, registered_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), host=os.uname().nodename.split(".")[0])
        try:
            Path(gd, MARKER).write_text(json.dumps(rec, indent=1))
            _log(f"REGISTER {path} owner={owner}")
            out.append(rec)
        except OSError as e:
            _log(f"WARN cannot write marker in {gd}: {e}")
    return out


def live_workspace_paths(exclude: str | None = None) -> set[str]:
    """Realpaths of workspace_path of open cards on the worker's board (minus *exclude*). Unreadable => {'*'}."""
    db = os.environ.get("HERMES_KANBAN_DB") or str(H / ".hermes" / "kanban.db")
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        rows = c.execute("select workspace_path from tasks where status not in ('done','archived') and id != ? "
                         "and coalesce(workspace_path,'') != ''", (exclude or "",)).fetchall()
        c.close()
        return {os.path.realpath(r[0]) for r in rows}
    except Exception:  # noqa: BLE001 - unknown means "maybe in use": keep everything
        return {"*"}


def _removable(path: str) -> str | None:
    """None if clean and HEAD is on a remote; else the reason to keep."""
    st = _git(path, "--no-optional-locks", "status", "--porcelain", timeout=300)
    if st.returncode != 0:
        return "git status failed"
    if st.stdout.strip():
        return "uncommitted/untracked changes"
    head = _git(path, "rev-parse", "HEAD").stdout.strip()
    if not _git(path, "branch", "-r", "--contains", head).stdout.strip():
        return "HEAD not on any remote"
    return None


def live_cwds() -> list[str]:
    r = subprocess.run(["/usr/sbin/lsof", "-a", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=120)
    return [l[1:] for l in r.stdout.splitlines() if l.startswith("n")]


def _remove(path: str, why_owned: str) -> dict:
    rp = os.path.realpath(path)
    if any(c == rp or c.startswith(rp + "/") or c == path or c.startswith(path + "/") for c in live_cwds()):
        _log(f"KEEP {path} ({why_owned}): a live process works in it")
        return dict(path=path, action="kept: live process")
    keep = _removable(path)
    if keep:
        _log(f"KEEP {path} ({why_owned}): {keep}; mac-worktree-gc archives it after 7 idle days")
        return dict(path=path, action=f"kept: {keep}")
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    main = os.path.dirname(common) if common else path
    r = _git(main, "worktree", "remove", path, timeout=600)
    if r.returncode != 0:
        _log(f"WARN remove {path} failed: {r.stderr.strip()[:200]}")
        return dict(path=path, action=f"kept: remove failed: {r.stderr.strip()[:120]}")
    _log(f"REMOVED {path} ({why_owned})")
    return dict(path=path, action="removed")


def teardown(task_id: str, env) -> list[dict]:
    results = []
    ws = env.get("HERMES_KANBAN_WORKSPACE") or ""
    for common in _common_dirs([ws]):
        for gd in glob.glob(os.path.join(common, "worktrees", "*")):
            try:
                rec = json.loads(Path(gd, MARKER).read_text())
            except (OSError, ValueError):
                continue
            path = rec.get("path") or _wt_path(gd)
            if rec.get("task_id") == task_id and path and os.path.isdir(path):
                results.append(_remove(path, f"registered to {task_id}"))
    # the card's own dir: workspace, when it is a linked worktree (a .git FILE) that no other open card uses
    if ws and os.path.isfile(os.path.join(ws, ".git")) and \
            not any(os.path.realpath(r["path"]) == os.path.realpath(ws) for r in results):
        live = live_workspace_paths(exclude=task_id)
        if "*" in live or os.path.realpath(ws) in live:
            _log(f"KEEP {ws}: another open card uses this workspace (or the board is unreadable)")
            results.append(dict(path=ws, action="kept: shared with an open card"))
        else:
            results.append(_remove(ws, f"dir workspace of {task_id}"))
    return results


def _mentions_worktree_add(text: str) -> bool:
    t = " ".join(text.replace('"', " ").replace("'", " ").replace(",", " ").split())
    return "worktree add" in t


def on_tool_call(tool_name: str, args: Any, duration_ms: Any = 0, env=None) -> list[dict]:
    env = os.environ if env is None else env
    try:
        name = str(tool_name or "")
        a = args if isinstance(args, dict) else {}
        if name in CREATE_TOOLS:
            if not _mentions_worktree_add(str(a.get("command") or a.get("code") or "")):
                return []
            dur = float(duration_ms) / 1000 if isinstance(duration_ms, (int, float)) and duration_ms else 600.0
            since = time.time() - dur - SLACK_S
            text = str(a.get("command") or a.get("code") or "")
            return register_markers(since, env, [a.get("workdir") or "", env.get("HERMES_KANBAN_WORKSPACE") or "", os.getcwd()], text)
        if name == "kanban_complete" and env.get("HERMES_KANBAN_TASK"):
            task = str(a.get("task_id") or env["HERMES_KANBAN_TASK"])
            if task != env["HERMES_KANBAN_TASK"]:
                return []  # completing another card (orchestrator/reviewer): not ours to tear down
            return teardown(task, env)
    except Exception as e:  # noqa: BLE001 - must never break a tool call
        _log(f"WARN hook failed for {tool_name}: {type(e).__name__}: {e}")
        logger.debug("worktree-owner hook failed", exc_info=True)
    return []


def _post_tool_call(tool_name: str = "", args: Any = None, duration_ms: Any = 0, **_: Any) -> None:
    on_tool_call(tool_name, args, duration_ms)
    return None


def register(ctx) -> None:
    ctx.register_hook("post_tool_call", _post_tool_call)
