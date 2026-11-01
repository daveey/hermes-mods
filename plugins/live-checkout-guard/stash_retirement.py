"""Archive-verified stash retirement. No checkout, index or HEAD writes; no ref-file writes.

Three argparse-independent entry points, each run entirely under the exclusive,
non-blocking flock on ``$HERMES_HOME/.hermes-sync.lock``. The locked file
description is passed (``pass_fds``) to every Git child, so an orphaned Git
process (for example ``update-ref`` after its Python parent died) keeps the
lock until it exits and no other invocation can act on a half-known state.
Every Git child is started in its own session (``start_new_session``), so a
signal sent to the caller's process group does not directly reach Git. The
prepared update-ref child is never killed by Python: on caller death it sees
EOF and finishes or rolls back. Other Git calls use subprocess.run, which can
kill its child on KeyboardInterrupt or timeout, leaving native lock files;
recovery then refuses and retains pins until operator inspection.

``prepare(root, archive)``
    Binds an existing stash archive (``manifest.json``: a list of
    ``{stash, patch, bytes, sha256}`` rows of ``git stash show -p --binary
    --include-untracked`` exports) to this repository. Every entry in the
    current stack must have a row (a missing row refuses; rows for OIDs retired
    by a completed transaction in this journal are allowed). Each export is
    checked for size, SHA-256 and byte equality with a fresh canonical export
    and round-tripped onto ``^1`` in a scratch index (it must reproduce the
    worktree tree overlaid with the untracked tree). Because that export omits
    the index (``^2``) tree, a canonical ``<oid>.index.patch`` (``git diff
    --binary --full-index <oid>^1 <oid>^2``) is added, round-trip verified, and
    never overwritten. The patches need ``^1`` (and its history) to apply, and
    a retired entry's base may be reachable only through the stash reflog (a
    deleted topic branch), so ``<oid>.bundle`` is added too: a self-contained
    Git bundle of the stash commit with all its parents and their full
    history. It is built in a temporary bare repository that fetches the exact
    OID from this one (no source ref or reflog is created; grafts and replace
    refs are ignored) and bundles the scratch ref ``refs/hermes-stash-archive/
    stash``. Its header must list exactly that ref at that OID and no
    prerequisite; its exact bytes are imported into a second empty bare
    repository, which must pass ``bundle verify``, ``fsck --full`` and a
    ``rev-list --objects --missing=error`` walk and hold exactly that OID with
    the snapshot's parents. An existing bundle file is reused only if it
    verifies; it is never overwritten. ``retirement-manifest.json`` (version 1)
    records the canonical root, common dir, origin URL and ref format, the full
    ordered reflog snapshot and every archive file's size and SHA-256. Archive
    rows and their ``include_untracked``/``index``/``bundle`` parts are
    validated strictly (exact keys, non-negative integer sizes, lowercase
    SHA-256, bundle file ``<oid>.bundle``, non-empty); rows written before
    bundles existed are refused, so run ``prepare`` again.

    Restoring a retired entry after GC: ``git bundle unbundle <oid>.bundle``
    (writes objects, no refs), then ``git stash store -m <subject> <oid>``.

``retire(root, archive, oids)``
    Selects entries by full OID only. Every archive row (targets and
    non-targets) is re-verified: patches byte-for-byte against a fresh export,
    the bundle by size, SHA-256 and the same full import check. The journal
    intent binds each target's patch and bundle hashes. One
    ``git update-ref --stdin`` transaction deletes ``refs/stash`` at the
    snapshot tip and creates ``refs/hermes-retire/keep/<txn>/<n>`` for every
    non-target plus the marker ``refs/hermes-retire/txn/<txn>``. After
    ``prepare: ok`` the full stack is re-read and byte-compared with the
    snapshot. A fsynced journal intent precedes ``commit``. After the commit
    the stack must no longer equal the snapshot or hold a target (else it is a
    partial commit: refused, nothing journaled, pins kept); the new ref files
    and their directories are fsynced, then ``committed`` is journaled.
    Non-targets are re-stored oldest first, the restored ref, reflog
    and directories are fsynced, the stack is verified, and only then are the
    retirement refs deleted.

``recover(root, archive)``
    Finishes or aborts an unfinished transaction (also run first by ``prepare``
    and ``retire``). It refuses while Git lock files of the transaction exist,
    refuses broken refs, and marks a transaction aborted only when no retirement
    refs exist, no post-commit event was journaled and the stash stack is
    byte-identical to the manifest snapshot. Anything else that is not a
    provable commit fails closed with a diagnosis and keeps every pin. Before
    journaling ``committed`` on behalf of a run that died, it applies the same
    partial-commit refusal (marker present but stack intact or a target still
    stacked).

Crash boundaries (what is and is not promised):

* Abrupt death of the Python process (alone, or with its whole process group):
  Git children live in their own session and survive. A handled Ctrl-C or timeout
  can instead kill a subprocess.run child during restore or pin cleanup, leaving
  locks and requiring inspection; it never kills the prepared transaction child.
  Before ``commit`` is sent
  Git rolls back on EOF. After it is sent the orphaned ``update-ref`` holds the
  flock until it exits and completes the commit.
* A kill aimed at the Git child itself (its pid or its session) is not
  prevented. Before Git reads ``commit`` it leaves its ``.lock`` files, and
  recovery refuses until an operator removes them. While Git applies the
  commit it can leave a partial commit; recovery never aborts it or records it
  as committed when the marker survives with the stack intact or a target
  still stacked, and refuses when refs are missing (see power loss). It is not
  atomic.
* Power loss: Git's files backend applies a multi-ref transaction as a series
  of renames/unlinks and is not crash atomic. ``core.fsync=reference`` makes Git
  fsync each new ref file; Git does not fsync directories or the stash reflog,
  so this module fsyncs them (read-only opens) before journaling ``committed``
  and before deleting the pins. A power loss inside Git's commit, or before
  those fsyncs, can leave any subset of the renames/unlinks on disk; recovery
  then refuses (never aborts) unless the stack equals the snapshot with no
  refs left. Durability is only as good as the filesystem's fsync (on macOS
  ``F_FULLFSYNC`` is used when available).
* Retired entries' commits become unreachable and GC may delete them and any
  base reachable only through them; the per-entry bundle keeps the commit, its
  parents and full history restorable. Pinned non-targets are reachable only
  through the keep refs while a transaction is pending.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import contextvars
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import stat
import subprocess
import sys
import tempfile
import time
import uuid

# Interpreter contract: the supported entry point is an unqualified ``python3``, which is
# Python 3.9 on stock macOS (Xcode/CommandLineTools). 3.9 is the oldest tested version
# (CI matrix); anything older is refused at import, before any lock, archive or ref write.
MIN_PYTHON = (3, 9)
if sys.version_info < MIN_PYTHON:
    raise RuntimeError('stash retirement needs Python %d.%d or newer, not %s; nothing was changed'
                       % (MIN_PYTHON + (sys.version.split()[0],)))

VERSION = 1  # rows without a 'bundle' part fail the strict row-key check; re-run prepare
FORMAT = 'hermes-stash-retirement-manifest'
LEGACY_MANIFEST = 'manifest.json'
MANIFEST = 'retirement-manifest.json'
JOURNAL = 'stash-retirement-journal.jsonl'
AUDIT = 'stash-retirement-audit.jsonl'
NAMESPACE = 'refs/hermes-retire/'
KEEP_NS = NAMESPACE + 'keep/'
TXN_NS = NAMESPACE + 'txn/'
OID_RE = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
SHA256_RE = re.compile(r'[0-9a-f]{64}\Z')
TXN_RE = re.compile(r'[0-9a-f]{32}\Z')
SELECTOR_RE = re.compile(r'refs/stash@\{(\d+) ([+-]\d{4})\}\Z')
LOG_FORMAT = '%H%x00%P%x00%gD%x00%gn%x00%ge%x00%gs'
GIT_TIMEOUT = 300
TXN_TIMEOUT = 120  # seconds to wait for each update-ref reply / exit
POST_COMMIT_EVENTS = ('committed', 'storing', 'stored', 'verified')
ARCHIVE_KINDS = ('include_untracked', 'index', 'bundle')
PART_KEYS = frozenset(('file', 'bytes', 'sha256'))
# The one ref every archive bundle carries; it exists only in scratch repositories.
BUNDLE_REF = 'refs/hermes-stash-archive/stash'
BUNDLE_SUFFIX = '.bundle'
# upload-pack runs in the source repository with Git's local-repo environment cleared,
# so grafts/replace refs are disabled on its own command line.
UPLOAD_PACK = ('GIT_GRAFT_FILE=/dev/null GIT_NO_REPLACE_OBJECTS=1 git --no-replace-objects '
               '-c uploadpack.allowAnySHA1InWant=true -c advice.graftFileDeprecated=false '
               '-c core.hooksPath=/dev/null upload-pack')

# Config pinned to Git defaults so local config cannot change canonical bytes,
# plus hooks/fsmonitor/attributes/signature output disabled, ref fsync on, and
# no background auto-gc (a detached child would inherit the sync lock).
CONFIG = (
    'core.hooksPath=/dev/null', 'core.fsmonitor=false', 'core.attributesFile=/dev/null',
    'core.quotePath=true', 'core.abbrev=auto', 'color.ui=false', 'log.showSignature=false',
    'diff.noprefix=false', 'diff.mnemonicPrefix=false', 'diff.srcPrefix=a/', 'diff.dstPrefix=b/',
    'diff.relative=false', 'diff.renames=true', 'diff.algorithm=myers', 'diff.indentHeuristic=true',
    'diff.suppressBlankEmpty=false', 'diff.context=3', 'diff.interHunkContext=0',
    'diff.submodule=short', 'apply.whitespace=nowarn', 'core.autocrlf=false', 'core.safecrlf=false',
    'core.fsync=reference', 'core.fsyncMethod=fsync', 'core.logAllRefUpdates=true',
    'core.commitGraph=false', 'gc.auto=0', 'maintenance.auto=false',
)
DIFF_FLAGS = ('--no-ext-diff', '--no-textconv', '--no-color', '-O/dev/null')

# The locked sync-lock fd of the current context; inherited by every Git child.
_LOCK_FD = contextvars.ContextVar('stash_retirement_lock_fd', default=None)


class RetirementError(ValueError):
    """Refusal or failure; the message says what was (not) changed."""


# Git plumbing ---------------------------------------------------------------

def _env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1',
               GIT_GRAFT_FILE=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat', PAGER='cat',
               LC_ALL='C', LANG='C', TZ='UTC',
               GIT_COMMITTER_NAME='hermes stash retirement',
               GIT_COMMITTER_EMAIL='hermes-stash-retirement@localhost',
               GIT_AUTHOR_NAME='hermes stash retirement',
               GIT_AUTHOR_EMAIL='hermes-stash-retirement@localhost')
    env.update(extra or {})
    return env


def _argv(root, args):
    argv = ['git', '--no-replace-objects', '-C', str(root)]
    for item in CONFIG:
        argv += ['-c', item]
    return argv + list(args)


def _pass_fds():
    fd = _LOCK_FD.get()
    return () if fd is None else (fd,)


def _git(root, *args, input=None, extra_env=None, check=True):
    try:
        # Isolate direct process-group signals. subprocess.run can still kill
        # this child on KeyboardInterrupt/timeout; recovery fails closed.
        p = subprocess.run(_argv(root, args), input=input, env=_env(extra_env),
                           capture_output=True, timeout=GIT_TIMEOUT, pass_fds=_pass_fds(),
                           start_new_session=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RetirementError(f'git {args[0]} could not run: {exc}') from exc
    if check and p.returncode:
        raise RetirementError(f'git {" ".join(args[:3])} failed: '
                              + p.stderr.decode(errors='replace').strip())
    return p if not check else p.stdout


def _line(data):
    return data.decode().rstrip('\n')


def _identity(root):
    top = Path(_line(_git(root, 'rev-parse', '--show-toplevel'))).resolve()
    if top != root:
        raise RetirementError('root must be the repository top level')
    common = Path(_line(_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir'))).resolve(strict=True)
    ref_format = _line(_git(root, 'rev-parse', '--show-ref-format'))
    if ref_format != 'files':
        raise RetirementError('only the files ref backend is supported, not ' + ref_format)
    if _line(_git(root, 'rev-parse', '--is-shallow-repository')) != 'false':
        raise RetirementError('shallow repositories are refused (shallow grafts change history)')
    p = _git(root, 'config', '--get', 'remote.origin.url', check=False)
    if p.returncode or not p.stdout.strip():
        raise RetirementError('repository has no origin URL to bind')
    return {'root': str(root), 'common_dir': str(common), 'origin': _line(p.stdout), 'ref_format': ref_format}


def _loose_files(common, prefix):
    """Every non-directory entry under <common>/<prefix> (refs namespace), as ref names."""
    base = Path(common) / prefix.rstrip('/')
    found = []
    if not base.is_dir() or base.is_symlink():
        if base.exists() or base.is_symlink():
            found.append(prefix.rstrip('/'))
        return found
    for dirpath, dirs, files in os.walk(base):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(dirpath, d))]:
            rel = os.path.relpath(os.path.join(dirpath, name), common)
            found.append(PurePosixPath(*Path(rel).parts).as_posix())
    return found


def _refs(root, prefix, common=None):
    """Refs under prefix. Broken refs (Git warns and skips them, rc 0) and, for the
    retirement namespace, loose files Git does not list are refused, never ignored."""
    p = _git(root, 'for-each-ref', '--format=%(refname)%00%(objectname)', prefix, check=False)
    err = p.stderr.decode(errors='replace').strip()
    if p.returncode or err:
        raise RetirementError(f'refusing: Git reports unreadable/broken refs under {prefix}: {err or p.returncode}'
                              '; nothing changed, pins retained')
    refs = {}
    for line in p.stdout.decode().splitlines():
        name, oid = line.split('\0')
        refs[name] = oid
    if common is not None:
        stray = sorted(set(_loose_files(common, prefix)) - set(refs))
        if stray:
            raise RetirementError('refusing: unexpected files in the retirement ref namespace (broken refs or '
                                  'Git lock files): ' + ', '.join(stray) + '; nothing changed, pins retained')
    return refs


def _snapshot(root):
    """(raw bytes, rows) for the full refs/stash reflog, newest first."""
    tip = _refs(root, 'refs/stash').get('refs/stash')
    if tip is None:
        return b'', []
    log = _git(root, 'log', '-g', '--no-abbrev', '--no-color', '--no-mailmap', '--date=raw',
               '--format=' + LOG_FORMAT, 'refs/stash')
    rows = []
    for line in log.decode().split('\n')[:-1] if log else []:
        fields = line.split('\0')
        if len(fields) != 6:
            raise RetirementError('unreadable stash reflog')
        oid, parents, selector, name, email, subject = fields
        match = SELECTOR_RE.fullmatch(selector)
        parents = parents.split()
        if (not OID_RE.fullmatch(oid) or not match or len(parents) not in (2, 3)
                or not all(OID_RE.fullmatch(p) for p in parents)):
            raise RetirementError('stash reflog entry is not a stash commit')
        rows.append({'oid': oid, 'parents': parents, 'time': int(match[1]), 'tz': match[2],
                     'name': name, 'email': email, 'subject': subject})
    if not rows or rows[0]['oid'] != tip:
        raise RetirementError('refs/stash tip does not match its reflog')
    return b'tip ' + tip.encode() + b'\n' + log, rows


def _parse_raw(raw):
    head, _, log = raw.partition(b'\n')
    if not head.startswith(b'tip '):
        raise RetirementError('invalid manifest snapshot')
    rows = []
    for line in log.decode().split('\n')[:-1]:
        oid, parents, selector, name, email, subject = line.split('\0')
        match = SELECTOR_RE.fullmatch(selector)
        if not match:
            raise RetirementError('invalid manifest snapshot')
        rows.append({'oid': oid, 'parents': parents.split(), 'time': int(match[1]), 'tz': match[2],
                     'name': name, 'email': email, 'subject': subject})
    if not rows or head[4:].decode() != rows[0]['oid']:
        raise RetirementError('invalid manifest snapshot')
    return rows


def export_include_untracked(root, oid):
    """Canonical form of the existing archive export."""
    return _git(root, 'stash', 'show', '-p', '--binary', '--include-untracked', *DIFF_FLAGS, oid)


def export_index(root, oid):
    """Canonical index-parent diff: HEAD-at-stash (^1) to the stashed index (^2)."""
    return _git(root, 'diff', '--binary', '--full-index', *DIFF_FLAGS, oid + '^1', oid + '^2')


def _apply_on_base(root, oid, patch, tmp, name):
    env = {'GIT_INDEX_FILE': str(Path(tmp) / name)}
    _git(root, 'read-tree', oid + '^1', extra_env=env)
    if patch:
        _git(root, 'apply', '--cached', '--binary', '-', input=patch, extra_env=env)
    return _line(_git(root, 'write-tree', extra_env=env))


def _verify_index_round_trip(root, oid, patch):
    with tempfile.TemporaryDirectory(prefix='stash-retire-index-') as tmp:
        tree = _apply_on_base(root, oid, patch, tmp, 'index')
    if tree != _line(_git(root, 'rev-parse', oid + '^2^{tree}')):
        raise RetirementError('index archive does not reproduce the stashed index: ' + oid)


def _verify_worktree_round_trip(root, oid, patch):
    """The include-untracked export applied to ^1 must equal the worktree tree with
    the untracked (^3) tree overlaid: every tracked and untracked byte and mode."""
    with tempfile.TemporaryDirectory(prefix='stash-retire-worktree-') as tmp:
        got = _apply_on_base(root, oid, patch, tmp, 'applied')
        env = {'GIT_INDEX_FILE': str(Path(tmp) / 'expected')}
        _git(root, 'read-tree', oid + '^{tree}', extra_env=env)
        has_u = _git(root, 'rev-parse', '-q', '--verify', oid + '^3', check=False).returncode == 0
        if has_u:
            listing = _git(root, 'ls-tree', '-r', '-z', '--full-tree', oid + '^3')
            if listing:
                _git(root, 'update-index', '-z', '--index-info', input=listing, extra_env=env)
        expected = _line(_git(root, 'write-tree', extra_env=env))
    if got != expected:
        raise RetirementError('worktree/untracked archive does not reproduce the stash: ' + oid)


def _store(root, oid, subject):
    _git(root, 'stash', 'store', '-m', subject, oid)


# Bundles ----------------------------------------------------------------------

def _object_format(root):
    fmt = _line(_git(root, 'rev-parse', '--show-object-format'))
    if fmt not in ('sha1', 'sha256'):
        raise RetirementError('unsupported object format: ' + fmt)
    return fmt


def _scratch_repo(tmp, name, fmt):
    path = Path(tmp) / name
    _git(tmp, 'init', '-q', '--bare', '--object-format=' + fmt, str(path))
    return path


def _bundle_header(data, oid, fmt):
    """The header must name exactly ``<oid> BUNDLE_REF`` and no prerequisite or filter."""
    head, sep, _ = data.partition(b'\n\n')
    try:
        lines = head.decode('ascii').split('\n')
    except UnicodeDecodeError:
        lines = []
    if not sep or not lines or lines[0] not in ('# v2 git bundle', '# v3 git bundle'):
        raise RetirementError('archive bundle has no valid bundle header: ' + oid)
    refs = []
    for line in lines[1:]:
        if line.startswith('-'):
            raise RetirementError('archive bundle has prerequisites (not self-contained): ' + oid)
        if line.startswith('@'):
            if lines[0] != '# v3 git bundle' or line != '@object-format=' + fmt:
                raise RetirementError('archive bundle has an unsupported capability: ' + oid)
            continue
        refs.append(line)
    if refs != [f'{oid} {BUNDLE_REF}']:
        raise RetirementError('archive bundle does not carry exactly the stash OID: ' + oid)
    if fmt != 'sha1' and lines[0] != '# v3 git bundle':
        raise RetirementError('archive bundle object format mismatch: ' + oid)


def _verify_bundle(data, oid, parents, fmt):
    """Import the exact bytes into a new empty bare repository; the stash commit, its
    parents and their whole history must be present and fully connected."""
    _bundle_header(data, oid, fmt)
    with tempfile.TemporaryDirectory(prefix='stash-retire-bundle-verify-') as tmp:
        path = Path(tmp) / 'check.bundle'
        path.write_bytes(data)
        repo = _scratch_repo(tmp, 'verify.git', fmt)
        try:
            _git(repo, 'bundle', 'verify', '-q', str(path))
            _git(repo, 'fetch', '-q', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules',
                 str(path), f'{BUNDLE_REF}:{BUNDLE_REF}')
            refs = _refs(repo, 'refs/')
            got_type = _line(_git(repo, 'cat-file', '-t', oid))
            got_parents = _line(_git(repo, 'rev-parse', oid + '^@')).split()
            _git(repo, 'fsck', '--full', '--no-dangling', '--no-progress')
            _git(repo, 'rev-list', '--objects', '--missing=error', '--quiet', oid)
        except RetirementError as exc:
            raise RetirementError(f'archive bundle does not import cleanly: {oid}: {exc}') from exc
    if refs != {BUNDLE_REF: oid} or got_type != 'commit' or got_parents != list(parents):
        raise RetirementError('archive bundle content mismatch (OID, type or parents): ' + oid)


def _build_bundle(root, oid, parents, fmt):
    """A self-contained bundle of one stash commit: fetched by exact OID into a scratch
    bare repository (no source ref is created) and bundled from a scratch ref there."""
    with tempfile.TemporaryDirectory(prefix='stash-retire-bundle-') as tmp:
        repo = _scratch_repo(tmp, 'build.git', fmt)
        _git(repo, 'fetch', '-q', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules',
             '--upload-pack', UPLOAD_PACK, str(root), f'{oid}:{BUNDLE_REF}')
        if _refs(repo, 'refs/') != {BUNDLE_REF: oid}:
            raise RetirementError('scratch fetch did not produce exactly the stash OID: ' + oid)
        out = Path(tmp) / 'out.bundle'
        _git(repo, 'bundle', 'create', '-q', str(out), BUNDLE_REF)
        data = _read_regular(out)
    _verify_bundle(data, oid, parents, fmt)
    return data


def _prepared_bundle(root, archive, oid, parents, fmt):
    """An existing archive bundle is reused only if it verifies (it is never overwritten);
    otherwise a new one is built and verified."""
    path, rel = _archive_path(archive, oid + BUNDLE_SUFFIX)
    if path.exists() or path.is_symlink():
        data = _read_regular(path)
        try:
            _verify_bundle(data, oid, parents, fmt)
        except RetirementError as exc:
            raise RetirementError(f'existing archive bundle {rel} does not verify; refusing to overwrite '
                                  f'it: {exc}') from exc
        return data
    return _build_bundle(root, oid, parents, fmt)


# Files ------------------------------------------------------------------------

def _archive_path(archive, name):
    if not isinstance(name, str) or not name or '\0' in name:
        raise RetirementError('invalid archive path')
    p = Path(name)
    if p.is_absolute():
        try:
            p = p.relative_to(archive)
        except ValueError:
            raise RetirementError('archive file is outside the archive: ' + name) from None
    if not p.parts or any(part in ('', '.', '..') for part in p.parts):
        raise RetirementError('invalid archive path: ' + name)
    target = archive
    for part in p.parts:
        target = target / part
        if target.is_symlink():
            raise RetirementError('archive symlink refused: ' + name)
    return target, PurePosixPath(*p.parts).as_posix()


def _read_regular(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise RetirementError(f'archive file unreadable: {path}: {exc.strerror}') from exc
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise RetirementError('archive file is not regular: ' + str(path))
        return f.read()


def _fsync_fd(fd):
    if hasattr(fcntl, 'F_FULLFSYNC'):  # macOS: plain fsync does not flush the drive cache
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def _fsync_path(path):
    """fsync an existing file or directory through a read-only, no-follow open (never a write)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RetirementError(f'cannot open {path} for fsync: {exc.strerror}') from exc
    try:
        _fsync_fd(fd)
    except OSError as exc:
        raise RetirementError(f'fsync failed for {path}: {exc.strerror}') from exc
    finally:
        os.close(fd)
    return True


