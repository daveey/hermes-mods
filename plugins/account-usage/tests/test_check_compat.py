"""check_compat.py: pin drift detection (stdlib only, synthetic cores plus the installed one)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('account_usage_check_compat', PLUGIN / 'check_compat.py')
check_compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_compat)


class CheckCompat(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plugin = self.root / 'plugins' / 'account-usage'
        self.plugin.mkdir(parents=True)
        (self.plugin / 'selection.py').write_text('# audited\n')
        self.core = self.root / 'core'
        (self.core / 'agent').mkdir(parents=True)
        (self.core / 'agent' / 'seam.py').write_text('def seam(): pass\n')
        digest = hashlib.sha256((self.core / 'agent' / 'seam.py').read_bytes()).hexdigest()
        (self.plugin / 'execution-compatibility.json').write_text(json.dumps({'agent/seam.py': digest}))
        frlf = self.root / 'plugins' / 'fast-rate-limit-fallback'
        frlf.mkdir()
        self.composition = frlf / 'account-usage-compatibility.json'
        self.composition.write_text(json.dumps({'selection_sha256': hashlib.sha256(b'# audited\n').hexdigest()}))

    def test_matching_pins_pass(self):
        report = check_compat.check(self.core, self.plugin)
        self.assertTrue(report['ok'], report)

    def test_core_file_drift_is_reported(self):
        (self.core / 'agent' / 'seam.py').write_text('def seam(): return 1\n')
        report = check_compat.check(self.core, self.plugin)
        self.assertFalse(report['ok'])
        self.assertEqual([d['file'] for d in report['drift']], ['agent/seam.py'])

    def test_missing_core_file_is_drift(self):
        (self.core / 'agent' / 'seam.py').unlink()
        self.assertEqual(check_compat.check(self.core, self.plugin)['drift'][0]['actual'], 'missing')

    def test_stale_fast_fallback_composition_pin_is_reported(self):
        (self.plugin / 'selection.py').write_text('# edited\n')
        drift = check_compat.check(self.core, self.plugin)['drift']
        self.assertEqual([d['pin'] for d in drift], ['fast-rate-limit-fallback/account-usage-compatibility.json'])

    def test_core_inputs_cover_function_pin_modules(self):
        """A partial tree built from core_inputs() must give the same verdict as the full core."""
        digest, pins = self._write_function_pin()
        pins.write_text(json.dumps({'agent.fn:seam': [digest], 'agent.sub.mod:f': 'x'}))
        self.assertEqual(check_compat.core_inputs(self.plugin), ['agent/fn.py', 'agent/seam.py', 'agent/sub/mod.py'])
        pins.write_text(json.dumps({'agent.fn:seam': [digest]}))
        partial = self.root / 'partial'
        for name in check_compat.core_inputs(self.plugin):
            (partial / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.core / name, partial / name)
        self.assertEqual(check_compat.check(partial, self.plugin)['drift'], [])

    def _write_function_pin(self):
        import ast
        source = '@staticmethod\ndef seam(x):\n    return x\n'
        (self.core / 'agent' / 'fn.py').write_text('def seam(): pass\n' + source)
        digest = hashlib.sha256(ast.dump(ast.parse(source).body[0], include_attributes=False).encode()).hexdigest()
        pins = self.composition.with_name('compatibility.json')
        pins.write_text(json.dumps({'agent.fn:seam': [digest]}))
        return digest, pins

    def test_function_pins_from_another_python_delegate_to_the_runtime_minor(self):
        """macOS /usr/bin/python3 (3.9) dumps a different AST: compute under AST_PYTHON instead."""
        from unittest import mock
        self.assertEqual(sys.version_info[:2], check_compat.AST_PYTHON, 'run under the Hermes runtime Python')
        digest, pins = self._write_function_pin()
        old = mock.patch.object(check_compat.sys, 'version_info', (3, 9, 6))
        with old, mock.patch.object(check_compat, '_ast_pythons', lambda core: iter([Path(sys.executable)])):
            report = check_compat.check(self.core, self.plugin)
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['skipped'], [])
        (self.core / 'agent' / 'fn.py').write_text('def seam(x):\n    return x + 1\n')
        with old, mock.patch.object(check_compat, '_ast_pythons', lambda core: iter([Path(sys.executable)])):
            self.assertEqual([d['file'] for d in check_compat.check(self.core, self.plugin)['drift']], ['agent.fn:seam'])
        with old, mock.patch.object(check_compat, '_ast_pythons', lambda core: iter([Path('/nonexistent/python')])):
            report = check_compat.check(self.core, self.plugin)
        self.assertEqual(report['drift'], [])  # a false drift from the wrong Python is worse than a visible skip
        self.assertEqual([s['pin'] for s in report['skipped']], ['fast-rate-limit-fallback/compatibility.json'])

    def test_fast_fallback_function_pin_drift_and_missing(self):
        digest, pins = self._write_function_pin()
        pins.write_text(json.dumps({'agent.fn:seam': [digest], 'agent.gone:seam': digest}))
        drift = check_compat.check(self.core, self.plugin)['drift']
        self.assertEqual([(d['pin'], d['file'], d['actual']) for d in drift],
                         [('fast-rate-limit-fallback/compatibility.json', 'agent.gone:seam', 'missing')])
        (self.core / 'agent' / 'fn.py').write_text('def seam(x):\n    return x + 1\n')
        pins.write_text(json.dumps({'agent.fn:seam': [digest]}))
        drift = check_compat.check(self.core, self.plugin)['drift']
        self.assertEqual([d['file'] for d in drift], ['agent.fn:seam'])

    def test_fast_fallback_pins_match_its_runtime_fingerprint(self):
        """The stdlib digest equals the inspect-based one fast-rate-limit-fallback's install() uses."""
        import ast
        import inspect
        import textwrap
        from core_path import core_path
        import importlib
        pins = json.loads((PLUGIN.parent / 'fast-rate-limit-fallback' / 'compatibility.json').read_text())
        for key in pins:
            module, name = key.split(':')
            function = getattr(importlib.import_module(module), name)
            function = inspect.unwrap(function)
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
            live = hashlib.sha256(ast.dump(tree.body[0], include_attributes=False).encode()).hexdigest()
            self.assertEqual(check_compat._function_digest(core_path(), key), live, key)

    def test_cli_exit_codes(self):
        script = self.plugin / 'check_compat.py'
        shutil.copy(PLUGIN / 'check_compat.py', script)
        shutil.copy(PLUGIN / 'pins.py', script.with_name('pins.py'))  # the shared drift definition
        run = lambda core: subprocess.run([sys.executable, str(script), '--core', str(core)],
                                          capture_output=True, text=True).returncode
        self.assertEqual(run(self.core), 0)
        (self.core / 'agent' / 'seam.py').write_text('changed\n')
        self.assertEqual(run(self.core), 1)
        self.assertEqual(run(self.root / 'nowhere'), 2)


