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
            cmd = [sys.executable, '-c', f'import os,time; open({str(marker)!r},"w").write(str(os.getpid())); time.sleep(60)']
            with self.assertRaises(subprocess.TimeoutExpired):
                starve.run(cmd, .04, .04, .5)
            pid = int(marker.read_text())
            self.assertFalse(Path(f'/proc/{pid}').exists())

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

    def test_cli_sigterm_reaps_its_stopped_child(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / 'pid'
            cmd = [sys.executable, '-c', f'import os,time; open({str(marker)!r},"w").write(str(os.getpid())); time.sleep(60)']
            wrapper = subprocess.Popen([sys.executable, str(HERE / 'starve_owned.py'), '--stopped', '.05', '--', *cmd])
            pid = None
            try:
                deadline = time.monotonic() + 5
                while not marker.exists():
                    self.assertLess(time.monotonic(), deadline); time.sleep(.01)
                pid = int(marker.read_text())
                wrapper.terminate()
                self.assertEqual(wrapper.wait(timeout=5), 130)
                self.assertFalse(Path(f'/proc/{pid}').exists())
            finally:
                if wrapper.poll() is None:
                    wrapper.kill(); wrapper.wait()
                if pid is not None and Path(f'/proc/{pid}').exists():
                    os.kill(pid, signal.SIGKILL)

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
