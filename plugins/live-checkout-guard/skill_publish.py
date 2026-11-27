"""Bounded, documentation-only publication; never commit or stage in the live tree."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from contextvars import ContextVar
import io
import importlib.util
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import shlex
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
        raise Deferred('publication deadline exceeded')
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
    p = bounded(['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null',
                 '-c', 'core.fsmonitor=false', *credential_args, *args], data=data, timeout=30, env=env)
    if p.returncode:
        message = p.stderr.decode(errors='replace').strip() or 'git command failed'
        # Only the fixed plugin-owned hook emits this refusal marker. Ordinary
        # transport/Git failures (including races) retain custody until deadline.
        cls = Refused if 'publication held: ' in message and 'commit' in args else Deferred
        raise cls(message)
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


class Refused(ValueError):
    """Terminal scope, content or staged-state refusal; source edits stay intact."""


# Operational holds retry under one worker per HERMES_HOME until the fixed
# deadline. Only explicit content/staged refusals or malformed records retire early.
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
        # Distinguish still-running workflows from unknown CI evidence.
        if all((r['status'] == 'completed' and r['conclusion'] == 'success')
               or r['status'] in RUNNING_STATUSES for r in latest.values()):
            return 'pending'
        return 'unknown'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return 'unknown'


def publication_overlap(root, paths):
    """Fresh, metered, fail-closed publication check, independent of the pre-hook."""
    spec = importlib.util.spec_from_file_location('publication_pr_overlap',
                                                 Path(__file__).with_name('pr_overlap.py'))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Check both effective destinations; a local/non-GitHub origin is unknown,
    # not a reason to publish without the PR hold.
    for options in [(), ('--push',)]:
        url = run(root, 'remote', 'get-url', *options, 'origin').decode().strip()
        slug = mod.repo_slug(url)
        if not slug:
            return {'status': 'unknown', 'reason': 'unrecognized publication origin'}
        result = mod.Checker(fetch=mod.gh_fetch, deadline=remaining(8), list_ttl=-1).check(slug, 'main', paths)
        if result.get('status') != 'clear':
            return result
    return {'status': 'clear'}


def require_overlap_clear(root, paths):
    try:
        result = publication_overlap(root, paths)
        state = result.get('status')
    except Exception:
        state = 'unknown'
    if state != 'clear':
        return {'status': 'skipped', 'reason': 'open PR overlap ' + (state if state in {'overlap', 'unknown'} else 'unknown')}
    return None


def trusted_hooks(archive):
    """Only plugin-owned code; never load .githooks or suite scripts from origin."""
    directory = archive / 'trusted-hooks'
    directory.mkdir(mode=0o700)
    helper = Path(__file__).resolve().with_name('publication_validate.py')
    for name in ('pre-commit', 'pre-push'):
        hook = directory / name
        hook.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, str(helper), name]) + '\n')
        hook.chmod(0o700)
    return str(directory)


def publish(root, paths, *, session_id='', task_id='', ci=latest_main_ci, clock=None):
    clock = clock or time.monotonic
    try:
        with operation_budget(WORK_BUDGET, clock):
            outcome = _publish(root, paths, session_id=session_id, task_id=task_id, ci=ci, clock=clock)
    except Refused as exc:
        dirt_notice(paths, 'refused', str(exc))
        raise
    except Exception as exc:
        dirt_notice(paths, 'held', str(exc))
        raise Deferred(str(exc)) from exc
    if outcome.get('status') == 'skipped':
        dirt_notice(paths, 'held', outcome.get('reason', 'unknown'))
    return outcome


def _publish(root, paths, *, session_id, task_id, ci, clock):
    root = Path(root).expanduser().resolve(strict=True)
    if run(root, 'rev-parse', '--show-toplevel').decode().strip() != str(root):
        raise Refused('root must be repository top level')
    paths = list(dict.fromkeys(paths))
    if not paths or len(paths) > MAX_PATHS:
        raise Refused('empty or oversized publication batch')
    try:
        targets = {n: selected_path(root, n) for n in paths}
    except ValueError as exc:
        raise Refused(str(exc)) from exc
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
            raise Refused('selected paths have staged changes')
        try:
            captured = {n: snapshot(p) for n, p in targets.items()}
        except ValueError as exc:
            raise Refused(str(exc)) from exc
        run(root, 'fetch', 'origin', 'main')
        base = run(root, 'rev-parse', 'origin/main').decode().strip()
        state = ci(root, base)
        if state != 'green':
            return {'status': 'skipped', 'reason': 'latest main CI ' + state}
        if hold := require_overlap_clear(root, paths):
            return hold
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
                raise Refused('non-document tracked mode refused')
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
        # No fetched hooks: privacy checks and the conservative doc fast path live
        # in this plugin. A skill needing tests is held, not run from fetched code.
        hooks = 'core.hooksPath=' + trusted_hooks(archive)
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
            run(worktree, '-c', hooks, 'commit', '-m', message, env=commit_env)
        for attempt in range(4):
            if hold := require_overlap_clear(root, paths):
                return hold
            # A prior ambiguous push may already be present on fetched main.
            # No update means no hook/push, but reconciliation is still required.
            if run(worktree, 'rev-parse', 'HEAD').decode().strip() == base:
                break
            try:
                run(worktree, '-c', hooks, 'push', 'origin', 'HEAD:main')
                break
            except Refused:
                raise
            except (Deferred, subprocess.SubprocessError):
                if attempt == 3:
                    raise
                run(root, 'fetch', 'origin', 'main')
                tip = run(root, 'rev-parse', 'origin/main').decode().strip()
                if tip == base:
                    raise Deferred('push refused without a main race')
                tip_state = ci(root, tip)
                if tip_state == 'pending':
                    # Nothing was pushed: a later retry from a fresh snapshot is safe.
                    raise Deferred('latest main CI pending during retry')
                if tip_state != 'green':
                    raise Deferred('latest main CI not green during retry')
                run(worktree, 'rebase', tip)
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
            raise Refused('selected paths were staged during publication')
        cmd = [sys.executable, str(Path(__file__).with_name('reconcile.py')),
               '--root', str(root), '--archive', str(archive), '--published', published]
        for name in paths:
            cmd.extend(['--path', name])
        result = bounded(cmd, timeout=90)
        if result.returncode:
            raise ValueError('reconcile refused: ' + result.stderr.decode(errors='replace'))
        return {'status': 'published', 'commit': published, 'archive': str(archive)}
    except Exception as exc:
        cls = Refused if isinstance(exc, Refused) else Deferred
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
# The observer never waits for CI (45 s budget, CI takes minutes). Each retry is
# a complete fresh publish(), retaining all CI, PR, privacy and custody gates.
# Operational failures retain the original record/deadline, never discard edits.

def pending_dir():
    home = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
    return home / 'cache' / 'skill-publish-pending'


def deferrable(outcome):
    return isinstance(outcome, dict) and outcome.get('status') == 'skipped'


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
    record['signatures'] = {}
    for name in paths:
        try:
            record['signatures'][name] = _signature(root, name)
        except (OSError, ValueError):
            pass
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


def dirt_notice(paths, disposition, reason, directory=None):
    """A single visible line; never silently strand edits behind mods-pull."""
    directory = Path(directory) if directory else pending_dir()
    message = 'skill publication dirt %s %s: %s' % (
        disposition, json.dumps(paths, ensure_ascii=True), str(reason).replace('\n', ' ')[:500])
    logging.getLogger(__name__).warning(message)
    _retry_log(directory, message)


def _signature(root, name):
    import hashlib
    snap = snapshot(selected_path(Path(root), name))
    return hashlib.sha256(snap[0]).hexdigest() if snap else 'deleted'


def _settle(directory, root, paths, signatures):
    # Discovery must not renew expired/refused bytes on every unrelated tool call.
    # Fingerprints are from BEFORE the attempt so a concurrent new edit is eligible.
    for name in paths:
        if name in signatures:
            (directory / ('.settled-' + _record_key(root, [name]))).write_text(signatures[name])


def kick(directory=None, *, root=None):
    """Recover orphan dirty docs as well as pending records, without duplicate workers."""
    directory = Path(directory) if directory else pending_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / '.worker.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    busy = False
    discovered = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            busy = True
        if root is not None:
            with operation_budget(LOCK_WAIT, time.monotonic):
                root = str(Path(root).resolve(strict=True))
                covered = set()
                for path in directory.glob('*.json'):
                    try:
                        rec = json.loads(path.read_text())
                        if (isinstance(rec, dict) and rec.get('root') == root
                                and isinstance(rec.get('paths'), list)
                                and all(isinstance(n, str) for n in rec['paths'])):
                            covered.update(rec['paths'])
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                # NUL paths, no rename ambiguity, includes deleted/untracked docs.
                dirty = set(run(root, 'diff', *DIFF_FLAGS, '--name-only', '-z', '--no-renames', 'HEAD', '--', 'skills').decode().rstrip('\0').split('\0'))
                dirty.update(run(root, 'ls-files', '--others', '--exclude-standard', '-z', '--', 'skills').decode().rstrip('\0').split('\0'))
                for name in sorted(dirty - covered - {''}):
                    try:
                        signature = _signature(root, name)
                    except ValueError as exc:
                        dirt_notice([name], 'refused', str(exc), directory)
                        continue
                    settled = directory / ('.settled-' + _record_key(root, [name]))
                    if settled.exists() and settled.read_text() == signature:
                        dirt_notice([name], 'held', 'previous refusal/expiry; reviewed recovery required', directory)
                        continue
                    defer(root, [name], spawn=False, directory=directory)
                    discovered = True
                    dirt_notice([name], 'held', 'orphan edit recovered for retry', directory)
        pending = any(directory.glob('*.json'))
    finally:
        os.close(fd)
    if pending and (not busy or discovered):
        # A new enqueue needs a contender even when the incumbent appeared busy:
        # it may have crossed its final empty check during discovery. Workers
        # still execute only under their flock; defer() uses the same handoff.
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
                rec = {}
                raw = None
                try:
                    raw = path.read_bytes()
                    rec = json.loads(raw)
                    root, paths = rec['root'], rec['paths']
                    if (not isinstance(root, str) or not isinstance(paths, list) or not paths or len(paths) > MAX_PATHS
                            or not all(isinstance(p, str) for p in paths)):
                        raise ValueError('malformed record')
                    deadline = float(rec['deadline'])
                    if not math.isfinite(deadline):
                        raise ValueError('nonfinite deadline')
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    _retry_log(directory, 'dropped malformed %s: %s' % (path.name, exc))
                    dirt_notice(rec.get('paths', [path.name]) if isinstance(rec, dict) else [path.name],
                                'dropped malformed', str(exc), directory)
                    if raw is not None:
                        _drop(path, raw)
                    counts['dropped'] += 1
                    continue
                signatures = {}
                for name in paths:
                    try:
                        signatures[name] = _signature(root, name)
                    except (ValueError, OSError):
                        pass
                if clock() >= deadline:
                    _retry_log(directory, 'expired; edits left in place %s' % paths)
                    dirt_notice(paths, 'expired', 'retry deadline; reviewed recovery required', directory)
                    enrolled = rec.get('signatures', {})
                    _settle(directory, root, paths, enrolled if isinstance(enrolled, dict) else {})
                    _drop(path, raw)
                    counts['expired'] += 1
                    continue
                try:
                    outcome = publish(root, paths, session_id=rec.get('session_id', ''),
                                      task_id=rec.get('task_id', ''), ci=ci)
                except Refused as exc:
                    dirt_notice(paths, 'dropped refused', str(exc), directory)
                    _settle(directory, root, paths, signatures)
                    _drop(path, raw)
                    counts['dropped'] += 1
                    continue
                except Exception as exc:
                    dirt_notice(paths, 'held retryable', str(exc), directory)
                    counts['kept'] += 1
                    continue
                if deferrable(outcome):
                    dirt_notice(paths, 'held', outcome.get('reason', 'unknown'), directory)
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
