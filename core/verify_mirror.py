#!/usr/bin/env python3
"""Fail-closed, merge-aware verification of a public linear patch mirror.

Only object IDs, counts and patch ordinals are reported, never source contents or
Git diagnostics (which may contain private patch context). No denylist is read.
"""
import argparse
import hashlib
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


def run_git(repo, *args, data=None):
    # Do not inherit another worktree's index, object directory or repository.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    return subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false',
         '-c', 'user.name=Mirror verifier', '-c', 'user.email=mirror@example.com',
         '-C', str(repo), *args], input=data, capture_output=True, env=env)


def git(repo, *args, data=None):
    result = run_git(repo, *args, data=data)
    if result.returncode:
        raise VerificationError('Git operation failed: ' + args[0])
    return result.stdout


def is_ancestor(repo, ancestor, descendant):
    """True/False from merge-base's exit status 0/1; anything else fails closed."""
    code = run_git(repo, 'merge-base', '--is-ancestor', ancestor, descendant).returncode
    if code not in (0, 1):
        raise VerificationError('Git operation failed: merge-base')
    return code == 0


def parents(repo, commit):
    return git(repo, 'rev-list', '--parents', '-1', commit).decode().split()[1:]


def resolve(repo, ref):
    return git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()


HEADER = re.compile(rb'From ([0-9a-f]{40}) Mon Sep 17 00:00:00 2001')