class InstalledCore(unittest.TestCase):
    """The pins in this checkout match the core this host runs. Red = re-audit before shipping."""
    def test_pins_match_installed_core(self):
        from core_path import core_path
        core = core_path()
        report = check_compat.check(core)
        self.assertTrue(report['ok'], json.dumps(report['drift'], indent=2))


class CoreImportIsolation(unittest.TestCase):
    def probe(self, route, code):
        import os
        from core_path import core_path
        core = core_path()
        with tempfile.TemporaryDirectory() as scratch:
            home = Path(scratch) / 'home'
            home.mkdir()
            env = {'HOME': str(home), 'TMPDIR': scratch, 'PATH': os.defpath,
                   'PYTHONPATH': str(Path(__file__).parent),
                   'HERMES_HOME': str(Path(scratch) / 'state')}
            if route == 'explicit':
                env['HERMES_TEST_CORE'] = str(core)
            elif route == 'pythonpath':
                env['PYTHONPATH'] += os.pathsep + str(core)
            elif route == 'fallback':
                (home / '.hermes').mkdir()
                (home / '.hermes' / 'hermes-agent').symlink_to(core, target_is_directory=True)
            else:
                env['HERMES_TEST_CORE'] = str(home / 'missing-core')
            if route == 'fallback':
                # Editable installs can expose core even without PYTHONPATH. Force
                # only the discovery miss so this case exercises the home fallback.
                code = ('import core_path; from unittest.mock import patch; '
                        'with_fallback = patch.object(core_path.importlib.util, "find_spec", return_value=None); '
                        'with_fallback.start(); core_path.use_core(); with_fallback.stop(); ' + code)
            result = subprocess.run([sys.executable, '-B', '-c', code], env=env,
                                    capture_output=True, text=True, timeout=60)
            self.assertFalse((home / '.hermes' / 'installs').exists())
            self.assertNotIn('preparing dependencies', result.stdout + result.stderr)
            return result

    def test_core_resolution_routes_do_not_bootstrap(self):
        for route in ('explicit', 'pythonpath', 'fallback'):
            with self.subTest(route=route):
                result = self.probe(route, 'from core_path import use_core; use_core(); '
                    'import test_selection, test_request_selection, test_selection_backend; '
                    'import os; assert os.environ["HERMES_DISABLE_LAZY_INSTALLS"] == "1"; '
                    'print("imports ok")')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('imports ok', result.stdout)

    def test_missing_explicit_core_fails_loudly(self):
        result = self.probe('missing', 'import test_selection')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Hermes test core not found', result.stderr)
        self.assertIn('HERMES_TEST_CORE', result.stderr)


if __name__ == '__main__':
    unittest.main()
