"""Module replacement must not unload native dependencies imported by a turn."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest

from core_path import core_path
from module_override import module_override


class ModuleOverride(unittest.TestCase):
    def test_restores_only_target_even_on_exception(self):
        target, late = '_account_usage_target', '_account_usage_late_import'
        original, replacement, imported = (ModuleType(n) for n in ('original', 'replacement', 'imported'))
        sys.modules[target] = original
        try:
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with module_override(target, replacement):
                    self.assertIs(sys.modules[target], replacement)
                    sys.modules[late] = imported
                    raise RuntimeError('fixture')
            self.assertIs(sys.modules[target], original)
            self.assertIs(sys.modules[late], imported)
        finally:
            sys.modules.pop(target, None)
            sys.modules.pop(late, None)

    def test_removes_previously_absent_target(self):
        target = '_account_usage_absent'
        self.assertNotIn(target, sys.modules)
        with module_override(target, ModuleType(target)):
            self.assertIn(target, sys.modules)
        self.assertNotIn(target, sys.modules)

    def test_fresh_real_turn_has_clean_native_relay_teardown(self):
        # A fresh process matters: pre-importing nemo_relay masks the old whole-
        # sys.modules rollback. Inspect output AFTER atexit, not pytest's count.
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, HERMES_HOME=str(Path(home) / '.hermes'),
                       # Retain the parent's provisioned test dependency paths;
                       # core alone drops test-all's SDK site in this cold child.
                       HERMES_TEST_CORE=str(core_path()),
                       PYTHONPATH=os.pathsep.join([str(core_path()), *sys.path]),
                       HERMES_DISABLE_LAZY_INSTALLS='1')
            target = str(Path(__file__).with_name('test_execution.py')) + (
                '::Execution::test_full_turn_unavailable_selection_never_sends_another_account')
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', target],
                                    env=env, text=True, capture_output=True, timeout=90)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('1 passed', output)
        self.assertNotIn('Hermes Relay orphaned scope drain failed', output)
        self.assertNotIn('closed with errors', output)
        self.assertNotIn('Traceback (most recent call last)', output)