def inventory(repo, base, tip, patch_dir):
    """Check the published series against the source's non-merge commit set.

    Published ordinals never move, and each pass appends the commits whose SHA
    header is not yet published. A commit that reached the fork through a merged
    branch can therefore follow a later-dated mainline commit, so the series
    order is not compared with rev-list order. What must hold: ordinals are
    contiguous from 0001, every patch has a SHA header, no header repeats, and
    the header set equals the source set. The replay still runs in published order.
    """
    expected = git(repo, 'rev-list', '--no-merges', base + '..' + tip).decode().split()
    files = sorted(Path(patch_dir).glob('*.patch'))
    for ordinal, path in enumerate(files, 1):
        match = re.fullmatch(r'(\d{4,})-.+\.patch', path.name)
        if not match or int(match[1]) != ordinal:
            raise VerificationError('Patch ordinals must be contiguous and unique')
    seen = {}
    for ordinal, path in enumerate(files, 1):
        data = path.read_bytes()
        header = HEADER.fullmatch(data.split(b'\n', 1)[0])
        if not header:
            raise VerificationError(f'Source header mismatch at ordinal {ordinal}: no SHA header')
        if sum(line.startswith(b'From ') for line in data.splitlines()) != 1:
            raise VerificationError(f'Exactly one mail entry required at ordinal {ordinal}')
        sha = header[1].decode()
        if sha in seen:
            raise VerificationError(
                f'Duplicate source header {sha} at ordinals {seen[sha]:04d} and {ordinal:04d}')
        seen[sha] = ordinal
    missing = set(expected) - set(seen)
    extra = set(seen) - set(expected)
    if missing or extra:
        detail = []
        if missing:
            detail.append('omitted source commits ' + ' '.join(sorted(missing)))
        if extra:
            detail.append('headers outside the source range at ordinals '
                          + ' '.join(f'{seen[s]:04d}' for s in sorted(extra, key=lambda s: seen[s])))
        raise VerificationError('Source header mismatch: ' + '; '.join(detail))
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
        return [], {}, []
    try:
        policy = json.loads(Path(path).read_text())
        if not {'source_tip', 'base', 'substitutions', 'fingerprints'} <= set(policy) <= {
                'source_tip', 'base', 'substitutions', 'fingerprints', 'renames', 'regex_substitutions'}:
            raise ValueError()
        if policy['source_tip'] != tip or policy['base'] != base:
            raise ValueError()
        substitutions = policy['substitutions']
        fingerprints = policy['fingerprints']
        renames = policy.get('renames', [])
        if (not isinstance(substitutions, list) or not isinstance(fingerprints, dict)
                or not isinstance(renames, list)):
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
        for rule in renames:
            if (not isinstance(rule, dict) or set(rule) != {'applied', 'source_blob', 'applied_blob'}
                    or not isinstance(rule['applied'], str) or not rule['applied']
                    or not all(isinstance(rule[k], str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', rule[k])
                               for k in ('source_blob', 'applied_blob'))):
                raise ValueError()
        if len({rule['applied'] for rule in renames}) != len(renames):
            raise ValueError()
        regex_rules = policy.get('regex_substitutions', [])
        if not isinstance(regex_rules, list):
            raise ValueError()
        compiled = []
        seen = set()
        for pair in regex_rules:
            if (not isinstance(pair, list) or len(pair) != 2
                    or not all(isinstance(x, str) and x for x in pair)
                    or pair[0] == pair[1] or pair[0] in seen):
                raise ValueError()
            pattern, replacement = pair
            rule = re.compile(pattern.encode())
            if rule.search(b'') is not None:
                raise ValueError()
            seen.add(pattern)
            compiled.append((rule, replacement.encode()))
        return [(a.encode(), b.encode()) for a, b in substitutions] + compiled, fingerprints, renames
    except (ValueError, TypeError, KeyError, AttributeError, re.error, UnicodeError):
        raise VerificationError('Invalid policy or policy snapshot mismatch') from None


def match_renames(source_tree, applied_tree, renames):
    """Pair source-only and applied-only paths through reviewed public renames.

    A rename names only the public (applied) path, so a private source file name
    never enters the public policy. Its source side is pinned by content instead:
    the one source-only path in the same directory with the same mode and the
    exact source blob. Every rule must be used and every path must be paired.
    """
    only_source = set(source_tree) - set(applied_tree)
    only_applied = set(applied_tree) - set(source_tree)
    for rule in renames:
        applied_path = rule['applied'].encode('utf-8', errors='surrogateescape')
        entry = applied_tree.get(applied_path)
        if applied_path not in only_applied or entry[1] != b'blob' or entry[2] != rule['applied_blob']:
            raise VerificationError('Unused or altered rename rule; policy must match exactly')
        parent = applied_path.rpartition(b'/')[0]
        candidates = [p for p in only_source
                      if p.rpartition(b'/')[0] == parent and source_tree[p][:2] == entry[:2]
                      and source_tree[p][2] == rule['source_blob']]
        if len(candidates) != 1:
            raise VerificationError('Rename rule matches no unique source path')
        only_source.discard(candidates[0])
        only_applied.discard(applied_path)
    if only_source or only_applied:
        raise VerificationError('Tree path inventory differs (including added or removed tests)')


def normalize(data, substitutions):
    """Ordered byte normalization; regex replacements are literal, not templates."""
    for old, new in substitutions:
        if isinstance(old, bytes):
            data = data.replace(old, new)
        else:
            def replace(match):
                if match.start() == match.end():
                    raise VerificationError('Zero-width regex normalization is forbidden')
                return new
            data = old.sub(replace, data)
    return data


def compare(repo, tip, applied, substitutions, fingerprints, renames=()):
    source_tree, applied_tree = tree(repo, tip), tree(repo, applied)
    if source_tree.keys() != applied_tree.keys():
        match_renames(source_tree, applied_tree, renames)
    elif renames:
        raise VerificationError('Unused or altered rename rule; policy must match exactly')
    used = set()
    for path, source in source_tree.items():
        if path not in applied_tree:
            continue
        actual = applied_tree[path]
        if source == actual:
            continue
        if source[:2] != actual[:2] or source[1] != b'blob':
            raise VerificationError('Tree mode or non-blob object differs')
        before = git(repo, 'cat-file', 'blob', source[2])
        after = git(repo, 'cat-file', 'blob', actual[2])
        before = normalize(before, substitutions)
        if before == after:
            continue
        name = path.decode('utf-8', errors='surrogateescape')
        if fingerprints.get(name) == [source[2], actual[2]]:
            used.add(name)
            continue
        raise VerificationError('Unexplained blob divergence: ' + source[2] + ' / ' + actual[2])
    if used != set(fingerprints):
        raise VerificationError('Unused historical fingerprints; policy must match exactly')


def load_ancestry_only(repo, tip, entries, headers, bridges):
    """Patches whose source commits survive only as merge ancestry, never as tree content.

    Each entry names a published single-parent patch commit and the frozen-tip merge
    that retains it: the merge's tree is exactly its first parent's tree, and the
    patch reaches the merge only through a non-first parent. Such a patch cannot
    replay in linear context, so it is verified in isolation instead (see verify).
    """
    if not isinstance(entries, list) or not entries:
        raise ValueError()
    ancestry = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'patch', 'source_merge'}:
            raise ValueError()
        sha, merge = entry['patch'], entry['source_merge']
        if not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{40}', x) for x in (sha, merge)):
            raise ValueError()
        if sha not in headers or sha in ancestry or sha in bridges:
            raise ValueError()
        if resolve(repo, sha) != sha or resolve(repo, merge) != merge:
            raise ValueError()
        merge_parents = parents(repo, merge)
        if len(parents(repo, sha)) != 1 or len(merge_parents) < 2:
            raise ValueError()
        if not is_ancestor(repo, merge, tip):
            raise ValueError()
        if git(repo, 'rev-parse', merge + '^{tree}') != git(repo, 'rev-parse', merge_parents[0] + '^{tree}'):
            raise ValueError()
        if not is_ancestor(repo, sha, merge) or is_ancestor(repo, sha, merge_parents[0]):
            raise ValueError()
        ancestry[sha] = merge
    return ancestry


