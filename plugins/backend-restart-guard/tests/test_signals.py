"""Safety regressions: only hook data, never execute a signal command."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signal_guard_test', HERE / '__init__.py')
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

CASES = [
    ('usr2', 'kill -USR2 {gw}', True),
    ('nine', 'kill -9 {gw}', True),
    ('default', 'kill {gw}', True),
    ('pattern', "pkill -f 'hermes gateway'", True),
    ('python', "python3 -c 'import os,signal; os.kill({gw}, signal.SIGUSR2)'", True),
    ('probe', 'kill -0 {gw}', False),
    ('other_usr2', 'kill -USR2 {other}', False),
    ('other_default', 'kill {other}', False),
    ('term', '/bin/kill -TERM {gw}', True),
    ('quit', 'sudo kill -QUIT {gw}', True),
    ('abrt', 'kill -s ABRT {gw}', True),
    ('numeric', 'kill -n 9 {gw}', True),
    ('long_signal', 'kill --signal=USR2 {gw}', True),
    ('probe_s', 'kill -s 0 {gw}', False),
    ('list', 'kill -l', False),
    ('group', 'kill -TERM -- -{group}', True),
    ('all', 'kill -9 -1', True),
    ('pkill_probe', "pkill -0 -f 'hermes gateway'", False),
    ('killall', 'killall {exe}', True),
    ('python_group', "python -c 'import os,signal; os.killpg({group}, signal.SIGTERM)'", True),
    ('python_probe', "python3 -c 'import os; os.kill({gw}, 0)'", False),
    ('late_list', 'kill -9 {gw} -l', True),
    ('sig0', 'kill -SIG0 {gw}', False),
    ('pkill_newest', 'pkill -n python3', True),
    ('pkill_backend_probe', "pkill -0 -f 'hermes serve'", False),
    ('wrapped_signal', 'python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- kill -9 {gw}', True),
]

class Signals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Separate group; cleanup terminates only our fixture children.
        self.child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], start_new_session=True)
        self.addCleanup(self.reap, self.child)
        self.other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], start_new_session=True)
        self.addCleanup(self.reap, self.other)
        self.home = Path(self.tmp.name)
        (self.home / 'gateway.pid').write_text(str(self.child.pid))
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.home), 'HERMES_BACKEND_RESTART_GUARD': 'on'})
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def reap(proc):
        proc.terminate()
        proc.wait(timeout=10)

    def command(self, template):
        return template.format(gw=self.child.pid, other=self.other.pid, group=self.child.pid,
                               exe=Path(sys.executable).name)

    def hermes(self, command, denied):
        verdict = guard._pre_tool_call('terminal', {'command': command})
        self.assertEqual(bool(verdict), denied, command)
        if denied:
            self.assertEqual(verdict['action'], 'block')
            self.assertIn('py-spy dump --nonblocking --pid', verdict['message'])
            self.assertIn('sample', verdict['message'])

    def claude(self, command, denied):
        res = subprocess.run([sys.executable, str(HERE / 'claude_hook.py')],
            input=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}}),
            capture_output=True, text=True, timeout=15)
        self.assertEqual(res.returncode, 2 if denied else 0, res.stderr or command)
        if denied:
            self.assertEqual(json.loads(res.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
            self.assertIn('py-spy dump --nonblocking --pid', res.stderr)

    def test_json_pid(self):
        (self.home / 'gateway.pid').write_text(json.dumps({'pid': self.child.pid}))
        self.hermes(self.command('kill {gw}'), True)
        self.claude(self.command('kill {gw}'), True)

    def test_discovery_roles_parents_and_failure(self):
        ps = '81234 81233 81234 python3 -m hermes_cli.main gateway run\n81233 1 81233 /bin/sh hermes-wrapper\n81235 1 81235 hermes serve\n81236 1 81236 hermes dashboard\n81237 1 81237 python3 hub_live_browser.py serve\n'
        with patch.object(guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, ps)):
            self.assertEqual(set(guard.protected_processes()), {81233, 81234, 81235, 81236})
        with patch.object(guard.subprocess, 'run', side_effect=OSError('fixture')), self.assertLogs(guard.logger, level='WARNING'):
            self.assertEqual(guard.protected_processes(), {})

    def test_unreadable_pid_logs(self):
        (self.home / 'gateway.pid').write_text('not a pid')
        with self.assertLogs(guard.logger, level='WARNING') as logs:
            guard.protected_processes()
        self.assertIn('fail open', ' '.join(logs.output))

    def test_ssh_inspection_options(self):
        rows = {81234: {'ppid': 1, 'pgid': 81234, 'args': 'hermes gateway run'}}
        with patch.object(guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(rows))) as run:
            guard.protected_processes((['-i', 'key', '-o', 'Port=22', '-o', 'ProxyCommand=BAD', '-L', '8080:localhost:80', '-E', 'logfile'], 'example.com'))
            argv = run.call_args.args[0]
            self.assertNotIn('ProxyCommand=BAD', argv)
            self.assertNotIn('-L', argv)
            self.assertNotIn('-E', argv)
            self.assertIn('key', argv)
            self.assertIn('ClearAllForwardings=yes', argv)

    def test_remote_claude(self):
        # A fake SSH transport executes only discovery source, using the isolated home.
        shim = self.home / 'ssh'
        shim.write_text('#!' + sys.executable + '\nimport subprocess,sys\nr=subprocess.run([sys.executable,"-"],input=sys.stdin.read(),text=True)\nsys.exit(r.returncode)\n')
        shim.chmod(0o700)
        with patch.dict(os.environ, {'PATH': str(self.home) + os.pathsep + os.environ['PATH']}):
            self.claude(self.command('ssh -i key -o BatchMode=yes example.com kill -USR2 {gw}'), True)
            self.claude(self.command('ssh example.com kill -0 {gw}'), False)

    def test_profile_pid(self):
        (self.home / 'gateway.pid').unlink()
        p = self.home / 'profiles/example/gateway.pid'
        p.parent.mkdir(parents=True)
        p.write_text(str(self.child.pid))
        self.hermes(self.command('kill {gw}'), True)
        self.claude(self.command('kill {gw}'), True)

    def test_remote_snapshot(self):
        # Remote PID differs from local PID; the SSH payload is inspection only.
        with patch.object(guard, 'protected_processes', return_value={81234: {'ppid': 1, 'pgid': 81234, 'args': 'hermes gateway run'}}) as lookup:
            self.hermes('ssh -i key -o BatchMode=yes example.com kill -USR2 81234', True)
            self.assertIsNotNone(lookup.call_args.args[0])

    def test_execute_code(self):
        v = guard._pre_tool_call('execute_code', {'code': self.command('import os,signal; os.kill({gw}, signal.SIGUSR2)')})
        self.assertEqual(v['action'], 'block')

for name, template, denied in CASES:
    for path in ('hermes', 'claude'):
        def test(self, template=template, denied=denied, path=path):
            getattr(self, path)(self.command(template), denied)
        setattr(Signals, 'test_' + path + '_' + name, test)
