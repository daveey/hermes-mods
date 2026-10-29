"""Tiny stdlib Git fixtures: no network, live checkout or private policy access."""
import contextlib
import importlib.util
import json
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('verify_mirror.py')
spec = importlib.util.spec_from_file_location('verify_mirror', SCRIPT)
assert spec is not None and spec.loader is not None
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mirror-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.write('runtime.py', 'value = 0\n')
        self.write('tests/test_regression.py', 'assert True\n')
        self.base = self.commit('base')
        self.write('runtime.py', 'value = 1\n')
        self.one = self.commit('first')
        self.write('second.py', 'second = True\n')
        self.tip = self.commit('second')
        self.patches = self.root / 'patches'
        self.export()
        self.sibling = self.root / 'someone-else'
        self.git('worktree', 'add', '--detach', str(self.sibling), self.base)
        self.addCleanup(lambda: self.git('worktree', 'remove', '--force', str(self.sibling)))

    def git(self, *args):
        return mirror.git(self.repo, *args).decode().strip()

    def write(self, name, text):
        file = self.repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def commit(self, subject):
        self.git('add', '.')
        self.git('commit', '-qm', subject)
        return self.git('rev-parse', 'HEAD')

    def export(self):
        self.patches.mkdir(exist_ok=True)
        for file in self.patches.glob('*.patch'):
            file.unlink()
        self.git('format-patch', '--no-signature', '-o', str(self.patches), self.base + '..' + self.tip)
        self.files = sorted(self.patches.glob('*.patch'))

    def policy(self, substitutions=None, fingerprints=None):
        file = self.root / 'policy.json'
        file.write_text(json.dumps(dict(source_tip=self.tip, base=self.base,
                                       substitutions=substitutions or [], fingerprints=fingerprints or {})))
        return file

    def verify(self, policy=None, error=None):
        head = self.git('rev-parse', 'HEAD')
        worktrees = self.git('worktree', 'list', '--porcelain')
        created = []
        original = mirror.git

        def observe(repo, *args, **kwargs):
            if args[:2] == ('worktree', 'add'):
                created.append(Path(args[3]))
            return original(repo, *args, **kwargs)

        with patch.object(mirror, 'git', side_effect=observe):
            with self.assertRaisesRegex(mirror.VerificationError, error) if error else contextlib.nullcontext():
                result = mirror.verify(self.repo, self.tip, self.base, self.patches, policy)
                self.assertEqual(result['patches'], len(self.files))
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('worktree', 'list', '--porcelain'), worktrees)
        self.assertTrue(self.sibling.is_dir())
        for worktree in created:
            self.assertFalse(worktree.exists())
            self.assertFalse(worktree.parent.exists())

    def test_faithful_replay_and_cleanup(self):
        self.verify()

    def test_inventory_missing_duplicate_reordered_headers(self):
        originals = [p.read_bytes() for p in self.files]
        cases = [b'not a header\n', originals[0].split(b'\n')[0] + b'\n',
                 originals[1].split(b'\n')[0] + b'\n']
        for header in cases:
            with self.subTest(header=header):
                self.files[0].write_bytes(header + originals[0].split(b'\n', 1)[1])
                # Duplicate case edits the second header, since the first is valid.
                if header == cases[1]:
                    self.files[1].write_bytes(header + originals[1].split(b'\n', 1)[1])
                self.verify(error='header mismatch')
                for path, data in zip(self.files, originals):
                    path.write_bytes(data)

    def test_missing_patch(self):
        self.files[1].unlink()
        self.verify(error='count differs')

    def test_duplicate_and_gap_ordinals(self):
        old = self.files[1]
        for name in ('0001-duplicate.patch', '0003-gap.patch'):
            with self.subTest(name=name):
                renamed = old.with_name(name)
                old.rename(renamed)
                self.verify(error='ordinals')
                renamed.rename(old)

    def test_omitted_merge_only_runtime_resolution(self):
        self.git('switch', '-qc', 'side', self.base)
        self.write('side.py', 'side = True\n')
        self.commit('side')
        self.git('switch', 'main')
        self.git('merge', '--no-ff', '--no-commit', 'side')
        self.write('runtime.py', 'value = 999\n')
        self.tip = self.commit('merge resolution')
        self.export()
        self.verify(error='Unexplained blob divergence')

    def test_deleted_regression_after_successful_am(self):
        # Deliberate public adaptation: patch header still names the right source
        # commit, and git am succeeds, but its diff deletes an unrelated test.
        self.git('rm', 'tests/test_regression.py')
        deletion = self.commit('bad public adaptation')
        diff = mirror.git(self.repo, 'diff', deletion + '^', deletion)
        path = self.files[-1]
        path.write_bytes(path.read_bytes() + b'\n' + diff)
        self.verify(error='Tree path inventory differs')

    def test_privacy_substitution_is_explicit_and_directional(self):
        self.write('second.py', "label = 'original-example'\n")
        self.tip = self.commit('example label')
        self.export()
        path = self.files[-1]
        path.write_bytes(path.read_bytes().replace(b'original-example', b'public-example'))
        self.verify(error='Unexplained blob divergence')
        self.verify(policy=self.policy([['original-example', 'public-example']]))
        self.verify(policy=self.policy([['public-example', 'original-example']]),
                    error='Unexplained blob divergence')

    def test_fingerprint_accepts_only_exact_paired_blobs(self):
        source_blob = self.git('rev-parse', self.tip + ':second.py')
        path = self.files[-1]
        path.write_bytes(path.read_bytes().replace(b'second = True', b'second = (True)'))
        applied_blob = mirror.git(self.repo, 'hash-object', '--stdin', data=b'second = (True)\n').decode().strip()
        policy = self.policy(fingerprints={'second.py': [source_blob, applied_blob]})
        self.verify(policy=policy)
        path.write_bytes(path.read_bytes().replace(b'second = (True)', b'second = False '))
        self.verify(policy=policy, error='Unexplained blob divergence')

    def test_wrong_snapshot_and_unused_fingerprints(self):
        policy = self.policy(fingerprints={'second.py': ['a' * 40, 'b' * 40]})
        self.verify(policy=policy, error='Unused historical fingerprints')
        obj = json.loads(policy.read_text())
        obj['source_tip'] = self.base
        policy.write_text(json.dumps(obj))
        self.verify(policy=policy, error='snapshot mismatch')

    def test_mode_change_is_not_normalized(self):
        path = self.files[-1]
        path.write_bytes(path.read_bytes().replace(b'new file mode 100644', b'new file mode 100755'))
        self.verify(error='Tree mode')

    def test_failed_am_cleanup(self):
        path = self.files[0]
        path.write_bytes(path.read_bytes().replace(b'-value = 0', b'-value = unavailable'))
        self.verify(error='Git operation failed: am')

    def test_cleanup_retries_transient_remove_failure(self):
        original = mirror.git
        attempts = []

        def transient(repo, *args, **kwargs):
            if args[:2] == ('worktree', 'remove'):
                attempts.append(args)
                if len(attempts) == 1:
                    raise mirror.VerificationError('injected transient removal failure')
            return original(repo, *args, **kwargs)

        with patch.object(mirror, 'git', side_effect=transient):
            self.verify()
        self.assertEqual(len(attempts), 2)

    def test_policy_rejects_malformed_or_unbound_rules(self):
        file = self.policy()
        good = json.loads(file.read_text())
        cases = [dict(good, extra=True), dict(good, base=self.one),
                 dict(good, substitutions=[['', 'public']]),
                 dict(good, substitutions=[['same', 'same']]),
                 dict(good, fingerprints={'second.py': ['not-a-hash', 'b' * 40]})]
        for obj in cases:
            with self.subTest(policy=obj):
                file.write_text(json.dumps(obj))
                self.verify(policy=file, error='Invalid policy')
        file.write_text('invalid JSON')
        self.verify(policy=file, error='Invalid policy')

    def test_base_ahead_of_merge_base(self):
        fork_tip = self.tip
        self.git('switch', '-qc', 'upstream', self.base)
        self.write('upstream.py', 'upstream = True\n')
        self.base = self.commit('upstream advanced')
        self.tip = fork_tip
        self.verify()

    def test_persistent_cleanup_failure_preserves_recoverable_worktree(self):
        original = mirror.git
        owned = []
        before = self.git('worktree', 'list', '--porcelain')

        def fail_remove(repo, *args, **kwargs):
            if args[:2] == ('worktree', 'add'):
                owned.append(Path(args[3]))
            if args[:2] == ('worktree', 'remove'):
                raise mirror.VerificationError('injected removal failure')
            return original(repo, *args, **kwargs)

        try:
            with patch.object(mirror, 'git', side_effect=fail_remove):
                with self.assertRaisesRegex(mirror.VerificationError, 'removal failure'):
                    mirror.verify(self.repo, self.tip, self.base, self.patches)
            self.assertEqual(len(owned), 1)
            self.assertTrue(owned[0].is_dir())
            self.assertIn(str(owned[0]), self.git('worktree', 'list', '--porcelain'))
            self.assertTrue(self.sibling.is_dir())
        finally:
            for path in owned:
                original(self.repo, 'worktree', 'remove', '--force', str(path))
                path.parent.rmdir()
        self.assertEqual(before, self.git('worktree', 'list', '--porcelain'))

    def test_cli_success_and_private_diagnostics_are_suppressed(self):
        command = [sys.executable, str(SCRIPT), '--source-repo', str(self.repo),
                   '--source-tip', self.tip, '--base', self.base, '--patch-dir', str(self.patches)]
        success = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)['patches'], 2)
        path = self.files[0]
        path.write_bytes(path.read_bytes().replace(b'-value = 0', b'-value = sensitive-fixture'))
        failure = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(failure.returncode, 1)
        self.assertEqual(failure.stdout, '')
        self.assertEqual(failure.stderr, 'Mirror verification FAILED\n')


if __name__ == '__main__':
    unittest.main()