def _fsync_dir(path):
    if not _fsync_path(path):
        raise RetirementError('directory to fsync is missing: ' + str(path))


def _fsync_refs(common, names, extra_dirs=()):
    """Durably record ref files (and deletions) under the common dir: each named
    file if present, every ancestor directory up to the common dir, packed-refs."""
    common = Path(common)
    dirs = {common, common / 'refs'} | {common / d for d in extra_dirs}
    for name in names:
        path = common / name
        _fsync_path(path)
        parent = path.parent
        while parent != common and common in parent.parents:
            dirs.add(parent)
            parent = parent.parent
    _fsync_path(common / 'packed-refs')
    for d in sorted(dirs, key=lambda d: len(d.parts), reverse=True):  # deepest first
        _fsync_path(d)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise RetirementError('short write')
        view = view[n:]


def _write_new(path, data):
    """Create-exclusive durable write; an existing file must already hold exactly data."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        if _read_regular(path) != data:
            raise RetirementError('refusing to overwrite differing archive file: ' + str(path)) from None
        return
    try:
        _write_all(fd, data)
        _fsync_fd(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)
    if _read_regular(path) != data:
        raise RetirementError('archive readback mismatch: ' + str(path))


def _replace_durably(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        try:
            _write_all(fd, data)
            _fsync_fd(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _fsync_dir(path.parent)
    if _read_regular(path) != data:
        raise RetirementError('manifest readback mismatch')


def _append(archive, name, record):
    path = archive / name
    created = not path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RetirementError(name + ' must be a regular file')
        line = json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(), **record),
                          sort_keys=True) + '\n'
        _write_all(fd, line.encode())  # O_APPEND + single writer (flock): pieces stay contiguous
        _fsync_fd(fd)
    finally:
        os.close(fd)
    if created:
        _fsync_dir(archive)


def _journal(archive, txn, event, **fields):
    _append(archive, JOURNAL, dict(txn=txn, event=event, **fields))


def _audit(archive, event, **fields):
    _append(archive, AUDIT, dict(event=event, **fields))


def _read_journal(archive):
    path = archive / JOURNAL
    if not path.exists() and not path.is_symlink():
        return []
    records = []
    for line in _read_regular(path).decode().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            raise RetirementError('unreadable retirement journal') from None
        if (not isinstance(rec, dict) or not isinstance(rec.get('txn'), str)
                or not TXN_RE.fullmatch(rec['txn']) or not isinstance(rec.get('event'), str)):
            raise RetirementError('invalid retirement journal record')
        records.append(rec)
    return records


def _completed_targets(archive):
    by_txn = {}
    for rec in _read_journal(archive):
        by_txn.setdefault(rec['txn'], []).append(rec)
    done = set()
    for events in by_txn.values():
        intent = next((e for e in events if e['event'] == 'intent'), None)
        if intent and any(e['event'] == 'complete' for e in events):
            done.update(t['oid'] for t in intent['targets'])
    return done


# Locking and location -------------------------------------------------------------

@contextmanager
def _locked(root, archive):
    try:
        root = Path(root).expanduser().resolve(strict=True)
        archive = Path(archive).expanduser().resolve(strict=True)
        home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes').expanduser().resolve(strict=True)
    except OSError as exc:
        raise RetirementError('root, archive or HERMES_HOME is missing: ' + str(exc)) from exc
    if not root.is_dir() or not archive.is_dir():
        raise RetirementError('root and archive must be directories')
    try:
        fd = os.open(home / '.hermes-sync.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise RetirementError('cannot open sync lock: ' + str(exc)) from exc
    token = None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RetirementError('sync lock must be a regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise RetirementError('sync lock is held by another process (possibly an orphaned Git child '
                                      'of an earlier run); nothing changed') from exc
            raise
        token = _LOCK_FD.set(fd)  # Git children inherit the locked description via pass_fds.
        ident = _identity(root)
        common = Path(ident['common_dir'])
        if archive == root or root in archive.parents or archive == common or common in archive.parents:
            raise RetirementError('archive must be outside the checkout and its Git directory')
        yield root, archive, ident
    finally:
        if token is not None:
            _LOCK_FD.reset(token)
        os.close(fd)  # Never unlink the shared flock inode.


def _native_locks(common, txn_id):
    """Git lock files that a live or killed update-ref of this transaction may hold."""
    common = Path(common)
    found = [p for p in (common / 'refs' / 'stash.lock', common / 'packed-refs.lock')
             if p.exists() or p.is_symlink()]
    for sub in ('txn', 'keep'):
        base = common / 'refs' / 'hermes-retire' / sub
        if base.is_dir():
            for dirpath, _, files in os.walk(base):
                found += [Path(dirpath) / f for f in files if f.endswith('.lock')]
    return [str(p) for p in found]


# Manifest ---------------------------------------------------------------------

def _legacy_rows(archive, oids_in_stack, retired):
    data = _read_regular(_archive_path(archive, LEGACY_MANIFEST)[0])
    try:
        rows = json.loads(data)
    except ValueError:
        raise RetirementError('unreadable legacy manifest') from None
    if not isinstance(rows, list) or not rows:
        raise RetirementError('legacy manifest must be a non-empty list')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('stash'), str)
                or not OID_RE.fullmatch(row['stash']) or not isinstance(row.get('bytes'), int)
                or isinstance(row.get('bytes'), bool) or not isinstance(row.get('sha256'), str)
                or not SHA256_RE.fullmatch(row['sha256'])):
            raise RetirementError('invalid legacy manifest row')
        if row['stash'] in seen:
            raise RetirementError('duplicate archived OID: ' + row['stash'])
        if row['stash'] not in oids_in_stack and row['stash'] not in retired:
            raise RetirementError('archived OID is not in the stash stack: ' + row['stash'])
        seen.add(row['stash'])
    missing = [oid for oid in oids_in_stack if oid not in seen]
    if missing:
        raise RetirementError('every stash entry needs an archive row; missing: ' + ', '.join(missing)
                              + '; nothing changed')
    return rows, hashlib.sha256(data).hexdigest()


def _checked_file(archive, name, size, digest, expected=None):
    path, rel = _archive_path(archive, name)
    data = _read_regular(path)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise RetirementError('archive size or SHA-256 mismatch (missing, truncated or tampered): ' + rel)
    if expected is not None and data != expected:
        raise RetirementError('archive differs from canonical Git export: ' + rel)
    return data, rel


def _load_manifest(archive, ident):
    data = _read_regular(_archive_path(archive, MANIFEST)[0])
    try:
        doc = json.loads(data)
        if doc.get('format') != FORMAT or doc.get('version') != VERSION:
            raise RetirementError('unsupported retirement manifest version')
        if doc['identity'] != ident:
            raise RetirementError('retirement manifest is bound to a different repository')
        snap = doc['snapshot']
        raw = base64.b64decode(snap['raw_b64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != snap['sha256']:
            raise RetirementError('retirement manifest snapshot hash mismatch')
        rows = _parse_raw(raw)
        if snap['entries'] != rows or snap['tip'] != rows[0]['oid']:
            raise RetirementError('retirement manifest entries differ from its raw snapshot')
        archives = {}
        stack_oids = [r['oid'] for r in rows]
        for row in doc['archives']:
            if not isinstance(row, dict) or set(row) != {'oid', *ARCHIVE_KINDS}:
                raise RetirementError('invalid retirement manifest archive row (keys)')
            oid = row['oid']
            if oid in archives or oid not in stack_oids:
                raise RetirementError('retirement manifest has unknown or duplicate archive OIDs')
            for kind in ARCHIVE_KINDS:
                part = row[kind]
                if (not isinstance(part, dict) or set(part) != PART_KEYS
                        or type(part['bytes']) is not int or part['bytes'] < 0
                        or not isinstance(part['sha256'], str) or not SHA256_RE.fullmatch(part['sha256'])
                        or not isinstance(part['file'], str)):
                    raise RetirementError('invalid retirement manifest archive row')
            if row['bundle']['file'] != oid + BUNDLE_SUFFIX or row['bundle']['bytes'] == 0:
                raise RetirementError('invalid retirement manifest bundle entry: ' + oid)
            archives[oid] = row
        if set(archives) != set(stack_oids):
            raise RetirementError('retirement manifest lacks archives for: '
                                  + ', '.join(o for o in stack_oids if o not in archives))
    except RetirementError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RetirementError('invalid retirement manifest: ' + str(exc)) from exc
    return doc, raw, rows, archives, hashlib.sha256(data).hexdigest()


def _verify_archive_row(root, archive, row, fresh, parents, fmt):
    """Patches: size, SHA-256 and byte equality with a fresh export. Bundle: size,
    SHA-256, then a full import of those exact bytes (exact OID and parents)."""
    oid = row['oid']
    for kind, export in (('include_untracked', export_include_untracked), ('index', export_index)):
        part = row[kind]
        _checked_file(archive, part['file'], part['bytes'], part['sha256'],
                      export(root, oid) if fresh else None)
    part = row['bundle']
    data, _ = _checked_file(archive, part['file'], part['bytes'], part['sha256'])
    _verify_bundle(data, oid, parents, fmt)


# Transaction ------------------------------------------------------------------------

class _Transaction:
    """``git update-ref --stdin`` driven line by line. Never killed: before ``commit``
    is sent, EOF makes Git roll back; after it is sent, Git must be left to finish.
    The child inherits the sync lock, so a hung or orphaned child keeps it."""

    def __init__(self, root):
        self.root = root
        self._buf = b''
        self._err = b''
        try:
            self.proc = subprocess.Popen(_argv(root, ['update-ref', '--stdin']), stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(),
                                         bufsize=0, pass_fds=_pass_fds(), start_new_session=True)
        except OSError as exc:
            raise RetirementError('git update-ref could not run: ' + str(exc)) from exc

    def send(self, line):
        stdin = self.proc.stdin
        if stdin is None:
            return  # EOF already sent: Git rolls back an uncommitted transaction.
        try:
            _write_all(stdin.fileno(), line.encode() + b'\n')
        except (BrokenPipeError, ValueError, OSError, RetirementError):
            pass  # The reply check reports the failure with Git's stderr.

    def readline(self, timeout):
        deadline = time.monotonic() + timeout
        if self.proc.stdout is None or self.proc.stdout.closed:
            line, sep, self._buf = self._buf.partition(b'\n')
            return line + sep  # drained: no further reply can arrive
        fd = self.proc.stdout.fileno()
        while b'\n' not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            self._buf += chunk
        line, sep, self._buf = self._buf.partition(b'\n')
        return line + sep

    def expect(self, command):
        self.send(command)
        reply = self.readline(TXN_TIMEOUT)
        if reply is None:
            raise RetirementError(f'update-ref did not answer {command!r} within {TXN_TIMEOUT}s; '
                                  + self.close_and_wait())
        if reply != (command + ': ok\n').encode():
            raise RetirementError(f'update-ref {command} failed: {self.close_and_wait()}')

    def close_stdin(self):
        """Send EOF once. The pipe is detached from the Popen so nothing (including
        Popen.communicate on Python 3.9, which flushes stdin unconditionally) ever
        touches the closed file again. Idempotent."""
        stdin, self.proc.stdin = self.proc.stdin, None
        if stdin is None:
            return
        try:
            stdin.close()
        except (OSError, ValueError):
            pass

    def close_and_wait(self, timeout=None):
        """EOF, then wait (bounded, no kill). Returns Git's stderr or a 'still running' note.

        Drains stdout/stderr with select + os.read and reaps with Popen.wait instead
        of Popen.communicate, whose per-version internal state is not safe to retry
        after a failure or a timeout. Safe to call repeatedly: stderr accumulates on
        the instance and pipes closed at EOF are skipped on the next call."""
        self.close_stdin()
        deadline = time.monotonic() + (TXN_TIMEOUT if timeout is None else timeout)
        streams = {s.fileno(): s for s in (self.proc.stdout, self.proc.stderr) if s is not None and not s.closed}
        err_fd = self.proc.stderr.fileno() if self.proc.stderr is not None and not self.proc.stderr.closed else None
        while streams:
            left = deadline - time.monotonic()
            if left <= 0:
                if self.proc.poll() is None:
                    return self._still_running()
                break  # Git exited; a hook grandchild may still hold the pipes.
            ready, _, _ = select.select(list(streams), [], [], min(left, 1.0))
            if not ready and self.proc.poll() is not None:
                break  # Git exited and wrote nothing more for 1s; something else holds the pipes.
            for fd in ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    chunk = b''
                if not chunk:
                    streams.pop(fd).close()
                elif fd == err_fd:
                    self._err += chunk
                else:
                    self._buf += chunk
        try:
            self.proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return self._still_running()
        return self._err.decode(errors='replace').strip() or f'exit {self.proc.returncode}'

    def _still_running(self):
        return (f'update-ref pid {self.proc.pid} is still running and holds the sync lock; '
                'it was left to finish (never killed)')

    def exited(self):
        return self.proc.poll() is not None

    def abort(self):
        """Pre-commit only: ask Git to roll back, else EOF does."""
        if self.proc.poll() is None:
            self.send('abort')
        self.close_and_wait()


def _prepare_transaction(root, txn_id, tip, keep):
    txn = _Transaction(root)
    try:
        txn.expect('start')
        txn.send(f'delete refs/stash {tip}')
        txn.send(f'create {TXN_NS}{txn_id} {tip}')
        for index, row in keep:
            txn.send(f'create {KEEP_NS}{txn_id}/{index} {row["oid"]}')
        txn.expect('prepare')
    except BaseException:
        txn.abort()
        raise
    return txn


def _check_prepared(root, ident, raw, txn):
    """With Git's refs/stash.lock held, the stack must still be byte-identical."""
    if txn.proc.poll() is not None:
        raise RetirementError('update-ref exited while prepared')
    if _snapshot(root)[0] != raw:
        raise RetirementError('stash stack changed before the lock was taken; aborted, nothing changed')
    if _identity(root) != ident:
        raise RetirementError('repository identity changed; aborted, nothing changed')


