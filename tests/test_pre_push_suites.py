"""Real Git publication tests; no network and no fake test-all success."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import json
import time
import runpy

ROOT = Path(__file__).resolve().parents[1]


class PushGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='push-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.remote = self.root / 'remote.git'
        self.env = dict(os.environ, GIT_AUTHOR_NAME='fixture', GIT_AUTHOR_EMAIL='fixture@example.com',
                        GIT_COMMITTER_NAME='fixture', GIT_COMMITTER_EMAIL='fixture@example.com')
        self.env.pop('HERMES_MODS_SKIP_PUSH_SUITES', None)
        self.env['HERMES_MODS_PYTHON'] = sys.executable
        self.git('init', '--bare', str(self.remote), cwd=self.root)
        self.git('init', '-b', 'main', str(self.repo), cwd=self.root)
        for file in ('.githooks/pre-push', 'scripts/pre-push-suites',
                     'scripts/ci-plugin-suites', 'scripts/test-all'):
            dest = self.repo / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / file, dest)
        self.test = self.repo / 'plugins/example/test_example.py'
        self.test.parent.mkdir(parents=True)
        self.test.write_text('def test_contract():\n    assert True\n')
        self.commit()
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', 'origin', 'HEAD:main')
        self.base = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('config', 'core.hooksPath', '.githooks')

    def git(self, *args, cwd=None, check=True, env=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, env=env or self.env,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              check=check, timeout=280)

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')

    def tip(self):
        return self.git('--git-dir', str(self.remote), 'rev-parse', 'main').stdout.strip()

    def test_failed_push_stays_local_even_if_dirty_tree_repairs_it(self):
        self.test.write_text('def test_contract():\n    assert False, "contract failure"\n')
        self.commit()
        self.test.write_text('def test_contract():\n    assert True\n')
        result = self.git('push', 'origin', 'HEAD:main', check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('contract failure', result.stdout)
        self.assertEqual(self.tip(), self.base)

    def test_non_head_good_revision_accepted_even_if_head_is_bad(self):
        self.test.write_text('def test_contract():\n    assert 1 == 1\n')
        self.commit()
        good = self.git('rev-parse', 'HEAD').stdout.strip()
        self.test.write_text('def test_contract():\n    assert False\n')
        self.commit()
        result = self.git('push', 'origin', good + ':main')
        self.assertIn('PASS', result.stdout)
        self.assertEqual(self.tip(), good)

    def test_logged_emergency_escape(self):
        self.test.write_text('def test_contract():\n    assert False\n')
        self.commit()
        env = dict(self.env, HERMES_MODS_SKIP_PUSH_SUITES='emergency fixture')
        result = self.git('push', 'origin', 'HEAD:main', env=env)
        self.assertIn('EMERGENCY BYPASS', result.stdout)
        self.assertEqual(self.tip(), self.git('rev-parse', 'HEAD').stdout.strip())

    def test_git_environment_and_snapshot_metadata_are_isolated(self):
        self.test.write_text('''import os, subprocess
from pathlib import Path

def test_contract():
    assert not any(k in os.environ for k in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'))
    subprocess.run(['git', 'update-ref', 'refs/heads/fixture-write', 'HEAD'], check=True)
    assert subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip() == ''
''')
        # A real Git write in the suite must stay in disposable metadata.
        (self.repo / 'expected-head-parent').write_text(self.base)
        self.commit()
        env = dict(self.env, GIT_DIR=str(self.repo / '.git'), GIT_WORK_TREE=str(self.repo),
                   GIT_INDEX_FILE=str(self.repo / '.git/index'))
        index = (self.repo / '.git/index').read_bytes()
        self.git('push', 'origin', 'HEAD:main', env=env)
        self.assertNotEqual(self.git('rev-parse', '--verify', 'refs/heads/fixture-write', check=False).returncode, 0)
        self.assertEqual((self.repo / '.git/index').read_bytes(), index)

    def test_prepared_exact_range_reuses_success_within_push_budget(self):
        self.test.write_text('def test_contract():\n    assert 2 == 2\n')
        self.commit()
        prepared = subprocess.run([sys.executable, str(self.repo / 'scripts/pre-push-suites'),
                                   '--prepare', self.base, 'HEAD'], cwd=self.repo, env=self.env,
                                  capture_output=True, text=True, timeout=280)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertIn('PASS', prepared.stdout)
        result = self.git('push', 'origin', 'HEAD:main')
        self.assertIn('verified local receipt', result.stdout)
        self.assertNotIn('test-all:', result.stdout)
        self.assertEqual(self.tip(), self.git('rev-parse', 'HEAD').stdout.strip())

    def test_changed_gate_invalidates_prepared_success(self):
        self.test.write_text('def test_contract():\n    assert 2 == 2\n')
        self.commit()
        prepared = subprocess.run([sys.executable, str(self.repo / 'scripts/pre-push-suites'),
                                   '--prepare', self.base, 'HEAD'], cwd=self.repo, env=self.env,
                                  capture_output=True, text=True, timeout=280)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        runner = self.repo / 'scripts/test-all'
        runner.write_text(runner.read_text() + '\n# changed gate\n')
        result = self.git('push', 'origin', 'HEAD:main')
        self.assertNotIn('verified local receipt', result.stdout)
        self.assertIn('PASS', result.stdout)

    def test_other_branch_is_not_main_publication(self):
        self.test.write_text('def test_contract():\n    assert False\n')
        self.commit()
        self.git('push', 'origin', 'HEAD:topic')
        self.assertEqual(self.tip(), self.base)


class GateEdges(unittest.TestCase):
    def setUp(self):
        self.gate = runpy.run_path(str(ROOT / 'scripts/pre-push-suites'))

    def test_receipt_requires_success_and_recent_nonfuture_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'receipt.json'
            for data in ({}, {'success': False, 'finished': time.time()},
                         {'success': True, 'finished': time.time() - 3601},
                         {'success': True, 'finished': time.time() + 10}):
                path.write_text(json.dumps(data))
                self.assertFalse(self.gate['has_receipt'](path), data)
            path.write_text('{broken')
            self.assertFalse(self.gate['has_receipt'](path))
            path.write_text(json.dumps({'success': True, 'finished': time.time()}))
            self.assertTrue(self.gate['has_receipt'](path))

    def test_deadline_refuses_and_kills_slow_command(self):
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.gate['run']([sys.executable, '-c', 'import time; time.sleep(10)'], start + .1)
        self.assertLess(time.monotonic() - start, 3)


if __name__ == '__main__':
    unittest.main()
