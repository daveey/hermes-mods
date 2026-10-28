"""Profile teardown and Chrome load-budget helpers. No real Chrome."""
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chrome_test_fixtures import (
    CHROME_READY_S, cleanup_disposable_tree, processes_holding_profile,
    reap_profile_processes, remove_tree_retrying, wait_until_ready)


def _hold_profile(root):
    """A process that stays up and names the profile the way Chrome does."""
    return subprocess.Popen([
        sys.executable, '-c', 'import time; time.sleep(30)',
        '--user-data-dir=' + root])


class _Proc:
    def __init__(self, code=None):
        self.pid = 4242
        self.code = code

    def poll(self):
        return self.code


class ProfileTeardownTests(unittest.TestCase):
    def test_chrome_ready_budget_covers_the_missed_20s_wait(self):
        # 4221d768 missed a 20s budget by under 50ms while Chrome was still alive.
        self.assertGreaterEqual(CHROME_READY_S, 60)

    def test_wait_returns_once_ready(self):
        calls = {'n': 0}

        def ready():
            calls['n'] += 1
            return calls['n'] >= 3

        wait_until_ready(ready, _Proc(), 2, lambda: 'unused')
        self.assertGreaterEqual(calls['n'], 3)

    def test_wait_includes_exit_and_detail(self):
        with self.assertRaises(AssertionError) as err:
            wait_until_ready(lambda: None, _Proc(code=1), 2, lambda: 'chrome log tail')
        self.assertIn('exited 1', str(err.exception))
        self.assertIn('chrome log tail', str(err.exception))

    def test_wait_includes_detail_at_the_deadline(self):
        with self.assertRaises(AssertionError) as err:
            wait_until_ready(lambda: None, _Proc(), 0.05, lambda: 'still starting')
        text = str(err.exception)
        self.assertIn('not ready within', text)
        self.assertIn('still starting', text)

    def test_remove_retries_enotempty(self):
        root = Path(tempfile.mkdtemp(prefix='profile-retry-'))
        (root / 'Default').mkdir()
        (root / 'Default' / 'a').write_text('x')
        real = shutil.rmtree
        calls = {'n': 0}

        def flaky(path, *args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OSError(errno.ENOTEMPTY, 'Directory not empty', 'Default')
            return real(path, *args, **kwargs)

        with mock.patch('chrome_test_fixtures.shutil.rmtree', flaky):
            remove_tree_retrying(root)
        self.assertFalse(root.exists())
        self.assertGreaterEqual(calls['n'], 2)

    def test_remove_refuses_a_live_profile_process(self):
        root = tempfile.mkdtemp(prefix='profile-live-')
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        proc = _hold_profile(root)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        with mock.patch('chrome_test_fixtures.reap_profile_processes', return_value=[proc.pid]):
            with self.assertRaises(AssertionError) as err:
                remove_tree_retrying(root)
        self.assertIn(str(proc.pid), str(err.exception))
        proc.kill()
        proc.wait(timeout=5)

    def test_reap_stops_a_process_named_with_the_profile(self):
        root = tempfile.mkdtemp(prefix='profile-reap-')
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        proc = _hold_profile(root)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertIn(proc.pid, processes_holding_profile(root))
        left = reap_profile_processes(root, wait_s=2)
        self.assertEqual(left, [])
        self.assertIsNotNone(proc.wait(timeout=2))

    def test_cleanup_removes_a_temporary_directory(self):
        tmp = tempfile.TemporaryDirectory(prefix='profile-cleanup-')
        marker = Path(tmp.name) / 'Default'
        marker.mkdir()
        (marker / 'a').write_text('x')
        cleanup_disposable_tree(tmp)
        self.assertFalse(os.path.exists(tmp.name))
        # The finalizer was detached by cleanup(); a second pass must not raise.
        tmp.cleanup()
