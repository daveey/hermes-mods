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
   kept). Dirty or unpushed trees are left for mac-worktree-gc, which archives then deletes them after 7 idle days,
   except trees nested in the card's scratch workspace (deleted by the dispatcher right after completion): those are
   archived to ~/.hermes/archive/worktrees/<name>-<date>/ first, then force-removed.

Log: ~/.hermes/logs/worktree-owner.log.
"""
from __future__ import annotations

import glob
import re
import sys
import tarfile
from contextlib import closing
import json
import logging
import os
import sqlite3
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

H = Path.home()
MARKER = "hermes-owner.json"
LOG = H / ".hermes" / "logs" / "worktree-owner.log"
ARCHIVE = H / ".hermes" / "archive" / "worktrees"
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


def _birth(p: str) -> float | None:
    """Creation time, never Unix ctime (which changes on metadata writes)."""
    st = os.stat(p)
    birth = getattr(st, "st_birthtime", 0)
    if birth > 0:
        return birth
    try:
        r = subprocess.run(["stat", "-c", "%W", "--", p], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and int(r.stdout.strip()) > 0:
            return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    # Only an intact initial HEAD reflog (null old object id) proves creation.
    # An expired/truncated log starts later and must not claim an old tree.
    try:
        with open(os.path.join(p, "logs", "HEAD")) as fh:
            first = fh.readline().split("\t", 1)[0]
        ids = first.split(" ", 2)
        timestamp, zone = first.rsplit("> ", 1)[1].split()
        if len(ids[0]) in (40, 64) and set(ids[0]) == {"0"} and int(timestamp) > 0:
            return float(timestamp)
    except (OSError, ValueError, IndexError):
        pass
    return None


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
                birth = _birth(gd)
                if birth is not None and birth >= since and not os.path.exists(os.path.join(gd, MARKER)):
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
                                    board_db=os.path.realpath(env['HERMES_KANBAN_DB']) if env.get('HERMES_KANBAN_DB') else None,
                                    run_id=env.get('HERMES_KANBAN_RUN_ID') or None,
                                    profile=env.get("HERMES_PROFILE") or None).items() if v}


def _ours(path: str | None, text: str, tool: str = "terminal") -> bool:
    """Attribution guard: the new tree's dir name is in the command, or (terminal only) a shell loop builds the paths
    from variables (`for b in ...; do git worktree add ... $W/$b`). A tree another agent creates in the same seconds
    and that this call does not name is not claimed. execute_code must name the path: Python code is full of `{`."""
    if not path or re.search(r'\bssh\b', text):
        return False
    if os.path.basename(path.rstrip("/")) in text:
        return True
    if tool != "terminal" or "$" not in text:
        return False
    return any(k in text for k in ("for ", "while ", "xargs", "parallel "))


def register_markers(since: float, env, extra_dirs: list[str] | None = None, text: str | None = None,
                     tool: str = "terminal", owner: dict | None = None) -> list[dict]:
    owner = owner_from_env(env) if owner is None else owner
    out = []
    for gd in new_gitdirs(since, extra_dirs):
        path = _wt_path(gd)
        if text is not None and not _ours(path, text, tool):
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


def _ro_connect(path, timeout=10):
    """Read-only open. mode=ro fails on a WAL db while its -wal/-shm is absent (writers delete them on last close),
    so fall back to mode=rw + query_only, which reads the same data and never writes rows."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)
        c.execute("select 1 from sqlite_master limit 1")
        return c
    except sqlite3.OperationalError:
        c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=timeout)
        c.execute("PRAGMA query_only=ON")
        return c


def live_workspace_paths(exclude: str | None = None) -> set[str]:
    """Realpaths of workspace_path of open cards on the worker's board (minus *exclude*). Unreadable => {'*'}."""
    db = os.environ.get("HERMES_KANBAN_DB") or str(H / ".hermes" / "kanban.db")
    try:
        c = _ro_connect(db, timeout=10)
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
    head = _git(path, 'rev-parse', 'HEAD')
    if head.returncode:
        return 'git status failed'
    remotes = _git(path, 'branch', '-r', '--contains', head.stdout.strip())
    if remotes.returncode:
        return 'git status failed'
    if not remotes.stdout.strip():
        return "HEAD not on any remote"
    return None


def _proc_cwds(root: str = '/proc') -> list[str]:
    paths = []
    for name in os.listdir(root):
        if not name.isdigit():
            continue
        proc = Path(root, name)
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            try:
                paths.append(os.readlink(proc / 'cwd'))
            except (FileNotFoundError, PermissionError):
                # Linux may report EACCES, not ENOENT, for a zombie cwd.
                # A zombie has no cwd; any other existing process is unknown.
                try:
                    state = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0]
                except FileNotFoundError:
                    continue  # exited during enumeration
                if state != 'Z':
                    raise RuntimeError(f'cannot read cwd for live pid {name}')
        except FileNotFoundError:
            continue  # disappeared before stat
    if not paths:
        raise RuntimeError('empty process cwd scan')
    return paths


