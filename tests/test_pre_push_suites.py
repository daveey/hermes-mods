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


class GateFixture(unittest.TestCase):
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
        # The hook execs `python3`; receipts bind the gate interpreter, so the
        # fixture's python3 must be the one running --prepare (CI's PATH differs).
        shim = self.root / 'bin'
        shim.mkdir()
        (shim / 'python3').symlink_to(sys.executable)
        self.env['PATH'] = f'{shim}{os.pathsep}{self.env.get("PATH", "")}'
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


class PushGate(GateFixture):
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

    def test_prepare_jobs_must_be_a_positive_integer(self):
        command = [sys.executable, str(self.repo / 'scripts/pre-push-suites'), '--prepare', self.base, 'HEAD']
        for bad in ('0', 'many', '-3'):
            result = subprocess.run(command, cwd=self.repo, env=dict(self.env, HERMES_MODS_PREPARE_JOBS=bad),
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 2, bad)
            self.assertIn('HERMES_MODS_PREPARE_JOBS', result.stderr)
        self.test.write_text('def test_contract():\n    assert 3 == 3\n')
        self.commit()
        wide = subprocess.run(command, cwd=self.repo, env=dict(self.env, HERMES_MODS_PREPARE_JOBS='12'),
                              capture_output=True, text=True, timeout=280)
        self.assertEqual(wide.returncode, 0, wide.stdout + wide.stderr)
        self.assertIn('verified local receipt', self.git('push', 'origin', 'HEAD:main').stdout)

    def test_failed_revalidation_removes_earlier_success(self):
        self.test.write_text('import os\ndef test_contract():\n    assert not os.environ.get("FAIL_FIXTURE")\n')
        self.commit()
        command = [sys.executable, str(self.repo / 'scripts/pre-push-suites'), '--prepare', self.base, 'HEAD']
        first = subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        failed = subprocess.run(command, cwd=self.repo, env=dict(self.env, FAIL_FIXTURE='1'), capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(list((self.repo / '.git/pre-push-suites').glob('*.json')))
        pushed = self.git('push', 'origin', 'HEAD:main')
        self.assertNotIn('verified local receipt', pushed.stdout)
        self.assertIn('PASS', pushed.stdout)

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


class DocOnlyPushGate(GateFixture):
    """A docs-only skill publication must not wait for that skill's code suite (live-checkout-guard
    skill_publish has a 45 s budget); any code path, or a suite that reads documents, still runs."""

    def setUp(self):
        super().setUp()
        self.git('config', '--unset', 'core.hooksPath')
        # Both suites fail whenever they run, so a pushed tip proves the suite was not run.
        self.skill = self.repo / 'skills/testing/coded'
        (self.skill / 'scripts').mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('---\nname: coded\n---\nbase\n')
        (self.skill / 'scripts/helper.py').write_text('VALUE = 1\n')
        (self.skill / 'scripts/test_coded.py').write_text(
            'def test_contract():\n    assert False, "coded suite ran"\n')
        self.reader = self.repo / 'skills/testing/reader'
        (self.reader / 'tests').mkdir(parents=True)
        (self.reader / 'SKILL.md').write_text('---\nname: reader\n---\nbase\n')
        (self.reader / 'tests/test_reader.py').write_text(
            'from pathlib import Path\n\ndef test_contract():\n'
            '    text = (Path(__file__).parents[1] / "SKILL.md").read_text()\n'
            '    assert "never" in text, "reader suite ran"\n')
        self.commit()
        self.git('push', 'origin', 'HEAD:main')
        self.base = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('config', 'core.hooksPath', '.githooks')

    def push(self):
        return self.git('push', 'origin', 'HEAD:main', check=False)

    def test_docs_only_edit_of_skill_with_suite_publishes_without_running_it(self):
        (self.skill / 'SKILL.md').write_text('---\nname: coded\n---\nedited\n')
        (self.skill / 'references').mkdir()
        (self.skill / 'references/topic.md').write_text('new reference\n')
        self.commit()
        started = time.monotonic()
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('doc-fast-path: skills/testing/coded', result.stdout)
        self.assertIn('0 suites', result.stdout)
        self.assertNotIn('coded suite ran', result.stdout)
        self.assertEqual(self.tip(), self.git('rev-parse', 'HEAD').stdout.strip())
        self.assertLess(time.monotonic() - started, 45)

    def test_code_path_in_the_same_range_still_runs_the_suite(self):
        (self.skill / 'SKILL.md').write_text('---\nname: coded\n---\nedited\n')
        self.commit()
        (self.skill / 'scripts/helper.py').write_text('VALUE = 2\n')
        self.commit()
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('coded suite ran', result.stdout)
        self.assertEqual(self.tip(), self.base)

    def test_docs_only_edit_still_runs_a_suite_that_reads_documents(self):
        (self.reader / 'SKILL.md').write_text('---\nname: reader\n---\nedited\n')
        self.commit()
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('reader suite ran', result.stdout)
        self.assertEqual(self.tip(), self.base)

    def test_executable_document_keeps_the_full_selection(self):
        (self.skill / 'SKILL.md').chmod(0o755)
        self.commit()
        result = self.push()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('not a regular document', result.stdout)
        self.assertIn('coded suite ran', result.stdout)
        self.assertEqual(self.tip(), self.base)


class DocOnlyPaths(unittest.TestCase):
    def setUp(self):
        self.mod = runpy.run_path(str(ROOT / 'scripts/ci-plugin-suites'))

    def test_only_skill_markdown_documents_qualify(self):
        doc_only = self.mod['doc_only']
        for path in ('skills/cat/n/SKILL.md', 'skills/cat/n/references/a.md',
                     'skills/cat/n/templates/deep/b.md', 'skills/cat/n/assets/c.md', 'skills/n/references/d.md'):
            self.assertTrue(doc_only(path), path)
        for path in ('skills/cat/n/scripts/SKILL.md', 'skills/cat/n/tests/references/a.md',
                     'skills/cat/n/references/test_doctest.md', 'skills/cat/n/references/conftest.md',
                     'skills/cat/n/references/a.py', 'skills/cat/n/references/a.json',
                     'skills/cat/n/references/a.txt', 'skills/cat/n/lifecycle.md', 'skills/cat/n/skill.MD',
                     'skills/cat/n/.hidden/SKILL.md', 'plugins/p/SKILL.md', 'plugins/p/references/a.md',
                     'scripts/pre-push-suites', 'skills/cat/n/plugins/references/a.md'):
            self.assertFalse(doc_only(path), path)

    def test_ci_selection_never_uses_the_fast_path(self):
        workflow = (ROOT / '.github/workflows/plugin-suites.yml').read_text()
        self.assertNotIn('--doc-fast-path', workflow)
        self.assertIn('--doc-fast-path', (ROOT / 'scripts/pre-push-suites').read_text())


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
