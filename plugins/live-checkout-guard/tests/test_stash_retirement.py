"""Archive-verified stash retirement: real Git fixtures in temp repos only.

Never touches a live checkout or real stash. Concurrency and crashes are injected
by monkeypatching module seams (no production environment hooks).
"""
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
MODULE = HERE / 'stash_retirement.py'
T0 = 1700000000

ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
ENV.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
           GIT_AUTHOR_NAME='Example', GIT_AUTHOR_EMAIL='you@example.com',
           GIT_COMMITTER_NAME='Example', GIT_COMMITTER_EMAIL='you@example.com', LC_ALL='C')


class SimulatedCrash(BaseException):
    """Process death stand-in: not an Exception, so no in-process cleanup runs."""


def load_module():
    spec = importlib.util.spec_from_file_location('stash_retirement_under_test', MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_git(cwd, *args, env=None, check=True, input=None):
    p = subprocess.run(['git', '-C', str(cwd), *args], env=env or ENV, capture_output=True, input=input)
    if check and p.returncode:
        raise AssertionError(f'git {args} failed: {p.stderr.decode(errors="replace")}')
    return p


class Base(unittest.TestCase):
    TIMES = (T0, T0 + 10, T0 + 20, T0 + 30)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='stash-retire-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.archive = self.base / 'archive'
        self.archive.mkdir()
        self.home = self.base / 'home'
        self.home.mkdir()
        envp = patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        envp.start()
        self.addCleanup(envp.stop)
        self.mod = load_module()
        self.g('init', '-q', '-b', 'main')
        self.g('remote', 'add', 'origin', 'https://example.com/repo.git')
        (self.root / 'tracked.txt').write_bytes(b'base\n')
        (self.root / 'bin.dat').write_bytes(bytes(range(256)))
        self.g('add', '.')
        self.g('commit', '-qm', 'base')
        self.oids = []  # oldest first while building; reversed to newest first below
        self.staged_marker = b'STAGED-ONLY-CONTENT\n'
        builders = [self._entry_untracked, self._entry_staged_only, self._entry_binary, self._entry_plain]
        for i, t in enumerate(self.TIMES):
            builders[i % len(builders)](i)
            env = dict(ENV, GIT_COMMITTER_DATE=f'{t} +0000', GIT_AUTHOR_DATE=f'{t} +0000')
            flags = ['-u'] if i % len(builders) in (0, 2) else []
            self.g('stash', 'push', '-q', *flags, '-m', f'entry-{i}', env=env)
            self.oids.append(self.tip())
        self.oids.reverse()  # newest first, matching stash@{n}
        self.write_legacy_archive()
        # Dirty checkout (staged, unstaged, untracked) must remain byte-identical.
        (self.root / 'tracked.txt').write_bytes(b'live staged\n')
        self.g('add', 'tracked.txt')
        (self.root / 'tracked.txt').write_bytes(b'live unstaged\n')
        (self.root / 'live-untracked').write_bytes(b'keep me\x00')
        self.before_checkout = self.checkout()
        self.before_stack = self.stack()

    # Fixture entries ------------------------------------------------------
    def _entry_untracked(self, i):
        (self.root / 'tracked.txt').write_bytes(b'changed %d\n' % i)
        (self.root / f'untracked-{i}.bin').write_bytes(b'untracked\x00\xff%d' % i)

    def _entry_staged_only(self, i):
        (self.root / 'tracked.txt').write_bytes(self.staged_marker)
        self.g('add', 'tracked.txt')
        (self.root / 'tracked.txt').write_bytes(b'worktree %d\n' % i)

    def _entry_binary(self, i):
        (self.root / 'bin.dat').write_bytes(bytes(reversed(range(256))) + bytes([i]))
        (self.root / f'u{i}.txt').write_bytes(b'untracked text %d\n' % i)

    def _entry_plain(self, i):
        (self.root / 'tracked.txt').write_bytes(b'plain %d\n' % i)

    # Helpers ----------------------------------------------------------------
    def g(self, *args, env=None, check=True, cwd=None):
        return run_git(cwd or self.root, *args, env=env, check=check).stdout

    def tip(self):
        return self.g('rev-parse', 'refs/stash').decode().strip()

    def write_legacy_archive(self, absolute=True):
        rows = []
        for n, oid in enumerate(self.oids):
            # Same command as the real archive evidence, run by an ordinary Git.
            data = self.g('stash', 'show', '-p', '--binary', '--include-untracked', oid)
            name = oid + '.patch'
            (self.archive / name).write_bytes(data)
            rows.append({'stash': oid, 'patch': str(self.archive / name) if absolute and n % 2 == 0 else name,
                         'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                         'paths': [], 'readback_verified': True})
        (self.archive / 'manifest.json').write_text(json.dumps(rows, indent=2))

    def stack(self):
        p = run_git(self.root, 'log', '-g', '--no-abbrev', '--format=%H %P|%gs', 'refs/stash', check=False)
        return p.stdout.decode().splitlines() if p.returncode == 0 else []

    def stack_oids(self):
        return [line.split()[0] for line in self.stack()]

    def checkout(self):
        files = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in sorted(self.root.rglob('*')) if p.is_file() and '.git' not in p.parts}
        return (self.g('rev-parse', 'HEAD'), self.g('symbolic-ref', 'HEAD'),
                (self.root / '.git' / 'index').read_bytes(), files)

    def hermes_refs(self):
        return self.g('for-each-ref', '--format=%(refname) %(objectname)', 'refs/hermes-retire/').decode().splitlines()

    def audit(self):
        p = self.archive / 'stash-retirement-audit.jsonl'
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def journal(self):
        p = self.archive / 'stash-retirement-journal.jsonl'
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def prepare(self):
        return self.mod.prepare(self.root, self.archive)

    def retire(self, oids):
        return self.mod.retire(self.root, self.archive, oids)

    def assert_unchanged(self):
        self.assertEqual(self.stack(), self.before_stack)
        self.assertEqual(self.checkout(), self.before_checkout)
        self.assertEqual(self.hermes_refs(), [])

    def expected_after(self, targets):
        return [line for line in self.before_stack if line.split()[0] not in targets]

    def assert_retired(self, targets, extra_newer=()):
        self.assertEqual(self.stack(), list(extra_newer) + self.expected_after(targets))
        self.assertEqual(self.checkout(), self.before_checkout)
        self.assertEqual(self.hermes_refs(), [])
        retired = {r['oid']: r for r in self.audit() if r['event'] == 'retired'}
        manifest = json.loads((self.archive / 'retirement-manifest.json').read_text())
        arch = {a['oid']: a for a in manifest['archives']}
        for oid in targets:
            self.assertEqual(retired[oid]['include_untracked_sha256'], arch[oid]['include_untracked']['sha256'])
            self.assertEqual(retired[oid]['index_sha256'], arch[oid]['index']['sha256'])
        self.assertEqual(self.journal()[-1]['event'], 'complete')

    def lock_free(self):
        fd = os.open(self.home / '.hermes-sync.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    def wait_lock_free(self, timeout=30):
        deadline = time.time() + timeout
        while not self.lock_free():
            self.assertLess(time.time(), deadline, 'sync lock never released')
            time.sleep(0.05)

    def events(self):
        return [r['event'] for r in self.journal()]

    def git_locks(self):
        git = self.root / '.git'
        return sorted(str(p.relative_to(git)) for p in list((git / 'refs').rglob('*.lock'))
                      + list(git.glob('packed-refs.lock')))

    def fake_stash(self, like, message='concurrent'):
        """A distinct stash-shaped commit built from existing objects (no checkout writes)."""
        tree = self.g('rev-parse', like + '^{tree}').decode().strip()
        parents = self.g('rev-parse', like + '^@').decode().split()
        args = sum((['-p', p] for p in parents), [])
        return self.g('commit-tree', tree, *args, '-m', message).decode().strip()

    def wrap(self, name, before=None, after=None, times=None):
        original = getattr(self.mod, name)
        calls = {'n': 0}

        def wrapper(*a, **k):
            calls['n'] += 1
            if before and (times is None or calls['n'] in times):
                before(*a, **k)
            result = original(*a, **k)
            if after and (times is None or calls['n'] in times):
                after(*a, **k)
            return result
        return patch.object(self.mod, name, wrapper)


class PrepareTests(Base):
    def test_prepare_writes_versioned_manifest_bound_to_repo_and_stack(self):
        self.prepare()
        doc = json.loads((self.archive / 'retirement-manifest.json').read_text())
        self.assertEqual(doc['version'], 1)
        ident = doc['identity']
        self.assertEqual(ident['root'], str(self.root.resolve()))
        self.assertEqual(ident['common_dir'], str((self.root / '.git').resolve()))
        self.assertEqual(ident['origin'], 'https://example.com/repo.git')
        raw = base64.b64decode(doc['snapshot']['raw_b64'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), doc['snapshot']['sha256'])
        self.assertEqual([e['oid'] for e in doc['snapshot']['entries']], self.oids)
        self.assertEqual([e['subject'] for e in doc['snapshot']['entries']],
                         [f'On main: entry-{i}' for i in reversed(range(len(self.oids)))])
        self.assertEqual([e['time'] for e in doc['snapshot']['entries']], list(reversed(self.TIMES)))
        for e in doc['snapshot']['entries']:
            self.assertEqual(e['parents'], self.g('rev-parse', e['oid'] + '^@').decode().split())
        self.assertEqual([a['oid'] for a in doc['archives']], self.oids)
        self.assert_unchanged()

    def test_R1_staged_only_bytes_archived_and_round_trip(self):
        staged_oid = self.oids[len(self.oids) - 2]  # entry-1 built by _entry_staged_only
        legacy = (self.archive / (staged_oid + '.patch')).read_bytes()
        self.assertNotIn(self.staged_marker, legacy)  # the R1 gap in the original export
        self.prepare()
        index_patch = (self.archive / (staged_oid + '.index.patch')).read_bytes()
        self.assertIn(self.staged_marker, index_patch)
        expected = self.g('diff', '--binary', '--full-index', staged_oid + '^1', staged_oid + '^2')
        self.assertEqual(index_patch, expected)
        doc = json.loads((self.archive / 'retirement-manifest.json').read_text())
        row = next(a for a in doc['archives'] if a['oid'] == staged_oid)
        self.assertEqual(row['index']['sha256'], hashlib.sha256(index_patch).hexdigest())
        self.assertEqual(row['index']['bytes'], len(index_patch))
        # Independent round trip: base tree + archived index patch == stash index tree.
        tmp_index = self.base / 'rt-index'
        env = dict(ENV, GIT_INDEX_FILE=str(tmp_index))
        self.g('read-tree', staged_oid + '^1', env=env)
        run_git(self.root, 'apply', '--cached', '--binary', '-', env=env, input=index_patch)
        self.assertEqual(self.g('write-tree', env=env), self.g('rev-parse', staged_oid + '^2^{tree}'))
        self.retire([staged_oid])
        self.assert_retired([staged_oid])

    def test_prepare_is_idempotent_and_never_overwrites_index_archive(self):
        self.prepare()
        first = (self.archive / 'retirement-manifest.json').read_bytes()
        self.prepare()
        self.assertEqual((self.archive / 'retirement-manifest.json').read_bytes(), first)
        p = self.archive / (self.oids[1] + '.index.patch')
        p.write_bytes(p.read_bytes() + b'x')
        with self.assertRaises(self.mod.RetirementError):
            self.prepare()
        self.assert_unchanged()

    def test_missing_truncated_tampered_symlinked_archives_refused(self):
        target = self.archive / (self.oids[0] + '.patch')
        original = target.read_bytes()
        cases = {
            'truncated': original[:-1],
            'empty': b'',
            'tampered-same-length': original[:-2] + bytes([original[-2] ^ 1]) + original[-1:],
        }
        for label, data in cases.items():
            with self.subTest(label):
                target.write_bytes(data)
                with self.assertRaises(self.mod.RetirementError):
                    self.prepare()
                self.assertFalse((self.archive / 'retirement-manifest.json').exists())
                self.assert_unchanged()
        # Tampered bytes with a matching recorded SHA still differ from fresh Git export.
        forged = original[:-2] + b'X' + original[-1:]
        self.assertNotEqual(forged, original)
        target.write_bytes(forged)
        rows = json.loads((self.archive / 'manifest.json').read_text())
        rows[0].update(bytes=len(forged), sha256=hashlib.sha256(forged).hexdigest())
        (self.archive / 'manifest.json').write_text(json.dumps(rows))
        with self.assertRaises(self.mod.RetirementError):
            self.prepare()
        target.unlink()
        with self.assertRaises(self.mod.RetirementError):
            self.prepare()
        outside = self.base / 'outside.patch'
        outside.write_bytes(original)
        target.symlink_to(outside)
        rows[0].update(bytes=len(original), sha256=hashlib.sha256(original).hexdigest())
        (self.archive / 'manifest.json').write_text(json.dumps(rows))
        with self.assertRaises(self.mod.RetirementError):
            self.prepare()
        rows[0]['patch'] = str(outside)  # absolute path outside the archive
        target.unlink()
        (self.archive / 'manifest.json').write_text(json.dumps(rows))
        with self.assertRaises(self.mod.RetirementError):
            self.prepare()
        self.assert_unchanged()

    def test_unknown_and_duplicate_archived_oids_refused(self):
        rows = json.loads((self.archive / 'manifest.json').read_text())
        head = self.g('rev-parse', 'HEAD').decode().strip()
        for label, mutated in {
            'unknown': rows + [dict(rows[0], stash=head)],
            'duplicate': rows + [dict(rows[0])],
            'short-oid': [dict(rows[0], stash=rows[0]['stash'][:12])] + rows[1:],
            'not-a-list': {'stash': rows[0]['stash']},
        }.items():
            with self.subTest(label):
                (self.archive / 'manifest.json').write_text(json.dumps(mutated))
                with self.assertRaises(self.mod.RetirementError):
                    self.prepare()
                self.assert_unchanged()

    def test_duplicate_oid_in_stack_refused(self):
        self.g('stash', 'store', '-m', 'dup', self.oids[2])
        with self.assertRaises(self.mod.RetirementError):
            self.prepare()

    def test_replace_objects_ignored(self):
        other = self.oids[1]
        self.g('replace', self.oids[0], other)
        # An ordinary replace-honouring export would now show the replacement's content.
        self.assertNotEqual(self.g('stash', 'show', '-p', '--binary', '--include-untracked', self.oids[0]),
                            (self.archive / (self.oids[0] + '.patch')).read_bytes())
        self.prepare()
        idx = (self.archive / (self.oids[0] + '.index.patch')).read_bytes()
        env = dict(ENV, GIT_NO_REPLACE_OBJECTS='1')
        self.assertEqual(idx, self.g('diff', '--binary', '--full-index', self.oids[0] + '^1',
                                     self.oids[0] + '^2', env=env))
        self.retire([self.oids[0]])
        self.assertEqual(self.stack_oids(), self.oids[1:])

    def test_hostile_local_diff_config_does_not_change_canonical_export(self):
        for k, v in [('diff.noprefix', 'true'), ('diff.mnemonicPrefix', 'true'), ('core.abbrev', '12'),
                     ('diff.algorithm', 'patience'), ('diff.context', '7'), ('apply.whitespace', 'fix'),
                     ('core.quotePath', 'false'), ('diff.renames', 'false')]:
            self.g('config', k, v)
        self.prepare()
        self.retire([self.oids[1]])
        self.assert_retired([self.oids[1]])


class RetireTests(Base):
    def setUp(self):
        super().setUp()
        self.prepare()

    def test_D_selective_preservation(self):
        targets = [self.oids[1], self.oids[3]]
        result = self.retire(targets)
        self.assertEqual(sorted(result['retired']), sorted(targets))
        self.assert_retired(targets)
        for line in self.stack():
            oid, *parents = line.split('|')[0].split()
            self.assertEqual(parents, self.g('rev-parse', oid + '^@').decode().split())

    def test_retire_newest_only(self):
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])

    def test_retire_all_including_last_entry(self):
        self.retire(list(self.oids))
        self.assertEqual(self.stack(), [])
        self.assertEqual(self.g('for-each-ref', 'refs/stash'), b'')
        self.assert_retired(list(self.oids))

    def test_selection_validation_refuses_without_mutation(self):
        head = self.g('rev-parse', 'HEAD').decode().strip()
        for label, sel in {'empty': [], 'string': self.oids[0], 'short': [self.oids[0][:10]],
                           'upper': [self.oids[0].upper()], 'duplicate': [self.oids[0], self.oids[0]],
                           'unknown-commit': [head], 'unknown-zero': ['0' * 40]}.items():
            with self.subTest(label), self.assertRaises(self.mod.RetirementError):
                self.retire(sel)
            self.assert_unchanged()

    def test_every_stack_entry_needs_an_archive_row_target_or_not(self):
        # Contract: an archive row for EVERY current entry, not only the selected ones.
        manifest = (self.archive / 'retirement-manifest.json').read_bytes()
        rows = json.loads((self.archive / 'manifest.json').read_text())
        for missing in (0, 2):
            with self.subTest(missing=missing):
                (self.archive / 'manifest.json').write_text(
                    json.dumps([r for n, r in enumerate(rows) if n != missing]))
                with self.assertRaisesRegex(self.mod.RetirementError, 'every stash entry needs an archive row'):
                    self.prepare()
                self.assertEqual((self.archive / 'retirement-manifest.json').read_bytes(), manifest)
                self.assert_unchanged()
        (self.archive / 'manifest.json').write_text(json.dumps(rows))
        self.retire([self.oids[1]])
        self.assert_retired([self.oids[1]])

    def test_non_target_archive_tampering_after_prepare_refused(self):
        # Retire re-verifies every row byte-for-byte, not only the selected ones.
        for suffix in ('.patch', '.index.patch'):
            p = self.archive / (self.oids[3] + suffix)
            original = p.read_bytes()
            with self.subTest(suffix=suffix):
                p.write_bytes(original + b'x' if not original else original[:-1] + bytes([original[-1] ^ 1]))
                with self.assertRaises(self.mod.RetirementError):
                    self.retire([self.oids[0]])
                self.assert_unchanged()
                p.unlink()
                with self.assertRaises(self.mod.RetirementError):
                    self.retire([self.oids[0]])
                self.assert_unchanged()
            p.write_bytes(original)
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])

    def test_archive_tampering_after_prepare_refused(self):
        oid = self.oids[2]
        for suffix in ('.patch', '.index.patch'):
            p = self.archive / (oid + suffix)
            original = p.read_bytes()
            for bad in (original[:-1], original + b'\n', b''):
                with self.subTest(suffix=suffix, n=len(bad)):
                    p.write_bytes(bad)
                    with self.assertRaises(self.mod.RetirementError):
                        self.retire([oid])
                    self.assert_unchanged()
            p.unlink()
            with self.assertRaises(self.mod.RetirementError):
                self.retire([oid])
            p.write_bytes(original)
        self.retire([oid])
        self.assert_retired([oid])

    def test_manifest_tampering_refused(self):
        p = self.archive / 'retirement-manifest.json'
        original = p.read_text()
        def mutate(f):
            doc = json.loads(original)
            f(doc)
            p.write_text(json.dumps(doc))
        cases = {
            'version': lambda d: d.update(version=2),
            'root': lambda d: d['identity'].update(root='/elsewhere'),
            'common': lambda d: d['identity'].update(common_dir='/elsewhere/.git'),
            'origin': lambda d: d['identity'].update(origin='https://example.com/other.git'),
            'entries-vs-raw': lambda d: d['snapshot']['entries'][1].update(subject='forged'),
            'raw-sha': lambda d: d['snapshot'].update(sha256='0' * 64),
            'archive-sha': lambda d: d['archives'][0]['index'].update(sha256='0' * 64),
            'archive-dup': lambda d: d['archives'].append(d['archives'][0]),
            'archive-missing-non-target': lambda d: d['archives'].pop(),
        }
        for label, f in cases.items():
            with self.subTest(label):
                mutate(f)
                with self.assertRaises(self.mod.RetirementError):
                    self.retire([self.oids[0]])
                self.assert_unchanged()
        p.write_text(original)
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])

    def test_repository_mismatch_refused(self):
        self.g('remote', 'set-url', 'origin', 'https://example.com/other.git')
        with self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[0]])
        self.g('remote', 'set-url', 'origin', 'https://example.com/repo.git')
        clone = self.base / 'clone'
        run_git(self.base, 'clone', '-q', str(self.root), str(clone))
        run_git(clone, 'remote', 'set-url', 'origin', 'https://example.com/repo.git')
        for oid in reversed(self.oids):
            run_git(clone, 'fetch', '-q', str(self.root), oid)
            run_git(clone, 'stash', 'store', '-m', 'x', oid)
        with self.assertRaises(self.mod.RetirementError):
            self.mod.retire(clone, self.archive, [self.oids[0]])
        self.assertEqual(len(run_git(clone, 'stash', 'list').stdout.splitlines()), len(self.oids))
        self.assert_unchanged()

    def test_reordered_and_replaced_stacks_refused(self):
        # Replaced: same OIDs and order, rewritten reflog metadata (new timestamp).
        self.g('stash', 'drop', '-q', 'stash@{0}')
        self.g('stash', 'store', '-m', 'On main: entry-3', self.oids[0],
               env=dict(ENV, GIT_COMMITTER_DATE=f'{T0 + 99} +0000'))
        self.assertEqual(self.stack_oids(), self.oids)
        with self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[1]])
        # Reordered: same OIDs, different order.
        self.g('stash', 'drop', '-q', 'stash@{1}')
        self.g('stash', 'store', '-m', 'On main: entry-2', self.oids[1])
        self.assertEqual(sorted(self.stack_oids()), sorted(self.oids))
        snapshot = self.stack()
        with self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[1]])
        self.assertEqual(self.stack(), snapshot)
        self.assertEqual(self.hermes_refs(), [])

    def test_replay_of_completed_manifest_refused(self):
        self.retire([self.oids[0]])
        after = self.stack()
        with self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[1]])
        self.assertEqual(self.stack(), after)

    def test_sync_lock_busy_fails_closed(self):
        with open(self.home / '.hermes-sync.lock', 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for call in (lambda: self.retire([self.oids[0]]), self.prepare,
                         lambda: self.mod.recover(self.root, self.archive)):
                with self.assertRaises(self.mod.RetirementError):
                    call()
        self.assert_unchanged()

    def test_lock_held_throughout_retire(self):
        def probe(*a, **k):
            fd = os.open(self.home / '.hermes-sync.lock', os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
        with self.wrap('_check_prepared', before=probe), self.wrap('_store', before=probe):
            self.retire([self.oids[2]])
        self.assert_retired([self.oids[2]])

    def test_stale_native_stash_lock_fails_closed(self):
        lock = self.root / '.git' / 'refs' / 'stash.lock'
        lock.write_bytes(b'')
        with self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[0]])
        self.assertTrue(lock.exists())
        lock.unlink()
        self.assert_unchanged()
        self.assertFalse(any(r['event'] == 'intent' for r in self.journal()))

    def test_stray_retirement_refs_fail_closed(self):
        self.g('update-ref', 'refs/hermes-retire/keep/' + 'a' * 32 + '/0', self.oids[0])
        for call in (lambda: self.retire([self.oids[1]]), self.prepare,
                     lambda: self.mod.recover(self.root, self.archive)):
            with self.assertRaises(self.mod.RetirementError):
                call()
        self.assertEqual(self.stack(), self.before_stack)