def live_cwds() -> list[str]:
    """Raise on an unavailable/incomplete process scan: unknown is never idle."""
    if sys.platform.startswith('linux'):
        return _proc_cwds()
    lsof = shutil.which("lsof")
    if not lsof and os.access("/usr/sbin/lsof", os.X_OK):
        lsof = "/usr/sbin/lsof"  # macOS launchers may omit sbin from PATH
    if not lsof:
        raise RuntimeError("cannot check live cwd: lsof is not installed")
    r = subprocess.run([lsof, "-a", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=120)
    paths = [l[1:] for l in r.stdout.splitlines() if l.startswith("n/")]
    if r.returncode != 0 or r.stderr.strip() or not paths:
        raise RuntimeError(f"cannot check live cwd: lsof failed or incomplete (exit {r.returncode}): {r.stderr.strip()[:200]}")
    return paths


def _archive(path: str) -> str:
    """Save unpushed commits (bundle + patches), tracked changes (binary diff) and untracked files (tar) of *path*
    under ARCHIVE/<name>-<YYYYMMDD>[-N]/. Returns the archive dir; raises on any failure."""
    stamp = time.strftime("%Y%m%d")
    dst = ARCHIVE / f"{os.path.basename(path.rstrip('/'))}-{stamp}"
    i = 1
    while dst.exists():
        i += 1
        dst = ARCHIVE / f"{os.path.basename(path.rstrip('/'))}-{stamp}-{i}"
    dst.mkdir(parents=True)
    head_result = _git(path, 'rev-parse', 'HEAD')
    if head_result.returncode:
        raise RuntimeError('HEAD lookup failed')
    head = head_result.stdout.strip()
    meta = dict(path=path, head=head,
                archived_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), by="worktree-owner")
    # Full history: recovery must not depend on the original repo or remote.
    b = _git(path, 'bundle', 'create', str(dst / 'unpushed.bundle'), 'HEAD', timeout=600)
    if b.returncode or _git(path, 'bundle', 'verify', str(dst / 'unpushed.bundle')).returncode:
        raise RuntimeError('bundle creation/verification failed')
    d = subprocess.run(["git", "-C", path, "diff", "HEAD", "--binary"], capture_output=True, timeout=600)
    if d.returncode != 0:
        raise RuntimeError("diff failed")
    if d.stdout.strip():
        (dst / "uncommitted.diff").write_bytes(d.stdout)
    listing = _git(path, 'ls-files', '-o', '--exclude-standard', '-z', timeout=600)
    if listing.returncode:
        raise RuntimeError('untracked listing failed')
    un = [f for f in listing.stdout.split('\0') if f]
    if un:
        lst = dst / "untracked.list"
        lst.write_text("\0".join(un) + "\0")
        t = subprocess.run(["tar", "-czf", str(dst / "untracked.tar.gz"), "-C", path, "--null", "-T", str(lst)],
                           capture_output=True, timeout=900)
        if t.returncode != 0:
            raise RuntimeError(f"tar failed: {t.stderr[:150]!r}")
        with tarfile.open(dst / 'untracked.tar.gz') as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        while stream.read(1024 * 1024):
                            pass
        lst.unlink()
        meta["untracked_files"] = len(un)
    (dst / "meta.json").write_text(json.dumps(meta, indent=1))
    return str(dst)


def _in_scratch(path: str, ws: str) -> bool:
    """Inside the card's own scratch workspace, which the dispatcher deletes after completion."""
    if not ws or "/kanban/workspaces/" not in ws:
        return False
    rp, rw = os.path.realpath(path), os.path.realpath(ws)
    return rp.startswith(rw + "/")


def _complex_reason(path: str) -> str | None:
    if os.path.realpath(path) != os.path.abspath(path):
        return 'symlink target'
    def fail(error):
        raise error
    for root, dirs, files in os.walk(path, onerror=fail, followlinks=False):
        for name in dirs + files:
            p = os.path.join(root, name)
            if os.path.islink(p) or (name == '.git' and root != path):
                return 'nested git or symlink'
            if not os.path.isdir(p) and not os.path.isfile(p):
                return 'special file'
    ignored = _git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z')
    if ignored.returncode or ignored.stdout:
        return 'ignored files or unreadable listing'
    return None


