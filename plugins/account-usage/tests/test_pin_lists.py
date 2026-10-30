"""Pins accept a list of audited sha256 values; a bare string stays valid.

The fleet runs mixed cores during a rollout, so a pin must accept every audited
version of a seam file at once. An unaudited version still fails closed, in both
install() (selection.check_pins) and the stdlib drift checker (check_compat.py).
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


check_compat = _load('account_usage_check_compat_lists', 'check_compat.py')
selection = _load('account_usage_selection_lists', 'selection.py')

OLD, NEW, OTHER = b'def seam(): pass\n', b'def seam(): return 1\n', b'def seam(): return 2\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class PinLists(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plugin = self.root / 'plugins' / 'account-usage'
        self.plugin.mkdir(parents=True)
        self.core = self.root / 'core'
        (self.core / 'agent').mkdir(parents=True)

    def core_file(self, data):
        (self.core / 'agent' / 'seam.py').write_bytes(data)

    def pins(self, value):
        pins = {'agent/seam.py': value}
        (self.plugin / 'execution-compatibility.json').write_text(json.dumps(pins))
        return pins

    def checker_ok(self):
        return check_compat.check(self.core, self.plugin)['ok']

    def install_check(self, pins):
        return selection.check_pins(self.core, pins)

    def test_core_matching_either_audited_hash_passes(self):
        pins = self.pins([sha(OLD), sha(NEW)])
        for data in (OLD, NEW):
            self.core_file(data)
            self.assertTrue(self.checker_ok(), data)
            self.assertEqual(self.install_check(pins), {'agent/seam.py': sha(data)})

    def test_unaudited_hash_still_fails_closed(self):
        pins = self.pins([sha(OLD), sha(NEW)])
        self.core_file(OTHER)
        report = check_compat.check(self.core, self.plugin)
        self.assertFalse(report['ok'])
        self.assertEqual(report['drift'][0]['actual'], sha(OTHER))
        self.assertEqual(report['drift'][0]['expected'], [sha(OLD), sha(NEW)])
        with self.assertRaisesRegex(selection.SelectionRejected, 'compatibility'):
            self.install_check(pins)

    def test_bare_string_pin_still_loads(self):
        pins = self.pins(sha(OLD))
        self.core_file(OLD)
        self.assertTrue(self.checker_ok())
        self.assertEqual(self.install_check(pins), {'agent/seam.py': sha(OLD)})
        self.core_file(NEW)
        self.assertFalse(self.checker_ok())
        with self.assertRaises(selection.SelectionRejected):
            self.install_check(pins)

    def test_malformed_pin_fails_closed(self):
        self.core_file(OLD)
        for bad in ([], [sha(OLD), 7], {'sha': sha(OLD)}, None, 7):
            pins = self.pins(bad)
            self.assertFalse(self.checker_ok(), bad)
            with self.assertRaises(selection.SelectionRejected, msg=repr(bad)):
                self.install_check(pins)

    def test_missing_core_file_fails_closed(self):
        pins = self.pins([sha(OLD)])
        self.assertFalse(self.checker_ok())
        with self.assertRaises(selection.SelectionRejected):
            self.install_check(pins)

    def test_cli_exit_codes_with_list_pins(self):
        import shutil
        import sys
        script = self.plugin / 'check_compat.py'
        shutil.copy(PLUGIN / 'check_compat.py', script)
        self.pins([sha(OLD), sha(NEW)])
        run = lambda: subprocess.run([sys.executable, str(script), '--core', str(self.core)],
                                     capture_output=True, text=True)
        self.core_file(NEW)
        self.assertEqual(run().returncode, 0)
        self.core_file(OTHER)
        result = run()
        self.assertEqual(result.returncode, 1)
        self.assertIn('agent/seam.py', result.stdout)


class ShippedPins(unittest.TestCase):
    """The shipped pin file: well formed, and re-audits only ever ADD hashes."""
    def setUp(self):
        self.pins = json.loads((PLUGIN / 'execution-compatibility.json').read_text())

    def test_every_pin_is_a_digest_or_a_nonempty_digest_list(self):
        for rel, value in self.pins.items():
            digests = [value] if isinstance(value, str) else value
            self.assertTrue(digests, rel)
            for digest in digests:
                self.assertRegex(digest, r'^[0-9a-f]{64}$', rel)

    def test_previously_audited_hashes_stay_accepted(self):
        # f3603a4 = the audit against fork core b8a7f53 (hosts on d722024949 still run it).
        try:
            old = subprocess.run(['git', '-C', str(PLUGIN), 'show',
                                  'f3603a4:plugins/account-usage/execution-compatibility.json'],
                                 capture_output=True, text=True, timeout=20, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            self.skipTest('hermes-mods history unavailable')
        for rel, digest in json.loads(old).items():
            accepted = [self.pins[rel]] if isinstance(self.pins[rel], str) else self.pins[rel]
            self.assertIn(digest, accepted, rel)


if __name__ == '__main__':
    unittest.main()