def _commit_transaction(root, txn):
    txn.send('commit')
    reply = txn.readline(TXN_TIMEOUT)
    if reply is None:
        raise RetirementError(f'no reply to commit within {TXN_TIMEOUT}s')
    if reply != b'commit: ok\n':
        raise RetirementError('update-ref commit failed: ' + txn.close_and_wait())
    err = txn.close_and_wait()
    if not txn.exited() or txn.proc.returncode:
        raise RetirementError('update-ref after commit: ' + err)


def _delete_txn_refs(root, txn_id, refs):
    lines = ''.join(f'delete {name} {oid}\n' for name, oid in sorted(refs.items())
                    if name == TXN_NS + txn_id or name.startswith(f'{KEEP_NS}{txn_id}/'))
    if lines:
        _git(root, 'update-ref', '--stdin', input=lines.encode())


# Restore / finish ---------------------------------------------------------------------

def _keep_rows(rows, targets):
    return [(i, row) for i, row in enumerate(rows) if row['oid'] not in targets]


def _expected_refs(txn_id, tip, keep):
    refs = {TXN_NS + txn_id: tip}
    refs.update({f'{KEEP_NS}{txn_id}/{i}': row['oid'] for i, row in keep})
    return refs


def _txn_refs(refs, txn_id):
    return {n: o for n, o in refs.items() if n == TXN_NS + txn_id or n.startswith(f'{KEEP_NS}{txn_id}/')}


