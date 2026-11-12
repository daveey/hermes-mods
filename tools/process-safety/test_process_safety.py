import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

starve = load('starve_owned')
health = load('agent_health')

def publish_pid(marker, delay=0.0):
    """Child code that publishes its pid atomically, so the marker never exists empty.

    `delay` sleeps between creating and filling the temp file (tests the race window)."""
    tmp = str(marker) + '.tmp'
    return (f'f=open({tmp!r},"w"); time.sleep({delay}); f.write(str(os.getpid())); f.close(); '
            f'os.replace({tmp!r}, {str(marker)!r})')

def tagged(tag):
    """(pid, state) of every other live process whose argv mentions `tag`."""
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if tag.encode() in (entry / 'cmdline').read_bytes():
                state = (entry / 'stat').read_text().rsplit(')', 1)[1].split()[0]
                if state != 'Z':
                    found.append((int(entry.name), state))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
    return found

def kill_tagged(tag):
    """SIGKILL (effective on stopped processes) every other process whose argv mentions `tag`.

    Pidfds pin identity between the argv check and the signal."""
    for pid, _ in tagged(tag):
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            if tag.encode() in Path(f'/proc/{pid}/cmdline').read_bytes():
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
        finally:
            os.close(fd)

class SafetyTests(unittest.TestCase):
    def test_foreign_same_name_process_never_receives_signal(self):
        # Same executable as the target, but a foreign session, like a shared agent.
        foreign = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        actual = signal.pidfd_send_signal
        seen = []
        def record(fd, sig):
            info = Path(f'/proc/self/fdinfo/{fd}').read_text()
            self.assertNotIn(f'Pid:\t{foreign.pid}\n', info)
            seen.append(sig)
            return actual(fd, sig)
        try:
            with patch.object(signal, 'pidfd_send_signal', record):
                rc = starve.run([sys.executable, '-c', 'import time; time.sleep(.15)'], .03, .02, 5)
            self.assertEqual(rc, 0)
            self.assertIn(signal.SIGSTOP, seen)
            self.assertNotIn('\nState:\tT', Path(f'/proc/{foreign.pid}/status').read_text())
        finally:
            foreign.kill(); foreign.wait()

    def test_timeout_does_not_leave_child_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / 'pid'
            cmd = [sys.executable, '-c', f'import os,time; {publish_pid(marker)}; time.sleep(60)']
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    starve.run(cmd, .04, .04, .5)
                pid = int(marker.read_text())
                self.assertFalse(Path(f'/proc/{pid}').exists())
            finally:
                # starve.run's own sweep normally kills it; never rely on the
                # code under test to clean up after a failing assertion.
                kill_tagged(d)

    def test_health_resumes_only_verified_gpg_agent(self):
        import shutil
        if not shutil.which('gpg-agent'):
            self.skipTest('gpg-agent unavailable')
        with tempfile.TemporaryDirectory() as d:
            agent = subprocess.Popen(['gpg-agent', '--homedir', d, '--server'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                time.sleep(.1)
                self.assertIsNone(agent.poll())
                agent.send_signal(signal.SIGSTOP)
                deadline = time.monotonic() + 2
                while '\nState:\tT' not in Path(f'/proc/{agent.pid}/status').read_text():
                    self.assertLess(time.monotonic(), deadline); time.sleep(.01)
                event = health.inspect_agent(agent.pid)
                self.assertTrue(event['recovered'])
                deadline = time.monotonic() + 2
                while '\nState:\tT' in Path(f'/proc/{agent.pid}/status').read_text():
                    self.assertLess(time.monotonic(), deadline); time.sleep(.01)
                self.assertFalse(health.inspect_agent(agent.pid)['recovered'])
            finally:
                agent.send_signal(signal.SIGCONT); agent.kill(); agent.wait(timeout=5)
                agent.stdin.close()

    def test_normal_exit_cleans_remaining_session_child(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / 'pid'
            code = ('import os,time; p=os.fork(); '
                    f'open({str(marker)!r},"w").write(str(p)) if p else None; '
                    'time.sleep(.2 if p else 30)')
            self.assertEqual(starve.run([sys.executable, '-c', code], .03, .02, 5), 0)
            pid = int(marker.read_text())
            status = Path(f'/proc/{pid}/status')
            deadline = time.monotonic() + 2
            while status.exists() and '\nState:\tZ' not in status.read_text():
                self.assertLess(time.monotonic(), deadline); time.sleep(.01)

    def test_permission_denied_does_not_skip_later_handles(self):
        with patch.object(signal, 'pidfd_send_signal', side_effect=[PermissionError(), None]) as sender:
            starve.send([10, 11], signal.SIGCONT)
        self.assertEqual(sender.call_count, 2)

    def test_publish_pid_writes_elsewhere_then_renames_onto_marker(self):
        # Pins atomicity directly, independent of the race-window delay that
        # test_pid_marker_never_visible_empty depends on (delay 0 included).
        for delay in (0, .3):
            with self.subTest(delay=delay), tempfile.TemporaryDirectory() as d:
                marker = Path(d) / 'pid'
                events = []
                def recording_open(path, *args, **kwargs):
                    events.append(('open', str(path)))
                    self.assertFalse(marker.exists(), 'marker visible before its contents')
                    return open(path, *args, **kwargs)
                def recording_replace(src, dst):
                    events.append(('replace', str(src), str(dst)))
                    self.assertTrue(Path(src).read_text(), 'renamed an empty pid file')
                    os.replace(src, dst)
                fake_os = type('fake_os', (), {'getpid': staticmethod(lambda: 4242),
                                               'replace': staticmethod(recording_replace)})
                fake_time = type('fake_time', (), {'sleep': staticmethod(lambda s: events.append(('sleep', s)))})
                exec(publish_pid(marker, delay), {'open': recording_open, 'os': fake_os, 'time': fake_time})
                opened = [e[1] for e in events if e[0] == 'open']
                self.assertTrue(opened)
                self.assertNotIn(str(marker), opened)
                self.assertEqual(events[-1][0], 'replace')
                self.assertEqual(events[-1][2], str(marker))
                self.assertIn(events[-1][1], opened)
                self.assertNotEqual(events[-1][1], str(marker))
                self.assertEqual(marker.read_text(), '4242')

    def test_pid_marker_never_visible_empty(self):
        # A marker created before its pid is written made polling readers parse ''.
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / 'pid'
            child = subprocess.Popen([sys.executable, '-c', f'import os,time; {publish_pid(marker, .3)}; time.sleep(30)'])
            try:
                deadline = time.monotonic() + 5
                while not marker.exists():
                    self.assertLess(time.monotonic(), deadline); time.sleep(.005)
                self.assertEqual(int(marker.read_text()), child.pid)
            finally:
                child.kill(); child.wait()

    def run_cli_sigterm(self, d, wait_for, marker_deadline=5.0):
        marker = Path(d) / 'pid'
        cmd = [sys.executable, '-c', f'import os,time; {publish_pid(marker)}; time.sleep(60)']
        wrapper = subprocess.Popen([sys.executable, str(HERE / 'starve_owned.py'), '--stopped', '.05', '--', *cmd])
        try:
            deadline = time.monotonic() + marker_deadline
            while not wait_for.exists():
                self.assertLess(time.monotonic(), deadline); time.sleep(.01)
            pid = int(marker.read_text())
            wrapper.terminate()
            self.assertEqual(wrapper.wait(timeout=5), 130)
            self.assertFalse(Path(f'/proc/{pid}').exists())
        finally:
            if wrapper.poll() is None:
                wrapper.kill(); wrapper.wait()
            # starve_owned runs the child in its own session, so killpg(wrapper)
            # would miss it; match the tmpdir in its argv instead, which works
            # even when the pid was never read (e.g. the marker deadline fired).
            kill_tagged(d)

    def test_cli_sigterm_reaps_its_stopped_child(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_cli_sigterm(d, Path(d) / 'pid')

    def test_failed_cli_sigterm_test_leaks_no_stopped_child(self):
        # The marker deadline fires before the pid is read: cleanup must still
        # kill the starved (SIGSTOPped, own-session) grandchild, which otherwise
        # holds the test's stdout open and hangs output-capturing harnesses.
        with tempfile.TemporaryDirectory() as d:
            try:
                with self.assertRaises(AssertionError):
                    self.run_cli_sigterm(d, Path(d) / 'never', marker_deadline=.5)
            finally:
                leaked = tagged(d)
                kill_tagged(d)
            self.assertEqual(leaked, [])

    def test_health_main_inactive_and_failure_and_json_log(self):
        import json
        for output, expected in [('MainPID=0\nActiveState=inactive\n', 0), ('MainPID=0\nActiveState=failed\n', 1), ('bad output', 1)]:
            with self.subTest(output=output), tempfile.TemporaryDirectory() as d:
                log = Path(d) / 'health.jsonl'
                with patch.object(sys, 'argv', ['health', '--log', str(log)]), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)):
                    self.assertEqual(health.main(), expected)
                record = json.loads(log.read_text())
                self.assertEqual('error' in record, bool(expected))

    def test_health_main_records_recovery(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'health.jsonl'
            with patch.object(sys, 'argv', ['health', '--log', str(log)]), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'MainPID=123\nActiveState=active\n')), patch.object(health, 'inspect_agent', return_value={'pid': 123, 'state': 'T', 'recovered': True}):
                self.assertEqual(health.main(), 0)
            self.assertTrue(json.loads(log.read_text())['recovered'])

    def test_health_rotates_bounded_sample_log(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'health.jsonl'
            log.write_text('x' * (8 * 1024 * 1024))
            with patch.object(sys, 'argv', ['health', '--log', str(log)]), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'MainPID=0\nActiveState=inactive\n')):
                self.assertEqual(health.main(), 0)
            self.assertEqual(log.with_suffix('.jsonl.1').stat().st_size, 8 * 1024 * 1024)
            self.assertLess(log.stat().st_size, 1000)

    def test_health_rejects_foreign_executable(self):
        child = subprocess.Popen(['sleep', '30'])
        try:
            with self.assertRaises(ValueError):
                health.inspect_agent(child.pid)
        finally:
            child.kill(); child.wait()

if __name__ == '__main__':
    unittest.main()
