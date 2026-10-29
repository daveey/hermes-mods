"""Stdlib harness tests, independent of the plugin's core/autouse fixtures."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('fallback_runner', Path(__file__).parents[1] / 'run_tests.py')
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        (self.repo / 'tests/agent').mkdir(parents=True)
        (self.repo / 'tests/agent/test_credential_pool.py').write_text('def test_core(): pass\n')
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test',
                        '-c', 'user.email=test@example.com', 'commit', '-qm', 'fixture'], check=True)
        (self.repo / 'untracked').write_text('must not be copied')

    def assert_clean(self):
        self.assertEqual(list(self.root.glob('fast_rate_limit_tests_*')), [])
        status = subprocess.check_output(['git', '-C', str(self.repo), 'status', '--short', 'tests/'], text=True)
        self.assertEqual(status, '')

    def invoke(self, child, extra=()):
        real_run = subprocess.run

        def run(command, **kwargs):
            if command[0] == 'git':
                return real_run(command, **kwargs)
            core = Path(command[5])
            self.assertNotEqual(core, self.repo)
            self.assertFalse((core / 'untracked').exists())
            self.assertTrue((core / 'tests/agent/fast_rate_limit_tests/source/plugin.yaml').is_file())
            return child(command, **kwargs)

        with patch.object(runner.tempfile, 'tempdir', str(self.root)), patch.object(runner.subprocess, 'run', run):
            return runner.main(['--repo', str(self.repo), *extra])

    def test_success_and_child_exit_codes_cleanup(self):
        for code in (0, 1, 2, 5, -15):
            with self.subTest(code=code):
                self.assertEqual(self.invoke(lambda *a, **k: subprocess.CompletedProcess(a[0], code)), code)
                self.assert_clean()

    def test_child_exception_and_interrupt_cleanup(self):
        for error in (OSError('spawn failure'), KeyboardInterrupt(), SystemExit(3)):
            with self.subTest(error=type(error).__name__):
                def fail(*args, **kwargs):
                    raise error
                with self.assertRaises(type(error)):
                    self.invoke(fail)
                self.assert_clean()

    def test_partial_staging_failure_cleanup(self):
        with patch.object(runner.shutil, 'copy2', side_effect=OSError('copy failure')):
            with self.assertRaises(OSError):
                self.invoke(lambda *a, **k: self.fail('child must not run'))
        self.assert_clean()

    def test_archive_failure_cleanup(self):
        with patch.object(runner.tempfile, 'tempdir', str(self.root)):
            with self.assertRaises(subprocess.CalledProcessError):
                runner.main(['--repo', str(self.root / 'missing')])
        self.assert_clean()

    def test_without_plugin_and_pytest_arguments(self):
        def child(command, **kwargs):
            self.assertEqual(command[7], '1')
            self.assertTrue(command[8].endswith('test_credential_pool_routing.py'))
            self.assertTrue(command[9].endswith('test_long_reset_pool_fallback.py'))
            self.assertEqual(command[10:], ['-x', '-q'])
            return subprocess.CompletedProcess(command, 0)
        self.assertEqual(self.invoke(child, ['--without-plugin', '-x', '-q']), 0)
        self.assert_clean()

    def test_fixture_uses_its_own_source_despite_stale_environment(self):
        import sys
        stage = self.root / 'suite'
        stage.mkdir()
        runner.shutil.copy2(runner.PLUGIN / 'tests/conftest.py', stage / 'conftest.py')
        (stage / 'source').mkdir()
        (stage / 'source/plugin.yaml').write_text('owned source')
        home = self.root / 'home'
        home.mkdir()
        manager = SimpleNamespace(
            discover_and_load=lambda: None, unload=lambda: None,
            _plugins={'fast-rate-limit-fallback': SimpleNamespace(error=None)})
        agent = ModuleType('agent')
        agent.__path__ = []
        helpers = ModuleType('agent.agent_runtime_helpers')
        helpers._recover_rate_limit = SimpleNamespace(_fast_rate_limit_fallback=True)
        agent.agent_runtime_helpers = helpers
        modules = {
            'pytest': SimpleNamespace(fixture=lambda **k: lambda f: f),
            'hermes_constants': SimpleNamespace(get_hermes_home=lambda: home),
            'hermes_cli.plugins': SimpleNamespace(PluginManager=lambda: manager),
            'run_agent': ModuleType('run_agent'), 'agent': agent,
            'agent.agent_runtime_helpers': helpers,
            'agent.turn_recovery': ModuleType('agent.turn_recovery'),
            'agent.turn_api_error': ModuleType('agent.turn_api_error'),
        }
        with patch.dict(sys.modules, modules), patch.dict(runner.os.environ, {
                'FAST_RATE_LIMIT_PLUGIN_SOURCE': str(self.root / 'missing/source'),
                'FAST_RATE_LIMIT_TEST_DISABLE': '0'}):
            spec = importlib.util.spec_from_file_location('staged_fixture', stage / 'conftest.py')
            assert spec is not None and spec.loader is not None
            fixture = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fixture)
            loaded = fixture.loaded_local_plugin(None, None)
            self.assertIs(next(loaded), manager)
            with self.assertRaises(StopIteration):
                next(loaded)
        self.assertEqual((home / 'plugins/fast-rate-limit-fallback/plugin.yaml').read_text(), 'owned source')

    def test_real_child_failure_cleanup(self):
        # Execute the actual worker. This minimal core intentionally lacks
        # bootstrap; the child fails before pytest and the parent still reaps it.
        with patch.object(runner.tempfile, 'tempdir', str(self.root)), patch.object(runner, 'INSTALLED', self.repo):
            self.assertNotEqual(runner.main(['--repo', str(self.repo)]), 0)
        self.assert_clean()


if __name__ == '__main__':
    unittest.main()