def load_replay_plan(path, repo, tip, base, patches):
    """Explicit opt-in only: pinned start and hash-bound merge diffs, never skips."""
    if path is None:
        return None, {}, {}
    try:
        plan = json.loads(Path(path).read_text())
        if not {'source_tip', 'base', 'start', 'bridges'} <= set(plan) <= {
                'source_tip', 'base', 'start', 'bridges', 'ancestry_only'}:
            raise ValueError()
        if plan['source_tip'] != tip or plan['base'] != base:
            raise ValueError()
        if not re.fullmatch(r'[0-9a-f]{40}', plan['start']):
            raise ValueError()
        start = resolve(repo, plan['start'])
        git(repo, 'merge-base', '--is-ancestor', start, tip)
        git(repo, 'merge-base', '--is-ancestor', start, base)
        headers = {HEADER.fullmatch(p.read_bytes().split(b'\n', 1)[0])[1].decode()
                   for p in patches}
        bridges = {}
        # An empty bridge list is allowed only when ancestry-only entries do the work.
        if not isinstance(plan['bridges'], list) or not (plan['bridges'] or 'ancestry_only' in plan):
            raise ValueError()
        for bridge in plan['bridges']:
            if set(bridge) != {'before', 'diff', 'sha256', 'source_merge'}:
                raise ValueError()
            before = bridge['before']
            if before not in headers or before in bridges:
                raise ValueError()
            if not re.fullmatch(r'[0-9a-f]{40}', bridge['source_merge']):
                raise ValueError()
            merge = resolve(repo, bridge['source_merge'])
            if len(git(repo, 'rev-list', '--parents', '-1', merge).split()) < 3:
                raise ValueError()
            git(repo, 'merge-base', '--is-ancestor', merge, before)
            relative = Path(bridge['diff'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError()
            data = (Path(path).resolve().parent / relative).read_bytes()
            if hashlib.sha256(data).hexdigest() != bridge['sha256']:
                raise ValueError()
            bridges[before] = data
        ancestry = {}
        if 'ancestry_only' in plan:
            ancestry = load_ancestry_only(repo, tip, plan['ancestry_only'], headers, bridges)
        return start, bridges, ancestry
    except (ValueError, TypeError, KeyError, AttributeError):
        raise VerificationError('Invalid replay plan or snapshot mismatch') from None


def remove_worktree(repo, path):
    # Retry a transient removal failure, without pruning sibling worktrees.
    try:
        git(repo, 'worktree', 'remove', '--force', str(path))
    except VerificationError:
        git(repo, 'worktree', 'remove', '--force', str(path))


def verify(source_repo, source_tip, base, patch_dir, policy=None, replay_plan=None):
    repo = Path(source_repo).resolve()
    tip, base = resolve(repo, source_tip), resolve(repo, base)
    patches = inventory(repo, base, tip, patch_dir)
    substitutions, fingerprints, renames = load_policy(policy, tip, base)
    merge_base = git(repo, 'merge-base', base, tip).decode().strip()
    start, bridges, ancestry = load_replay_plan(replay_plan, repo, tip, base, patches)
    replay_start = start or merge_base
    # Only remove the uniquely allocated worktrees; never prune anyone else's.
    directory = Path(tempfile.mkdtemp(prefix='mirror-verify-'))
    worktree = directory / 'replay'
    owned = {}  # path -> whether `worktree add` returned successfully

    def add(path, commit):
        owned[path] = False
        git(repo, 'worktree', 'add', '--detach', str(path), commit)
        owned[path] = True

    try:
        add(worktree, replay_start)
        # Batch each uninterrupted segment; all source patches still replay once.
        pending = []
        for patch in patches:
            sha = HEADER.fullmatch(patch.read_bytes().split(b'\n', 1)[0])[1].decode()
            if sha in bridges or sha in ancestry:
                if pending:
                    git(worktree, 'am', '-3', '--empty=keep', data=b''.join(pending))
                    pending = []
            if sha in bridges:
                git(worktree, 'apply', '--index', '--3way', data=bridges[sha])
                git(worktree, 'commit', '--allow-empty', '-qm', 'Explicit upstream merge bridge')
            if sha in ancestry:
                # Prove the unchanged published bytes recreate the source commit's whole
                # tree in its own first-parent context: privacy substitutions only, no
                # fingerprints or renames. Then mark the merge that discards its tree.
                side = directory / ('ancestry-' + sha)
                add(side, parents(repo, sha)[0])
                git(side, 'am', '-3', '--empty=keep', data=patch.read_bytes())
                compare(repo, sha, resolve(side, 'HEAD'), substitutions, {}, ())
                remove_worktree(repo, side)
                del owned[side]
                git(worktree, 'commit', '--allow-empty', '-qm',
                    'Ancestry-only source merge ' + ancestry[sha] + ' retains ' + sha)
                continue
            pending.append(patch.read_bytes())
        if pending:
            git(worktree, 'am', '-3', '--empty=keep', data=b''.join(pending))
        applied = resolve(worktree, 'HEAD')
        compare(repo, tip, applied, substitutions, fingerprints, renames)
    finally:
        # If removal fails twice, preserve the directory and registry together for
        # recovery rather than leaving a dangling prunable registry entry.
        failure = None
        for path, added in reversed(list(owned.items())):
            if added or path.exists():
                try:
                    remove_worktree(repo, path)
                except VerificationError as error:
                    failure = failure or error
        if failure:
            raise failure
        shutil.rmtree(directory)
    result = {'patches': len(patches), 'source_tip': tip, 'base': base, 'merge_base': merge_base}
    if replay_plan is not None:
        result.update(replay_start=replay_start, bridges=len(bridges))
    if ancestry:
        result.update(ancestry_only=len(ancestry))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', required=True)
    parser.add_argument('--source-tip', required=True)
    parser.add_argument('--base', required=True, help='Upstream/exclusion commit, not necessarily merge-base')
    parser.add_argument('--patch-dir', required=True)
    parser.add_argument('--policy', help='Explicit public substitutions and snapshot-bound blob pairs (JSON)')
    parser.add_argument('--replay-plan', help='Snapshot-bound historical start and hash-pinned merge bridges (JSON)')
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