def _remove(path: str, why_owned: str, ws: str = "") -> dict:
    rp = os.path.realpath(path)
    if any(c == rp or c.startswith(rp + "/") or c == path or c.startswith(path + "/") for c in live_cwds()):
        _log(f"KEEP {path} ({why_owned}): a live process works in it")
        return dict(path=path, action="kept: live process")
    complex_reason = _complex_reason(path)
    if complex_reason:
        return dict(path=path, action=f'kept: {complex_reason}')
    keep = _removable(path)
    archived = None
    if keep and _in_scratch(path, ws) and keep != "git status failed":
        # the dispatcher will rmtree this scratch dir right after completion: save the work, then remove properly
        try:
            archived = _archive(path)
            _log(f"ARCHIVED {path} -> {archived} ({keep})")
        except Exception as e:  # noqa: BLE001
            _log(f"WARN archive of {path} failed: {e}")
            return dict(path=path, action=f"kept: {keep}; archive failed: {e}")
    elif keep:
        _log(f"KEEP {path} ({why_owned}): {keep}; mac-worktree-gc archives it after 7 idle days")
        return dict(path=path, action=f"kept: {keep}")
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    main = os.path.dirname(common) if common else path
    r = _git(main, "worktree", "remove", *(["--force", "--force"] if archived else []), path, timeout=600)
    if r.returncode != 0:
        _log(f"WARN remove {path} failed: {r.stderr.strip()[:200]}")
        return dict(path=path, action=f"kept: remove failed: {r.stderr.strip()[:120]}")
    _log(f"REMOVED {path} ({why_owned})")
    return dict(path=path, action="archived+removed" if archived else "removed", archive=archived)


def teardown(task_id: str, env) -> list[dict]:
    db, run = env.get('HERMES_KANBAN_DB'), env.get('HERMES_KANBAN_RUN_ID')
    if not db or not run:
        return []
    # mode=rw never creates a missing board. Hold the writer reservation through
    # filesystem removal so reopen/reassignment cannot race our authorization.
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + '?mode=rw', uri=True, timeout=10)) as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('select status, current_run_id, workspace_path from tasks where id=?', (task_id,)).fetchone()
        if not row or row[0] != 'done' or row[1] != run:
            return []
        live = {os.path.realpath(r[0]) for r in c.execute(
            "select workspace_path from tasks where status not in ('done','archived') and id != ? and coalesce(workspace_path,'') != ''", (task_id,))}
        return _teardown_locked(task_id, env, row[2] or '', live)


def _overlap(a: str, b: str) -> bool:
    a, b = os.path.realpath(a), os.path.realpath(b)
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def _teardown_locked(task_id: str, env, ws: str, live: set[str]) -> list[dict]:
    results = []
    def remove(path, why, scratch=''):
        if any(_overlap(path, other) for other in live):
            return dict(path=path, action='kept: shared with an open card')
        return _remove(path, why, scratch)
    for common in _common_dirs([ws]):
        for gd in glob.glob(os.path.join(common, "worktrees", "*")):
            try:
                rec = json.loads(Path(gd, MARKER).read_text())
            except (OSError, ValueError):
                continue
            path = rec.get("path") or _wt_path(gd)
            if (rec.get('task_id') == task_id and rec.get('run_id') == env.get('HERMES_KANBAN_RUN_ID')
                    and rec.get('board_db') == os.path.realpath(env['HERMES_KANBAN_DB'])
                    and path and os.path.isdir(path) and os.path.realpath(path) == os.path.realpath(_wt_path(gd) or '/')):
                results.append(remove(path, f"registered to {task_id}", ws))
    # the card's own dir: workspace, when it is a linked worktree (a .git FILE) that no other open card uses
    if ws and os.path.isfile(os.path.join(ws, ".git")) and \
            not any(os.path.realpath(r["path"]) == os.path.realpath(ws) for r in results):
        results.append(remove(ws, f"dir workspace of {task_id}"))
    return results


def _mentions_worktree_add(text: str) -> bool:
    t = " ".join(text.replace('"', " ").replace("'", " ").replace(",", " ").split())
    return "worktree add" in t


def on_tool_call(tool_name: str, args: Any, duration_ms: Any = 0, env=None, result=None) -> list[dict]:
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
            return register_markers(since, env, [a.get("workdir") or "", env.get("HERMES_KANBAN_WORKSPACE") or "", os.getcwd()],
                                    text, name)
        if name == "kanban_complete" and env.get("HERMES_KANBAN_TASK"):
            task = str(a.get("task_id") or env["HERMES_KANBAN_TASK"])
            if task != env["HERMES_KANBAN_TASK"]:
                return []  # completing another card (orchestrator/reviewer): not ours to tear down
            r = json.loads(result) if isinstance(result, str) else result
            if (not isinstance(r, dict) or r.get('ok') is not True or r.get('error')
                    or r.get('task_id') != task or not env.get('HERMES_KANBAN_RUN_ID')
                    or r.get('run_id') != env['HERMES_KANBAN_RUN_ID']):
                return []
            # An explicit board is safe only when the worker identifies that board.
            if a.get('board') is not None and a['board'] != env.get('HERMES_KANBAN_BOARD'):
                return []
            return teardown(task, env)
    except Exception as e:  # noqa: BLE001 - must never break a tool call
        _log(f"WARN hook failed for {tool_name}: {type(e).__name__}: {e}")
        logger.debug("worktree-owner hook failed", exc_info=True)
    return []


def _post_tool_call(tool_name: str = "", args: Any = None, duration_ms: Any = 0, result=None, **_: Any) -> None:
    on_tool_call(tool_name, args, duration_ms, result=result)
    return None


def register(ctx) -> None:
    ctx.register_hook("post_tool_call", _post_tool_call)