def _refuse_partial_commit(root, txn_id, raw, targets):
    """Before 'committed' is journaled the commit must be whole: refs/stash deleted. A
    stack still byte-identical to the snapshot, or still holding a target, means the
    deletion did not land (partial commit, e.g. power loss inside Git's commit)."""
    current, rows = _snapshot(root)
    present = sorted(targets & {r['oid'] for r in rows})
    if current == raw or present:
        why = ('the stash stack still equals the manifest snapshot' if current == raw
               else 'targets are still in the stash stack')
        raise RetirementError(f'refusing to treat retirement {txn_id} as committed: partial commit ({why}; '
                              f'retired OID is present in the stash stack: {", ".join(present)}). Nothing '
                              'was journaled, no pin was removed and it is never treated as aborted. '
                              'Inspect `git for-each-ref refs/hermes-retire/` and `git stash list`.')


def _fsync_committed(common, txn_id, keep):
    """After commit: new ref files, their new directories, the deleted refs/stash and reflog."""
    names = [f'refs/hermes-retire/txn/{txn_id}'] + [f'refs/hermes-retire/keep/{txn_id}/{i}' for i, _ in keep]
    _fsync_refs(common, names + ['refs/stash', 'logs/refs/stash'], extra_dirs=('logs', 'logs/refs'))