class TiedTimestampTests(Base):
    TIMES = (T0, T0, T0, T0)

    def test_tied_timestamps_retire_by_oid(self):
        self.prepare()
        doc = json.loads((self.archive / 'retirement-manifest.json').read_text())
        self.assertEqual({e['time'] for e in doc['snapshot']['entries']}, {T0})
        self.retire([self.oids[0], self.oids[2]])
        self.assert_retired([self.oids[0], self.oids[2]])


class ConcurrencyTests(Base):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.wt = self.base / 'wt'
        self.g('worktree', 'add', '-q', '--detach', str(self.wt), 'HEAD')
        self.before_checkout = self.checkout()

    def wt_push(self):
        (self.wt / 'tracked.txt').write_bytes(b'concurrent worktree edit\n')
        return run_git(self.wt, 'stash', 'push', '-m', 'concurrent', check=False)

    def test_A_native_writers_fail_unchanged_while_prepared(self):
        target = self.oids[1]
        new = self.fake_stash(self.oids[0])
        backdated = dict(ENV, GIT_COMMITTER_DATE=f'{self.TIMES[2]} +0000')
        results = {}

        def race(*a, **k):
            snap = self.stack()
            results['push'] = self.wt_push()
            self.assertEqual((self.wt / 'tracked.txt').read_bytes(), b'concurrent worktree edit\n')
            results['store'] = run_git(self.root, 'stash', 'store', '-m', 'c', new, check=False)
            results['store-backdated'] = run_git(self.root, 'stash', 'store', '-m', 'c', new,
                                                 env=backdated, check=False)
            results['drop'] = run_git(self.root, 'stash', 'drop', 'stash@{2}', check=False)
            results['drop-tip'] = run_git(self.root, 'stash', 'drop', check=False)
            results['expire'] = run_git(self.root, 'reflog', 'expire', '--expire=all', 'refs/stash', check=False)
            results['delete'] = run_git(self.root, 'reflog', 'delete', '--updateref', '--rewrite',
                                        'stash@{1}', check=False)
            results['clear'] = run_git(self.root, 'stash', 'clear', check=False)
            results['update-ref'] = run_git(self.root, 'update-ref', 'refs/stash', new, check=False)
            self.assertEqual(self.stack(), snap)
        with self.wrap('_check_prepared', before=race):
            self.retire([target])
        for name, p in results.items():
            with self.subTest(name):
                self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn(new, self.stack_oids())
        self.assert_retired([target])

    def test_B_push_before_prepare_refused_by_cas(self):
        def race(*a, **k):
            self.assertEqual(self.wt_push().returncode, 0)
        with self.wrap('_prepare_transaction', before=race), self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[0]])
        oids = self.stack_oids()
        self.assertEqual(oids[1:], self.oids)
        self.assertEqual(self.stack()[1:], self.before_stack)
        self.assertEqual(self.checkout(), self.before_checkout)
        self.assertEqual(self.hermes_refs(), [])
        self.assertFalse(any(r['event'] == 'intent' for r in self.journal()))

    def test_C_older_drop_before_prepare_aborts_after_locked_compare(self):
        calls = []
        def race(*a, **k):
            self.g('stash', 'drop', '-q', 'stash@{2}')
        original = self.mod._check_prepared
        def spy(*a, **k):
            calls.append(1)
            return original(*a, **k)
        with self.wrap('_prepare_transaction', before=race), patch.object(self.mod, '_check_prepared', spy), \
                self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[0]])
        self.assertEqual(calls, [1])  # the tip CAS passed; only the locked full compare caught it
        self.assertEqual(self.stack_oids(), [o for o in self.oids if o != self.oids[2]])
        self.assertEqual(self.checkout(), self.before_checkout)
        self.assertEqual(self.hermes_refs(), [])
        self.assertFalse(any(r['event'] == 'intent' for r in self.journal()))

    def test_R2_backdated_writer_after_commit_is_not_lost(self):
        target = self.oids[0]
        new = self.fake_stash(self.oids[1])
        backdated = dict(ENV, GIT_COMMITTER_DATE=f'{self.TIMES[-1]} +0000')  # ties the target timestamp

        def race(*a, **k):
            run_git(self.root, 'stash', 'store', '-m', 'backdated', new, env=backdated)
        with self.wrap('_store', before=race, times={1}):
            self.retire([target])
        oids = self.stack_oids()
        self.assertIn(new, oids)
        self.assertNotIn(target, oids)
        self.assertEqual([o for o in oids if o != new], self.oids[1:])
        self.assertEqual(self.hermes_refs(), [])

    def test_R2_backdated_writer_after_commit_with_retire_all(self):
        new = self.fake_stash(self.oids[1])
        backdated = dict(ENV, GIT_COMMITTER_DATE=f'{self.TIMES[-1]} +0000')
        def race(*a, **k):
            run_git(self.root, 'stash', 'store', '-m', 'backdated', new, env=backdated)
        with self.wrap('_finish', before=race, times={1}):
            self.retire(list(self.oids))
        self.assertEqual(self.stack_oids(), [new])
        self.assertEqual(self.hermes_refs(), [])

    def test_E_child_eof_after_prepare_aborts_without_mutation(self):
        def eof(root, ident, raw, txn):
            txn.proc.stdin.close()
            txn.proc.wait(timeout=30)
            raise SimulatedCrash()
        with patch.object(self.mod, '_check_prepared', eof), self.assertRaises(SimulatedCrash):
            self.retire([self.oids[0]])
        self.assert_unchanged()
        self.assertFalse((self.root / '.git' / 'refs' / 'stash.lock').exists())
        self.assertEqual(self.mod.recover(self.root, self.archive), {'completed': [], 'aborted': []})
        self.assert_unchanged()


