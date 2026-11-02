"""Ancestry-only merge replay: stdlib Git fixtures, no network or private data.

Reuses test_verify_mirror's fixture helpers (and its loaded verify_mirror module,
so patch.object targets the same module object) without subclassing MirrorTests,
which would run its 25 tests a second time under discovery.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    'test_verify_mirror_base', Path(__file__).with_name('test_verify_mirror.py'))
assert _spec is not None and _spec.loader is not None
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
mirror, SCRIPT = base.mirror, base.SCRIPT


class AncestryOnlyTests(unittest.TestCase):
    setUp = base.MirrorTests.setUp
    git = base.MirrorTests.git
    write = base.MirrorTests.write
    commit = base.MirrorTests.commit
    bridge_fixture = base.MirrorTests.bridge_fixture
    export = base.MirrorTests.export

    def history(self, merge_edit=None, old_merge=False):
        """An old branch commit retained only by ancestry, then a different current-main port.

        The old patch changes runtime.py from its base value, so it conflicts after
        mainline's own change: a linear replay cannot carry it. The merge keeps the
        first parent's tree (`-s ours`), and the later port is not identical to it.
        """
        self.mainline_base, second = self.base, self.tip
        self.git('switch', '-qc', 'old', self.base)
        extra = []
        if old_merge:
            # A merge inside the old branch, so a bridge boundary at the old patch is valid.
            self.git('switch', '-qc', 'tiny', self.base)
            self.write('tiny.py', 'tiny = True\n')
            extra.append(self.commit('tiny side'))
            self.git('switch', '-q', 'old')
            self.git('merge', '-q', '--no-ff', '-m', 'old side merge', 'tiny')
            self.old_merge = self.git('rev-parse', 'HEAD')
        self.write('runtime.py', 'value = 2\n')
        self.write('feature.py', "label = 'original-example'\n")
        self.old = self.commit('old branch fix')
        self.git('switch', '-q', 'main')
        self.git('merge', '-q', '--no-ff', '--no-commit', '-s', 'ours', 'old')
        if merge_edit:
            self.write(*merge_edit)
        self.merge = self.commit('ancestry-only merge')
        self.write('runtime.py', 'value = 2\n')
        self.write('port.py', 'port = True\n')
        self.port = self.commit('current-main port')
        self.tip = self.port
        for file in self.patches.glob('*.patch'):
            file.unlink()
        for number, sha in enumerate([self.one, second, self.old, self.port, *extra], 1):
            self.git('format-patch', '-q', '--no-signature', '-1', sha, '--start-number', str(number),
                     '-o', str(self.patches))
        self.files = sorted(self.patches.glob('*.patch'))
        self.plan = dict(source_tip=self.tip, base=self.base, start=self.mainline_base, bridges=[],
                         ancestry_only=[dict(patch=sha, source_merge=self.merge) for sha in [self.old, *extra]])
        self.plan_path = self.root / 'replay.json'
        self.save(self.plan)

    def save(self, plan):
        self.plan_path.write_text(json.dumps(plan))

    def run_verify(self, plan=None, policy=None, error=None):
        """Run verify, then assert every worktree it added is gone and nothing else moved."""
        head = self.git('rev-parse', 'HEAD')
        before = self.git('worktree', 'list', '--porcelain')
        added, commits = [], []
        original = mirror.git

        def observe(repo, *args, **kwargs):
            if args[:2] == ('worktree', 'add'):
                added.append(Path(args[3]))
            if args[:2] == ('commit', '--allow-empty'):
                commits.append(args)
            return original(repo, *args, **kwargs)

        result = None
        with patch.object(mirror, 'git', side_effect=observe):
            if error:
                with self.assertRaisesRegex(mirror.VerificationError, error):
                    mirror.verify(self.repo, self.tip, self.base, self.patches, policy,
                                  plan or self.plan_path)
            else:
                result = mirror.verify(self.repo, self.tip, self.base, self.patches, policy,
                                       plan or self.plan_path)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('worktree', 'list', '--porcelain'), before)
        self.assertTrue(self.sibling.is_dir())
        for path in added:
            self.assertFalse(path.exists())
            self.assertFalse(path.parent.exists())
        return result, added, commits

    def test_original_patch_verified_in_its_own_context_and_marked(self):
        self.history()
        # Without the plan entry the old branch patch conflicts in the linear replay.
        with self.assertRaisesRegex(mirror.VerificationError, 'Git operation failed: am'):
            mirror.verify(self.repo, self.tip, self.base, self.patches)
        result, added, commits = self.run_verify()
        self.assertEqual(result['ancestry_only'], 1)
        self.assertEqual(result['bridges'], 0)
        self.assertEqual(result['patches'], 4)
        self.assertEqual(len(added), 2, 'main replay plus one isolated side worktree')
        self.assertEqual([c for c in commits if self.merge in c[-1]][0][:2], ('commit', '--allow-empty'))
        cli = subprocess.run([sys.executable, str(SCRIPT), '--source-repo', str(self.repo),
                              '--source-tip', self.tip, '--base', self.base,
                              '--patch-dir', str(self.patches), '--replay-plan', str(self.plan_path)],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), result)

    def test_side_comparison_uses_only_policy_substitutions(self):
        self.history()
        path = self.files[2]
        path.write_bytes(path.read_bytes().replace(b'original-example', b'public-example'))
        self.run_verify(error='Unexplained blob divergence')
        policy = self.root / 'policy.json'
        policy.write_text(json.dumps(dict(source_tip=self.tip, base=self.base, fingerprints={},
                                          substitutions=[['original-example', 'public-example']])))
        result, _, _ = self.run_verify(policy=policy)
        self.assertEqual(result['ancestry_only'], 1)

    def test_tampered_body_that_still_applies_fails_and_cleans_up(self):
        self.history()
        path = self.files[2]
        path.write_bytes(path.read_bytes().replace(b"+label = 'original-example'", b"+label = 'tampered'"))
        _, added, _ = self.run_verify(error='Unexplained blob divergence')
        self.assertEqual(len(added), 2)

    def test_patch_that_does_not_apply_at_its_parent_cleans_up(self):
        self.history()
        path = self.files[2]
        path.write_bytes(path.read_bytes().replace(b'-value = 0', b'-value = unavailable'))
        _, added, _ = self.run_verify(error='Git operation failed: am')
        self.assertEqual(len(added), 2)

    def test_merge_that_changes_the_tree_is_rejected(self):
        self.history(merge_edit=('merge-only.py', 'resolution = True\n'))
        self.run_verify(error='Invalid replay plan')

    def test_rejects_unrepresentable_or_malformed_entries(self):
        self.history()
        self.git('switch', '-qc', 'stray', self.one)
        self.git('merge', '-q', '--no-ff', '-s', 'ours', '-m', 'stray merge', 'old')
        stray = self.git('rev-parse', 'HEAD')
        self.git('switch', '-q', 'main')
        entry = self.plan['ancestry_only'][0]
        cases = {
            'patch reachable from first parent': [dict(patch=self.one, source_merge=self.merge)],
            'merge not in frozen tip': [dict(entry, source_merge=stray)],
            'source merge is not a merge': [dict(entry, source_merge=self.port)],
            'duplicate patch': [entry, dict(entry)],
            'unlisted patch': [dict(entry, patch=self.merge)],
            'abbreviated patch': [dict(entry, patch=self.old[:12])],
            'abbreviated merge': [dict(entry, source_merge=self.merge[:12])],
            'symbolic merge': [dict(entry, source_merge='main~1')],
            'extra key': [dict(entry, reviewed=True)],
            'empty list': [],
            'not a list': entry,
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                self.save(dict(self.plan, ancestry_only=value))
                self.run_verify(error='Invalid replay plan')
        with self.subTest(case='no bridges and no ancestry entries'):
            plan = copy.deepcopy(self.plan)
            del plan['ancestry_only']
            self.save(plan)
            self.run_verify(error='Invalid replay plan')

    def test_multiple_entries_and_bridge_boundary_overlap(self):
        self.history(old_merge=True)
        result, added, _ = self.run_verify()
        self.assertEqual(result['ancestry_only'], 2)
        self.assertEqual(len(added), 3)
        data = b''
        (self.root / 'empty.diff').write_bytes(data)
        bridge = dict(before=self.old, diff='empty.diff', sha256=hashlib.sha256(data).hexdigest(),
                      source_merge=self.old_merge)
        self.save(dict(self.plan, bridges=[bridge]))
        self.run_verify(error='Invalid replay plan')

    def assert_extra_mail_rejected(self, index):
        self.history()
        path = self.files[index]
        extra = (b'From ' + b'f' * 40 + b' Mon Sep 17 00:00:00 2001\n'
                 b'From: Example <you@example.com>\n'
                 b'Date: Mon, 1 Jan 2024 00:00:00 +0000\n'
                 b'Subject: [PATCH] unlisted empty commit\n\n'
                 b'An extra message must not hide behind a valid first header.\n\n')
        path.write_bytes(path.read_bytes() + b'\n' + extra)
        _, added, _ = self.run_verify(error='Exactly one mail entry')
        self.assertEqual(added, [])

    def test_extra_mail_in_ancestry_patch_rejected(self):
        self.assert_extra_mail_rejected(2)

    def test_extra_mail_in_main_patch_rejected(self):
        self.assert_extra_mail_rejected(3)

    def test_plan_without_ancestry_only_keeps_old_behavior(self):
        path, plan = self.bridge_fixture()
        self.plan_path = path
        result, added, _ = self.run_verify()
        self.assertNotIn('ancestry_only', result)
        self.assertEqual(result['bridges'], 1)
        self.assertEqual(len(added), 1)
        plan['ancestry_only'] = []
        path.write_text(json.dumps(plan))
        self.run_verify(error='Invalid replay plan')


if __name__ == '__main__':
    unittest.main()