def _fsync_stash(common):
    _fsync_refs(common, ['refs/stash', 'logs/refs/stash'], extra_dirs=('logs', 'logs/refs'))


def _fsync_cleanup(common, txn_id):
    """Make the pin deletions durable: every surviving ancestor directory and packed-refs."""
    _fsync_refs(common, [], extra_dirs=(f'refs/hermes-retire/keep/{txn_id}', 'refs/hermes-retire/keep',
                                        'refs/hermes-retire/txn', 'refs/hermes-retire'))


def _restore(root, archive, txn_id, keep, events):
    stored = {r['index'] for r in events if r['event'] == 'stored'}
    for index, row in reversed(keep):  # oldest first
        if index in stored:
            continue
        present = row['oid'] in {r['oid'] for r in _snapshot(root)[1]}
        if not present:
            _journal(archive, txn_id, 'storing', index=index, oid=row['oid'])
            _store(root, row['oid'], row['subject'])
        _journal(archive, txn_id, 'stored', index=index, oid=row['oid'], already_present=present)


def _verify_restored(root, keep, targets):
    _, current = _snapshot(root)
    present_targets = targets & {r['oid'] for r in current}
    if present_targets:
        # A partial commit, or a writer re-storing a target: never audit it as retired.
        raise RetirementError('retired OID is present in the stash stack; keep refs retained: '
                              + ', '.join(sorted(present_targets)))
    last = -1
    for _, row in keep:
        hits = [n for n, r in enumerate(current) if r['oid'] == row['oid']]
        if len(hits) != 1:
            raise RetirementError('restored entry missing or duplicated; keep refs retained: ' + row['oid'])
        got = current[hits[0]]
        if got['subject'] != row['subject'] or got['parents'] != row['parents'] or hits[0] <= last:
            raise RetirementError('restored entry order/metadata mismatch; keep refs retained: ' + row['oid'])
        last = hits[0]


