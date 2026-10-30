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
        core = Path.home() / '.hermes' / 'hermes-agent'
        if not (core / 'agent').is_dir():
            self.skipTest('no installed Hermes core on this host')
        report = check_compat.check(core)
        self.assertTrue(report['ok'], json.dumps(report['drift'], indent=2))


if __name__ == '__main__':
    unittest.main()
