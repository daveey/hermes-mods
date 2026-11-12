import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import tempfile
import sys

import repair_hostname_escapes as repair
import verify_mirror as mirror
import check_hostname_escape_repair as checker

HERE = Path(__file__).resolve().parent


class EscapeTests(unittest.TestCase):
    def setUp(self):
        policy = HERE / 'mirror-policy-2253.json'
        obj = json.loads(policy.read_text())
        self.rules, _, _ = mirror.load_policy(policy, obj['source_tip'], obj['base'])

    def test_word_boundary_normalization_and_semantics(self):
        original = br'\bsams-mac-mini\b'
        # This captures the exact inherited failure, not merely a synthetic typo.
        self.assertEqual(mirror.normalize(original, self.rules), br'\mini-1\b')
        fixed = repair.normalize_regex(original, self.rules)
        self.assertEqual(fixed, br'\bmini-1\b')
        pattern = re.compile(fixed)
        self.assertIsNotNone(pattern.search(b'on mini-1 now'))
        self.assertIsNone(pattern.search(b'on xmini-1 now'))
        self.assertIsNone(pattern.search(b'on mini-10 now'))

    def test_case_suffix_serialized_escapes_and_idempotence(self):
        for prefix in (br'\b', br'\B', br'\\b'):
            for host, public in ((b'sams-mac-mini', b'mini-1'),
                                 (b'SAMS-MAC-MINI', b'mini-1'),
                                 (b'Alex-MBP', b'laptop-1'),
                                 (b'MacBook-Pro-42', b'laptop-2')):
                with self.subTest(prefix=prefix, host=host):
                    expected = prefix + public + br'.local\b'
                    actual = repair.normalize_regex(prefix + host + br'.local\b', self.rules)
                    self.assertEqual(actual, expected)
                    self.assertEqual(repair.normalize_regex(actual, self.rules), expected)

    def test_guard_rejects_corrupt_marker(self):
        with self.assertRaises((ValueError, re.error)):
            repair.validate_markers(br'DEFAULT_LOCAL_MARKERS = (r"\mini-1\b",)')

    def test_overlay_and_cli_fail_closed(self):
        before = (br'DEFAULT_LOCAL_MARKERS = (r"\mini-1\b",)' + b'\n'
                  + br'TITLE_ONLY_LOCAL_MARKERS = {r"\mini-1\b"}' + b'\n')
        after = before.replace(br'\mini-1', br'\bmini-1')
        with patch.object(repair, 'BEFORE', repair.blob_id(before)), \
                patch.object(repair, 'AFTER', repair.blob_id(after)):
            self.assertEqual(repair.repair_bytes(before), after)
            for wrong in (before + b' ', after):
                with self.assertRaisesRegex(ValueError, 'preimage'):
                    repair.repair_bytes(wrong)
            with patch.object(repair, 'AFTER', '0' * 40):
                with self.assertRaisesRegex(ValueError, 'postimage'):
                    repair.repair_bytes(before)
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / repair.TARGET
                target.parent.mkdir()
                target.write_bytes(before)
                with patch.object(sys, 'argv', ['repair', directory]):
                    repair.main()
                    self.assertEqual(target.read_bytes(), after)
                    with self.assertRaises(SystemExit):
                        repair.main()
                    target.unlink()
                    other = Path(directory) / 'other.py'
                    other.write_bytes(before)
                    target.symlink_to(other)
                    with self.assertRaises(SystemExit):
                        repair.main()
                    self.assertEqual(other.read_bytes(), before)

    def test_occurrence_guard(self):
        before = br'DEFAULT_LOCAL_MARKERS = (r"\mini-1\b",)'
        with patch.object(repair, 'BEFORE', repair.blob_id(before)):
            with self.assertRaisesRegex(ValueError, 'occurrence'):
                repair.repair_bytes(before)

    def test_named_markers_and_missing_table(self):
        repair.validate_markers(b'NAMED = "valid"\nDEFAULT_LOCAL_MARKERS = (NAMED,)')
        with self.assertRaisesRegex(ValueError, 'Missing'):
            repair.validate_markers(b'x = 1')

    def test_composed_named_markers_are_folded_and_compiled(self):
        # 2678: a table entry built from names with ``+`` is folded from the AST, never executed.
        repair.validate_markers(b'A = r"\\bx"\nB = (A + "|" + r"y\\b")\nDEFAULT_LOCAL_MARKERS = (B, A)')
        with self.assertRaises(re.error):
            repair.validate_markers(b'A = r"\\m"\nB = A + "x"\nDEFAULT_LOCAL_MARKERS = (B,)')
        with self.assertRaises(KeyError):
            repair.validate_markers(b'B = f() + "x"\nDEFAULT_LOCAL_MARKERS = (B,)')

    def test_repair_policy_delta_is_exact(self):
        checker.checked_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('mirror-policy-2253.json', 'mirror-policy-2253-escape-repair.json'):
                (root / name).write_bytes((HERE / name).read_bytes())
            with patch.object(checker, 'HERE', root):
                checker.checked_policy()
                path = root / 'mirror-policy-2253-escape-repair.json'
                obj = json.loads(path.read_text())
                obj['fingerprints']['another.py'] = ['0' * 40, '1' * 40]
                path.write_text(json.dumps(obj))
                with self.assertRaisesRegex(ValueError, 'only the exact'):
                    checker.checked_policy()

    def test_plain_hosts_and_protocol_literals(self):
        original = b'sams-mac-mini.local Alex-MBP hermes-hub worker-1'
        expected = b'mini-1.local laptop-1 hermes-hub worker-1'
        self.assertEqual(repair.normalize_regex(original, self.rules), expected)


if __name__ == '__main__':
    unittest.main()