def _finish(root, archive, ident, txn_id, intent, keep, events):
    common = ident['common_dir']
    targets = {t['oid'] for t in intent['targets']}
    if not any(r['event'] == 'verified' for r in events):
        _restore(root, archive, txn_id, keep, events)
        _fsync_stash(common)
        _verify_restored(root, keep, targets)
        for target in intent['targets']:
            _audit(archive, 'retired', txn=txn_id, oid=target['oid'], root=intent['identity']['root'],
                   include_untracked_sha256=target['include_untracked_sha256'],
                   index_sha256=target['index_sha256'], bundle_sha256=target['bundle_sha256'],
                   manifest_sha256=intent['manifest_sha256'])
        _journal(archive, txn_id, 'verified')
    # Pins go only after the restored stack is re-verified (it may have been lost
    # since 'verified' was journaled) and durably on disk.
    refs = _refs(root, NAMESPACE, common)
    if _txn_refs(refs, txn_id):
        _verify_restored(root, keep, targets)
        _fsync_stash(common)
        _delete_txn_refs(root, txn_id, refs)
        _fsync_cleanup(common, txn_id)
    _journal(archive, txn_id, 'complete')


# Recovery -------------------------------------------------------------------------------

def _object_exists(root, oid):
    return _git(root, 'cat-file', '-e', oid + '^{commit}', check=False).returncode == 0