class CrashRecoveryTests(Base):
    def setUp(self):
        super().setUp()
        self.prepare()

    def crash_on_store(self, call, after_store):
        original = self.mod._store
        n = {'i': 0}
        def crashing(*a, **k):
            n['i'] += 1
            if n['i'] == call:
                if after_store:
                    original(*a, **k)
                raise SimulatedCrash()
            return original(*a, **k)
        return patch.object(self.mod, '_store', crashing)

    def test_crash_after_commit_before_restore_then_recover(self):
        targets = [self.oids[1]]
        with self.crash_on_store(1, after_store=False), self.assertRaises(SimulatedCrash):
            self.retire(targets)
        self.assertEqual(self.stack(), [])  # whole stack removed atomically...
        refs = self.hermes_refs()
        kept = sorted(line.split()[1] for line in refs if '/keep/' in line)
        self.assertEqual(kept, sorted(o for o in self.oids if o not in targets))  # ...non-targets pinned
        self.assertEqual(self.checkout(), self.before_checkout)
        result = self.mod.recover(self.root, self.archive)
        self.assertEqual(len(result['completed']), 1)
        self.assert_retired(targets)

    def test_crash_after_a_store_recovers_without_double_store(self):
        targets = [self.oids[2]]
        with self.crash_on_store(2, after_store=True), self.assertRaises(SimulatedCrash):
            self.retire(targets)
        self.assertEqual(len(self.stack()), 2)
        self.assertNotEqual(self.hermes_refs(), [])
        self.mod.recover(self.root, self.archive)
        oids = self.stack_oids()
        self.assertEqual(len(oids), len(set(oids)))
        self.assert_retired(targets)

    def test_crash_after_a_store_then_concurrent_push_no_double_store(self):
        # Git skips a reflog entry when storing the current tip, so bury the stored
        # entry under a concurrent push to make a double store observable.
        targets = [self.oids[2]]
        with self.crash_on_store(1, after_store=True), self.assertRaises(SimulatedCrash):
            self.retire(targets)
        stored_first = self.stack_oids()
        self.assertEqual(stored_first, [self.oids[3]])
        new = self.fake_stash(self.oids[0])
        self.g('stash', 'store', '-m', 'concurrent', new)
        self.mod.recover(self.root, self.archive)
        oids = self.stack_oids()
        self.assertEqual(oids.count(self.oids[3]), 1)
        self.assertEqual(oids, [self.oids[0], self.oids[1], new, self.oids[3]])
        self.assertEqual(self.hermes_refs(), [])
        self.assertEqual(self.journal()[-1]['event'], 'complete')

    def test_next_invocation_finishes_committed_transaction(self):
        targets = [self.oids[0]]
        with self.crash_on_store(1, after_store=True), self.assertRaises(SimulatedCrash):
            self.retire(targets)
        with self.assertRaises(self.mod.RetirementError):  # recovers, then the stale manifest is refused
            self.retire([self.oids[1]])
        self.assert_retired(targets)
        self.prepare()  # rebinding to the new stack works after recovery
        self.retire([self.oids[1]])
        self.assertEqual(self.stack(), self.expected_after(targets + [self.oids[1]]))

    def test_crash_after_verified_before_ref_cleanup(self):
        with self.wrap('_delete_txn_refs', before=lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash())), \
                self.assertRaises(SimulatedCrash):
            self.retire([self.oids[3]])
        self.assertNotEqual(self.hermes_refs(), [])
        self.mod.recover(self.root, self.archive)
        self.assert_retired([self.oids[3]])
        self.assertEqual(sum(1 for r in self.audit() if r['event'] == 'retired'), 1)

    def test_crafted_journal_cannot_store_unvalidated_content(self):
        with self.crash_on_store(1, after_store=False), self.assertRaises(SimulatedCrash):
            self.retire([self.oids[1]])
        jp = self.archive / 'stash-retirement-journal.jsonl'
        original = jp.read_text()
        refs_before = self.hermes_refs()
        foreign = self.fake_stash(self.oids[0], 'foreign')
        def forge(f):
            lines = [json.loads(x) for x in original.splitlines()]
            intent = next(r for r in lines if r['event'] == 'intent')
            f(intent)
            jp.write_text(''.join(json.dumps(r) + '\n' for r in lines))
        cases = {
            'foreign-keep-oid': lambda i: i['keep'][0].update(oid=foreign),
            'target-as-keep': lambda i: i['keep'][0].update(oid=self.oids[1]),
            'drop-keep': lambda i: i['keep'].pop(),
            'manifest-sha': lambda i: i.update(manifest_sha256='0' * 64),
            'identity': lambda i: i['identity'].update(origin='https://evil.example/x.git'),
            'target-sha': lambda i: i['targets'][0].update(index_sha256='0' * 64),
        }
        for label, f in cases.items():
            with self.subTest(label):
                forge(f)
                with self.assertRaises(self.mod.RetirementError):
                    self.mod.recover(self.root, self.archive)
                self.assertEqual(self.stack(), [])
                self.assertEqual(self.hermes_refs(), refs_before)
        jp.write_text(original)
        # A forged keep ref is equally refused.
        keep_ref = next(line.split()[0] for line in refs_before if '/keep/' in line)
        self.g('update-ref', keep_ref, foreign)
        with self.assertRaises(self.mod.RetirementError):
            self.mod.recover(self.root, self.archive)
        self.assertEqual(self.stack(), [])
        self.g('update-ref', keep_ref, refs_before[[l.split()[0] for l in refs_before].index(keep_ref)].split()[1])
        self.mod.recover(self.root, self.archive)
        self.assert_retired([self.oids[1]])

    def test_target_present_at_verification_is_not_audited_as_retired(self):
        # Stand-in for a partially applied commit (or a writer re-storing a target).
        target = self.oids[1]
        with self.crash_on_store(1, after_store=False), self.assertRaises(SimulatedCrash):
            self.retire([target])
        self.g('stash', 'store', '-m', 'On main: entry-2', target)
        with self.assertRaises(self.mod.RetirementError):
            self.mod.recover(self.root, self.archive)
        self.assertNotEqual(self.hermes_refs(), [])
        self.assertFalse(any(r['event'] == 'retired' for r in self.audit()))
        self.assertEqual(sorted(self.stack_oids()), sorted(self.oids))  # nothing lost

    def test_missing_paths_refused(self):
        with self.assertRaises(self.mod.RetirementError):
            self.mod.retire(self.base / 'nope', self.archive, [self.oids[0]])
        with self.assertRaises(self.mod.RetirementError):
            self.mod.retire(self.root, self.base / 'nope', [self.oids[0]])
        with patch.dict(os.environ, {'HERMES_HOME': str(self.base / 'nope')}), \
                self.assertRaises(self.mod.RetirementError):
            self.mod.retire(self.root, self.archive, [self.oids[0]])
        self.assert_unchanged()

    def test_verification_failure_retains_keep_refs(self):
        original = self.mod._store
        n = {'i': 0}
        def store_then_drop(*a, **k):
            original(*a, **k)
            n['i'] += 1
            if n['i'] == 3:  # a concurrent writer drops a restored entry
                run_git(self.root, 'stash', 'drop', '-q', 'stash@{2}')
        with patch.object(self.mod, '_store', store_then_drop), self.assertRaises(self.mod.RetirementError):
            self.retire([self.oids[0]])
        self.assertNotEqual(self.hermes_refs(), [])
        with self.assertRaises(self.mod.RetirementError):
            self.mod.recover(self.root, self.archive)
        self.assertNotEqual(self.hermes_refs(), [])  # dropped entry stays pinned, never lost
        self.assertNotEqual(self.journal()[-1]['event'], 'complete')


