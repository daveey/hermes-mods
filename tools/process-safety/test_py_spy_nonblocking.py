"""Tests for py_spy_nonblocking.py (the ~/.local/bin/py-spy guard). Cross-platform."""
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
WRAPPER = HERE / 'py_spy_nonblocking.py'
spec = importlib.util.spec_from_file_location('py_spy_nonblocking', WRAPPER)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# The exact command that froze the hub's always-on backend on 2026-11-13 (t_01a1d68c494c8717).
INCIDENT = ['record', '-r', '20', '-d', '90', '-f', 'raw', '-o', 'before-sv-4114470.raw', '-p', '4114470']


class RewriteTest(unittest.TestCase):
    def test_incident_command_gets_nonblocking(self):
        args, changed = guard.rewrite(INCIDENT)
        self.assertTrue(changed)
        self.assertEqual(args[:2], ['record', '--nonblocking'])
        self.assertEqual(args[2:], INCIDENT[1:])

    def test_every_attaching_form(self):
        for argv in (['top', '--pid', '12'], ['dump', '-p', '12'], ['record', '--pid=12', '-d', '5'],
                     ['record', '-p12'], ['dump', '--locals', '--pid', '9']):
            args, changed = guard.rewrite(argv)
            self.assertTrue(changed, argv)
            self.assertIn('--nonblocking', args)
            self.assertEqual(args.count('--nonblocking'), 1)

    def test_left_alone(self):
        for argv in ([], ['--version'], ['record', '--nonblocking', '-p', '1'],
                     ['record', '-o', 'x', '--', 'python3', '-p', '1'],  # launched fixture, not attached
                     ['record', '--help'], ['top', '-h', '-p', '1'], ['record', '-o', 'x', '-r', '20']):
            args, changed = guard.rewrite(argv)
            self.assertFalse(changed, argv)
            self.assertEqual(args, argv)


class EndToEndTest(unittest.TestCase):
    """Run the wrapper through a ~/.local/bin/py-spy symlink against a fake real binary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        local = Path(self.tmp.name) / 'home' / '.local'
        real = local / 'share' / 'uv' / 'tools' / 'py-spy' / 'bin' / 'py-spy'
        real.parent.mkdir(parents=True)
        real.write_text('#!%s\nimport sys\nprint("REAL " + " ".join(sys.argv[1:]))\n' % sys.executable)
        real.chmod(real.stat().st_mode | stat.S_IXUSR)
        self.link = local / 'bin' / 'py-spy'
        self.link.parent.mkdir(parents=True)
        os.symlink(WRAPPER, self.link)

    def tearDown(self):
        self.tmp.cleanup()

    def run_link(self, *args, env_extra=None, timeout=30):
        env = dict(os.environ, HOME='/nonexistent-root-home')  # sudo may reset HOME; must not matter
        env.pop('PY_SPY_REAL', None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(self.link), *args], capture_output=True, text=True,
                              env=env, timeout=timeout)

    def test_incident_command_reaches_real_binary_nonblocking(self):
        done = self.run_link(*INCIDENT)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), 'REAL record --nonblocking ' + ' '.join(INCIDENT[1:]))
        self.assertIn('added --nonblocking', done.stderr)

    def test_unattached_passthrough_is_silent(self):
        done = self.run_link('record', '-o', 'x', '--', 'python3', 'fixture.py')
        self.assertEqual(done.stdout.strip(), 'REAL record -o x -- python3 fixture.py')
        self.assertEqual(done.stderr, '')

    def test_missing_real_binary_fails_loudly(self):
        (Path(self.tmp.name) / 'home/.local/share/uv/tools/py-spy/bin/py-spy').unlink()
        done = self.run_link('record', '-p', '1')
        self.assertEqual(done.returncode, 127)
        self.assertIn('real py-spy not found', done.stderr)

    def test_real_path_linked_to_the_wrapper_fails_loudly(self):
        # A mis-run `ln -sfn` can point the uv path at the wrapper itself. The guard must refuse it
        # rather than exec itself: the re-exec'd copy derives its home from that deeper argv[0], so
        # today it stops one exec later, but only by luck of the layout.
        real = Path(self.tmp.name) / 'home/.local/share/uv/tools/py-spy/bin/py-spy'
        real.unlink()
        os.symlink(WRAPPER, real)
        done = self.run_link('record', '-p', '1', timeout=10)
        self.assertEqual(done.returncode, 127, done.stderr)
        self.assertEqual(done.stderr.count('real py-spy not found'), 1, done.stderr)
        self.assertNotIn('added --nonblocking', done.stderr)  # refused before rewriting/exec

    def test_env_override(self):
        other = Path(self.tmp.name) / 'other-py-spy'
        other.write_text('#!%s\nimport sys\nprint("OTHER " + " ".join(sys.argv[1:]))\n' % sys.executable)
        other.chmod(0o755)
        done = self.run_link('dump', '-p', '7', env_extra={'PY_SPY_REAL': str(other)})
        self.assertEqual(done.stdout.strip(), 'OTHER dump --nonblocking -p 7')


if __name__ == '__main__':
    unittest.main()
