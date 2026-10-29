#!/usr/bin/env python3
"""Fail-closed, merge-aware verification of a public linear patch mirror.

Only object IDs, counts and patch ordinals are reported, never source contents or
Git diagnostics (which may contain private patch context). No denylist is read.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class VerificationError(Exception):
    pass


def git(repo, *args, data=None):
    # Do not inherit another worktree's index, object directory or repository.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false',
         '-c', 'user.name=Mirror verifier', '-c', 'user.email=mirror@example.com',
         '-C', str(repo), *args], input=data, capture_output=True, env=env)
    if result.returncode:
        raise VerificationError('Git operation failed: ' + args[0])
    return result.stdout


def resolve(repo, ref):
    return git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()


def inventory(repo, base, tip, patch_dir):
    expected = git(repo, 'rev-list', '--reverse', '--no-merges', base + '..' + tip).decode().splitlines()
    files = sorted(Path(patch_dir).glob('*.patch'))
    if len(files) != len(expected):
        raise VerificationError('Patch count differs from source inventory')
    for ordinal, path in enumerate(files, 1):
        match = re.fullmatch(r'(\d{4,})-.+\.patch', path.name)
        if not match or int(match[1]) != ordinal:
            raise VerificationError('Patch ordinals must be contiguous and unique')
    for ordinal, (path, sha) in enumerate(zip(files, expected), 1):
        first = path.read_bytes().split(b'\n', 1)[0]
        if first != f'From {sha} Mon Sep 17 00:00:00 2001'.encode():
            raise VerificationError(f'Source header mismatch at ordinal {ordinal}')
    return files


def tree(repo, ref):
    entries = {}
    for record in git(repo, 'ls-tree', '-rz', ref).split(b'\0'):
        if record:
            meta, path = record.split(b'\t', 1)
            mode, kind, oid = meta.split()
            entries[path] = (mode, kind, oid.decode())
    return entries


def load_policy(path, tip, base):
    if path is None:
        return [], {}
    try:
        policy = json.loads(Path(path).read_text())
        if set(policy) != {'source_tip', 'base', 'substitutions', 'fingerprints'}:
            raise ValueError()
        if policy['source_tip'] != tip or policy['base'] != base:
            raise ValueError()
        substitutions = policy['substitutions']
        fingerprints = policy['fingerprints']
        if not isinstance(substitutions, list) or not isinstance(fingerprints, dict):
            raise ValueError()
        for pair in substitutions:
            if (not isinstance(pair, list) or len(pair) != 2
                    or not all(isinstance(x, str) and x for x in pair)
                    or pair[0] == pair[1]):
                raise ValueError()
        for name, pair in fingerprints.items():
            if (not isinstance(name, str) or not isinstance(pair, list) or len(pair) != 2
                    or not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', x)
                               for x in pair)):
                raise ValueError()
        return [(a.encode(), b.encode()) for a, b in substitutions], fingerprints
    except (ValueError, TypeError, KeyError):
        raise VerificationError('Invalid policy or policy snapshot mismatch') from None


def compare(repo, tip, applied, substitutions, fingerprints):
    source_tree, applied_tree = tree(repo, tip), tree(repo, applied)
    if source_tree.keys() != applied_tree.keys():
        raise VerificationError('Tree path inventory differs (including added or removed tests)')
    used = set()
    for path, source in source_tree.items():
        actual = applied_tree[path]
        if source == actual:
            continue
        if source[:2] != actual[:2] or source[1] != b'blob':
            raise VerificationError('Tree mode or non-blob object differs')
        before = git(repo, 'cat-file', 'blob', source[2])
        after = git(repo, 'cat-file', 'blob', actual[2])
        for old, new in substitutions:
            before = before.replace(old, new)
        if before == after:
            continue
        name = path.decode('utf-8', errors='surrogateescape')
        if fingerprints.get(name) == [source[2], actual[2]]:
            used.add(name)
            continue
        raise VerificationError('Unexplained blob divergence: ' + source[2] + ' / ' + actual[2])
    if used != set(fingerprints):
        raise VerificationError('Unused historical fingerprints; policy must match exactly')


def verify(source_repo, source_tip, base, patch_dir, policy=None):
    repo = Path(source_repo).resolve()
    tip, base = resolve(repo, source_tip), resolve(repo, base)
    patches = inventory(repo, base, tip, patch_dir)
    substitutions, fingerprints = load_policy(policy, tip, base)
    merge_base = git(repo, 'merge-base', base, tip).decode().strip()
    # Only remove the uniquely allocated worktree; never prune anyone else's.
    directory = Path(tempfile.mkdtemp(prefix='mirror-verify-'))
    worktree = directory / 'replay'
    added = False
    try:
        git(repo, 'worktree', 'add', '--detach', str(worktree), merge_base)
        added = True
        # Stream mboxes via stdin to avoid command-line limits for long series.
        if patches:
            git(worktree, 'am', '-3', '--empty=keep', data=b''.join(p.read_bytes() for p in patches))
        applied = resolve(worktree, 'HEAD')
        compare(repo, tip, applied, substitutions, fingerprints)
    finally:
        # Retry a transient removal failure, without pruning sibling worktrees.
        # If both attempts fail, preserve the directory and registry together for
        # recovery rather than leaving a dangling prunable registry entry.
        if added or worktree.exists():
            try:
                git(repo, 'worktree', 'remove', '--force', str(worktree))
            except VerificationError:
                git(repo, 'worktree', 'remove', '--force', str(worktree))
        shutil.rmtree(directory)
    return {'patches': len(patches), 'source_tip': tip, 'base': base, 'merge_base': merge_base}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', required=True)
    parser.add_argument('--source-tip', required=True)
    parser.add_argument('--base', required=True, help='Upstream/exclusion commit, not necessarily merge-base')
    parser.add_argument('--patch-dir', required=True)
    parser.add_argument('--policy', help='Explicit public substitutions and snapshot-bound blob pairs (JSON)')
    args = parser.parse_args()
    try:
        result = verify(**vars(args))
    except (VerificationError, OSError):
        # Do not print file names, private text, or subprocess stderr.
        print('Mirror verification FAILED', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