class KillTests(Base):
    """Real SIGKILL of the retiring process at the prepared and intent-written points."""

    def setUp(self):
        super().setUp()
        self.prepare()

    def run_and_kill(self, seam):
        script = textwrap.dedent(f'''
            import importlib.util, sys, time
            spec = importlib.util.spec_from_file_location('m', {str(MODULE)!r})
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            original = getattr(m, {seam!r})
            def hang(*a, **k):
                print('AT-SEAM', flush=True)
                time.sleep(600)
            setattr(m, {seam!r}, hang)
            m.retire({str(self.root)!r}, {str(self.archive)!r}, [{self.oids[0]!r}])
        ''')
        p = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=dict(os.environ))
        try:
            self.assertEqual(p.stdout.readline(), b'AT-SEAM\n', p.stderr.read() if p.poll() is not None else '')
            self.assertTrue((self.root / '.git' / 'refs' / 'stash.lock').exists())
            os.kill(p.pid, signal.SIGKILL)
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
            p.stderr.close()
        deadline = time.time() + 30
        while (self.root / '.git' / 'refs' / 'stash.lock').exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse((self.root / '.git' / 'refs' / 'stash.lock').exists())
        self.wait_lock_free()  # the update-ref child held the inherited sync lock until it exited

    def test_E_sigkill_while_prepared_aborts(self):
        self.run_and_kill('_check_prepared')
        self.assert_unchanged()
        self.assertEqual(self.mod.recover(self.root, self.archive), {'completed': [], 'aborted': []})

    def test_sigkill_after_intent_before_commit_aborts(self):
        self.run_and_kill('_commit_transaction')
        self.assert_unchanged()
        self.assertEqual(self.journal()[-1]['event'], 'intent')
        result = self.mod.recover(self.root, self.archive)
        self.assertEqual(len(result['aborted']), 1)
        self.assertEqual(self.journal()[-1]['event'], 'aborted')
        self.assert_unchanged()
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])


