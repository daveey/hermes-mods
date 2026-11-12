"""Bounded, documentation-only publication; never commit or stage in the live tree."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from contextvars import ContextVar
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Callable

MAX_PATHS = 16
MAX_BYTES = 1024 * 1024
LOCK_WAIT = 5.0
WORK_BUDGET = 45.0
CLEANUP_BUDGET = 5.0
REAP_BUDGET = 2.0
_budget: ContextVar[tuple[float, Callable[[], float]] | None] = ContextVar(
    'skill_publication_budget', default=None)


@contextmanager
def operation_budget(seconds, clock):
    token = _budget.set((clock() + seconds, clock))
    try:
        yield
    finally:
        _budget.reset(token)


def remaining(cap):
    budget = _budget.get()
    if budget is None:
        return cap
    deadline, clock = budget
    left = deadline - clock()
    if left <= 0:
        raise ValueError('publication deadline exceeded')
    return min(cap, left)


GIT_ROUTING_ENV = {
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_PREFIX',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE',
    'GIT_QUARANTINE_PATH', 'GIT_IMPLICIT_WORK_TREE', 'GIT_CEILING_DIRECTORIES',
    'GIT_DISCOVERY_ACROSS_FILESYSTEM', 'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE',
    'GIT_REPLACE_REF_BASE', 'GIT_NO_REPLACE_OBJECTS', 'GIT_EXTERNAL_DIFF'}
DIFF_FLAGS = ('--no-ext-diff', '--no-textconv', '--no-color')
SAFE_SUFFIXES = {'.md', '.txt', '.rst', '.csv', '.json', '.yaml', '.yml',
                 '.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf'}


# Host tool directories (gitleaks, gh) appended to PATH for every publisher subprocess.
# A launchd-started backend (the MacBook Desktop serve) inherits a PATH without
# /opt/homebrew/bin, so the pre-commit hook could not find gitleaks and refused.
TOOL_DIRS = ('/opt/homebrew/bin', '/usr/local/bin', '~/.local/bin')


def tool_path(path):
    """PATH with each existing TOOL_DIRS entry appended once, after the inherited order."""
    entries = path.split(os.pathsep) if path else []
    present = {os.path.normpath(e) for e in entries if e}
    for directory in TOOL_DIRS:
        directory = os.path.normpath(os.path.expanduser(directory))
        if directory not in present and os.path.isdir(directory):
            entries.append(directory)
            present.add(directory)
    return os.pathsep.join(entries)


def clean_env(env=None):
    """Inherited environment minus Git repository-routing variables, with host tool dirs on PATH."""
    env = {k: v for k, v in (os.environ if env is None else env).items()
           if k not in GIT_ROUTING_ENV and not k.startswith('GIT_CONFIG')}
    env['PATH'] = tool_path(env.get('PATH', os.defpath))
    return env


def bounded(command, *, timeout, data=None, env=None, cwd=None):
    """Timeout the whole subprocess group, including Git transports and hooks."""
    timeout = remaining(timeout)
    # An explicit env (from run) is already sanitized and may carry a snapshot index.
    env = dict(clean_env() if env is None else env,
               GIT_TERMINAL_PROMPT='0', GH_PROMPT_DISABLED='1')
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, cwd=cwd,
                          start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(data, timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=REAP_BUDGET)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def run(root, *args, data=None, env=None, index=None):
    """Run Git; inherited routing is stripped, only an explicit snapshot index is honoured."""
    env = dict(clean_env(env), GIT_OPTIONAL_LOCKS='0')
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    credential_args = []
    # Resolve the effective fetch/push URL (including insteadOf and pushurl).
    # Only this Git subprocess receives the helper; never persist config or extract
    # a token into argv, environment, logs, or an archive.
    if 'fetch' in args or 'push' in args:
        from urllib.parse import urlsplit
        options = ('--push',) if 'push' in args else ()
        url = run(root, 'remote', 'get-url', *options, 'origin', env=env).decode().strip()
        parsed = urlsplit(url)
        if parsed.scheme == 'https' and parsed.hostname:
            scope = 'credential.https://' + parsed.hostname + '.helper'
            credential_args = ['-c', scope + '=', '-c', scope + '=!gh auth git-credential']
    p = bounded(['git', '-C', str(root), *credential_args, *args], data=data, timeout=30, env=env)
    if p.returncode:
        raise ValueError(p.stderr.decode(errors='replace').strip() or 'git command failed')
    return p.stdout


def selected_path(root, name):
    p = PurePosixPath(name)
    if (not name or str(p) != name or p.is_absolute() or '..' in p.parts
            or '\\' in name or any(part.startswith('.') for part in p.parts)):
        raise ValueError('unsafe path: ' + name)
    parts = p.parts
    # skills/[optional category/...]/name/SKILL.md, or a supporting doc below it.
    if len(parts) < 3 or parts[0] != 'skills':
        raise ValueError('not a skill documentation path: ' + name)
    support = next((i for i, part in enumerate(parts) if part in
                    {'references', 'templates', 'assets', 'scripts', 'plugins'}), None)
    if support is None:
        allowed = parts[-1] == 'SKILL.md'
    else:
        allowed = (support >= 2 and parts[support] in {'references', 'templates', 'assets'}
                   and len(parts) > support + 1 and p.suffix.lower() in SAFE_SUFFIXES
                   and not any(x in {'scripts', 'plugins'} for x in parts[support:]))
    if not allowed:
        raise ValueError('documentation-only scope refused: ' + name)
    target = root
    for component in parts:
        target = target / component
        if target.is_symlink():
            raise ValueError('symlink refused: ' + name)
    return target


def snapshot(path):
    try:
        s = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(s.st_mode) or s.st_mode & 0o111 or s.st_size > MAX_BYTES:
        raise ValueError('non-document, executable or oversized file: ' + str(path))
    data = path.read_bytes()
    if len(data) > MAX_BYTES or data.startswith(b'#!'):
        raise ValueError('oversized or script content refused')
    return data, (s.st_ino, s.st_mtime_ns, s.st_ctime_ns)


RUNNING_STATUSES = {'queued', 'in_progress', 'waiting', 'requested', 'pending'}


class Deferred(ValueError):
    """Publication did not run yet for a transient reason; nothing was pushed."""


# Deferred publication: a skip only because main CI is still running is retried
# by one detached worker per HERMES_HOME until green (publish), anything else
# (drop; edits stay in place for the existing dirt recovery), or the window ends.
RETRY_INTERVAL = 20.0
RETRY_WINDOW = 30 * 60.0


def latest_main_ci(root, sha):
    """Require a completed successful latest push CI run for this exact main tip."""
    try:
        p = bounded(['gh', 'run', 'list', '--branch', 'main', '--event', 'push',
                     '--commit', sha, '--limit', '100', '--json',
                     'headSha,status,conclusion,workflowName,createdAt'], cwd=root, timeout=20)
        if p.returncode:
            return 'unknown'
        rows = json.loads(p.stdout)
        # All workflows at the fetched tip must have a successful latest run.
        latest = {}
        for row in sorted(rows, key=lambda r: r['createdAt'], reverse=True):
            if row['headSha'] == sha:
                latest.setdefault(row['workflowName'], row)
        if not latest or len(rows) >= 100:
            return 'unknown'
        if any(r['status'] == 'completed' and r['conclusion'] in
               {'failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure'}
               for r in latest.values()):
            return 'red'
        if all(r['status'] == 'completed' and r['conclusion'] == 'success' for r in latest.values()):
            return 'green'
        # Still running (and nothing else odd): the only state the publisher defers and retries.
        if all((r['status'] == 'completed' and r['conclusion'] == 'success')
               or r['status'] in RUNNING_STATUSES for r in latest.values()):
            return 'pending'
        return 'unknown'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return 'unknown'


def publish(root, paths, *, session_id='', task_id='', ci=latest_main_ci, clock=None):
    clock = clock or time.monotonic
    with operation_budget(WORK_BUDGET, clock):
        return _publish(root, paths, session_id=session_id, task_id=task_id, ci=ci, clock=clock)


def _publish(root, paths, *, session_id, task_id, ci, clock):
    root = Path(root).expanduser().resolve(strict=True)
    if run(root, 'rev-parse', '--show-toplevel').decode().strip() != str(root):
        raise ValueError('root must be repository top level')
    paths = list(dict.fromkeys(paths))
    if not paths or len(paths) > MAX_PATHS:
        raise ValueError('empty or oversized publication batch')
    targets = {n: selected_path(root, n) for n in paths}
    gitdir = Path(run(root, 'rev-parse', '--absolute-git-dir').decode().strip())
    lock = gitdir / 'skill-publish.lock'
    # flock is released by the kernel on crash, so no stale lock; the file is never
    # unlinked (that would let holders lock different inodes). Contention is bounded.
    deadline = clock() + remaining(LOCK_WAIT)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if clock() >= deadline:
                os.close(fd)
                raise Deferred('publication lock busy')
            time.sleep(0.05)
    archive = None
    worktree = None
    try:
        head = run(root, 'rev-parse', 'HEAD').decode().strip()
        if run(root, 'diff', *DIFF_FLAGS, '--cached', '--name-only', '--', *paths):
            raise ValueError('selected paths have staged changes')
        captured = {n: snapshot(p) for n, p in targets.items()}
        run(root, 'fetch', 'origin', 'main')
        base = run(root, 'rev-parse', 'origin/main').decode().strip()
        state = ci(root, base)
        if state != 'green':
            return {'status': 'skipped', 'reason': 'latest main CI ' + state}
        # Durable recovery evidence is outside both deployment and worktree cleanup.
        home = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
        evidence = home / 'kanban/evidence/mods-reconcile'
        evidence.mkdir(parents=True, exist_ok=True)
        archive = Path(tempfile.mkdtemp(prefix='skill-publish-', dir=evidence))
        index = archive / 'snapshot-index'
        run(root, 'read-tree', head, index=index)
        untracked = []
        for name, snap in captured.items():
            entry = run(root, 'ls-tree', head, '--', name)
            if entry and not entry.startswith(b'100644 blob '):
                raise ValueError('non-document tracked mode refused')
            if snap is None:
                run(root, 'update-index', '--force-remove', '--', name, index=index)
            else:
                oid = run(root, 'hash-object', '-w', '--stdin', data=snap[0]).decode().strip()
                run(root, 'update-index', '--add', '--cacheinfo', '100644', oid, name, index=index)
                if not entry:
                    untracked.append(name)
        changed = set(run(root, 'diff', *DIFF_FLAGS, '--cached', '--name-only', '-z', '--no-renames',
                          head, '--', *paths, index=index).decode().rstrip('\0').split('\0'))
        paths = [name for name in paths if name in changed]
        if not paths:
            return {'status': 'unchanged'}
        untracked = [name for name in untracked if name in changed]
        patch = run(root, 'diff', *DIFF_FLAGS, '--cached', '--binary', '--full-index', '--no-renames',
                    head, '--', *paths, index=index)
        # reconcile's archive patch excludes untracked additions, stored in its tar instead.
        tracked = [n for n in paths if n not in untracked]
        (archive / 'changes.patch').write_bytes(run(
            root, 'diff', *DIFF_FLAGS, '--cached', '--binary', '--full-index', head, '--', *tracked, index=index)
            if tracked else b'')
        with tarfile.open(archive / 'untracked.tar.gz', 'w:gz') as tar:
            for name in untracked:
                snap = captured[name]
                assert snap is not None
                info = tarfile.TarInfo(name)
                info.size = len(snap[0])
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(snap[0]))
        (archive / 'manifest.json').write_text(json.dumps({
            'head': head, 'origin': base, 'status': run(root, 'status', '--porcelain').decode(),
            'untracked': untracked}))
        (archive / 'publish.patch').write_bytes(patch)
        index.unlink()
        worktree = archive / 'worktree'
        run(root, 'worktree', 'add', '--detach', str(worktree), base)
        run(worktree, 'apply', '--3way', '--index', '-', data=patch)
        # No identity overrides, and no hook bypass even if the caller has opted out globally.
        if not (worktree / '.githooks/pre-commit').is_file():
            raise ValueError('required .githooks/pre-commit missing')
        commit_env = {k: v for k, v in clean_env().items() if k not in {
            'HERMES_MODS_ALLOW_UNSCANNED', 'HERMES_MODS_ALLOW_MULTI_MOD'}}
        # apply --3way stages the full batch. Commit one skill at a time so the real
        # repository scope hook remains enabled for cross-skill tool batches.
        run(worktree, 'reset', '--mixed', 'HEAD')
        groups = {}
        for name in paths:
            parts = name.split('/')
            boundary = next((i for i, part in enumerate(parts) if part in
                             {'references', 'templates', 'assets'}), len(parts) - 1)
            groups.setdefault('/'.join(parts[:boundary]), []).append(name)
        clean = lambda s: re.sub(r'[^a-zA-Z0-9_.:/,-]', '_', str(s))[:160]
        for label, names in groups.items():
            run(worktree, 'add', '--', *names)
            if not run(worktree, 'diff', *DIFF_FLAGS, '--cached', '--name-only'):
                continue
            message = 'skills: publish %s [session:%s card:%s]' % (
                clean(label), clean(session_id or 'unknown'), clean(task_id or 'none'))
            run(worktree, '-c', 'core.hooksPath=.githooks', 'commit', '-m', message, env=commit_env)
        for attempt in range(4):
            try:
                run(worktree, '-c', 'core.hooksPath=.githooks', 'push', 'origin', 'HEAD:main')
                break
            except ValueError:
                if attempt == 3:
                    raise
                run(root, 'fetch', 'origin', 'main')
                tip = run(root, 'rev-parse', 'origin/main').decode().strip()
                if tip == base:
                    raise ValueError('push refused without a main race')
                tip_state = ci(root, tip)
                if tip_state == 'pending':
                    # Nothing was pushed: a later retry from a fresh snapshot is safe.
                    raise Deferred('latest main CI pending during retry')
                if tip_state != 'green':
                    raise ValueError('latest main CI not green during retry')
                run(worktree, '-c', 'core.hooksPath=.githooks', 'rebase', tip)
                base = tip
        published = run(worktree, 'rev-parse', 'HEAD').decode().strip()
        run(root, 'fetch', 'origin', 'main')
        run(root, 'merge-base', '--is-ancestor', published, 'origin/main')
        # Keep newer edits intact. reconcile independently checks again immediately before writes.
        if run(root, 'rev-parse', 'HEAD').decode().strip() != head:
            raise ValueError('live HEAD moved; publication archived, reconciliation refused')
        for name in paths:
            if snapshot(selected_path(root, name)) != captured[name]:
                raise ValueError('new writer detected; publication archived')
        if run(root, 'diff', *DIFF_FLAGS, '--cached', '--name-only', '--', *paths):
            raise ValueError('selected paths were staged during publication')
        cmd = [sys.executable, str(Path(__file__).with_name('reconcile.py')),
               '--root', str(root), '--archive', str(archive), '--published', published]
        for name in paths:
            cmd.extend(['--path', name])
        result = bounded(cmd, timeout=90)
        if result.returncode:
            raise ValueError('reconcile refused: ' + result.stderr.decode(errors='replace'))
        return {'status': 'published', 'commit': published, 'archive': str(archive)}
    except Exception as exc:
        # Deferred stays Deferred so the observer/worker can schedule a retry.
        cls = Deferred if isinstance(exc, Deferred) else ValueError
        raise cls('%s%s' % (exc, ('; archive: ' + str(archive)) if archive else '')) from exc
    finally:
        try:
            if worktree is not None and worktree.exists():
                # Only the disposable publication worktree; source edits and archive stay intact.
                with operation_budget(CLEANUP_BUDGET, clock):
                    run(root, 'worktree', 'remove', '--force', str(worktree))
        finally:
            os.close(fd)


def written_paths(args, result, root):
    """Core flat patch returns no path; batch rows carry name/action/file_path only."""
    root = Path(root).expanduser().resolve()
    if isinstance(result, str):
        result = json.loads(result)
    if (not isinstance(result, dict) or result.get('success') is not True
            or result.get('staged') or result.get('pending_id')):
        return []
    ops = args.get('operations')
    rows = result.get('results')
    if ops is None:
        ops, rows = [args], [result]
    if not isinstance(rows, list) or len(rows) != len(ops):
        return []
    paths = []
    for op, row in zip(ops, rows):
        if row.get('success') is not True or op.get('action') not in {'create', 'edit', 'patch', 'write_file'}:
            continue
        raw = row.get('path')
        if raw:
            target = Path(raw).expanduser()
            if target.is_dir():
                target = target / (op.get('file_path') or 'SKILL.md')
        else:
            # Match core lookup precedence and external_dirs, never search by basename ourselves.
            from tools.skill_manager_tool import _find_skill
            found = _find_skill(op.get('name', ''))
            if not found:
                continue
            target = found['path'] / (op.get('file_path') or 'SKILL.md')
        if not target.is_absolute() or '..' in target.parts:
            raise ValueError('invalid skill output path')
        # Resolve an external skill alias only up to its first entrance into the
        # live root. Retain the remaining lexical components for symlink checks.
        prefix = Path(target.anchor)
        resolved = target.resolve()
        for index, component in enumerate(target.parts[1:], 1):
            prefix = prefix / component
            entered = prefix.resolve()
            if entered.is_relative_to(root):
                lexical = entered.relative_to(root).joinpath(*target.parts[index + 1:])
                selected_path(root, lexical.as_posix())
                break
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        name = relative.as_posix()
        selected_path(root, name)
        paths.append(name)
    return list(dict.fromkeys(paths))


# --- Deferred publication -------------------------------------------------------------
# The observer never waits for CI (45 s budget, CI takes minutes). A skip that is only
# "main CI still running" (or a busy publication lock) is recorded under HERMES_HOME and
# retried by one detached worker. Each retry is a full publish(): fresh snapshot, fetch,
# CI must be green for the exact tip, the same scope/race/gitleaks rules. Red CI or any
# publication error drops the record (edits stay in place, as for a direct skip); a tip
# still pending or unknown is retried until the record's window ends.

def pending_dir():
    home = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
    return home / 'cache' / 'skill-publish-pending'


def deferrable(outcome):
    return isinstance(outcome, dict) and outcome == {
        'status': 'skipped', 'reason': 'latest main CI pending'}


def _record_key(root, paths):
    import hashlib
    blob = json.dumps([str(root), sorted(paths)]).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def _retry_log(directory, message):
    try:
        logs = directory.parent.parent / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / 'skill-publish-retry.log', 'a') as fh:
            fh.write('%s %s\n' % (time.strftime('%Y-%m-%dT%H:%M:%S%z'), message))
    except OSError:
        pass


def defer(root, paths, *, session_id='', task_id='', spawn=True, clock=None, directory=None):
    """Record a pending publication (newest edit wins, window restarts) and start the worker."""
    clock = clock or time.time
    directory = Path(directory) if directory else pending_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = str(Path(root).expanduser().resolve(strict=True))
    paths = list(dict.fromkeys(paths))
    if not paths or len(paths) > MAX_PATHS:
        raise ValueError('empty or oversized publication batch')
    for name in paths:
        selected_path(Path(root), name)
    target = directory / (_record_key(root, paths) + '.json')
    now = clock()
    try:
        created = json.loads(target.read_text()).get('created', now)
    except (OSError, ValueError, AttributeError):
        created = now
    record = {'version': 1, 'root': root, 'paths': paths, 'session_id': str(session_id or ''),
              'task_id': str(task_id or ''), 'created': created, 'deadline': now + RETRY_WINDOW}
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.record-', suffix='.tmp')
    with os.fdopen(fd, 'w') as fh:
        json.dump(record, fh)
    os.replace(tmp, target)
    _retry_log(directory, 'deferred %s %s' % (record['session_id'] or '-', paths))
    if spawn:
        spawn_worker(directory)
    return {'status': 'deferred', 'record': str(target)}


def spawn_worker(directory=None):
    """Start a detached retry worker (it exits at once if one already holds the lock)."""
    directory = Path(directory) if directory else pending_dir()
    logs = directory.parent.parent / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / 'skill-publish-retry.log', 'a') as out:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--retry-pending',
                                    '--dir', str(directory)],
                                   stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                   start_new_session=True, close_fds=True)
    # Reap it when it exits, without blocking the observer (no zombie, no ResourceWarning).
    import threading
    threading.Thread(target=process.wait, name='skill-publish-retry-reaper', daemon=True).start()


def kick(directory=None):
    """Restart the worker when records exist (e.g. after a reboot killed the last one)."""
    directory = Path(directory) if directory else pending_dir()
    if directory.is_dir() and any(directory.glob('*.json')):
        spawn_worker(directory)
        return True
    return False


def _drop(path, raw):
    # Keep a record a newer defer() rewrote while this attempt ran.
    try:
        if path.read_bytes() == raw:
            path.unlink()
    except FileNotFoundError:
        pass


def retry_pending(directory=None, *, interval=None, ci=None, clock=None, sleep=time.sleep,
                  passes=None):
    """Retry every pending record until none remain. One worker per directory (flock)."""
    directory = Path(directory) if directory else pending_dir()
    clock = clock or time.time
    ci = ci or latest_main_ci
    if interval is None:
        interval = float(os.environ.get('HERMES_SKILL_PUBLISH_RETRY_INTERVAL') or RETRY_INTERVAL)
    counts = {'published': 0, 'kept': 0, 'dropped': 0, 'expired': 0, 'busy': 0}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / '.worker.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            counts['busy'] = 1
            return counts
        done = 0
        while True:
            records = sorted(directory.glob('*.json'))
            if not records:
                if passes is not None:
                    return counts
                # Release, then look again: a record written by a defer() whose own
                # worker lost the lock race to us must still be picked up.
                fcntl.flock(fd, fcntl.LOCK_UN)
                if not any(directory.glob('*.json')):
                    return counts
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return counts
                continue
            for path in records:
                try:
                    raw = path.read_bytes()
                    rec = json.loads(raw)
                    root, paths = rec['root'], rec['paths']
                    if (not isinstance(root, str) or not isinstance(paths, list)
                            or not all(isinstance(p, str) for p in paths)):
                        raise ValueError('malformed record')
                    deadline = float(rec['deadline'])
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    _retry_log(directory, 'dropped malformed %s: %s' % (path.name, exc))
                    path.unlink(missing_ok=True)
                    counts['dropped'] += 1
                    continue
                if clock() > deadline:
                    _retry_log(directory, 'expired; edits left in place %s' % paths)
                    _drop(path, raw)
                    counts['expired'] += 1
                    continue
                try:
                    outcome = publish(root, paths, session_id=rec.get('session_id', ''),
                                      task_id=rec.get('task_id', ''), ci=ci)
                except Deferred:
                    counts['kept'] += 1
                    continue
                except Exception as exc:
                    _retry_log(directory, 'preserved edits %s: %s' % (paths, exc))
                    _drop(path, raw)
                    counts['dropped'] += 1
                    continue
                if isinstance(outcome, dict) and outcome.get('status') == 'skipped' and \
                        outcome.get('reason') in ('latest main CI pending', 'latest main CI unknown'):
                    counts['kept'] += 1
                    continue
                _retry_log(directory, 'retry %s %s' % (paths, outcome))
                _drop(path, raw)
                counts['published' if outcome.get('status') == 'published' else 'dropped'] += 1
            done += 1
            if passes is not None and done >= passes:
                return counts
            sleep(interval)
    finally:
        os.close(fd)


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retry-pending', action='store_true', required=True)
    parser.add_argument('--dir')
    args = parser.parse_args(argv)
    counts = retry_pending(args.dir)
    if not counts['busy']:
        _retry_log(Path(args.dir) if args.dir else pending_dir(), 'worker exit %s' % counts)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