def _diagnosis(root, txn_id, events, expected, mine, snapshot_equal, keep, why):
    names = [e['event'] for e in events]
    missing = sorted(set(expected) - set(mine))
    objects = ', '.join(f'{r["oid"]}={"present" if _object_exists(root, r["oid"]) else "MISSING"}'
                        for _, r in keep) or 'none'
    return (f'refusing to resolve retirement {txn_id}: {why}. Journal events: {names}; retirement refs found: '
            f'{sorted(mine) or "none"}; expected but missing: {missing or "none"}; stash stack '
            f'{"equals" if snapshot_equal else "differs from"} the manifest snapshot; non-target objects: '
            f'{objects}. Nothing was changed and no pin was removed; never treated as aborted. Inspect '
            '`git for-each-ref refs/hermes-retire/` and `git stash list`; a missing non-target can be '
            're-stored with `git stash store -m <subject> <oid>` from the manifest.')


def _recover_locked(root, archive, ident):
    common = ident['common_dir']
    by_txn = {}
    for rec in _read_journal(archive):
        by_txn.setdefault(rec['txn'], []).append(rec)
    pending = {t: evs for t, evs in by_txn.items()
               if any(e['event'] == 'intent' for e in evs)
               and not any(e['event'] in ('complete', 'aborted') for e in evs)}
    for txn_id in pending:
        locks = _native_locks(common, txn_id)
        if locks:
            raise RetirementError(f'refusing to recover retirement {txn_id}: Git lock files exist ('
                                  + ', '.join(locks) + '); an update-ref may still be running or was killed. '
                                  'Nothing changed. Remove them only after confirming no Git process is active.')
    refs = _refs(root, NAMESPACE, common)
    ref_txns = set()
    for name in refs:
        m = re.fullmatch(r'refs/hermes-retire/(?:txn/([0-9a-f]{32})|keep/([0-9a-f]{32})/\d+)', name)
        if not m:
            raise RetirementError('unexplained retirement ref: ' + name)
        ref_txns.add(m[1] or m[2])
    if ref_txns - set(pending):
        raise RetirementError('retirement refs not explained by this archive journal: '
                              + ', '.join(sorted(ref_txns - set(pending))))
    if len(pending) > 1:
        raise RetirementError('more than one unfinished retirement transaction')
    result = {'completed': [], 'aborted': []}
    for txn_id, events in pending.items():
        intent = next(e for e in events if e['event'] == 'intent')
        keep, tip, raw = _validate_intent(root, archive, ident, intent)
        mine = _txn_refs(refs, txn_id)
        expected = _expected_refs(txn_id, tip, keep)
        marker = TXN_NS + txn_id
        verified = any(e['event'] == 'verified' for e in events)
        post_commit = any(e['event'] in POST_COMMIT_EVENTS for e in events)
        if any(expected.get(n) != o for n, o in mine.items()):
            raise RetirementError(_diagnosis(root, txn_id, events, expected, mine, _snapshot(root)[0] == raw,
                                             keep, 'retirement refs do not match the bound manifest'))
        if marker in mine or (mine and post_commit):
            # Committed: the marker proves it, or the journal does and every pin is intact.
            if not verified and set(expected) - set(mine) - {marker}:
                raise RetirementError(_diagnosis(root, txn_id, events, expected, mine, _snapshot(root)[0] == raw,
                                                 keep, 'committed, but keep refs are missing before '
                                                 'verification (partial or lost Git commit)'))
            for index, row in keep:
                if (f'{KEEP_NS}{txn_id}/{index}' in mine
                        and _line(_git(root, 'rev-parse', row['oid'] + '^@')).split() != row['parents']):
                    raise RetirementError('pinned stash parents differ from manifest; refusing')
            if not post_commit:
                _refuse_partial_commit(root, txn_id, raw, {t['oid'] for t in intent['targets']})
                _fsync_committed(common, txn_id, keep)
                _journal(archive, txn_id, 'committed', by='recovery')
            _audit(archive, 'recovering', txn=txn_id)
            _finish(root, archive, ident, txn_id, intent, keep, events)
            result['completed'].append(txn_id)
            continue
        if mine:
            raise RetirementError(_diagnosis(root, txn_id, events, expected, mine, _snapshot(root)[0] == raw,
                                             keep, 'retirement refs without marker and no journaled commit'))
        if verified:  # pins were already removed after verification; only 'complete' is missing
            _journal(archive, txn_id, 'complete')
            result['completed'].append(txn_id)
            continue
        snapshot_equal = _snapshot(root)[0] == raw
        if post_commit:
            raise RetirementError(_diagnosis(root, txn_id, events, expected, mine, snapshot_equal, keep,
                                             'the journal records a commit but its refs are missing'))
        if not snapshot_equal:
            raise RetirementError(_diagnosis(root, txn_id, events, expected, mine, snapshot_equal, keep,
                                             'no retirement refs and the stack differs from the snapshot '
                                             '(possible partial commit)'))
        _journal(archive, txn_id, 'aborted', reason='transaction never committed (stack equals snapshot)')
        _audit(archive, 'aborted', txn=txn_id, reason='recovery found no commit and the stack unchanged')
        result['aborted'].append(txn_id)
    return result


def _validate_intent(root, archive, ident, intent):
    """Bind a journal intent to the manifest; return (keep rows from the manifest, tip, raw)."""
    try:
        doc, raw, rows, archives, msha = _load_manifest(archive, ident)
        if intent['manifest_sha256'] != msha or intent['identity'] != ident:
            raise RetirementError('journal intent is not bound to the current manifest/repository')
        if intent['tip'] != rows[0]['oid']:
            raise RetirementError('journal intent tip mismatch')
        targets = {t['oid'] for t in intent['targets']}
        if len(targets) != len(intent['targets']) or not targets:
            raise RetirementError('journal intent targets invalid')
        for t in intent['targets']:
            row = archives.get(t['oid'])
            if (row is None or t['include_untracked_sha256'] != row['include_untracked']['sha256']
                    or t['index_sha256'] != row['index']['sha256']
                    or t['bundle_sha256'] != row['bundle']['sha256']):
                raise RetirementError('journal intent target not bound to a verified archive')
        keep = _keep_rows(rows, targets)
        if intent['keep'] != [{'index': i, 'oid': r['oid']} for i, r in keep]:
            raise RetirementError('journal intent keep list differs from manifest')
    except RetirementError:
        raise
    except (KeyError, TypeError, AttributeError) as exc:
        raise RetirementError('invalid journal intent: ' + str(exc)) from exc
    return keep, rows[0]['oid'], raw


# Entry points -------------------------------------------------------------------------

def recover(root, archive):
    """Finish (or, only when provably uncommitted, mark aborted) an unfinished transaction."""
    with _locked(root, archive) as (root, archive, ident):
        return _recover_locked(root, archive, ident)


