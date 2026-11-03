"""Without core the core-backed tests skip with the reason; a requested core that is missing still fails."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core_path


class RequireCore(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        env = {k: v for k, v in os.environ.items() if k not in ('HERMES_TEST_CORE', 'HERMES_CORE_CHECKOUT')}
        for p in (patch.dict(os.environ, env, clear=True),
                  patch.object(core_path.importlib.util, 'find_spec', return_value=None),
                  patch.object(core_path.Path, 'home', return_value=self.home)):
            p.start()
            self.addCleanup(p.stop)

    def fake_core(self, at):
        (at / 'agent').mkdir(parents=True)
        (at / 'hermes_constants.py').write_text('')
        return at

    def test_no_core_anywhere_skips_with_the_not_found_reason(self):
        with self.assertRaises(unittest.SkipTest) as caught:
            core_path.require_core()
        self.assertIn('Hermes test core not found at', str(caught.exception))
        self.assertIn(str(self.home / '.hermes' / 'hermes-agent'), str(caught.exception))
        with self.assertRaises(ImportError):  # the bare locator still raises for its own callers
            core_path.core_path()

    def test_missing_requested_core_fails_instead_of_skipping(self):
        for var in ('HERMES_TEST_CORE', 'HERMES_CORE_CHECKOUT'):
            with self.subTest(var=var), patch.dict(os.environ, {var: str(self.home / 'missing-core')}):
                with self.assertRaisesRegex(ImportError, 'Hermes test core not found'):
                    core_path.require_core()

    def test_found_core_is_returned(self):
        core = self.fake_core(self.home / '.hermes' / 'hermes-agent')
        self.assertEqual(core_path.require_core(), core)
        explicit = self.fake_core(self.home / 'explicit')
        with patch.dict(os.environ, {'HERMES_TEST_CORE': str(explicit)}):
            self.assertEqual(core_path.require_core(), explicit.resolve())


if __name__ == '__main__':
    unittest.main()
