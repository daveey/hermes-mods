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
   kept). Dirty or unpushed trees are always preserved, whatever they are registered to, nested scratch trees
   included. Requires core Git-bearing scratch preservation: the post-tool hook cannot recover data already
   deleted by older cores. Teardown requires the
   successful own-task/run result and durable completed run under a board writer reservation.

Log: ~/.hermes/logs/worktree-owner.log.
"""
from __future__ import annotations

import fnmatch
import glob
import re
import sys
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
REPO_GLOBS = [str(H / "code" / "*" / ".git"), str(H / "code" / "*" / "*" / ".git"), str(H / ".hermes" / "hermes-agent" / ".git")]
CREATE_TOOLS = {"terminal", "execute_code"}
SLACK_S = 5.0  # clock slack around the tool call window


_DRY = False  # sweep --dry-run: decide and report, remove/archive/log nothing


def _log(msg: str) -> None:
    if _DRY:
        print(f"DRY {msg}", file=sys.stderr)
        return
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


_NAME_CH = r"A-Za-z0-9._-"


def _names(text: str, name: str) -> bool:
    """*name* appears in *text* as a whole path component (`session-outcome` is not named by `session-outcome-desktop`)."""
    return bool(name) and re.search(rf"(?<![{_NAME_CH}]){re.escape(name)}(?![{_NAME_CH}])", text) is not None


def _built_prefixes(text: str, bases: list[str]) -> list[str]:
    """Literal path prefixes that a shell command builds worktree paths from: the part of a token before its first
    `$` (`~/code/wt-$b` -> `~/code/wt-`) and mktemp templates (`base.XXXX` -> `base.`). Relative ones are resolved
    against every candidate base dir. Returned as realpaths; a trailing `/` marks a bare directory prefix."""
    home = str(H)
    t = text.replace("${HOME}", home).replace("$HOME", home)
    out = []
    for tok in re.split(r"[\s'\"=;|&()<>`]+", t):
        if "$" in tok:
            pre = tok.split("$", 1)[0]
        elif re.search(r"X{3,}$", tok):
            pre = re.sub(r"X+$", "", tok)
        else:
            continue
        pre = re.split(r"[*?\[{]", pre, 1)[0]
        if not pre or pre in ("~", "~/"):
            continue
        pre = os.path.expanduser(pre)
        cands = [pre] if os.path.isabs(pre) else [os.path.join(b, pre) for b in bases if b and os.path.isabs(b)]
        for c in cands:
            head, tail = os.path.split(c)
            rp = os.path.realpath(head)
            out.append(os.path.join(rp, tail) if tail else rp.rstrip("/") + "/")
    return out


def _private_dir(d: str, env) -> bool:
    """A bare directory prefix is specific to this caller only when it is the card's own workspace (or inside it).
    Shared roots (~/code/_wt/, the scratch dir) are never enough: another agent puts its trees there in the same
    seconds. Neither is a directory born during the call: `git worktree add` creates missing parents for anyone."""
    ws = (env or {}).get("HERMES_KANBAN_WORKSPACE")
    return bool(ws) and (os.path.realpath(d) + "/").startswith(os.path.realpath(ws).rstrip("/") + "/")


def _ours(path: str | None, text: str, tool: str = "terminal", env=None, bases: list[str] | None = None) -> bool:
    """Attribution guard. A tree born during this call is claimed only on evidence that THIS call made it:
    - its directory name appears in the command as a whole path component, or
    - (terminal only) the command builds paths from variables and the tree lies under a literal prefix that
      command spells out: a partial name (`~/code/wt-$b`, `mktemp -d .../base.XXXX`), or a bare directory
      (`~/code/_wt/$b`) only if that directory is the card's own workspace.
    Being concurrent is never enough: at 12:47 on 2026-10-29 a card's `for i in 1 2; ... $D` test command claimed two
    trees a Claude Code session made in ~/code/_wt meanwhile, and the card's teardown removed them. Unclaimed trees
    stay unowned (the GC backstop handles them). execute_code must name the path: Python code is full of `{`."""
    if not path or re.search(r'\bssh\b', text):
        return False
    if _names(text, os.path.basename(path.rstrip("/"))):
        return True
    if tool != "terminal" or "$" not in text and not re.search(r"X{3,}", text):
        return False
    rp = os.path.realpath(path)
    for pre in _built_prefixes(text, list(bases or [])):
        if pre.endswith("/"):
            d = pre.rstrip("/") or "/"
            if (rp == d or rp.startswith(d + "/")) and rp != d and _private_dir(d, env):
                return True
        elif rp.startswith(pre) and os.path.basename(pre):
            return True
    return False


def _cd_targets(text: str, bases: list[str]) -> list[str]:
    out = []
    for m in re.finditer(r"(?:\bcd|\bgit\s+-C)\s+(['\"]?)([^\s;&|'\"]+)\1", text):
        p = os.path.expanduser(m.group(2))
        if "$" in p:
            continue
        out += [p] if os.path.isabs(p) else [os.path.join(b, p) for b in bases if b and os.path.isabs(b)]
    return out


def register_markers(since: float, env, extra_dirs: list[str] | None = None, text: str | None = None,
                     tool: str = "terminal", owner: dict | None = None) -> list[dict]:
    owner = owner_from_env(env) if owner is None else owner
    out = []
    bases = [d for d in (extra_dirs or []) if d]
    if text is not None:
        bases += _cd_targets(text, bases or [os.getcwd()])
    for gd in new_gitdirs(since, extra_dirs):
        path = _wt_path(gd)
        if text is not None and not _ours(path, text, tool, env, bases):
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


def _disposable_untracked(path: str, entry: str) -> bool:
    """Untracked porcelain entry that is a cache-named symlink (`node_modules` linked to a shared install, which a
    `node_modules/` ignore rule does not match): git unlinks it on removal and never touches the target."""
    rel = entry.rstrip("/")
    return os.path.islink(os.path.join(path, rel)) and _named_disposable(rel, _disposable_globs())


def _untracked_links(path: str) -> list[str] | None:
    """Disposable untracked symlinks when they are the only change in the tree; None if anything else changed."""
    st = _git(path, "--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all", timeout=300)
    if st.returncode != 0:
        return None
    entries = [e for e in st.stdout.split("\0") if e]
    if all(e.startswith("?? ") and _disposable_untracked(path, e[3:]) for e in entries):
        return [e[3:] for e in entries]
    return None


def _removable(path: str) -> str | None:
    """None if clean (cache-named untracked links aside) and HEAD is on a remote; else the reason to keep."""
    st = _git(path, "--no-optional-locks", "status", "--porcelain", timeout=300)
    if st.returncode != 0:
        return "git status failed"
    if st.stdout.strip() and _untracked_links(path) is None:
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


def _sudo_proc_enabled() -> bool:
    """Explicit per-profile opt-in; never prompt or change sudo policy."""
    home = Path(os.environ.get('HERMES_HOME') or H / '.hermes')
    try:
        return json.loads((home / 'worktree-owner.json').read_text()).get('linux_sudo_readlink') is True
    except FileNotFoundError:
        return False


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
                    before = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                except FileNotFoundError:
                    continue  # exited during enumeration
                if before[0] == 'Z':
                    continue
                if not _sudo_proc_enabled():
                    raise RuntimeError(f'cannot read cwd for live pid {name}')
                # Non-dumpable same-UID processes (systemd --user) deny readlink.
                # Only this read-only command may elevate, never Python or a shell.
                r = subprocess.run(['sudo', '-n', '--', '/usr/bin/readlink', '--', str(proc / 'cwd')],
                                   capture_output=True, text=True, timeout=10)
                try:
                    after = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                except FileNotFoundError:
                    continue
                if after[0] == 'Z':
                    continue
                if (r.returncode or r.stderr or not r.stdout.startswith('/') or
                        len(before) < 20 or len(after) < 20 or before[19] != after[19] or
                        proc.stat().st_uid != os.getuid()):
                    raise RuntimeError(f'cannot read cwd for live pid {name}')
                paths.append(r.stdout.removesuffix('\n'))
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


# Rebuildable caches: tools recreate them, so they never make a tree worth keeping. Any OTHER git-ignored file
# (.env, notes, local data) still keeps the tree. Directories carrying the standard CACHEDIR.TAG count too
# (https://bford.info/cachedir/: pytest, ruff, mypy, cargo and others write it). A host can add repo-specific
# build/test artifacts as globs in <HERMES_HOME>/worktree-owner.json `disposable_ignored` (matched against each
# path component and each leading relative path, e.g. "test_durations.json", ".hermes/bin").
CACHE_NAMES = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"})
DISPOSABLE_GLOBS = ("*.egg-info",)
CACHEDIR_TAG = "CACHEDIR.TAG"


def _disposable_globs() -> tuple[str, ...]:
    home = Path(os.environ.get('HERMES_HOME') or H / '.hermes')
    try:
        extra = json.loads((home / 'worktree-owner.json').read_text()).get('disposable_ignored') or []
    except (OSError, ValueError, AttributeError):
        extra = []
    return DISPOSABLE_GLOBS + tuple(str(g) for g in extra if isinstance(g, str) and g.strip('/*'))


def _named_disposable(rel: str, globs: tuple[str, ...]) -> bool:
    parts = [x for x in rel.strip("/").split("/") if x]
    for i, name in enumerate(parts):
        prefix = "/".join(parts[:i + 1])
        if name in CACHE_NAMES or any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(prefix, g.strip("/")) for g in globs):
            return True
    return False


def _is_cache_dir(p: str, root: str = "", globs: tuple[str, ...] = ()) -> bool:
    if os.path.basename(p.rstrip("/")) in CACHE_NAMES or os.path.isfile(os.path.join(p, CACHEDIR_TAG)):
        return True
    return bool(root and globs and _named_disposable(os.path.relpath(p, root), globs))


def _is_cache_entry(path: str, rel: str, globs: tuple[str, ...] = ()) -> bool:
    """True if ignored entry *rel* (relative to tree *path*) is, or is inside, a rebuildable cache dir. A collapsed
    directory entry (`pkg/` holding only `__pycache__`) counts when every file under it is in a cache."""
    if _named_disposable(rel, globs):
        return True
    parts = [x for x in rel.rstrip("/").split("/") if x]
    cur = path
    for x in parts:
        cur = os.path.join(cur, x)
        if os.path.isdir(cur) and not os.path.islink(cur) and os.path.isfile(os.path.join(cur, CACHEDIR_TAG)):
            return True
    if not (rel.endswith("/") and os.path.isdir(cur) and not os.path.islink(cur)):
        return False
    for root, dirs, files in os.walk(cur, followlinks=False):
        dirs[:] = [d for d in dirs if not _is_cache_dir(os.path.join(root, d), path, globs)]
        if any(not _named_disposable(os.path.relpath(os.path.join(root, f), path), globs) for f in files) \
                or any(os.path.islink(os.path.join(root, d)) for d in dirs):
            return False
    return True


def _complex_reason(path: str) -> str | None:
    if os.path.realpath(path) != os.path.abspath(path):
        return 'symlink target'
    globs = _disposable_globs()
    # Symlinks committed to the repo (mode 120000) are restored by any checkout: not data at risk.
    staged_links = _git(path, 'ls-files', '-s', '-z')
    if staged_links.returncode:
        return 'unreadable index'
    tracked_links = {e.split('\t', 1)[1] for e in staged_links.stdout.split('\0') if e.startswith('120000 ')}
    def fail(error):
        raise error
    for root, dirs, files in os.walk(path, onerror=fail, followlinks=False):
        for name in dirs + files:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, path)
            # An untracked link named like a cache (node_modules -> a shared install) is unlinked, never followed.
            if (os.path.islink(p) and rel not in tracked_links and not _named_disposable(rel, globs)) \
                    or (name == '.git' and root != path):
                return f'nested git or symlink: {rel}'
            if not os.path.islink(p) and not os.path.isdir(p) and not os.path.isfile(p):
                return f'special file: {rel}'
        # Don't descend into caches (node_modules/.bin is all symlinks); `git worktree remove` unlinks, never follows.
        dirs[:] = [d for d in dirs if not _is_cache_dir(os.path.join(root, d), path, globs)]
    staged = _git(path, 'diff', '--cached', '--quiet', 'HEAD')
    if staged.returncode != 0:
        return 'staged changes or unreadable index'
    ignored = _git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory', '-z')
    if ignored.returncode:
        return 'unreadable ignored-file listing'
    real = [e for e in ignored.stdout.split('\0') if e and not _is_cache_entry(path, e, globs)]
    if real:
        return 'ignored files: ' + ', '.join(real[:5]) + (f' (+{len(real) - 5} more)' if len(real) > 5 else '')
    return None


def _remove(path: str, why_owned: str) -> dict:
    rp = os.path.realpath(path)
    if any(c == rp or c.startswith(rp + "/") or c == path or c.startswith(path + "/") for c in live_cwds()):
        _log(f"KEEP {path} ({why_owned}): a live process works in it")
        return dict(path=path, action="kept: live process")
    complex_reason = _complex_reason(path)
    if complex_reason:
        _log(f"KEEP {path} ({why_owned}): {complex_reason}")
        return dict(path=path, action=f'kept: {complex_reason}')
    # Uncommitted/untracked changes or a HEAD on no remote keep the tree, whoever it is registered to and wherever
    # it lives (a nested scratch tree too: core preserves Git-bearing scratch). A card's cleanup never deletes work
    # that exists nowhere else; a mis-registered marker must not be able to cost data.
    keep = _removable(path)
    if keep:
        _log(f"KEEP {path} ({why_owned}): {keep}; {ARCHIVE_KEEP_SUFFIX}")
        return dict(path=path, action=f"kept: {keep}")
    if _DRY:
        return dict(path=path, action="would remove")
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    main = os.path.dirname(common) if common else path
    force = ["--force"] if _untracked_links(path) else []
    r = _git(main, "worktree", "remove", *force, path, timeout=600)
    if r.returncode != 0:
        _log(f"WARN remove {path} failed: {r.stderr.strip()[:200]}")
        return dict(path=path, action=f"kept: remove failed: {r.stderr.strip()[:120]}")
    _log(f"REMOVED {path} ({why_owned})")
    return dict(path=path, action="removed")


def teardown(task_id: str, env) -> list[dict]:
    """Completion path: the worker that just completed *task_id* in run HERMES_KANBAN_RUN_ID tears its trees down."""
    db, run = env.get('HERMES_KANBAN_DB'), env.get('HERMES_KANBAN_RUN_ID')
    if not db or not run:
        return []
    return _teardown(task_id, db, str(run))


RUN_SETTLED = (('done', 'completed'), ('completed', 'completed'))  # worker kanban_complete; operator/CLI close


def _teardown(task_id: str, db: str, run: str | None, min_age_s: float = 0.0,
              declined: list[str] | None = None) -> list[dict]:
    """Verify under the board writer reservation that *task_id* is settled (done, no current run, latest run
    done/completed and equal to *run*, or any id when *run* is None, no open child), then remove its trees.
    Every decline that has trees at stake is logged as a KEEP line; the sweep passes *declined* to get the
    reason for the checks below, which return before any tree is looked at."""
    # mode=rw never creates a missing board. Hold the writer reservation through
    # filesystem removal so reopen/reassignment cannot race our authorization.
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + '?mode=rw', uri=True, timeout=10)) as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('select status, current_run_id, workspace_path from tasks where id=?', (task_id,)).fetchone()
        if not row or row[0] != 'done' or row[1] is not None:
            if declined is not None and row and row[0] == 'done':
                declined.append(f'card has a current run {row[1]}')
            return []
        ended = c.execute('select id, status, outcome, ended_at from task_runs where task_id=? order by id desc limit 1',
                          (task_id,)).fetchone()
        if (not ended or (run is not None and str(ended[0]) != run) or tuple(ended[1:3]) not in RUN_SETTLED
                or ended[3] is None):
            if declined is not None and ended and run is None:
                declined.append(f'latest run {ended[0]} is {ended[1]}/{ended[2]}'
                                + ('' if ended[3] is not None else ', not ended'))
            return []
        if min_age_s and time.time() - float(ended[3]) < min_age_s:
            return []  # sweep: let the completing worker finish its own teardown first
        # Children consume parent artifacts even before receiving a workspace.
        kids = [r[0] for r in c.execute(
            "select t.id from task_links l join tasks t on t.id=l.child_id "
            "where l.parent_id=? and t.status not in ('done','archived','failed','cancelled')", (task_id,))]
        if kids:
            for path in _marked_paths(task_id, row[2] or ''):
                _log(f"KEEP {path} (registered to {task_id}): open child card(s) {', '.join(kids[:5])}; "
                     "torn down when they finish")
            return []
        live = {os.path.realpath(r[0]) for r in c.execute(
            "select workspace_path from tasks where status not in ('done','archived') and id != ? and coalesce(workspace_path,'') != ''", (task_id,))}
        # A tree made by ANY run of this card counts: review/retry loops finish in a later run than the one that
        # created the tree. The completing run was just verified to be the card's latest run.
        runs = {str(r[0]) for r in c.execute('select id from task_runs where task_id=?', (task_id,))}
        env = {'HERMES_KANBAN_DB': db, 'HERMES_KANBAN_RUN_ID': str(ended[0])}
        return _teardown_locked(task_id, env, row[2] or '', live, runs)


def _markers(extra: list[str] | None = None):
    """Yield (gitdir, marker dict, tree path) for every owner marker in the scanned repos."""
    for common in _common_dirs(extra or []):
        for gd in glob.glob(os.path.join(common, "worktrees", "*")):
            try:
                rec = json.loads(Path(gd, MARKER).read_text())
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict):
                yield gd, rec, rec.get("path") or _wt_path(gd)


def _marked_paths(task_id: str, ws: str) -> list[str]:
    return [p for _, rec, p in _markers([ws]) if rec.get('task_id') == task_id and p and os.path.isdir(p)]


SWEEP_STAMP = H / ".hermes" / "cache" / "worktree-owner-sweep.stamp"
SWEEP_EVERY_S = 3600.0
SWEEP_MIN_AGE_S = 600.0
SWEEP_MAX_AGE_S = 8 * 86400.0  # older trees are mac-worktree-gc's (it archives after 7 idle days)
DEFAULT_BOARD = H / ".hermes" / "kanban.db"  # board of legacy markers (no board_db), as mac-worktree-gc assumes
ARCHIVE_KEEP_SUFFIX = "mac-worktree-gc archives it after 7 idle days"  # GC: kept on purpose, not stale


def sweep(min_age_s: float = SWEEP_MIN_AGE_S, max_age_s: float = SWEEP_MAX_AGE_S, dry_run: bool = False) -> list[dict]:
    """Tear down the trees of every settled card on this host, whatever closed it.

    The completion hook runs once, in the completing worker. It declines while a child card is open, a process
    still works in a tree, or when the card was closed by the CLI/REST, and nothing retried those later: trees of
    finished cards stayed until the 7-day GC. The sweep re-runs the same verified teardown for each marked card
    on its own board (status done, latest run done/completed, no open child). mac-worktree-gc --all-repos runs it
    hourly too, so hosts with few completions re-check. *dry_run* reports what it would do and changes nothing."""
    global _DRY
    if dry_run:
        _DRY = True
        try:
            return sweep(min_age_s, max_age_s)
        finally:
            _DRY = False
    results = []
    seen = set()
    for _, rec, path in list(_markers()):
        task, db = rec.get('task_id'), rec.get('board_db')
        legacy = not db or rec.get('run_id') is None
        db = db or str(DEFAULT_BOARD)
        if not task or (not legacy and (db, task) in seen) or not path or not os.path.isdir(path) \
                or not os.path.isfile(db):
            continue
        seen.add((db, task))
        try:
            with closing(_ro_connect(db)) as c:
                ended = c.execute('select status, outcome, ended_at from task_runs where task_id=? order by id desc limit 1',
                                  (task,)).fetchone()
                st = c.execute('select status from tasks where id=?', (task,)).fetchone()
            if not st or st[0] != 'done' or not ended or ended[2] is None or \
                    time.time() - float(ended[2]) > max_age_s or time.time() - float(ended[2]) < min_age_s:
                continue
            if legacy:
                # Written before markers carried board_db/run_id (4e8d1f2): the fence can't prove this card made
                # the tree, so it is the GC's. Say so, or the report keeps quoting the completion-time reason.
                _log(f"KEEP {path} (registered to {task}): legacy marker without a board/run id; "
                     f"{ARCHIVE_KEEP_SUFFIX}")
                results.append(dict(path=path, action='kept: legacy marker'))
                continue
            declined: list[str] = []
            results += _teardown(task, db, None, min_age_s, declined)
            for why in declined:
                for p in _marked_paths(task, ''):
                    _log(f"KEEP {p} (registered to {task}): {why}")
                    results.append(dict(path=p, action=f'kept: {why}'))
        except Exception as e:  # noqa: BLE001 - one bad board must not stop the others
            _log(f"WARN sweep of {task} on {db} failed: {type(e).__name__}: {e}")
    return results


def _spawn_sweep() -> None:
    """At most hourly, run sweep() in a detached child so the completing tool call never waits on it."""
    try:
        if SWEEP_STAMP.exists() and time.time() - SWEEP_STAMP.stat().st_mtime < SWEEP_EVERY_S:
            return
        SWEEP_STAMP.parent.mkdir(parents=True, exist_ok=True)
        SWEEP_STAMP.touch()
        env = {k: v for k, v in os.environ.items() if not k.startswith('HERMES_KANBAN_')}
        subprocess.Popen([sys.executable, '-B', os.path.abspath(__file__), 'sweep'], cwd='/', env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception as e:  # noqa: BLE001
        _log(f"WARN cannot start sweep: {type(e).__name__}: {e}")


def _overlap(a: str, b: str) -> bool:
    a, b = os.path.realpath(a), os.path.realpath(b)
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def _marker_fence(rec: dict, task_id: str, env, runs: set[str] | None) -> str | None:
    """None when marker *rec* proves this card made the tree (this board, one of its runs); else why not."""
    if rec.get('board_db') != os.path.realpath(env['HERMES_KANBAN_DB']):
        return f"marker board {rec.get('board_db')} is not this board"
    run = rec.get('run_id')
    if run is None:
        return 'legacy marker without a run id'
    if str(run) not in {str(env.get('HERMES_KANBAN_RUN_ID'))} | (runs or set()):
        return f'marker run {run} is not a run of {task_id}'
    return None


def _teardown_locked(task_id: str, env, ws: str, live: set[str], runs: set[str] | None = None) -> list[dict]:
    results = []
    def remove(path, why):
        if any(_overlap(path, other) for other in live):
            _log(f"KEEP {path} ({why}): shared with an open card")
            return dict(path=path, action='kept: shared with an open card')
        return _remove(path, why)
    for gd, rec, path in _markers([ws]):
        if rec.get('task_id') != task_id or not path or not os.path.isdir(path):
            continue
        if os.path.realpath(path) != os.path.realpath(_wt_path(gd) or '/'):
            _log(f"KEEP {path} (registered to {task_id}): marker path is not git's path for this worktree")
            continue
        fence = _marker_fence(rec, task_id, env, runs)
        if fence:
            _log(f"KEEP {path} (registered to {task_id}): {fence}")
            continue
        results.append(remove(path, f"registered to {task_id}"))
    # the card's own dir: workspace, when it is a linked worktree (a .git FILE) that no other open card uses
    if ws and os.path.isfile(os.path.join(ws, ".git")) and \
            not any(os.path.realpath(r["path"]) == os.path.realpath(ws) for r in results):
        gd = _git(ws, 'rev-parse', '--absolute-git-dir')
        if gd.returncode:
            return results
        marker = Path(gd.stdout.strip(), MARKER)
        if marker.exists():
            try:
                rec = json.loads(marker.read_text())
            except (OSError, ValueError):
                return results
            if rec.get('task_id'):
                fence = (f"marker belongs to {rec.get('task_id')}" if rec.get('task_id') != task_id
                         else _marker_fence(rec, task_id, env, runs))
                if fence:
                    _log(f"KEEP {ws} (dir workspace of {task_id}): {fence}")
                    return results  # own workspace fallback must not bypass marker fence
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
                    or str(r.get('run_id')) != env['HERMES_KANBAN_RUN_ID']):
                return []
            # An explicit board is safe only when the worker identifies that board.
            if a.get('board') is not None and a['board'] != env.get('HERMES_KANBAN_BOARD'):
                return []
            res = teardown(task, env)
            _spawn_sweep()  # parents waiting on this card, CLI-closed cards, trees a process held at completion
            return res
    except Exception as e:  # noqa: BLE001 - must never break a tool call
        _log(f"WARN hook failed for {tool_name}: {type(e).__name__}: {e}")
        logger.debug("worktree-owner hook failed", exc_info=True)
    return []


def _post_tool_call(tool_name: str = "", args: Any = None, duration_ms: Any = 0, result=None, **_: Any) -> None:
    on_tool_call(tool_name, args, duration_ms, result=result)
    return None


def register(ctx) -> None:
    ctx.register_hook("post_tool_call", _post_tool_call)


if __name__ == "__main__":
    if sys.argv[1:] in (["sweep"], ["sweep", "--dry-run"]):
        for r in sweep(dry_run=sys.argv[2:] == ["--dry-run"]):
            print(json.dumps(r))
    else:
        print("usage: __init__.py sweep [--dry-run]", file=sys.stderr)
        sys.exit(2)
