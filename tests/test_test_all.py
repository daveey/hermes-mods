"""scripts/test-all: one broken plugin is reported by name, never swallowed, and the exit code says so."""
import os
import runpy
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "test-all"


def make_tree(root: Path, plugins: dict[str, str]) -> None:
    for name, body in plugins.items():
        d = root / "plugins" / name
        d.mkdir(parents=True)
        # Same basename in every plugin: a single-process run would collide on it.
        (d / "test_load.py").write_text(textwrap.dedent(body))


def run(root: Path, *args: str):
    env = {k: v for k, v in os.environ.items() if k != "HERMES_CORE_CHECKOUT"}
    env["HERMES_MODS_PYTHON"] = sys.executable
    return subprocess.run([sys.executable, str(RUNNER), "--root", str(root), "--hermes-core", "/nonexistent", *args],
                          capture_output=True, text=True, env=env, timeout=120)


class TestAll(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_session_outcome_reports_real_core_results_even_on_success(self):
        make_tree(self.root, {"session-outcome": """
            import os
            def test_real_core_visible():
                assert os.environ['HERMES_DISABLE_LAZY_INSTALLS'] == '1'
                assert os.environ['HERMES_CORE_CHECKOUT']
        """})
        # More than the usual 40-line failure tail: don't truncate the early integration result.
        with (self.root / 'plugins/session-outcome/test_load.py').open('a') as f:
            for i in range(45):
                f.write(f'\ndef test_extra_{i}():\n    pass\n')
        core = self.root / 'core'
        core.mkdir()
        (core / 'hermes_bootstrap.py').touch()
        r = run(self.root, '-k', 'session-outcome', '--hermes-core', str(core))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('test_real_core_visible PASSED', r.stdout)
        self.assertIn('46 passed', r.stdout)

    def test_same_named_test_modules_do_not_collide_and_all_pass(self):
        make_tree(self.root, {"a": "def test_a():\n    assert True\n", "b": "def test_b():\n    assert True\n"})
        r = run(self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 PASS", r.stdout)

    def test_collection_error_in_one_plugin_is_reported_by_name_and_fails_the_run(self):
        make_tree(self.root, {"good": "def test_ok():\n    assert True\n",
                              "broken": "import module_that_does_not_exist\n\ndef test_x():\n    pass\n"})
        r = run(self.root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"FAIL\s+plugins/broken")
        self.assertRegex(r.stdout, r"PASS\s+plugins/good")
        self.assertIn("module_that_does_not_exist", r.stdout)
        self.assertIn("FAILED: plugins/broken", r.stdout)

    def test_process_that_dies_during_collection_is_labelled_silent_exit_not_swallowed(self):
        # The real incident: a test module re-exec'd/exited the interpreter at import, so pytest
        # printed nothing and exited 1.
        make_tree(self.root, {"dies": "import os\nos._exit(1)\n", "good": "def test_ok():\n    pass\n"})
        r = run(self.root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"SILENT-EXIT\s+plugins/dies")
        self.assertIn("exit 1 with no pytest summary", r.stdout)

    def test_hanging_suite_times_out_and_is_reported(self):
        make_tree(self.root, {"hang": "import time\ntime.sleep(60)\n"})
        r = run(self.root, "--timeout", "3")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"TIMEOUT\s+plugins/hang")

    def test_integration_f3_is_skipped_with_reason_without_a_core_checkout(self):
        f3 = self.root / "tests" / "integration_f3"
        f3.mkdir(parents=True)
        (f3 / "test_f3.py").write_text("def test_f3():\n    raise SystemExit('needs core')\n")
        (self.root / "tests" / "test_top.py").write_text("def test_top():\n    pass\n")
        runner = runpy.run_path(str(RUNNER))
        suite = next(s for s in runner['discover'](self.root, None, False)
                     if s['name'] == 'tests/integration_f3')
        result = runner['run_suite'](suite, sys.executable, None, 3, self.root)
        self.assertEqual(result['status'], 'SKIP')
        self.assertIn('needs a core checkout', result['reason'])
        env = dict(os.environ, HOME=str(self.root), PATH='', HERMES_MODS_PYTHON=sys.executable)
        env.pop('HERMES_CORE_CHECKOUT', None)
        r = subprocess.run([sys.executable, str(RUNNER), '--root', str(self.root),
                            '-k', 'integration_f3'], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('skipped tests/integration_f3: needs a core checkout', r.stdout)

    def test_python_selection_order_with_fake_paths(self):
        runner = runpy.run_path(str(RUNNER))
        core = self.root / 'core'
        venv = core / '.venv/bin/python'
        production = self.root / 'Applications/Hermes Python.app/Contents/MacOS/python3.14'
        override = self.root / 'override-python'
        for p in (venv, production, override):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        with patch('pathlib.Path.home', return_value=self.root), patch('sys.platform', 'darwin'), \
                patch.dict(os.environ, {'HERMES_MODS_PYTHON': str(override)}):
            self.assertEqual(runner['select_python'](core), (str(override), False))
            override.unlink()  # a missing override is not a candidate
            self.assertEqual(runner['select_python'](core), (str(venv), False))
            os.environ.pop('HERMES_MODS_PYTHON')
            venv.unlink()
            self.assertEqual(runner['select_python'](core), (str(production), False))
            production.unlink()
            self.assertEqual(runner['select_python'](core), (sys.executable, True))

    def test_linux_uses_gateway_launcher_resolution(self):
        runner = runpy.run_path(str(RUNNER))
        core = self.root / 'core'
        launcher = core / '.hermes/bin/hermes'
        launcher.parent.mkdir(parents=True)
        launcher.touch()
        production = self.root / 'managed-python'
        production.touch()
        import json
        with patch('sys.platform', 'linux'), patch.dict(os.environ, {'HERMES_MODS_PYTHON': ''}), \
                patch('subprocess.check_output', return_value=json.dumps([str(production), '-I'])) as command:
            self.assertEqual(runner['select_python'](core), (str(production), False))
            command.assert_called_once_with([str(launcher), '--print-runtime-command'], text=True, timeout=30)
        with patch('sys.platform', 'linux'), patch('subprocess.check_output', return_value='invalid json'):
            self.assertIsNone(runner['production_python'](core))

    def test_fallback_warning_in_header_and_final_summary(self):
        import contextlib
        import io
        runner = runpy.run_path(str(RUNNER))
        make_tree(self.root, {'ok': 'def test_ok(): pass'})
        main = runner['main']
        output = io.StringIO()
        for fallback in (True, False):
            output.seek(0)
            output.truncate()
            with patch.dict(main.__globals__, {
                'select_python': lambda core: ('/fake/python', fallback),
                'python_version': lambda py: '3.14.9',
                'ensure_test_site': lambda py: self.root,
                'run_suite': lambda *args: dict(name='plugins/ok', status='PASS', secs=0, reason=''),
            }), contextlib.redirect_stdout(output):
                self.assertEqual(main(['--root', str(self.root)]), 0)
            lines = output.getvalue().splitlines()
            self.assertIn('python=/fake/python (3.14.9)', lines[0])
            warning = 'NOT the production interpreter (3.14.9)'
            self.assertEqual(lines.count(warning), 2 if fallback else 0)
            if fallback:
                self.assertEqual(lines[1], warning)
                self.assertEqual(lines[-1], warning)

    def test_test_site_is_versioned_and_installed_for_selected_python(self):
        runner = runpy.run_path(str(RUNNER))
        ensure = runner['ensure_test_site']
        with patch.dict(ensure.__globals__, {'TEST_SITE': self.root, 'python_version': lambda py: '3.14.9'}), \
                patch('shutil.which', return_value='/bin/uv'), patch('subprocess.run') as install:
            site = ensure('/fake/python with spaces')
            self.assertEqual(site, self.root / 'python-3.14')
            install.assert_called_once_with(
                ['/bin/uv', 'pip', 'install', '-q', '--python', '/fake/python with spaces',
                 '--target', str(site), 'pytest', 'pytest-asyncio', 'pyyaml', 'fastapi', 'httpx', 'psutil'], check=True)
            for name in ('pytest', 'pytest_asyncio', 'yaml', 'fastapi', 'httpx', 'psutil'):
                (site / name).mkdir(parents=True)
            ensure('/fake/python with spaces')
            self.assertEqual(install.call_count, 1)

    def test_test_site_finds_user_uv_or_reports_missing_installer(self):
        runner = runpy.run_path(str(RUNNER))
        ensure = runner['ensure_test_site']
        uv = self.root / '.local/bin/uv'
        uv.parent.mkdir(parents=True)
        uv.touch()
        with patch.dict(ensure.__globals__, {'TEST_SITE': self.root / 'cache', 'python_version': lambda py: '3.14.9'}), \
                patch('pathlib.Path.home', return_value=self.root), patch('shutil.which', return_value=None), \
                patch('subprocess.run') as install:
            ensure('/fake/python')
            self.assertEqual(install.call_args.args[0][0], str(uv))
            uv.unlink()
            with self.assertRaisesRegex(RuntimeError, 'uv is required'):
                ensure('/fake/python')

    def test_shell_override_quotes_interpreter_with_spaces(self):
        runner = runpy.run_path(str(RUNNER))
        py = self.root / 'Python App/python'
        py.parent.mkdir()
        py.symlink_to(sys.executable)
        suite = dict(name='shell', cmd=['sh', '-ec', '{py} -c "print(123)"'])
        result = runner['run_suite'](suite, str(py), None, 10, self.root, self.root)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['tail'], '123')

    def test_isolated_override_does_not_inherit_test_site(self):
        runner = runpy.run_path(str(RUNNER))
        suite = dict(name='isolated', no_core_path=True,
                     cmd=[sys.executable, '-c', 'import os; print(os.environ["PYTHONPATH"])'])
        result = runner['run_suite'](suite, sys.executable, self.root / 'core', 10,
                                     self.root, self.root / 'test-site')
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['tail'], str(self.root))

    def test_default_core_checkout_is_discovered(self):
        runner = runpy.run_path(str(RUNNER))
        core = self.root / '.hermes' / 'hermes-agent'
        core.mkdir(parents=True)
        (core / 'hermes_bootstrap.py').touch()
        with patch('pathlib.Path.home', return_value=self.root), patch.dict(os.environ):
            os.environ.pop('HERMES_CORE_CHECKOUT', None)
            found, explicit = runner['find_core'](None)
        self.assertEqual(found, core.resolve())
        self.assertFalse(explicit)
        (self.root / 'tests' / 'integration_f3').mkdir(parents=True)
        suite = next(s for s in runner['discover'](self.root, found, explicit)
                     if s['name'] == 'tests/integration_f3')
        self.assertNotIn('skip', suite)

    def test_integration_f3_runs_with_implicitly_discovered_core(self):
        f3 = self.root / 'tests' / 'integration_f3'
        f3.mkdir(parents=True)
        runner = runpy.run_path(str(RUNNER))
        core = self.root / 'core'
        suite = next(s for s in runner['discover'](self.root, core, False)
                     if s['name'] == 'tests/integration_f3')
        self.assertNotIn('skip', suite)
        self.assertEqual(suite['args'], ['--hermes-core', str(core), '--confcutdir', str(f3)])
        # Real subprocess: no accidental parent conftest, and both CLI/env core selection work.
        (self.root / 'conftest.py').write_text("raise RuntimeError('parent conftest leaked')\n")
        (core).mkdir()
        (core / 'hermes_bootstrap.py').touch()
        (f3 / 'conftest.py').write_text(
            "def pytest_addoption(parser):\n    parser.addoption('--hermes-core')\n")
        (f3 / 'test_f3.py').write_text(
            "def test_core(request):\n    assert request.config.getoption('--hermes-core')\n")
        for use_env in (False, True):
            env = dict(os.environ, HERMES_MODS_PYTHON=sys.executable)
            env.pop('HERMES_CORE_CHECKOUT', None)
            args = []
            if use_env:
                env['HERMES_CORE_CHECKOUT'] = str(core)
            else:
                args = ['--hermes-core', str(core)]
            r = subprocess.run([sys.executable, str(RUNNER), '--root', str(self.root),
                                '-k', 'integration_f3', *args], env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('1 PASS', r.stdout)
            self.assertNotIn('SKIP', r.stdout)


if __name__ == "__main__":
    unittest.main()
