"""Offline regression coverage for the snapshot-bound 3529 overlay."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import repair_hostname_escapes as original
import repair_hostname_escapes_3529 as repair
import verify_mirror as mirror


class SnapshotEscapeTests(unittest.TestCase):
    def setUp(self):
        self.before = (br'DEFAULT_LOCAL_MARKERS = (r"\mini-1\b",)' + b'\n'
                       + br'TITLE_ONLY_LOCAL_MARKERS = {r"\mini-1\b"}' + b'\n'
                       + br'BODY_LOCAL_MARKERS = (r"\mini-1\b",)' + b'\n')
        self.after = self.before.replace(br'\mini-1\b', br'\bmini-1\b')
        self.snapshot_pins = (repair.BEFORE, repair.AFTER)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(repair, 'BEFORE', original.blob_id(self.before)))
        self.stack.enter_context(patch.object(repair, 'AFTER', original.blob_id(self.after)))

    def test_snapshot_constants_and_real_policy_binding(self):
        # The fixtures above are synthetic, but the shipped snapshot pins are not.
        self.assertEqual(self.snapshot_pins, ('c0c1c2ef196920aba7cc33442f6b05c730e37f6e',
                                              '36a08cc24975150f7cc91181bc012a00b1edc126'))
        with patch.object(repair, 'BEFORE', 'c0c1c2ef196920aba7cc33442f6b05c730e37f6e'), \
                patch.object(repair, 'AFTER', '36a08cc24975150f7cc91181bc012a00b1edc126'):
            policy = repair.checked_policy()
            self.assertEqual(policy['fingerprints'][original.TARGET][1], repair.AFTER)

    def test_valid_repair_and_wrong_or_repeated_preimage(self):
        self.assertEqual(repair.repair_bytes(self.before), self.after)
        original.validate_markers(self.after)
        for data in (self.before + b' ', self.after):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'preimage'):
                repair.repair_bytes(data)

    def test_marker_count_and_postimage_guards(self):
        for count in (2, 4):
            data = self.before.splitlines(keepends=True)[0] * count
            with self.subTest(count=count), patch.object(repair, 'BEFORE', original.blob_id(data)):
                with self.assertRaisesRegex(ValueError, 'marker count'):
                    repair.repair_bytes(data)
        with patch.object(repair, 'AFTER', '0' * 40):
            with self.assertRaisesRegex(ValueError, 'postimage'):
                repair.repair_bytes(self.before)

    def test_marker_validator_is_required(self):
        with patch.object(original, 'validate_markers', side_effect=ValueError('invalid marker')) as validate:
            with self.assertRaisesRegex(ValueError, 'invalid marker'):
                repair.repair_bytes(self.before)
            validate.assert_called_once_with(self.after)

    def test_policy_only_allows_target_applied_fingerprint_change(self):
        source = {'source_tip': 'a' * 40, 'base': 'b' * 40,
                  'fingerprints': {original.TARGET: ['c' * 40, repair.BEFORE]},
                  'substitutions': []}
        repaired = copy.deepcopy(source)
        repaired['fingerprints'][original.TARGET][1] = repair.AFTER
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(left, right):
                (root / 'mirror-policy-3529.json').write_text(json.dumps(left))
                (root / 'mirror-policy-3529-escape-repair.json').write_text(json.dumps(right))
            write(source, repaired)
            with patch.object(repair, 'HERE', root):
                self.assertEqual(repair.checked_policy(), repaired)
                for field in ('source_tip', 'base', 'substitutions', 'fingerprints'):
                    altered = copy.deepcopy(repaired)
                    if field == 'fingerprints':
                        altered[field][original.TARGET][0] = 'd' * 40
                    else:
                        altered[field] = ['unauthorized']
                    write(source, altered)
                    with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'only the overlay'):
                        repair.checked_policy()
                wrong = copy.deepcopy(source)
                wrong['fingerprints'][original.TARGET][1] = '0' * 40
                write(wrong, repaired)
                with self.assertRaisesRegex(ValueError, 'preimage binding'):
                    repair.checked_policy()

    def test_tree_cli_repairs_once_and_refuses_symlink_without_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / original.TARGET
            target.parent.mkdir(parents=True)
            target.write_bytes(self.before)
            with patch.object(repair, 'checked_policy', return_value={}), \
                    patch.object(sys, 'argv', ['repair', '--tree', directory]), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                repair.main()
                self.assertEqual(target.read_bytes(), self.after)
                with self.assertRaises(SystemExit) as rerun:
                    repair.main()
                self.assertEqual(rerun.exception.code, 2)
                self.assertEqual(target.read_bytes(), self.after)
                target.unlink()
                other = root / 'other.py'
                other.write_bytes(self.before)
                target.symlink_to(other)
                with self.assertRaises(SystemExit) as symlink:
                    repair.main()
                self.assertEqual(symlink.exception.code, 2)
                self.assertTrue(target.is_symlink())
                self.assertEqual(other.read_bytes(), self.before)

    def test_real_git_tree_check_rejects_unrelated_divergence(self):
        # No source checkout, network, commits or user Git identity required.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()
            mirror.git(repo, 'init', '-q')
            target = repo / original.TARGET
            target.parent.mkdir(parents=True)
            target.write_bytes(self.before)
            other = repo / 'unrelated.txt'
            other.write_text('unchanged\n')
            mirror.git(repo, 'add', '.')
            source = mirror.git(repo, 'write-tree').decode().strip()
            target.write_bytes(self.after)
            mirror.git(repo, 'add', '.')
            applied = mirror.git(repo, 'write-tree').decode().strip()
            policy = dict(source_tip=source, base=source, substitutions=[],
                          fingerprints={original.TARGET: [repair.BEFORE, repair.BEFORE]})
            (root / 'mirror-policy-3529.json').write_text(json.dumps(policy))
            policy['fingerprints'][original.TARGET][1] = repair.AFTER
            (root / 'mirror-policy-3529-escape-repair.json').write_text(json.dumps(policy))
            with patch.object(repair, 'HERE', root):
                self.assertEqual(repair.check(repo, applied)['whole_tree'], 'pass')
                other.write_text('unauthorized change\n')
                mirror.git(repo, 'add', '.')
                wrong = mirror.git(repo, 'write-tree').decode().strip()
                with self.assertRaisesRegex(mirror.VerificationError, 'Unexplained blob divergence'):
                    repair.check(repo, wrong)
                with self.assertRaisesRegex(mirror.VerificationError, 'Unused historical fingerprints'):
                    repair.check(repo, source)

    def test_check_requires_whole_tree_and_overlay_postimage(self):
        policy = {'source_tip': 'a' * 40, 'base': 'b' * 40}
        with patch.object(repair, 'checked_policy', return_value=policy), \
                patch.object(mirror, 'load_policy', return_value=('rules', 'prints', 'renames')) as load, \
                patch.object(mirror, 'compare') as compare, \
                patch.object(mirror, 'git', return_value=self.after) as git:
            result = repair.check('repo', 'applied')
            self.assertEqual(result, dict(source_tip=policy['source_tip'], overlay_blob=repair.AFTER,
                                          whole_tree='pass', marker_compile='pass'))
            load.assert_called_once_with(repair.HERE / 'mirror-policy-3529-escape-repair.json',
                                         policy['source_tip'], policy['base'])
            compare.assert_called_once_with('repo', policy['source_tip'], 'applied', 'rules', 'prints', 'renames')
            git.assert_called_once_with('repo', 'show', 'applied:' + original.TARGET)
            git.return_value = self.before
            with self.assertRaisesRegex(ValueError, 'postimage not present'):
                repair.check('repo', 'applied')
            git.return_value = self.after
            compare.side_effect = mirror.VerificationError('wrong applied tree')
            git.reset_mock()
            with self.assertRaisesRegex(mirror.VerificationError, 'wrong applied tree'):
                repair.check('repo', 'applied')
            git.assert_not_called()


if __name__ == '__main__':
    unittest.main()