class HardeningPrepareTests(Base):
    """Grafts, shallow history, broken refs and worktree/untracked export completeness."""

    def test_grafts_file_ignored_but_replace_off_alone_would_honour_it(self):
        target = self.oids[0]
        true_parents = self.g('rev-parse', target + '^@').decode().split()
        true_index = self.g('diff', '--binary', '--full-index', target + '^1', target + '^2')
        grafts = self.root / '.git' / 'info' / 'grafts'
        grafts.parent.mkdir(exist_ok=True)
        grafts.write_text(f'{target} {self.oids[2]}\n')
        # GIT_NO_REPLACE_OBJECTS alone still honours info/grafts:
        env = dict(ENV, GIT_NO_REPLACE_OBJECTS='1')
        self.assertEqual(self.g('rev-parse', target + '^@', env=env).decode().split(), [self.oids[2]])
        self.prepare()
        doc = json.loads((self.archive / 'retirement-manifest.json').read_text())
        self.assertEqual(doc['snapshot']['entries'][0]['parents'], true_parents)
        self.assertEqual((self.archive / (target + '.index.patch')).read_bytes(), true_index)
        self.retire([self.oids[1]])
        nograft = dict(ENV, GIT_GRAFT_FILE=os.devnull)
        self.assertEqual(self.g('rev-parse', 'refs/stash^@', env=nograft).decode().split(), true_parents)
        grafts.unlink()
        self.assert_retired([self.oids[1]])

    def test_shallow_repository_refused_by_every_entry_point(self):
        self.prepare()
        shallow = self.root / '.git' / 'shallow'
        shallow.write_text(self.g('rev-parse', 'HEAD').decode())
        for call in (self.prepare, lambda: self.retire([self.oids[0]]),
                     lambda: self.mod.recover(self.root, self.archive)):
            with self.assertRaisesRegex(self.mod.RetirementError, 'shallow'):
                call()
        self.assert_unchanged()
        shallow.unlink()
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])

    def test_broken_stash_ref_refused_not_read_as_empty(self):
        self.prepare()
        ref = self.root / '.git' / 'refs' / 'stash'
        original = ref.read_bytes()
        ref.write_bytes(b'')
        for call in (self.prepare, lambda: self.retire([self.oids[0]])):
            with self.assertRaisesRegex(self.mod.RetirementError, 'broken'):
                call()
        ref.write_bytes(original)
        self.assert_unchanged()

    def test_worktree_untracked_export_round_trips_for_every_entry(self):
        calls = []
        with self.wrap('_verify_worktree_round_trip', before=lambda root, oid, patch: calls.append(oid)):
            self.prepare()
        self.assertEqual(sorted(calls), sorted(self.oids))
        for oid in self.oids:
            export = (self.archive / (oid + '.patch')).read_bytes()
            self.mod._verify_worktree_round_trip(self.root.resolve(), oid, export)
        untracked = self.oids[3]  # entry-0: tracked change plus untracked binary file
        export = (self.archive / (untracked + '.patch')).read_bytes()
        cut = export.index(b'diff --git a/untracked-0.bin')
        for bad in (export[:cut], (self.archive / (self.oids[0] + '.patch')).read_bytes()):
            with self.assertRaisesRegex(self.mod.RetirementError, 'does not reproduce'):
                self.mod._verify_worktree_round_trip(self.root.resolve(), untracked, bad)

    def test_unexportable_stash_refused(self):
        # A stash whose untracked tree duplicates a worktree path: git cannot export it at all.
        self.g('rm', '-q', '--cached', 'bin.dat')
        self.g('stash', 'push', '-q', '-u', '-m', 'rm-cached')
        bad = self.tip()
        self.assertNotEqual(run_git(self.root, 'stash', 'show', '-p', '--include-untracked', bad,
                                    check=False).returncode, 0)
        rows = json.loads((self.archive / 'manifest.json').read_text())
        rows.append({'stash': bad, 'patch': 'x.patch', 'bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()})
        (self.archive / 'x.patch').write_bytes(b'x')
        (self.archive / 'manifest.json').write_text(json.dumps(rows))
        with self.assertRaisesRegex(self.mod.RetirementError, 'stash show'):
            self.prepare()
        self.assertFalse((self.archive / 'retirement-manifest.json').exists())

    def test_config_pins_ref_fsync_and_no_auto_gc(self):
        got = {k: self.mod._git(self.root, 'config', '--get', k).decode().strip()
               for k in ('core.fsync', 'core.fsyncMethod', 'gc.auto')}
        self.assertEqual(got, {'core.fsync': 'reference', 'core.fsyncMethod': 'fsync', 'gc.auto': '0'})
        self.assertEqual(self.mod._env()['GIT_GRAFT_FILE'], os.devnull)

    def test_append_survives_short_writes(self):
        real = os.write
        def short(fd, data):
            return real(fd, bytes(data[:3]))
        with patch.object(self.mod.os, 'write', short):
            self.mod._append(self.archive, 'stash-retirement-journal.jsonl',
                             {'txn': 'a' * 32, 'event': 'intent', 'payload': 'x' * 200})
        rec = self.journal()
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]['payload'], 'x' * 200)


ORPHAN_SCRIPT = '''
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location('m', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def commit(root, txn):
    os.kill(txn.proc.pid, signal.SIGSTOP)   # Git has not read 'commit' yet
    txn.send('commit')
    print(txn.proc.pid, flush=True)
    os._exit(9)                             # Python dies; nothing else runs
m._commit_transaction = commit
m.retire(sys.argv[2], sys.argv[3], [sys.argv[4]])
'''


class OrphanTests(Base):
    """B1: Python dies after sending commit while the update-ref child is stopped."""

    def setUp(self):
        super().setUp()
        self.prepare()

    def orphan(self):
        p = subprocess.run([sys.executable, '-c', ORPHAN_SCRIPT, str(MODULE), str(self.root), str(self.archive),
                            self.oids[1]], capture_output=True, env=dict(os.environ), timeout=120)
        self.assertEqual(p.returncode, 9, p.stderr)
        pid = int(p.stdout.decode().strip())
        self.addCleanup(lambda: os.kill(pid, signal.SIGKILL) if not self.lock_free() else None)
        return pid

    def assert_refused_pending(self, pattern):
        for call in (lambda: self.mod.recover(self.root, self.archive), self.prepare,
                     lambda: self.retire([self.oids[0]])):
            with self.assertRaisesRegex(self.mod.RetirementError, pattern):
                call()
        self.assertEqual(self.events(), ['intent'])

    def test_stopped_orphan_keeps_lock_then_commits_and_recover_finishes(self):
        pid = self.orphan()
        self.assertFalse(self.lock_free())  # the stopped Git child still holds the inherited flock
        self.assertIn('refs/stash.lock', self.git_locks())
        self.assert_refused_pending('sync lock is held')
        self.assertEqual(self.stack(), self.before_stack)
        os.kill(pid, signal.SIGCONT)  # Git now reads 'commit' and commits
        self.wait_lock_free()
        self.assertEqual(self.stack(), [])
        result = self.mod.recover(self.root, self.archive)
        self.assertEqual(len(result['completed']), 1)
        self.assertNotIn('aborted', self.events())
        self.assertIn('committed', self.events())
        self.assert_retired([self.oids[1]])

    def test_killed_orphan_leaves_git_locks_recover_refuses_until_cleared(self):
        pid = self.orphan()
        os.kill(pid, signal.SIGKILL)  # dies before reading 'commit'
        self.wait_lock_free()
        locks = self.git_locks()
        self.assertIn('refs/stash.lock', locks)
        self.assert_refused_pending('Git lock files exist')
        self.assertEqual(self.stack(), self.before_stack)
        for rel in locks:  # operator confirms no Git process and removes the stale locks
            (self.root / '.git' / rel).unlink()
        result = self.mod.recover(self.root, self.archive)
        self.assertEqual(len(result['aborted']), 1)
        self.assert_unchanged()
        self.retire([self.oids[1]])
        self.assert_retired([self.oids[1]])


class TimeoutTests(Base):
    """update-ref reads are bounded; the child is never killed and keeps the lock."""

    def setUp(self):
        super().setUp()
        self.prepare()
        self.stopped = []
        self.addCleanup(self._release)

    def _release(self):
        for pid in self.stopped:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass

    def stop_on(self, command):
        original = self.mod._Transaction.send
        def send(txn, line):
            if line == command:
                os.kill(txn.proc.pid, signal.SIGSTOP)
                self.stopped.append(txn.proc.pid)
            return original(txn, line)
        return patch.object(self.mod._Transaction, 'send', send)

    def test_prepare_reply_timeout_leaves_child_to_roll_back(self):
        with patch.object(self.mod, 'TXN_TIMEOUT', 1), self.stop_on('prepare'):
            with self.assertRaisesRegex(self.mod.RetirementError, 'did not answer'):
                self.retire([self.oids[0]])
        self.assertFalse(self.lock_free())
        with self.assertRaisesRegex(self.mod.RetirementError, 'sync lock is held'):
            self.mod.recover(self.root, self.archive)
        self._release()
        self.wait_lock_free()
        self.assertEqual(self.git_locks(), [])
        self.assert_unchanged()
        self.assertNotIn('intent', self.events())

    def test_commit_reply_timeout_stays_pending_then_recovers(self):
        with patch.object(self.mod, 'TXN_TIMEOUT', 1), self.stop_on('commit'):
            with self.assertRaisesRegex(self.mod.RetirementError, 'stays pending'):
                self.retire([self.oids[2]])
        self.assertEqual(self.events(), ['intent'])
        with self.assertRaisesRegex(self.mod.RetirementError, 'sync lock is held'):
            self.mod.recover(self.root, self.archive)
        self._release()
        self.wait_lock_free()
        self.assertEqual(len(self.mod.recover(self.root, self.archive)['completed']), 1)
        self.assert_retired([self.oids[2]])

    def test_lost_commit_reply_after_real_commit_is_finished(self):
        original = self.mod._commit_transaction
        def lost(root, txn):
            original(root, txn)
            raise self.mod.RetirementError('reply lost')
        with patch.object(self.mod, '_commit_transaction', lost):
            result = self.retire([self.oids[1]])
        self.assertIn('finished by recovery', result['note'])
        self.assertNotIn('aborted', self.events())
        self.assert_retired([self.oids[1]])


