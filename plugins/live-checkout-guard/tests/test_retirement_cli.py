"""Exercise the public CLI and raw mutation guard using temporary Git repos."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# Reuse only the synthetic fixture, not its test classes.
spec = importlib.util.spec_from_file_location('retirement_fixture', Path(__file__).with_name('test_stash_retirement.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
Base = fixture.Base
HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retirement_guard', HERE / 'guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class TestCLI(Base):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(HERE / 'reconcile.py'),
                               '--root', str(self.root), '--archive', str(self.archive), *args],
                              capture_output=True, text=True)

    def test_prepare_retire_recover(self):
        p = self.cli('--prepare-stash-retirement')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(set(json.loads(p.stdout)['archived']), set(self.oids))
        p = self.cli('--retire-stashes', '--oid', self.oids[1])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)['retired'], [self.oids[1]])
        self.assertEqual([r.split()[0] for r in self.stack()], [o for o in self.oids if o != self.oids[1]])
        p = self.cli('--recover-stash-retirement')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), {'completed': [], 'aborted': []})
        self.assertEqual(self.checkout(), self.before_checkout)

    def test_bad_cli_combinations_never_mutate(self):
        for args in [('--retire-stashes',), ('--oid', self.oids[0]),
                     ('--prepare-stash-retirement', '--path', 'tracked.txt'),
                     ('--prepare-stash-retirement', '--oid', self.oids[0]),
                     ('--retire-stashes', '--published', self.oids[0]),
                     ('--prepare-stash-retirement', '--retire-stashes')]:
            with self.subTest(args=args):
                self.assertNotEqual(self.cli(*args).returncode, 0)
                self.assertEqual(self.stack(), self.before_stack)
                self.assertEqual(self.checkout(), self.before_checkout)

    def test_guard_public_helper_and_raw_mutations(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'LIVE_CHECKOUT_ROOT': str(self.root)}):
            for command in ['git update-ref -d refs/stash', 'git update-ref --stdin',
                            'git update-ref refs/hermes-retire/keep/abc/0 ' + self.oids[0],
                            'git reflog delete stash@{0}', 'git reflog expire --all',
                            'git reflog drop refs/stash', 'git stash drop stash@{0}']:
                with self.subTest(command=command):
                    self.assertIsNotNone(guard.check_shell(command, str(self.root)))
                    self.assertIsNotNone(guard.check_python('subprocess.run(' + repr(command) + ', shell=True)', str(self.root)))
            for command in ['git stash list', 'git stash show', 'git reflog show refs/stash',
                            'python3 ' + str(HERE / 'reconcile.py') + ' --retire-stashes --archive /archive --oid ' + self.oids[0]]:
                self.assertIsNone(guard.check_shell(command, str(self.root)))
            self.assertIsNone(guard.check_shell('git update-ref -d refs/stash', str(self.base)))
