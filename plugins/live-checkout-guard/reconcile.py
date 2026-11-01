"""Archive-verified file reconciliation and separate locked stash retirement.

Stash modes: --prepare-stash-retirement, --retire-stashes --oid FULL_OID (repeatable),
or --recover-stash-retirement; each requires --archive DIR. See README.md for the
stash manifest, bundle verification and fail-closed recovery boundaries. These
modes never write checkout files, the live index or HEAD.

File mode (no fetch, stash, reset or live index writes):
python reconcile.py --archive DIR --published SHA --path relative/path
Repeat --path for an explicitly selected set; --root defaults to ~/code/hermes-mods.
The caller fetches origin/main first. The archive must contain manifest.json with
head/origin/status/untracked, changes.patch (git diff --binary HEAD), and
untracked.tar.gz. Reconstruction happens in a disposable clone, never live.
All selected files must still match that snapshot before any replacement occurs.
The published commit must already be on origin/main; selected leftovers are reset
to the current live HEAD bytes (without touching its index), leaving it ff-able.
Afterward merge --ff-only origin/main on the clean live tree to deploy publication.
This narrow helper refuses symlinks and non-regular files rather than following
them. Published-absent archived untracked files may be removed, never directories.
An exclusive cooperating-helper lock and last-moment identity/content checks
refuse observed new writers; arbitrary noncooperating filesystem writers cannot
be locked out portably. Each replacement is atomic, not the whole multi-file set.
Successful operations append reconcile-audit.jsonl inside the archive.
"""
import sys

MIN_PYTHON = (3, 9)  # oldest tested; stock macOS python3 is 3.9
if sys.version_info < MIN_PYTHON:  # preflight: refuse before parsing or touching anything
    sys.stderr.write('reconcile.py needs Python %d.%d or newer, not %s; nothing was changed\n'
                     % (MIN_PYTHON + (sys.version.split()[0],)))
    sys.exit(2)

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip())
    return result.stdout


def safe_path(root, name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or '..' in p.parts or '.git' in p.parts
            or '\\' in name or str(p) != name):
        raise ValueError('unsafe path: ' + name)
    target = root / name
    part = root
    for component in p.parts:
        part = part / component
        if part.is_symlink():
            raise ValueError('symlink path refused: ' + name)
    if target.exists() and not target.is_file():
        raise ValueError('non-regular path refused: ' + name)
    return target


def snapshot(path):
    try:
        s = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('non-regular file refused: ' + str(path))
    return (path.read_bytes(), bool(s.st_mode & 0o111))


