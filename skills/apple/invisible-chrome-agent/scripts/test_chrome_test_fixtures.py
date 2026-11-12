"""require_real_linux_chrome: a missing tool skips on a dev host but fails under CI=true.

No real Chrome: sys.platform, PATH lookups and CI are patched around a Probe test.
"""
import os
import sys
import unittest
from unittest import mock

import chrome_test_fixtures as fixtures


class RequireRealLinuxChromeTests(unittest.TestCase):
    def outcome(self, ci, harness=True, present=()):
        class Probe(unittest.TestCase):
            def runTest(self):
                fixtures.require_real_linux_chrome(self, harness=harness)

        env = {k: v for k, v in os.environ.items() if k != 'CI'}
        if ci is not None:
            env['CI'] = ci
        which = lambda tool: '/usr/bin/' + tool if tool in present else None
        result = unittest.TestResult()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, 'platform', 'linux'), \
                mock.patch.object(fixtures.shutil, 'which', side_effect=which):
            Probe().run(result)
        return result

    def test_ci_fails_naming_the_missing_harness(self):
        for ci in ('true', 'TRUE'):
            with self.subTest(ci=ci):
                result = self.outcome(ci, present=('google-chrome',))
                self.assertEqual(result.skipped, [])
                self.assertEqual(len(result.failures), 1)
                self.assertIn('real Linux Chrome and harness required, under CI=true: '
                              'browser-harness not on PATH (provision it in the workflow)',
                              result.failures[0][1])

    def test_ci_fails_naming_missing_chrome(self):
        result = self.outcome('true', harness=False)
        self.assertEqual(result.skipped, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn('isolated Linux Chrome required, under CI=true: google-chrome not on PATH',
                      result.failures[0][1])

    def test_dev_host_skips_with_the_reason(self):
        for ci in (None, '', 'false'):
            with self.subTest(ci=ci):
                result = self.outcome(ci, present=('google-chrome',))
                self.assertEqual(result.failures, [])
                self.assertEqual([r for _, r in result.skipped],
                                 ['real Linux Chrome and harness required'])

    def test_provisioned_tools_run_the_test(self):
        result = self.outcome('true', present=('google-chrome', 'browser-harness'))
        self.assertEqual((result.failures, result.errors, result.skipped), ([], [], []))
        self.assertEqual(result.testsRun, 1)

    def test_off_linux_skips_even_under_ci(self):
        class Probe(unittest.TestCase):
            def runTest(self):
                fixtures.require_real_linux_chrome(self, harness=True)
        result = unittest.TestResult()
        with mock.patch.dict(os.environ, {'CI': 'true'}), mock.patch.object(sys, 'platform', 'darwin'):
            Probe().run(result)
        self.assertEqual(result.failures, [])
        self.assertEqual([r for _, r in result.skipped], ['real Linux Chrome and harness required'])


if __name__ == '__main__':
    unittest.main()