def prepare(root, archive):
    with _locked(root, archive) as (root, archive, ident):
        recovered = _recover_locked(root, archive, ident)
        raw, rows = _snapshot(root)
        if not rows:
            raise RetirementError('no stashes to prepare')
        oids = [r['oid'] for r in rows]
        if len(set(oids)) != len(oids):
            raise RetirementError('duplicate OIDs in the stash stack are ambiguous')
        retired = _completed_targets(archive)
        legacy, legacy_sha = _legacy_rows(archive, oids, retired)
        fmt = _object_format(root)
        parents = {r['oid']: r['parents'] for r in rows}
        verified = {}
        for row in legacy:
            oid = row['stash']
            if oid not in oids:  # Retired by a completed transaction in this journal.
                _checked_file(archive, row['patch'], row['bytes'], row['sha256'])
                continue
            data, rel = _checked_file(archive, row['patch'], row['bytes'], row['sha256'],
                                      export_include_untracked(root, oid))
            _verify_worktree_round_trip(root, oid, data)
            index = export_index(root, oid)
            _verify_index_round_trip(root, oid, index)
            verified[oid] = (rel, data, index, _prepared_bundle(root, archive, oid, parents[oid], fmt))
        if _snapshot(root)[0] != raw or _identity(root) != ident:
            raise RetirementError('stash stack or repository changed during preparation')
        archives = []
        for oid in oids:
            rel, data, index, bundle = verified[oid]
            index_name = oid + '.index.patch'
            _write_new(archive / index_name, index)
            bundle_name = oid + BUNDLE_SUFFIX
            _write_new(archive / bundle_name, bundle)
            written, _ = _checked_file(archive, bundle_name, len(bundle), hashlib.sha256(bundle).hexdigest())
            _verify_bundle(written, oid, parents[oid], fmt)  # what is on disk, not what was built
            archives.append({
                'oid': oid,
                'include_untracked': {'file': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
                'index': {'file': index_name, 'bytes': len(index), 'sha256': hashlib.sha256(index).hexdigest()},
                'bundle': {'file': bundle_name, 'bytes': len(bundle), 'sha256': hashlib.sha256(bundle).hexdigest()},
            })
        doc = {'format': FORMAT, 'version': VERSION, 'identity': ident,
               'snapshot': {'tip': oids[0], 'raw_b64': base64.b64encode(raw).decode(),
                            'sha256': hashlib.sha256(raw).hexdigest(), 'entries': rows},
               'legacy_manifest': {'file': LEGACY_MANIFEST, 'sha256': legacy_sha},
               'archives': archives}
        data = (json.dumps(doc, indent=2, sort_keys=True) + '\n').encode()
        path = archive / MANIFEST
        if path.is_symlink():
            raise RetirementError('retirement manifest must not be a symlink')
        if not path.exists() or _read_regular(path) != data:
            if path.exists():
                old = _read_regular(path)
                _write_new(archive / f'retirement-manifest.superseded-{hashlib.sha256(old).hexdigest()[:16]}.json', old)
            _replace_durably(path, data)
            _audit(archive, 'prepared', manifest_sha256=hashlib.sha256(data).hexdigest(),
                   root=ident['root'], stack=len(rows), archived=list(verified))
        return {'manifest': str(path), 'stack': oids, 'archived': [a['oid'] for a in archives],
                'recovered': recovered}


def _selection(oids):
    if isinstance(oids, (str, bytes)) or not isinstance(oids, (list, tuple)):
        raise RetirementError('select a list of full stash OIDs')
    if not oids:
        raise RetirementError('empty selection; nothing changed')
    if any(not isinstance(o, str) or not OID_RE.fullmatch(o) for o in oids):
        raise RetirementError('select full lowercase stash OIDs (no selectors or abbreviations)')
    if len(set(oids)) != len(oids):
        raise RetirementError('duplicate OID in selection')
    return list(oids)


def retire(root, archive, oids):
    selected = _selection(oids)
    with _locked(root, archive) as (root, archive, ident):
        recovered = _recover_locked(root, archive, ident)
        doc, raw, rows, archives, msha = _load_manifest(archive, ident)
        current, _ = _snapshot(root)
        if current != raw:
            raise RetirementError('stash stack differs from the prepared snapshot; run prepare again')
        stack_oids = [r['oid'] for r in rows]
        if len(set(stack_oids)) != len(stack_oids):
            raise RetirementError('duplicate OIDs in the stash stack are ambiguous')
        for oid in selected:
            if oid not in stack_oids:
                raise RetirementError('selected OID is not in the stash stack: ' + oid)
        fmt = _object_format(root)
        for oid in stack_oids:  # every entry, target or not, still matches its archive
            parents = next(r['parents'] for r in rows if r['oid'] == oid)
            _verify_archive_row(root, archive, archives[oid], True, parents, fmt)
        targets = set(selected)
        keep = _keep_rows(rows, targets)
        tip = rows[0]['oid']
        txn_id = uuid.uuid4().hex
        intent = {'manifest_sha256': msha, 'identity': ident, 'tip': tip,
                  'targets': [{'oid': o, 'include_untracked_sha256': archives[o]['include_untracked']['sha256'],
                               'index_sha256': archives[o]['index']['sha256'],
                               'bundle_sha256': archives[o]['bundle']['sha256']} for o in selected],
                  'keep': [{'index': i, 'oid': r['oid']} for i, r in keep]}
        txn = _prepare_transaction(root, txn_id, tip, keep)
        intent_written = False
        try:
            _check_prepared(root, ident, raw, txn)
            _journal(archive, txn_id, 'intent', **intent)
            intent_written = True
            _audit(archive, 'committing', txn=txn_id, targets=intent['targets'], manifest_sha256=msha)
        except BaseException as exc:
            txn.abort()  # 'commit' was never sent: Git rolls back.
            if intent_written and isinstance(exc, Exception) and txn.exited():
                try:  # aborts only if the stack is provably unchanged
                    _recover_locked(root, archive, ident)
                except RetirementError as rexc:
                    raise RetirementError(f'{exc}; then: {rexc}') from exc
            raise
        try:
            _commit_transaction(root, txn)
        except BaseException as exc:
            txn.close_stdin()  # never kill: a sent commit may be in progress
            if not isinstance(exc, Exception):
                raise
            note = txn.close_and_wait()
            if not txn.exited():
                raise RetirementError(f'{exc}; {note}; transaction {txn_id} stays pending: run recover after '
                                      'that process exits') from exc
            outcome = _recover_locked(root, archive, ident)
            if txn_id in outcome['aborted']:
                raise RetirementError(f'{exc}; Git did not commit; aborted, nothing changed') from exc
            return {'txn': txn_id, 'retired': selected, 'preserved': [r['oid'] for _, r in keep],
                    'recovered': recovered, 'note': f'commit reply lost ({exc}); finished by recovery'}
        mine = _txn_refs(_refs(root, NAMESPACE, ident['common_dir']), txn_id)
        if mine != _expected_refs(txn_id, tip, keep):
            raise RetirementError(f'refs after commit differ from the transaction; {txn_id} stays pending')
        _refuse_partial_commit(root, txn_id, raw, {t['oid'] for t in intent['targets']})
        _fsync_committed(ident['common_dir'], txn_id, keep)
        _journal(archive, txn_id, 'committed')
        _finish(root, archive, ident, txn_id, intent, keep, [])
        return {'txn': txn_id, 'retired': selected, 'preserved': [r['oid'] for _, r in keep],
                'recovered': recovered}