class PowerLossTests(Base):
    """B2 stand-ins: the on-disk states a power loss can leave inside or after Git's commit."""

    def setUp(self):
        super().setUp()
        self.prepare()
        git = self.root / '.git'
        self.stash_files = {p: p.read_bytes() for p in (git / 'refs' / 'stash', git / 'logs' / 'refs' / 'stash')}
        self.target = self.oids[1]

    def crash_after(self, seam):
        with self.wrap(seam, before=lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash())), \
                self.assertRaises(SimulatedCrash):
            self.retire([self.target])
        self.assertEqual(self.stack(), [])

    def ref_files(self):
        base = self.root / '.git' / 'refs' / 'hermes-retire'
        return {str(p.relative_to(self.root / '.git')): p for p in base.rglob('*') if p.is_file()}

    def restore_stash_files(self):
        for p, data in self.stash_files.items():
            p.write_bytes(data)

    def assert_never_aborted(self, pattern):
        before = self.events()
        for call in (lambda: self.mod.recover(self.root, self.archive), self.prepare):
            with self.assertRaisesRegex(self.mod.RetirementError, pattern):
                call()
        self.assertEqual(self.events(), before)
        self.assertNotIn('aborted', before)
        self.assertFalse(any(r['event'] in ('aborted', 'retired') for r in self.audit()))

    def test_committed_with_broken_ref_files_refuses(self):
        self.crash_after('_finish')
        self.assertEqual(self.events(), ['intent', 'committed'])
        files = self.ref_files()
        saved = {k: p.read_bytes() for k, p in files.items()}
        for p in files.values():
            p.write_bytes(b'')  # data blocks lost
        self.assert_never_aborted('broken')
        for k, p in files.items():
            p.write_bytes(saved[k])
        self.mod.recover(self.root, self.archive)
        self.assert_retired([self.target])

    def test_committed_with_missing_marker_recovers_from_valid_keep_refs(self):
        self.crash_after('_finish')
        marker = next(p for k, p in self.ref_files().items() if '/txn/' in k)
        marker.unlink()
        result = self.mod.recover(self.root, self.archive)
        self.assertEqual(len(result['completed']), 1)
        self.assert_retired([self.target])

    def test_committed_with_marker_and_a_keep_ref_missing_refuses_keeping_pins(self):
        self.crash_after('_finish')
        files = self.ref_files()
        next(p for k, p in files.items() if '/txn/' in k).unlink()
        next(p for k, p in sorted(files.items()) if '/keep/' in k).unlink()
        left = self.hermes_refs()
        self.assert_never_aborted('keep refs are missing|without marker')
        self.assertEqual(self.hermes_refs(), left)

    def test_committed_with_all_refs_missing_is_never_aborted(self):
        self.crash_after('_finish')
        for p in self.ref_files().values():
            p.unlink()
        self.assert_never_aborted('records a commit but its refs are missing')
        # Even a stack identical to the snapshot does not turn a journaled commit into an abort.
        self.restore_stash_files()
        self.assertEqual(self.stack(), self.before_stack)
        self.assert_never_aborted('records a commit')

    def test_uncommitted_journal_new_refs_lost_stash_deleted_refuses_with_diagnosis(self):
        self.crash_after('_fsync_committed')  # Git committed; 'committed' never journaled
        self.assertEqual(self.events(), ['intent'])
        for p in self.ref_files().values():
            p.unlink()
        with self.assertRaises(self.mod.RetirementError) as cm:
            self.mod.recover(self.root, self.archive)
        msg = str(cm.exception)
        self.assertIn('differs from the manifest snapshot', msg)
        self.assertIn('never treated as aborted', msg)
        for oid in self.oids:
            if oid != self.target:
                self.assertIn(f'{oid}=present', msg)
        self.assertEqual(self.events(), ['intent'])

    def test_uncommitted_journal_stash_deletion_lost_refuses_and_keeps_pins(self):
        self.crash_after('_fsync_committed')
        refs = self.hermes_refs()
        self.restore_stash_files()  # refs/stash deletion did not persist; pins did
        with self.assertRaisesRegex(self.mod.RetirementError, 'retired OID is present'):
            self.mod.recover(self.root, self.archive)
        self.assertEqual(self.hermes_refs(), refs)
        self.assertEqual(self.stack(), self.before_stack)
        self.assertNotIn('aborted', self.events())
        self.assertFalse(any(r['event'] == 'retired' for r in self.audit()))

    def test_uncommitted_journal_whole_commit_lost_aborts(self):
        self.crash_after('_fsync_committed')
        for p in self.ref_files().values():
            p.unlink()
        self.restore_stash_files()  # every rename/unlink lost: byte-identical to the snapshot
        result = self.mod.recover(self.root, self.archive)
        self.assertEqual(len(result['aborted']), 1)
        self.assert_unchanged()

    def test_native_lock_with_pending_intent_refuses(self):
        self.crash_after_intent()
        lock = self.root / '.git' / 'refs' / 'stash.lock'
        lock.write_bytes(b'')
        with self.assertRaisesRegex(self.mod.RetirementError, 'Git lock files exist'):
            self.mod.recover(self.root, self.archive)
        self.assertEqual(self.events(), ['intent'])
        lock.unlink()
        self.assertEqual(len(self.mod.recover(self.root, self.archive)['aborted']), 1)
        self.assert_unchanged()

    def crash_after_intent(self):
        with self.wrap('_commit_transaction', before=lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash())), \
                self.assertRaises(SimulatedCrash):
            self.retire([self.target])
        self.wait_lock_free()
        self.assert_unchanged()

    def test_fsync_order_and_read_only_opens(self):
        log = []
        git = (self.root / '.git').resolve()
        real_fsync_path, real_journal, real_delete = (self.mod._fsync_path, self.mod._journal,
                                                      self.mod._delete_txn_refs)
        real_open = os.open
        opens = []

        def fsync_path(path):
            log.append(('fsync', str(Path(path).relative_to(git)) if git in Path(path).parents or Path(path) == git
                        else str(path)))
            return real_fsync_path(path)

        def journal(archive, txn, event, **f):
            log.append(('journal', event))
            return real_journal(archive, txn, event, **f)

        def delete(*a, **k):
            log.append(('delete',))
            return real_delete(*a, **k)

        def rec_open(path, flags, *a, **k):
            if str(path).startswith(str(git)):
                opens.append((str(path), flags))
            return real_open(path, flags, *a, **k)

        with patch.object(self.mod, '_fsync_path', fsync_path), patch.object(self.mod, '_journal', journal), \
                patch.object(self.mod, '_delete_txn_refs', delete), patch.object(self.mod.os, 'open', rec_open):
            result = self.retire([self.target])
        txn = result['txn']
        committed = log.index(('journal', 'committed'))
        before_commit = {e[1] for e in log[:committed] if e[0] == 'fsync'}
        expected = {f'refs/hermes-retire/txn/{txn}', f'refs/hermes-retire/keep/{txn}',
                    'refs/hermes-retire/keep', 'refs/hermes-retire/txn', 'refs/hermes-retire', 'refs',
                    'logs/refs', 'logs', '.'}
        expected |= {f'refs/hermes-retire/keep/{txn}/{i}' for i, o in enumerate(self.oids) if o != self.target}
        self.assertLessEqual(expected, before_commit)  # '.' is the common dir itself
        delete_at = log.index(('delete',))
        verified = log.index(('journal', 'verified'))
        between = {e[1] for e in log[committed:delete_at] if e[0] == 'fsync'}
        self.assertLessEqual({'refs/stash', 'logs/refs/stash', 'refs', 'logs/refs', 'logs'}, between)
        self.assertLess(verified, delete_at)
        complete = log.index(('journal', 'complete'))
        self.assertIn(('fsync', 'refs'), log[delete_at:complete])
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        self.assertTrue(opens)
        for path, flags in opens:
            self.assertEqual(flags & write_flags, 0, path)
        self.assert_retired([self.target])


BUNDLE_REF = 'refs/hermes-stash-archive/stash'


def bundle_header(data):
    """(version line, capability lines, prerequisite lines, ref lines) of a bundle file."""
    head, sep, _ = data.partition(b'\n\n')
    assert sep, 'bundle header has no terminator'
    lines = head.decode().split('\n')
    caps = [x for x in lines[1:] if x.startswith('@')]
    prereqs = [x for x in lines[1:] if x.startswith('-')]
    refs = [x for x in lines[1:] if not x.startswith(('@', '-'))]
    return lines[0], caps, prereqs, refs


