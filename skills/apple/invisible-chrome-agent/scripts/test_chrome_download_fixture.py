"""Mock Keychain belongs only to a fresh disposable Chrome smoke profile."""
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import chrome_test_fixtures as fixtures
import smoke_chrome_lifecycle as smoke
from test_hermes_chrome_agent import hca


class DownloadFixtureTests(unittest.TestCase):
    def test_platform_arguments_require_owned_fresh_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            # Keep the allocation object: a pathname alone is not fixture ownership.
            with self.assertRaises(ValueError):
                fixtures.fresh_profile_arguments(directory, Path(directory) / 'profile')
        with tempfile.TemporaryDirectory() as outer:
            allocation = tempfile.TemporaryDirectory(dir=outer)
            try:
                profile = Path(allocation.name) / 'profile'
                for platform in ('darwin', 'linux', 'win32'):
                    with self.subTest(platform=platform), mock.patch.object(sys, 'platform', platform):
                        expected = ['--user-data-dir=' + str(profile)]
                        if platform == 'darwin':
                            expected.append('--use-mock-keychain')
                        self.assertEqual(fixtures.fresh_profile_arguments(allocation, profile), expected)
                        self.assertFalse(profile.exists())
                for invalid in (Path(outer) / 'outside', Path(allocation.name), profile / 'nested'):
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        fixtures.fresh_profile_arguments(allocation, invalid)
                profile.symlink_to(Path(outer) / 'missing')
                with self.assertRaises(ValueError):
                    fixtures.fresh_profile_arguments(allocation, profile)
                profile.unlink()
                profile.mkdir()
                with self.assertRaises(ValueError):
                    fixtures.fresh_profile_arguments(allocation, profile)
            finally:
                allocation.cleanup()
            with self.assertRaises(ValueError):
                fixtures.fresh_profile_arguments(allocation, profile)

    def test_smoke_validates_before_any_launch(self):
        class Validated(Exception):
            pass

        def validate(allocation, profile):
            with mock.patch.object(sys, 'platform', 'darwin'):
                self.assertIn('--use-mock-keychain', fixtures.fresh_profile_arguments(allocation, profile))
            raise Validated

        with mock.patch.object(sys, 'argv', ['smoke', '--chrome', sys.executable]), \
             mock.patch.object(smoke, 'fresh_profile_arguments', side_effect=validate) as helper, \
             mock.patch.object(smoke.subprocess, 'Popen') as spawn:
            with self.assertRaises(Validated):
                smoke.main()
            helper.assert_called_once()
            spawn.assert_not_called()
        self.assertFalse(Path(helper.call_args.args[0].name).exists())

    def test_smoke_launch_argv_uses_selected_fixture_flags(self):
        class LaunchCaptured(Exception):
            pass

        original_load = smoke.importlib.machinery.SourceFileLoader.exec_module

        def load(loader, module):
            original_load(loader, module)
            if loader.name == 'smoke_hca':
                module.cdp_version = lambda **kwargs: None
                # Invoke the actual smoke wake closure before any server starts.
                module.CDPProxy = lambda *args, **kw: mock.Mock(start=kw['lifecycle'].wake)

        for platform in ('darwin', 'linux'):
            for headful in (False, True):
                argv = ['smoke', '--chrome', sys.executable] + (['--headful'] if headful else [])
                with self.subTest(platform=platform, headful=headful), \
                     mock.patch.object(fixtures, 'sys', mock.Mock(platform=platform)), \
                     mock.patch.object(sys, 'argv', argv), \
                     mock.patch.object(smoke.importlib.machinery.SourceFileLoader, 'exec_module', load), \
                     mock.patch.object(smoke.subprocess, 'Popen', side_effect=LaunchCaptured) as spawn:
                    with self.assertRaises(LaunchCaptured):
                        smoke.main()
                    launch = spawn.call_args.args[0]
                    self.assertEqual('--use-mock-keychain' in launch, platform == 'darwin')
                    self.assertEqual('--headless=new' in launch, not headful)
                    profile = Path(next(a.split('=', 1)[1] for a in launch if a.startswith('--user-data-dir=')))
                    self.assertEqual(spawn.call_args.kwargs['env']['HOME'], str(profile.parent))
                    self.assertFalse(profile.parent.exists())

    def test_production_launch_never_uses_mock_keychain(self):
        # Exercise both real argv builders, without starting Chrome or touching
        # any installed profile. A Darwin fixture selection cannot affect them.
        with tempfile.TemporaryDirectory() as directory:
            for managed in (False, True):
                with self.subTest(managed=managed), \
                     mock.patch.object(sys, 'platform', 'darwin'), \
                     mock.patch.multiple(hca, PROFILE=directory), \
                     mock.patch.object(hca.subprocess, 'Popen') as spawn, \
                     mock.patch.object(hca, 'cdp_version', return_value={'Browser': 'Fake'}), \
                     mock.patch.object(hca.Supervisor, '_kill_stale_chrome'), \
                     mock.patch.object(hca.Supervisor, 'owned_version', return_value={'Browser': 'Fake'}), \
                     mock.patch('chrome_agent_process.require_free_endpoint'):
                    supervisor = hca.Supervisor()
                    supervisor.lifecycle = mock.Mock() if managed else None
                    spawn.return_value.poll.return_value = None
                    supervisor.start_chrome()
                    argv = spawn.call_args.args[0]
                    self.assertNotIn('--use-mock-keychain', argv)
                    self.assertIn('--user-data-dir=' + directory, argv)
        source = inspect.getsource(hca)
        self.assertNotIn('chrome_test_fixtures', source)
        self.assertNotIn('--use-mock-keychain', source)


if __name__ == '__main__':
    unittest.main()
