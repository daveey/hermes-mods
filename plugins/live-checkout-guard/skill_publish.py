"""Bounded, documentation-only publication; never commit or stage in the live tree."""
from __future__ import annotations

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

MAX_PATHS = 16
MAX_BYTES = 1024 * 1024
SAFE_SUFFIXES = {'.md', '.txt', '.rst', '.csv', '.json', '.yaml', '.yml',
                 '.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf'}


def bounded(command, *, timeout, data=None, env=None, cwd=None):
    """Timeout the whole subprocess group, including Git transports and hooks."""
    env = dict(os.environ if env is None else env,
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
            process.communicate(timeout=5)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def run(root, *args, data=None, env=None):
    if env is None:
        env = dict(os.environ)
    env = dict(env, GIT_OPTIONAL_LOCKS='0')
    p = bounded(['git', '-C', str(root), *args], data=data, timeout=30, env=env)
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
        return ('green' if all(r['status'] == 'completed' and r['conclusion'] == 'success'
                               for r in latest.values()) else 'unknown')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return 'unknown'


def publish(root, paths, *, session_id='', task_id='', ci=latest_main_ci):
    root = Path(root).expanduser().resolve(strict=True)
    if run(root, 'rev-parse', '--show-toplevel').decode().strip() != str(root):
        raise ValueError('root must be repository top level')
    paths = list(dict.fromkeys(paths))
    if not paths or len(paths) > MAX_PATHS:
        raise ValueError('empty or oversized publication batch')
    targets = {n: selected_path(root, n) for n in paths}
    gitdir = Path(run(root, 'rev-parse', '--absolute-git-dir').decode().strip())
    lock = gitdir / 'skill-publish.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    archive = None
    worktree = None
    try:
        head = run(root, 'rev-parse', 'HEAD').decode().strip()
        if run(root, 'diff', '--cached', '--name-only', '--', *paths):
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
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        run(root, 'read-tree', head, env=env)
        untracked = []
        for name, snap in captured.items():
            entry = run(root, 'ls-tree', head, '--', name)
            if entry and not entry.startswith(b'100644 blob '):
                raise ValueError('non-document tracked mode refused')
            if snap is None:
                run(root, 'update-index', '--force-remove', '--', name, env=env)
            else:
                oid = run(root, 'hash-object', '-w', '--stdin', data=snap[0]).decode().strip()
                run(root, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
                if not entry:
                    untracked.append(name)
        changed = set(run(root, 'diff', '--cached', '--name-only', '-z', '--no-renames',
                          head, '--', *paths, env=env).decode().rstrip('\0').split('\0'))
        paths = [name for name in paths if name in changed]
        if not paths:
            return {'status': 'unchanged'}
        untracked = [name for name in untracked if name in changed]
        patch = run(root, 'diff', '--cached', '--binary', '--full-index', '--no-renames',
                    head, '--', *paths, env=env)
        # reconcile's archive patch excludes untracked additions, stored in its tar instead.
        tracked = [n for n in paths if n not in untracked]
        (archive / 'changes.patch').write_bytes(run(
            root, 'diff', '--cached', '--binary', '--full-index', head, '--', *tracked, env=env)
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
        commit_env = {k: v for k, v in os.environ.items() if k not in {
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
            if not run(worktree, 'diff', '--cached', '--name-only'):
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
                if ci(root, tip) != 'green':
                    raise ValueError('latest main CI not green during retry')
                run(worktree, '-c', 'core.hooksPath=.githooks', 'rebase', 'origin/main')
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
        if run(root, 'diff', '--cached', '--name-only', '--', *paths):
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
        raise ValueError('%s%s' % (exc, ('; archive: ' + str(archive)) if archive else '')) from exc
    finally:
        try:
            if worktree is not None and worktree.exists():
                # Only the disposable publication worktree; source edits and archive stay intact.
                run(root, 'worktree', 'remove', '--force', str(worktree))
        finally:
            os.close(fd)
            lock.unlink()


def written_paths(args, result, root):
    """Core flat patch returns no path; batch rows carry name/action/file_path only."""
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