class BundleTests(Base):
    """A self-contained, import-verified Git bundle per stash entry survives retirement + GC."""

    def add_deleted_topic_stash(self):
        """A stash whose ^1 is a commit on a topic that no branch references (deleted topic),
        built with plumbing only so the dirty checkout stays byte-identical."""
        idx = dict(ENV, GIT_INDEX_FILE=str(self.base / 'topic-index'))
        head = self.g('rev-parse', 'HEAD').decode().strip()
        self.g('read-tree', head, env=idx)
        topic_blob = run_git(self.root, 'hash-object', '-w', '--stdin', input=b'topic-only line\n' * 50).stdout
        self.g('update-index', '--add', '--cacheinfo', '100644,%s,topic.txt' % topic_blob.decode().strip(), env=idx)
        topic_tree = self.g('write-tree', env=idx).decode().strip()
        topic = self.g('commit-tree', topic_tree, '-p', head, '-m', 'topic').decode().strip()
        index_commit = self.g('commit-tree', topic_tree, '-p', topic, '-m', 'index on topic: wip').decode().strip()
        edit = run_git(self.root, 'hash-object', '-w', '--stdin',
                       input=b'topic-only line\n' * 50 + b'stashed edit\n').stdout.decode().strip()
        self.g('update-index', '--cacheinfo', '100644,%s,topic.txt' % edit, env=idx)
        wt_tree = self.g('write-tree', env=idx).decode().strip()
        stash = self.g('commit-tree', wt_tree, '-p', topic, '-p', index_commit, '-m',
                       'On topic: wip').decode().strip()
        self.g('stash', 'store', '-m', 'On topic: wip', stash,
               env=dict(ENV, GIT_COMMITTER_DATE=f'{T0 + 40} +0000'))
        self.oids.insert(0, stash)
        self.write_legacy_archive()
        self.before_stack = self.stack()
        return stash, topic

    def manifest(self):
        return json.loads((self.archive / 'retirement-manifest.json').read_text())

    def bundle_row(self, oid):
        return next(a for a in self.manifest()['archives'] if a['oid'] == oid)['bundle']

    def import_bundle(self, data, name='imported'):
        dest = self.base / name
        run_git(self.base, 'init', '-q', '--bare', str(dest))
        path = self.base / (name + '.bundle')
        path.write_bytes(data)
        run_git(dest, 'bundle', 'verify', '-q', str(path))
        run_git(dest, 'fetch', '-q', '--no-tags', str(path), f'{BUNDLE_REF}:{BUNDLE_REF}')
        run_git(dest, 'fsck', '--full', '--no-dangling', '--no-progress')
        return dest

    def gc(self):
        self.g('gc', '-q', '--prune=now')

    def test_deleted_topic_base_restorable_from_bundle_after_retire_and_gc(self):
        stash, topic = self.add_deleted_topic_stash()
        self.prepare()
        row = self.bundle_row(stash)
        self.assertEqual(row['file'], stash + '.bundle')
        self.retire([stash])
        self.assert_retired([stash])
        self.gc()
        # The loss the bundle exists for: the base commit is gone from the repository.
        self.assertNotEqual(run_git(self.root, 'cat-file', '-e', topic + '^{commit}', check=False).returncode, 0)
        self.assertNotEqual(run_git(self.root, 'cat-file', '-e', stash + '^{commit}', check=False).returncode, 0)
        # ...and the include-untracked patch no longer applies to anything present.
        patch_bytes = (self.archive / (stash + '.patch')).read_bytes()
        self.assertNotEqual(run_git(self.root, 'apply', '--check', '-', input=patch_bytes, check=False).returncode, 0)
        data = (self.archive / row['file']).read_bytes()
        self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (row['bytes'], row['sha256']))
        # Restore: unbundle objects (no ref writes), then re-store the entry.
        bpath = self.base / 'restore.bundle'
        bpath.write_bytes(data)
        heads = self.g('bundle', 'unbundle', str(bpath)).decode().split()
        self.assertEqual(heads[:2], [stash, BUNDLE_REF])
        self.assertEqual(self.g('rev-parse', stash + '^@').decode().split(),
                         self.manifest()['snapshot']['entries'][0]['parents'])
        self.g('stash', 'store', '-m', 'On topic: wip', stash)
        self.assertEqual(self.g('stash', 'show', '-p', '--binary', '--include-untracked', stash), patch_bytes)
        self.assertEqual(self.g('cat-file', 'blob', stash + ':topic.txt'),
                         b'topic-only line\n' * 50 + b'stashed edit\n')
        self.assertEqual(self.g('rev-parse', stash + '^1'), (topic + '\n').encode())
        self.assertEqual(self.g('rev-parse', stash + '^1^1'), self.g('rev-parse', 'HEAD'))
        self.assertEqual(self.checkout(), self.before_checkout)

    def test_every_entry_gets_self_contained_verified_bundle(self):
        self.prepare()
        doc = self.manifest()
        parents = {e['oid']: e['parents'] for e in doc['snapshot']['entries']}
        self.assertEqual([a['oid'] for a in doc['archives']], self.oids)
        for n, a in enumerate(doc['archives']):
            with self.subTest(oid=a['oid']):
                data = (self.archive / a['bundle']['file']).read_bytes()
                self.assertEqual(a['bundle']['bytes'], len(data))
                self.assertEqual(a['bundle']['sha256'], hashlib.sha256(data).hexdigest())
                version, caps, prereqs, refs = bundle_header(data)
                self.assertIn(version, ('# v2 git bundle', '# v3 git bundle'))
                self.assertEqual(prereqs, [])  # self-contained: no prerequisite commits
                self.assertEqual(refs, [f'{a["oid"]} {BUNDLE_REF}'])
                dest = self.import_bundle(data, f'imp{n}')
                self.assertEqual(run_git(dest, 'rev-parse', BUNDLE_REF).stdout.decode().strip(), a['oid'])
                self.assertEqual(run_git(dest, 'rev-parse', a['oid'] + '^@').stdout.decode().split(),
                                 parents[a['oid']])
                # Full history back to the root commit is inside the bundle.
                self.assertEqual(run_git(dest, 'rev-list', '--max-parents=0', a['oid']).stdout,
                                 run_git(self.root, 'rev-list', '--max-parents=0', a['oid']).stdout)
        self.assert_unchanged()

    def test_prepare_creates_no_source_refs_or_reflogs(self):
        refs_before = self.g('for-each-ref', '--format=%(refname) %(objectname)')
        logs = self.root / '.git' / 'logs'
        logs_before = sorted(str(p.relative_to(logs)) for p in logs.rglob('*'))
        packed = self.root / '.git' / 'packed-refs'
        packed_before = packed.read_bytes() if packed.exists() else None
        head_log = (logs / 'HEAD').read_bytes()
        self.prepare()
        self.assertEqual(self.g('for-each-ref', '--format=%(refname) %(objectname)'), refs_before)
        self.assertEqual(sorted(str(p.relative_to(logs)) for p in logs.rglob('*')), logs_before)
        self.assertEqual(packed.read_bytes() if packed.exists() else None, packed_before)
        self.assertEqual((logs / 'HEAD').read_bytes(), head_log)
        self.assert_unchanged()

    def test_corrupted_or_missing_bundle_refused_by_retire(self):
        self.prepare()
        for victim in (self.oids[0], self.oids[2]):  # target and non-target
            row = self.bundle_row(victim)
            p = self.archive / row['file']
            original = p.read_bytes()
            mid = len(original) // 2
            for label, bad in {'truncated': original[:-1], 'flipped': original[:mid] + bytes([original[mid] ^ 1])
                               + original[mid + 1:], 'empty': b''}.items():
                with self.subTest(victim=victim, case=label):
                    p.write_bytes(bad)
                    with self.assertRaisesRegex(self.mod.RetirementError, 'mismatch'):
                        self.retire([self.oids[0]])
                    self.assert_unchanged()
            p.unlink()
            with self.subTest(victim=victim, case='missing'):
                with self.assertRaisesRegex(self.mod.RetirementError, 'unreadable'):
                    self.retire([self.oids[0]])
                self.assert_unchanged()
            p.write_bytes(original)
        self.assertFalse(any(r['event'] == 'intent' for r in self.journal()))
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])

    def test_bundle_with_matching_hash_but_wrong_content_refused_by_retire(self):
        """Hash + bytes are not enough: retire re-imports and checks the exact OID and parents."""
        self.prepare()
        mp = self.archive / 'retirement-manifest.json'
        original_manifest = mp.read_bytes()
        victim = self.oids[1]
        row = self.bundle_row(victim)
        p = self.archive / row['file']
        original = p.read_bytes()
        other = (self.archive / self.bundle_row(self.oids[2])['file']).read_bytes()  # valid, different OID
        # A thin bundle of the right OID (prerequisite ^1): not self-contained.
        work = self.base / 'thin.git'
        run_git(self.base, 'init', '-q', '--bare', str(work))
        run_git(work, 'fetch', '-q', '--no-tags', str(self.root), f'{victim}:{BUNDLE_REF}')
        thin_path = self.base / 'thin.bundle'
        run_git(work, 'bundle', 'create', '-q', str(thin_path), BUNDLE_REF, '^' + victim + '^1')
        thin = thin_path.read_bytes()
        self.assertTrue(bundle_header(thin)[2])
        garbage = b'# v2 git bundle\n' + victim.encode() + b' ' + BUNDLE_REF.encode() + b'\n\nnot a pack'
        for label, bad in {'other-oid': other, 'thin': thin, 'garbage-pack': garbage}.items():
            with self.subTest(label):
                p.write_bytes(bad)
                doc = json.loads(original_manifest)
                part = next(a for a in doc['archives'] if a['oid'] == victim)['bundle']
                part.update(bytes=len(bad), sha256=hashlib.sha256(bad).hexdigest())
                mp.write_text(json.dumps(doc))
                with self.assertRaises(self.mod.RetirementError):
                    self.retire([self.oids[0]])
                self.assert_unchanged()
        p.write_bytes(original)
        mp.write_bytes(original_manifest)
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])

    def test_prepare_never_overwrites_or_trusts_a_differing_bundle(self):
        victim = self.oids[3]
        p = self.archive / (victim + '.bundle')
        p.write_bytes(b'not a bundle')
        with self.assertRaisesRegex(self.mod.RetirementError, 'bundle'):
            self.prepare()
        self.assertEqual(p.read_bytes(), b'not a bundle')
        self.assertFalse((self.archive / 'retirement-manifest.json').exists())
        p.unlink()
        self.prepare()
        first = (self.archive / 'retirement-manifest.json').read_bytes()
        self.prepare()  # an existing verified bundle is reused: the manifest is byte-identical
        self.assertEqual((self.archive / 'retirement-manifest.json').read_bytes(), first)
        self.assert_unchanged()

    def test_new_manifest_fields_strictly_validated(self):
        self.prepare()
        p = self.archive / 'retirement-manifest.json'
        original = p.read_text()

        def bundle(d):
            return d['archives'][1]['bundle']
        cases = {
            'bundle-missing': lambda d: d['archives'][1].pop('bundle'),
            'bundle-not-object': lambda d: d['archives'][1].update(bundle='x.bundle'),
            'bundle-bytes-bool': lambda d: bundle(d).update(bytes=True),
            'bundle-bytes-str': lambda d: bundle(d).update(bytes=str(bundle(d)['bytes'])),
            'bundle-bytes-negative': lambda d: bundle(d).update(bytes=-1),
            'bundle-bytes-zero': lambda d: bundle(d).update(bytes=0),
            'bundle-sha-upper': lambda d: bundle(d).update(sha256=bundle(d)['sha256'].upper()),
            'bundle-sha-int': lambda d: bundle(d).update(sha256=1),
            'bundle-extra-key': lambda d: bundle(d).update(prerequisites=[]),
            'bundle-wrong-file': lambda d: bundle(d).update(file=d['archives'][0]['bundle']['file']),
            'row-extra-key': lambda d: d['archives'][1].update(note='x'),
            'index-bytes-bool': lambda d: d['archives'][1]['index'].update(bytes=False),
        }
        for label, f in cases.items():
            with self.subTest(label):
                doc = json.loads(original)
                f(doc)
                p.write_text(json.dumps(doc))
                with self.assertRaisesRegex(self.mod.RetirementError, 'manifest'):
                    self.retire([self.oids[0]])
                self.assert_unchanged()
        p.write_text(original)
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])
        audit = next(r for r in self.audit() if r['event'] == 'retired')
        self.assertEqual(audit['bundle_sha256'], json.loads(original)['archives'][0]['bundle']['sha256'])

    def test_journal_intent_bound_to_bundle_hash(self):
        self.prepare()
        target = self.oids[1]
        with self.wrap('_store', before=lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash()), times={1}), \
                self.assertRaises(SimulatedCrash):
            self.retire([target])
        jp = self.archive / 'stash-retirement-journal.jsonl'
        original = jp.read_text()
        lines = [json.loads(x) for x in original.splitlines()]
        intent = next(r for r in lines if r['event'] == 'intent')
        self.assertEqual(intent['targets'][0]['bundle_sha256'], self.bundle_row(target)['sha256'])
        refs = self.hermes_refs()
        intent['targets'][0]['bundle_sha256'] = '0' * 64
        jp.write_text(''.join(json.dumps(r) + '\n' for r in lines))
        with self.assertRaisesRegex(self.mod.RetirementError, 'not bound'):
            self.mod.recover(self.root, self.archive)
        self.assertEqual(self.hermes_refs(), refs)
        jp.write_text(original)
        self.mod.recover(self.root, self.archive)
        self.assert_retired([target])

    def test_bundle_build_ignores_source_grafts_and_replace_refs(self):
        target = self.oids[0]
        true_parents = self.g('rev-parse', target + '^@').decode().split()
        self.g('replace', self.oids[1], self.oids[2])
        grafts = self.root / '.git' / 'info' / 'grafts'
        grafts.parent.mkdir(exist_ok=True)
        grafts.write_text(f'{target} {self.oids[2]}\n')
        self.prepare()
        data = (self.archive / self.bundle_row(target)['file']).read_bytes()
        grafts.unlink()
        dest = self.import_bundle(data)
        self.assertEqual(run_git(dest, 'rev-parse', target + '^@').stdout.decode().split(), true_parents)


