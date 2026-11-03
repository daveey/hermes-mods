"""scripts/test-all: one broken plugin is reported by name, never swallowed, and the exit code says so."""
import json
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


def _old_python():
    """A python3 older than 3.11 (no tomllib), e.g. macOS's /usr/bin/python3, or None."""
    for cand in ("/usr/bin/python3",):
        try:
            if Path(cand).is_file() and subprocess.run(
                    [cand, "-c", "import sys; sys.exit(0 if sys.version_info < (3, 11) else 1)"],
                    capture_output=True, timeout=30).returncode == 0:
                return cand
        except (OSError, subprocess.SubprocessError):
            pass
    return None


OLD_PYTHON = _old_python()


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

    @unittest.skipUnless(OLD_PYTHON, "needs a python3 older than 3.11 on this host (macOS /usr/bin/python3)")
    def test_driver_started_by_an_old_python3_reruns_itself_under_a_newer_one(self):
        # `scripts/test-all` runs under `#!/usr/bin/env python3`; on macOS that is often 3.9, which has
        # no tomllib, so the driver died on import before running any suite.
        make_tree(self.root, {"alpha": "def test_ok():\n    pass\n"})
        env = {k: v for k, v in os.environ.items() if k not in ("HERMES_CORE_CHECKOUT", "HERMES_TEST_ALL_REEXEC")}
        env["HERMES_MODS_PYTHON"] = sys.executable
        r = subprocess.run([str(OLD_PYTHON), str(RUNNER), "--root", str(self.root), "--hermes-core", "/nonexistent", "--list"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("plugins/alpha", r.stdout.split())

    @unittest.skipUnless(OLD_PYTHON, "needs a python3 older than 3.11 on this host (macOS /usr/bin/python3)")
    def test_driver_with_no_newer_python_says_so_instead_of_a_traceback(self):
        env = {k: v for k, v in os.environ.items() if k != "HERMES_CORE_CHECKOUT"}
        env["HERMES_TEST_ALL_REEXEC"] = "1"  # as if the re-run already happened and found nothing newer
        r = subprocess.run([str(OLD_PYTHON), str(RUNNER), "--list"], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Python >= 3.11", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

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

    def test_repo_guards_run_in_every_plugin_scoped_run(self):
        """A -k run (what a worker runs before pushing one plugin) must still run the repo-wide guards: the
        coach's bare mode=ro open shipped because `-k coach` never ran tests/test_sqlite_ro_guard.py."""
        make_tree(self.root, {"coach": "def test_coach():\n    pass\n"})
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_sqlite_ro_guard.py").write_text("def test_guard():\n    raise SystemExit('guard ran')\n")
        (tests / "test_top.py").write_text("def test_top():\n    pass\n")
        runner = runpy.run_path(str(RUNNER))
        names = {s['name']: s for s in runner['discover'](self.root, None, False)}
        self.assertEqual(names['repo-guards']['targets'], [str(tests / 'test_sqlite_ro_guard.py')])
        self.assertNotIn(str(tests / 'test_sqlite_ro_guard.py'), names['tests']['targets'])
        listed = run(self.root, '-k', 'coach', '--list')
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertEqual(listed.stdout.split(), ['plugins/coach', 'repo-guards'])
        r = run(self.root, '-k', 'coach')
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('repo-guards', r.stdout)
        self.assertEqual(run(self.root, '-k', 'no-such-suite', '--list').stdout.split(), [])

    def test_exact_selects_whole_suite_names_without_adding_repo_guards(self):
        """plugin-suites.yml runs `--exact -k <suite>` per selected suite: `-k plugins/coach` must not also run
        plugins/coach-extra, and repo-guards (run by repo-guards.yml) is not added."""
        make_tree(self.root, {"coach": "def test_coach():\n    pass\n", "coach-extra": "def test_x():\n    pass\n"})
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_sqlite_ro_guard.py").write_text("def test_guard():\n    pass\n")
        listed = run(self.root, '--exact', '-k', 'plugins/coach', '--list')
        self.assertEqual(listed.stdout.split(), ['plugins/coach'], listed.stderr)
        both = run(self.root, '--exact', '-k', 'plugins/coach', '-k', 'repo-guards', '--list')
        self.assertEqual(both.stdout.split(), ['repo-guards', 'plugins/coach'], both.stderr)
        bad = run(self.root, '--exact', '-k', 'coach', '--list')
        self.assertEqual(bad.returncode, 2)
        self.assertIn('no such suite(s): coach', bad.stderr)

    def test_json_reports_per_suite_counts_for_pytest_and_unittest(self):
        make_tree(self.root, {
            "mixed": textwrap.dedent("""
                import pytest
                def test_a(): pass
                def test_b(): pass
                def test_c(): assert False
                @pytest.mark.skip(reason="needs core")
                def test_d(): pass
                @pytest.fixture
                def boom(): raise RuntimeError("x")
                def test_e(boom): pass
            """),
            "green": "def test_ok():\n    pass\n"})
        out = self.root / "r.json"
        r = run(self.root, '--exact', '-k', 'plugins/mixed', '-k', 'plugins/green', '--json', str(out))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        data = json.loads(out.read_text())
        rows = {s['name']: s for s in data['suites']}
        self.assertEqual(set(rows), {'plugins/mixed', 'plugins/green'})
        self.assertEqual({k: rows['plugins/mixed'][k] for k in ('status', 'passed', 'failed', 'errors', 'skipped')},
                         {'status': 'FAIL', 'passed': 2, 'failed': 1, 'errors': 1, 'skipped': 1})
        self.assertEqual((rows['plugins/green']['status'], rows['plugins/green']['passed']), ('PASS', 1))
        self.assertRegex(data['python'], r'^3\.\d+')
        runner = runpy.run_path(str(RUNNER))
        self.assertEqual(runner['counts']("Ran 7 tests in 0.010s\n\nFAILED (failures=1, errors=2, skipped=3)\n"),
                         {'passed': 1, 'failed': 1, 'errors': 2, 'skipped': 3})
        # pytest then unittest in one suite (fast-rate-limit-fallback): both count; a nested summary inside a
        # failure's captured output (before the final line) does not.
        self.assertEqual(runner['counts']("99 passed in 1s\n=== 4 passed, 1 skipped in 2.10s ===\n"
                                          "Ran 3 tests in 0.1s\nOK (skipped=1)\n"),
                         {'passed': 6, 'failed': 0, 'errors': 0, 'skipped': 2})
        self.assertEqual(runner['counts']("no summary"), {'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0})

    def test_suite_needing_core_is_skipped_with_reason_without_one(self):
        (self.root / "plugins" / "fast-rate-limit-fallback").mkdir(parents=True)
        (self.root / "plugins" / "fast-rate-limit-fallback" / "test_x.py").write_text("def test_x():\n    pass\n")
        runner = runpy.run_path(str(RUNNER))
        frl = next(s for s in runner['discover'](self.root, None, False) if s['name'] == 'plugins/fast-rate-limit-fallback')
        self.assertIn('needs a core checkout', frl['skip'])
        with_core = next(s for s in runner['discover'](self.root, self.root, True)
                         if s['name'] == 'plugins/fast-rate-limit-fallback')
        self.assertNotIn('skip', with_core)

    def test_skill_script_tests_are_one_suite_per_skill_and_a_red_one_fails_the_run(self):
        """skills/**/scripts/test_*.py were never run: a red hermes-coach askgate test sat on main unnoticed."""
        coach = self.root / 'skills' / 'productivity' / 'hermes-coach'
        (coach / 'scripts').mkdir(parents=True)
        (coach / 'SKILL.md').write_text('---\nname: hermes-coach\n---\n')
        # stdlib unittest module importing its sibling, as the real skill scripts do
        (coach / 'scripts' / 'mod.py').write_text('X = 1\n')
        (coach / 'scripts' / 'test_mod.py').write_text(textwrap.dedent("""
            import sys, unittest
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import mod
            class T(unittest.TestCase):
                def test_x(self):
                    self.assertEqual(mod.X, 1)
        """))
        other = self.root / 'skills' / 'devops' / 'other'
        (other / 'tests').mkdir(parents=True)
        (other / 'SKILL.md').write_text('---\nname: other\n---\n')
        (other / 'tests' / 'test_mod.py').write_text('def test_ok():\n    pass\n')  # same basename
        (self.root / 'skills' / 'devops' / 'no-tests').mkdir()
        (self.root / 'skills' / 'devops' / 'no-tests' / 'SKILL.md').write_text('x')
        listed = run(self.root, '--list')
        self.assertEqual(listed.stdout.split(), ['skills/devops/other', 'skills/productivity/hermes-coach'])
        self.assertEqual(run(self.root, '-k', 'hermes-coach', '--list').stdout.split(),
                         ['skills/productivity/hermes-coach'])
        ok = run(self.root)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn('2 PASS', ok.stdout)
        (coach / 'scripts' / 'mod.py').write_text('X = 2\n')
        red = run(self.root, '-k', 'hermes-coach')
        self.assertEqual(red.returncode, 1, red.stdout + red.stderr)
        self.assertIn('FAILED: skills/productivity/hermes-coach', red.stdout)

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
                'ensure_test_site': lambda py, core, uv: self.root,
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

    def make_core(self, deps):
        import json
        core = Path(tempfile.mkdtemp(dir=self.root)) / 'core'
        core.mkdir()
        (core / 'hermes_bootstrap.py').touch()
        (core / 'pyproject.toml').write_text('[project]\ndependencies = ' + json.dumps(deps) + '\n')
        return core

    def test_core_python_floor_is_the_highest_python_version_bound_of_core_deps(self):
        runner = runpy.run_path(str(RUNNER))
        floor = runner['core_python_floor']
        self.assertIsNone(floor(None))
        # Each quote style must carry the highest bound once, or a parser that reads only one style still passes.
        core = self.make_core(["python-dotenv==1.2.4; python_version >= '3.14'",
                               "psutil==7.2.2; sys_platform != 'android' and python_version >= \"3.12\"",
                               "old==1; python_version >= '3.9'", "plain==1"])
        self.assertEqual(floor(core), (3, 14))
        core = self.make_core(["python-dotenv==1.2.4; python_version >= '3.12'",
                               "psutil==7.2.2; sys_platform != 'android' and python_version >= \"3.13\""])
        self.assertEqual(floor(core), (3, 13))
        (core / 'pyproject.toml').write_text('[project]\ndependencies = ["plain==1"]\n')
        self.assertIsNone(floor(core))
        (core / 'pyproject.toml').write_text('not toml [')
        self.assertIsNone(floor(core))

    def run_main_with(self, py_version, fallback, deps):
        import contextlib
        import io
        runner = runpy.run_path(str(RUNNER))
        core = self.make_core(deps)
        if not (self.root / 'plugins').exists():
            make_tree(self.root, {'ok': 'def test_ok(): pass'})
        main = runner['main']
        out, err, calls = io.StringIO(), io.StringIO(), []
        with patch.dict(main.__globals__, {
            'select_python': lambda core: ('/fake/python', fallback),
            'python_version': lambda py: py_version,
            'ensure_test_site': lambda py, core, uv: calls.append('site') or self.root,
            'run_suite': lambda *args: calls.append('suite') or dict(name='plugins/ok', status='PASS', secs=0, reason=''),
        }), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(['--root', str(self.root), '--hermes-core', str(core)])
        return rc, err.getvalue(), calls

    GATED = ["python-dotenv==1.2.4; python_version >= '3.14'"]

    def test_fallback_below_core_python_floor_is_refused_before_installing_or_running(self):
        # A scratch HOME on the hub falls back to /usr/bin/python3 3.12: core's deps are all gated on 3.14,
        # so the test site got none and user-terminal reported 56 `No module named 'dotenv'` failures.
        rc, err, calls = self.run_main_with('3.12.3', True, self.GATED)
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])
        self.assertIn('refusing fallback python=/fake/python (3.12.3)', err)
        self.assertIn('Python >= 3.14', err)
        self.assertIn('HERMES_MODS_PYTHON', err)

    def test_fallback_at_core_floor_and_explicit_old_python_still_run(self):
        for version, fallback in (('3.14.9', True), ('3.12.3', False)):
            rc, err, calls = self.run_main_with(version, fallback, self.GATED)
            self.assertEqual((rc, calls), (0, ['site', 'suite']), (version, fallback, err))
            self.assertNotIn('refusing', err)

    def test_fallback_runs_when_core_sets_no_python_floor(self):
        rc, err, calls = self.run_main_with('3.12.3', True, ['plain==1'])
        self.assertEqual((rc, calls), (0, ['site', 'suite']), err)

    def assert_core_sdk_install(self, lock, extra, expected, warning=False):
        import contextlib
        import io
        runner = runpy.run_path(str(RUNNER))
        core = self.root / 'core'
        core.mkdir()
        (core / 'hermes_bootstrap.py').touch()
        if lock is not None:
            (core / 'uv.lock').write_text(lock)
        (core / 'pyproject.toml').write_text(
            '[project.optional-dependencies]\nanthropic = [' + repr(extra) + ']\n')
        make_tree(self.root, {'ok': 'def test_ok(): pass'})
        main = runner['main']
        output = io.StringIO()
        with patch.dict(main.__globals__, {
            'TEST_SITE': self.root / 'cache',
            'select_python': lambda core: ('/fake/python', False),
            'python_version': lambda py: '3.14.9',
            'run_suite': lambda *args: dict(name='plugins/ok', status='PASS', secs=0, reason=''),
        }), patch('shutil.which', return_value='/bin/uv'), \
                patch('subprocess.run') as install, contextlib.redirect_stderr(output):
            self.assertEqual(main(['--root', str(self.root), '--hermes-core', str(core)]), 0)
        self.assertIn(f'anthropic=={expected}', install.call_args.args[0])
        if warning:
            self.assertIn('WARNING', output.getvalue())
            self.assertIn(f'fallback anthropic=={expected}', output.getvalue())
        else:
            self.assertEqual(output.getvalue(), '')

    def test_core_current_lock_pin_is_installed(self):
        self.assert_core_sdk_install('[[package]]\nname = "anthropic"\nversion = "0.125.0"\n',
                                     'anthropic==0.124.0', '0.125.0')

    def test_core_changed_lock_pin_is_installed(self):
        self.assert_core_sdk_install('[[package]]\nname = "anthropic"\nversion = "0.126.0"\n',
                                     'anthropic==0.125.0', '0.126.0')

    def test_core_extra_exact_pin_is_installed_without_lock(self):
        self.assert_core_sdk_install(None, 'anthropic == 0.126.0', '0.126.0')

    def test_core_extra_exact_pin_is_installed_without_locked_sdk(self):
        self.assert_core_sdk_install('version = 1\n', 'anthropic==0.126.0', '0.126.0')

    def test_core_no_exact_pin_warns_and_installs_fallback(self):
        self.assert_core_sdk_install(None, 'anthropic>=0.126.0', '0.125.0', warning=True)

    def test_core_wildcard_pin_warns_and_installs_fallback(self):
        self.assert_core_sdk_install(None, 'anthropic==0.126.*', '0.125.0', warning=True)

    def test_test_site_is_versioned_and_installed_for_selected_python(self):
        runner = runpy.run_path(str(RUNNER))
        ensure = runner['ensure_test_site']
        with patch.dict(ensure.__globals__, {'TEST_SITE': self.root, 'python_version': lambda py: '3.14.9'}), \
                patch('shutil.which', return_value='/bin/uv'), patch('subprocess.run') as install:
            site = ensure('/fake/python with spaces')
            # A real site-packages component: coach's isolated_env forwards only those sys.path entries to the
            # children its transport tests spawn (they need core's deps, which live here on macOS).
            self.assertEqual(site, self.root / 'python-3.14' / 'site-packages')
            install.assert_called_once_with(
                ['/bin/uv', 'pip', 'install', '-q', '--python', '/fake/python with spaces',
                 '--target', str(site), 'pytest', 'pytest-asyncio', 'pytest-xdist', 'pyyaml', 'ruamel.yaml',
                 'fastapi', 'httpx', 'psutil', 'anthropic==0.125.0'], check=True)
            for name in ('pytest', 'pytest_asyncio', 'xdist', 'yaml', 'ruamel', 'fastapi', 'httpx', 'psutil', 'anthropic'):
                (site / name).mkdir(parents=True)
            metadata = site / 'anthropic-0.125.0.dist-info' / 'METADATA'
            metadata.parent.mkdir()
            metadata.write_text('Name: anthropic\nVersion: 0.125.0\n')
            ensure('/fake/python with spaces')
            self.assertEqual(install.call_count, 1)
            # A cache from before SDK provisioning must be repaired, not reused.
            (site / 'anthropic').rmdir()
            ensure('/fake/python with spaces')
            self.assertEqual(install.call_count, 2)
            self.assertIn('anthropic==0.125.0', install.call_args.args[0])
            (site / 'anthropic').mkdir()
            metadata.write_text('Name: anthropic\nVersion: 2.3.0\n')
            ensure('/fake/python with spaces')
            self.assertEqual(install.call_count, 3)
            # A selected core upgrade invalidates an otherwise complete old cache.
            metadata.write_text('Name: anthropic\nVersion: 0.125.0\n')
            core = self.root / 'core'
            core.mkdir()
            (core / 'uv.lock').write_text(
                '[[package]]\nname = "anthropic"\nversion = "0.126.0"\n')
            ensure('/fake/python with spaces', core)
            self.assertEqual(install.call_count, 4)
            self.assertIn('anthropic==0.126.0', install.call_args.args[0])

    def test_test_site_missing_any_required_package_is_repaired(self):
        # Core's hermes_yaml imports ruamel, and coach tests run a nested `pytest -n 2`: a site built before
        # both were provisioned failed 11 coach tests on Mac py3.14 that pass under the core venv.
        runner = runpy.run_path(str(RUNNER))
        ensure = runner['ensure_test_site']
        required = {'ruamel': 'ruamel.yaml', 'xdist': 'pytest-xdist'}
        with patch.dict(ensure.__globals__, {'TEST_SITE': self.root, 'python_version': lambda py: '3.14.9'}), \
                patch('shutil.which', return_value='/bin/uv'), patch('subprocess.run') as install:
            site = self.root / 'python-3.14' / 'site-packages'
            for name in ('pytest', 'pytest_asyncio', 'yaml', 'fastapi', 'httpx', 'psutil', 'anthropic', *required):
                (site / name).mkdir(parents=True)
            metadata = site / 'anthropic-0.125.0.dist-info' / 'METADATA'
            metadata.parent.mkdir()
            metadata.write_text('Name: anthropic\nVersion: 0.125.0\n')
            ensure('/fake/python')
            self.assertEqual(install.call_count, 0)  # complete site: reused
            for count, (module_dir, requirement) in enumerate(required.items(), start=1):
                (site / module_dir).rmdir()
                ensure('/fake/python')
                self.assertEqual(install.call_count, count, module_dir)
                self.assertIn(requirement, install.call_args.args[0])
                (site / module_dir).mkdir()
            ensure('/fake/python')
            self.assertEqual(install.call_count, len(required))

    def test_test_site_carries_core_runtime_deps_unless_running_core_venv(self):
        # The macOS production interpreter (any interpreter outside core's venv) sees only this site: core's own
        # deps live under the Hermes home, which coach's home guard forbids. Without core's [project].dependencies,
        # 13 coach files failed collection on Mac py3.14 (dotenv, then requests) and remote-screen (cryptography);
        # without core's [tool.uv] overrides those exact pins don't resolve (browser-harness vs Pillow 13).
        runner = runpy.run_path(str(RUNNER))
        ensure = runner['ensure_test_site']
        core = self.root / 'core'
        core.mkdir()
        (core / 'uv.lock').write_text('[[package]]\nname = "anthropic"\nversion = "0.125.0"\n')
        pyproject = core / 'pyproject.toml'

        def declare(*deps):
            pyproject.write_text('[project]\ndependencies = [' + ', '.join(f"'{d}'" for d in deps) + ']\n\n'
                                 '[tool.uv]\noverride-dependencies = ["Pillow==13.0.0"]\n')

        deps = ['python-dotenv==1.2.4; python_version >= "3.14"', 'requests==2.34.2', 'browser-harness==0.1.13']
        declare(*deps)
        production = '/fake/production/python3.14'
        with patch.dict(ensure.__globals__, {'TEST_SITE': self.root / 'cache', 'python_version': lambda py: '3.14.9'}), \
                patch('shutil.which', return_value='/bin/uv'), patch('subprocess.run') as install:
            site = ensure(production, core)
            overrides = site / '.core-overrides.txt'
            argv = install.call_args.args[0]
            self.assertEqual(install.call_count, 1)
            self.assertEqual(argv[argv.index('anthropic==0.125.0') + 1:], [*deps, '--override', str(overrides)])
            self.assertEqual(overrides.read_text(), 'Pillow==13.0.0\n')
            for name in ('pytest', 'pytest_asyncio', 'xdist', 'yaml', 'ruamel', 'fastapi', 'httpx', 'psutil', 'anthropic'):
                (site / name).mkdir(parents=True)
            metadata = site / 'anthropic-0.125.0.dist-info' / 'METADATA'
            metadata.parent.mkdir()
            metadata.write_text('Name: anthropic\nVersion: 0.125.0\n')
            ensure(production, core)
            self.assertEqual(install.call_count, 1)  # same core deps: reused
            declare(*deps, 'rich==14.3.4')
            ensure(production, core)
            self.assertEqual(install.call_count, 2)  # core added a dependency: re-provisioned
            self.assertIn('rich==14.3.4', install.call_args.args[0])
            # Interpreters inside core (the hub's .venv, the macOS venv/ bridge) already have core's deps.
            for venv in ('.venv', 'venv'):
                ensure(str(core / venv / 'bin' / 'python'), core)
            self.assertEqual(install.call_count, 2)
            (site / 'xdist').rmdir()
            ensure(str(core / '.venv' / 'bin' / 'python'), core)
            self.assertEqual(install.call_count, 3)
            self.assertEqual(install.call_args.args[0][-1], 'anthropic==0.125.0')

    def test_overrides_keep_the_markers_of_the_deps_they_replace(self):
        # A uv override replaces the whole requirement, marker included: core's unconditional Pillow override
        # made its 3.14-only Pillow dep unconditional, so no Python 3.9 site could resolve (Pillow 13 needs 3.11).
        runner = runpy.run_path(str(RUNNER))
        core = self.root / 'core'
        core.mkdir()
        (core / 'pyproject.toml').write_text(
            '[project]\ndependencies = ["Pillow==13.0.0; python_version >= \'3.14\'", "cryptography==50.0.2", '
            '"psutil==7.2.2; sys_platform != \'android\'", "psutil @ git+https://example.com/p.git ; '
            'sys_platform == \'android\'", "ruamel.yaml==0.18.16; python_version >= \'3.14\'"]\n\n'
            '[tool.uv]\noverride-dependencies = ["Pillow==13.0.0", "cryptography==50.0.2", "pynacl>=1.6,<1.7", '
            '"psutil==7.2.2", "ruamel_yaml==0.18.16; os_name == \'posix\'"]\n')
        deps, overrides = runner['core_runtime_deps']('/fake/production/python3.9', core)
        self.assertEqual(len(deps), 5)
        self.assertEqual(overrides, [
            "Pillow==13.0.0; (python_version >= '3.14')",
            'cryptography==50.0.2',  # unmarked dep: nothing to inherit
            'pynacl>=1.6,<1.7',  # transitive only: unchanged
            "psutil==7.2.2; (sys_platform != 'android') or (sys_platform == 'android')",
            "ruamel_yaml==0.18.16; (os_name == 'posix') and ((python_version >= '3.14'))",
        ])

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

    def assert_uv_suite(self, on_path):
        runner = runpy.run_path(str(RUNNER))
        uv = self.root / ('.local/bin/uv' if not on_path else 'bin/uv')
        uv.parent.mkdir(parents=True)
        uv.write_text('#!/bin/sh\nprintf "1 passed\\n"\n')
        uv.chmod(0o755)
        with patch('pathlib.Path.home', return_value=self.root), \
                patch('shutil.which', return_value=str(uv) if on_path else None):
            resolved = runner['find_uv']()
        self.assertEqual(resolved, str(uv))
        result = runner['run_suite'](dict(name='uv-suite', cmd=['uv', 'run']),
                                     sys.executable, None, 10, self.root, self.root, resolved)
        self.assertEqual(result['status'], 'PASS', result)

    def test_suite_uv_on_path(self):
        self.assert_uv_suite(True)

    def test_suite_uv_only_in_user_bin(self):
        self.assert_uv_suite(False)

    def test_missing_uv_and_other_executable_fail_without_losing_other_results(self):
        import contextlib
        import io
        runner = runpy.run_path(str(RUNNER))
        main = runner['main']
        suites = [dict(name='uv-suite', cmd=['uv', 'run']),
                  dict(name='missing-suite', cmd=[str(self.root / 'missing')]),
                  dict(name='good-suite', cmd=[sys.executable, '-c', 'print("1 passed")'])]
        output = io.StringIO()
        with patch('pathlib.Path.home', return_value=self.root), \
                patch('shutil.which', return_value=None), patch.dict(main.__globals__, {
                    'discover': lambda *args, **kwargs: suites,
                    'select_python': lambda core: (sys.executable, False),
                    'ensure_test_site': lambda *args: self.root,
                }), contextlib.redirect_stdout(output):
            self.assertIsNone(runner['find_uv']())
            self.assertEqual(main(['--root', str(self.root)]), 1)
        self.assertRegex(output.getvalue(), r'FAIL\s+uv-suite')
        self.assertRegex(output.getvalue(), r'FAIL\s+missing-suite')
        self.assertRegex(output.getvalue(), r'PASS\s+good-suite')
        self.assertIn('executable not found: uv', output.getvalue())
        self.assertIn('cannot execute', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())

    def test_shell_override_quotes_interpreter_with_spaces(self):
        runner = runpy.run_path(str(RUNNER))
        py = self.root / 'Python App/python'
        py.parent.mkdir()
        # A venv's python can be a copied binary that finds its stdlib only through the pyvenv.cfg beside it (the
        # macOS 3.11 bridge venv): link its base interpreter, as an override outside any venv would be.
        py.symlink_to(os.path.realpath(getattr(sys, '_base_executable', None) or sys.executable))
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

    def test_suites_see_the_selected_core_as_HERMES_CORE_SOURCE(self):
        # chat-closeout's core-backed tests skip unless HERMES_CORE_SOURCE names the core checkout. test-all
        # exported only HERMES_CORE_CHECKOUT, so its chat-closeout run skipped 12 of them (the watchdog-card
        # origin-link regression tests among them) and still reported the suite green.
        runner = runpy.run_path(str(RUNNER))
        probe = 'import os; print(os.environ.get("HERMES_CORE_SOURCE"))'
        suite = dict(name='core-env', cmd=[sys.executable, '-c', probe])
        core = self.root / 'core'
        for inherited in (None, '/stale/core'):  # a stale inherited value must not point tests at another core
            with patch.dict(os.environ):
                os.environ.pop('HERMES_CORE_SOURCE', None)
                if inherited:
                    os.environ['HERMES_CORE_SOURCE'] = inherited
                result = runner['run_suite'](suite, sys.executable, core, 10, self.root, self.root / 'test-site')
            self.assertEqual(result['status'], 'PASS', result)
            self.assertEqual(result['tail'], str(core), inherited)
        with patch.dict(os.environ):
            os.environ.pop('HERMES_CORE_SOURCE', None)
            result = runner['run_suite'](suite, sys.executable, None, 10, self.root, self.root / 'test-site')
        self.assertEqual(result['tail'], 'None')  # no core found: those suites keep skipping with their reason

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