def reconcile(root, archive, published, paths):
    root = root.expanduser().resolve(strict=True)
    archive = archive.expanduser().resolve(strict=True)
    if git(root, 'rev-parse', '--show-toplevel').decode().strip() != str(root):
        raise ValueError('root must be the repository top level')
    if not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', published):
        raise ValueError('published must be a full commit SHA')
    git(root, 'cat-file', '-e', published + '^{commit}')
    git(root, 'merge-base', '--is-ancestor', published, 'refs/remotes/origin/main')
    manifest = json.loads((archive / 'manifest.json').read_text())
    if not isinstance(manifest['status'], str):
        raise ValueError('invalid archive status')
    head = manifest['head']
    if not isinstance(head, str) or not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', head):
        raise ValueError('invalid archive head')
    archived_origin = manifest['origin']
    if isinstance(archived_origin, str) and re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', archived_origin):
        git(root, 'cat-file', '-e', archived_origin + '^{commit}')
        git(root, 'merge-base', '--is-ancestor', archived_origin, 'refs/remotes/origin/main')
    elif archived_origin != git(root, 'remote', 'get-url', 'origin').decode().strip():
        raise ValueError('archive origin mismatch')
    live_head = git(root, 'rev-parse', 'HEAD').decode().strip()
    paths = list(dict.fromkeys(paths))
    if not paths:
        raise ValueError('no selected paths')
    targets = {name: safe_path(root, name) for name in paths}
    lock = Path(git(root, 'rev-parse', '--absolute-git-dir').decode().strip()) / 'reconcile.lock'
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    audit_fd = None
    try:
        audit_fd = os.open(archive / 'reconcile-audit.jsonl',
                           os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        if not stat.S_ISREG(os.fstat(audit_fd).st_mode):
            raise ValueError('audit destination is not a regular file')
        with tempfile.TemporaryDirectory(prefix='reconcile-') as tmp:
            staging = Path(tmp) / 'snapshot'
            git(root, 'clone', '--quiet', '--shared', '--no-checkout', str(root), str(staging))
            git(staging, '-c', 'core.hooksPath=/dev/null', 'checkout', '--quiet', '--detach', head)
            patch = (archive / 'changes.patch').read_bytes()
            if patch:
                result = subprocess.run(['git', '-C', str(staging), 'apply', '--binary', '-'],
                                        input=patch, capture_output=True)
                if result.returncode:
                    raise ValueError('archive patch does not apply: ' + result.stderr.decode(errors='replace'))
            dirty = set(git(staging, 'diff', '--name-only', '-z', 'HEAD').decode().split('\0')) - {''}
            untracked = manifest['untracked']
            if not isinstance(untracked, list) or not all(isinstance(p, str) for p in untracked):
                raise ValueError('invalid untracked manifest')
            members_seen = set()
            with tarfile.open(archive / 'untracked.tar.gz', 'r:gz') as tar:
                for member in tar:
                    dest = safe_path(staging, member.name)
                    if not member.isfile() or member.name not in untracked or member.name in members_seen:
                        raise ValueError('invalid untracked archive member')
                    if dest.exists():
                        raise ValueError('untracked member overwrites tracked file')
                    members_seen.add(member.name)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    if source is None:
                        raise ValueError('unreadable untracked archive member')
                    with source:
                        dest.write_bytes(source.read())
                    dest.chmod(member.mode & 0o777)
            if members_seen != set(untracked):
                raise ValueError('untracked archive is incomplete')
            dirty.update(members_seen)
            expected, replacements, identities = {}, {}, {}
            for name, target in targets.items():
                if name not in dirty:
                    raise ValueError('selected path is not archived dirt: ' + name)
                expected[name] = snapshot(safe_path(staging, name))
                if snapshot(target) != expected[name]:
                    raise ValueError('current file differs from archive: ' + name)
                identities[name] = target.lstat() if target.exists() else None
                entries = git(root, 'ls-tree', '-z', live_head, '--', name).split(b'\0')
                entries = [e for e in entries if e]
                if not entries:
                    replacements[name] = None
                else:
                    mode, kind, oid_path = entries[0].split(b' ', 2)
                    oid, actual_name = oid_path.split(b'\t', 1)
                    if kind != b'blob' or mode not in (b'100644', b'100755') or actual_name.decode() != name:
                        raise ValueError('published path is not a regular file')
                    replacements[name] = (git(root, 'cat-file', 'blob', oid.decode()), mode == b'100755')
            # Validate the entire selection again before the first write.
            def unchanged(name):
                if git(root, 'rev-parse', 'HEAD').decode().strip() != live_head:
                    raise ValueError('live HEAD moved during reconciliation')
                target = safe_path(root, name)
                old = identities[name]
                now = target.lstat() if target.exists() else None
                if snapshot(target) != expected[name] or (old is None) != (now is None):
                    raise ValueError('new writer detected: ' + name)
                if old is not None and now is not None and (old.st_ino, old.st_mtime_ns, old.st_ctime_ns) != (now.st_ino, now.st_mtime_ns, now.st_ctime_ns):
                    raise ValueError('new writer detected: ' + name)
            for name in paths:
                unchanged(name)
            for name, target in targets.items():
                unchanged(name)
                replacement = replacements[name]
                if replacement is None:
                    if expected[name] is not None:
                        target.unlink()
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix='.reconcile-', dir=target.parent)
                try:
                    with os.fdopen(fd, 'wb') as out:
                        out.write(replacement[0])
                        out.flush()
                        os.fchmod(out.fileno(), 0o755 if replacement[1] else 0o644)
                        os.fsync(out.fileno())
                    unchanged(name)
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            record = {'timestamp': datetime.now(timezone.utc).isoformat(),
                      'published': published, 'archive_head': head, 'root': str(root), 'paths': paths}
            with os.fdopen(os.dup(audit_fd), 'a') as audit:
                audit.write(json.dumps(record) + '\n')
                audit.flush()
                os.fsync(audit.fileno())
    finally:
        if audit_fd is not None:
            os.close(audit_fd)
        os.close(lock_fd)
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('~/code/hermes-mods'))
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--published')
    parser.add_argument('--path', action='append')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prepare-stash-retirement', action='store_true',
                       help='verify existing stash exports and add complete index archives')
    modes.add_argument('--retire-stashes', action='store_true',
                       help='retire exact OIDs using the prepared archive and native Git lock')
    modes.add_argument('--recover-stash-retirement', action='store_true',
                       help='finish an interrupted retirement without starting a new one')
    parser.add_argument('--oid', action='append', help='full stash OID; repeat for each target')
    args = parser.parse_args()
    retirement = args.prepare_stash_retirement or args.retire_stashes or args.recover_stash_retirement
    if retirement:
        if args.published or args.path:
            parser.error('stash retirement cannot be combined with --published or --path')
        if bool(args.oid) != bool(args.retire_stashes):
            parser.error('--oid is required only with --retire-stashes')
        # Load the maintained sibling, never an arbitrary module on PYTHONPATH.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'reconcile_stash_retirement', Path(__file__).resolve().with_name('stash_retirement.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            if args.prepare_stash_retirement:
                result = module.prepare(args.root, args.archive)
            elif args.recover_stash_retirement:
                result = module.recover(args.root, args.archive)
            else:
                result = module.retire(args.root, args.archive, args.oid)
        except (ValueError, OSError) as exc:
            parser.exit(1, 'stash retirement refused: ' + str(exc) + '\n')
        print(json.dumps(result, sort_keys=True))
        return
    if args.oid or not args.published or not args.path:
        parser.error('file reconciliation requires --published and --path, without --oid')
    try:
        reconcile(args.root, args.archive, args.published, args.path)
    except (ValueError, OSError, KeyError, tarfile.TarError) as exc:
        parser.exit(1, 'reconcile refused: ' + str(exc) + '\n')
    print('reconciled ' + ', '.join(args.path))


if __name__ == '__main__':
    main()