GROUP_KILL_SCRIPT = '''
import importlib.util, sys, time
spec = importlib.util.spec_from_file_location('m', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def hang(*a, **k):
    print('AT-SEAM', flush=True)
    time.sleep(600)
setattr(m, sys.argv[5], hang)
m.retire(sys.argv[2], sys.argv[3], [sys.argv[4]])
'''


class GroupIsolationTests(Base):
    """Signals to the caller's process group (Ctrl-C, killpg) never reach Git children."""

    def setUp(self):
        super().setUp()
        self.prepare()

    def test_git_children_run_in_their_own_session(self):
        seen = {}
        def probe(root, ident, raw, txn):
            seen['txn'] = os.getsid(txn.proc.pid)
        original_run = subprocess.run
        kwargs = []
        def run(*a, **k):
            kwargs.append(k.get('start_new_session'))
            return original_run(*a, **k)
        with self.wrap('_check_prepared', before=probe), patch.object(self.mod.subprocess, 'run', run):
            self.retire([self.oids[1]])
        self.assertNotEqual(seen['txn'], os.getsid(0))
        self.assertTrue(kwargs)
        self.assertEqual(set(kwargs), {True})
        self.assert_retired([self.oids[1]])

    def kill_group_at(self, seam):
        p = subprocess.Popen([sys.executable, '-c', GROUP_KILL_SCRIPT, str(MODULE), str(self.root),
                              str(self.archive), self.oids[0], seam],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ),
                             start_new_session=True)  # its own group, so killpg spares this test runner
        try:
            self.assertEqual(p.stdout.readline(), b'AT-SEAM\n', p.stderr.read() if p.poll() is not None else '')
            self.assertTrue((self.root / '.git' / 'refs' / 'stash.lock').exists())
            os.killpg(p.pid, signal.SIGKILL)  # the whole caller group, as a terminal or supervisor would
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
            p.stderr.close()
        self.wait_lock_free()
        deadline = time.time() + 30
        while self.git_locks() and time.time() < deadline:
            time.sleep(0.05)

    def test_group_kill_while_prepared_lets_git_roll_back(self):
        self.kill_group_at('_check_prepared')
        self.assertEqual(self.git_locks(), [])  # Git survived the group kill and released its locks
        self.assert_unchanged()
        self.assertEqual(self.mod.recover(self.root, self.archive), {'completed': [], 'aborted': []})

    def test_group_kill_after_intent_aborts_cleanly(self):
        self.kill_group_at('_commit_transaction')
        self.assertEqual(self.git_locks(), [])
        self.assert_unchanged()
        self.assertEqual(self.events(), ['intent'])
        self.assertEqual(len(self.mod.recover(self.root, self.archive)['aborted']), 1)
        self.assert_unchanged()
        self.retire([self.oids[0]])
        self.assert_retired([self.oids[0]])


class PartialCommitTests(Base):
    """A marker ref with an intact snapshot is a partial commit: refused before 'committed'."""

    def setUp(self):
        super().setUp()
        self.prepare()
        git = self.root / '.git'
        self.stash_files = {p: p.read_bytes() for p in (git / 'refs' / 'stash', git / 'logs' / 'refs' / 'stash')}
        self.target = self.oids[1]

    def crash_before_committed_journal(self):
        with self.wrap('_fsync_committed', before=lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash())), \
                self.assertRaises(SimulatedCrash):
            self.retire([self.target])
        self.assertEqual(self.events(), ['intent'])
        self.assertTrue(any('/txn/' in r for r in self.hermes_refs()))

    def restore_stash_files(self):
        for p, data in self.stash_files.items():
            p.write_bytes(data)

    def assert_refused_uncommitted(self, refs):
        for call in (lambda: self.mod.recover(self.root, self.archive), self.prepare,
                     lambda: self.retire([self.oids[0]])):
            with self.assertRaisesRegex(self.mod.RetirementError, 'partial commit'):
                call()
        self.assertEqual(self.events(), ['intent'])  # never journaled 'committed' (nor aborted)
        self.assertEqual(self.hermes_refs(), refs)
        self.assertFalse(any(r['event'] in ('retired', 'aborted', 'recovering') for r in self.audit()))

    def test_marker_present_snapshot_intact_refused_before_committed(self):
        self.crash_before_committed_journal()
        refs = self.hermes_refs()
        self.restore_stash_files()  # the refs/stash deletion was lost; the new refs were not
        self.assertEqual(self.stack(), self.before_stack)
        self.assert_refused_uncommitted(refs)
        self.assertEqual(self.stack(), self.before_stack)

    def test_marker_present_target_still_stacked_under_new_push_refused(self):
        self.crash_before_committed_journal()
        refs = self.hermes_refs()
        self.restore_stash_files()
        self.g('stash', 'store', '-m', 'concurrent', self.fake_stash(self.oids[0]))
        self.assertIn(self.target, self.stack_oids())
        self.assert_refused_uncommitted(refs)

    def test_commit_ok_but_stack_intact_refused_in_retire(self):
        original = self.mod._commit_transaction
        def commit_then_lose_deletion(root, txn):
            original(root, txn)
            self.restore_stash_files()
        with patch.object(self.mod, '_commit_transaction', commit_then_lose_deletion), \
                self.assertRaisesRegex(self.mod.RetirementError, 'partial commit'):
            self.retire([self.target])
        self.assertEqual(self.events(), ['intent'])
        self.assertEqual(self.stack(), self.before_stack)
        self.assertNotEqual(self.hermes_refs(), [])

    def test_full_commit_still_recovered(self):
        # Control: marker present and the stack really deleted is a provable commit.
        self.crash_before_committed_journal()
        self.assertEqual(self.stack(), [])
        self.assertEqual(len(self.mod.recover(self.root, self.archive)['completed']), 1)
        self.assert_retired([self.target])


if __name__ == '__main__':
    unittest.main()
