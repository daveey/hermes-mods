"""Fixture tests for core/check_coverage.py (stdlib only; throwaway Git repos)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('check_coverage.py')
A = 'a' * 40
B = 'b' * 40


def header(sha):
    return f'From {sha} Mon Sep 17 00:00:00 2001\nFrom: you <you@example.com>\nSubject: [PATCH] x\n\n'


class CoverageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='coverage-fixture-')
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.git('init', '-q')
        (self.repo / 'core/patches').mkdir(parents=True)
        (self.repo / 'core/feature.md').write_text('# Feature\n')
        (self.repo / 'core/README.md').write_text('| Feature | Patches |\n|---|---|\n'
                                                  '| [feature.md](feature.md) | 0001 0002 |\n')

    def git(self, *args):
        subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)

    def stage(self, patches):
        for name, sha in patches.items():
            (self.repo / 'core/patches' / name).write_text(header(sha))
        self.git('add', 'core')

    def run_check(self):
        return subprocess.run([sys.executable, '-B', str(SCRIPT)], cwd=self.repo, capture_output=True, text=True)

    def test_unique_headers_pass(self):
        self.stage({'0001-one.patch': A, '0002-two.patch': B})
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_header_fails_naming_both_ordinals(self):
        self.stage({'0001-one.patch': A, '0002-two.patch': A})
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate patch source headers', result.stderr)
        self.assertIn(f'ordinals 0001 and 0002 share source header {A}', result.stderr)

    def test_checks_staged_not_worktree_copy(self):
        self.stage({'0001-one.patch': A, '0002-two.patch': A})
        (self.repo / 'core/patches/0002-two.patch').write_text(header(B))  # unstaged fix
        self.assertEqual(self.run_check().returncode, 1)
        self.git('add', 'core')
        self.assertEqual(self.run_check().returncode, 0)

    def test_uncovered_patch_still_fails(self):
        self.stage({'0001-one.patch': A, '0002-two.patch': B, '0003-three.patch': 'c' * 40})
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('patches with no core/README.md row: 0003', result.stderr)
        self.assertNotIn('duplicate', result.stderr)


if __name__ == '__main__':
    unittest.main()
