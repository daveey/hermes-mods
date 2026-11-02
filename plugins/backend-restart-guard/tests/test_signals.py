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

    def fake_ssh(self, connect_delay=0.0):
        """SSH shim: spends ``connect_delay`` s in connection setup (as a ProxyCommand route probe does),
        honours -o ConnectTimeout like OpenSSH (banner timeout, rc 255), then runs only the discovery
        source locally against the isolated home."""
        shim = self.home / 'ssh'
        shim.write_text('#!' + sys.executable + '\n'
                        'import re,subprocess,sys,time\n'
                        f'delay={connect_delay!r}\n'
                        'm=re.search(r"ConnectTimeout=(\\d+)", " ".join(sys.argv))\n'
                        'if m and delay > int(m.group(1)):\n'
                        '    time.sleep(int(m.group(1)))\n'
                        '    sys.stderr.write("Connection timed out during banner exchange\\n"); sys.exit(255)\n'
                        'time.sleep(delay)\n'
                        'r=subprocess.run([sys.executable,"-"],input=sys.stdin.read(),text=True)\n'
                        'sys.exit(r.returncode)\n')
        shim.chmod(0o700)
        return patch.dict(os.environ, {'PATH': str(self.home) + os.pathsep + os.environ['PATH']})

    # The read-only command from the 2026-11-01 13:59 PT incident (host/path scrubbed): the only
    # "kill" is a word inside a double-quoted grep pattern nested in $(...).
    INCIDENT = ("timeout 120 ssh -o BatchMode=yes -o ConnectTimeout=20 ubuntu@example.com "
                "'P=~/work/probe.py; echo \"== ssh alias box: $(ssh -G box 2>/dev/null | grep -E "
                "\"^(hostname|user|proxycommand) \" | tr \"\\n\" \" \" | cut -c1-200)\"; echo \"== probe "
                "script ($(wc -l < $P) lines), writes/side effects: $(grep -n -E \"open\\([^)]*[\\\"\\x27][wa]"
                "|unlink|remove|kill|launchctl|hermes update|subprocess|os\\.system\" $P | cut -c1-120 | "
                "tr \"\\n\" \";\" | cut -c1-600)\"; sed -n \"1,40p\" $P | cut -c1-170; echo \"== run once, "
                "exactly as the wake check:\"; timeout 60 ssh -i ~/.ssh/key -o BatchMode=yes -o "
                "ConnectTimeout=10 you@example.net python3 < $P 2>&1 | tail -5 | cut -c1-300; echo "
                "\"rc=${PIPESTATUS[0]}\"' 2>&1 | grep -v '^hub SSH route:'")

    def test_incident_command_runs_no_discovery(self):
        with patch.object(guard, 'protected_processes', return_value={}) as lookup:
            self.assertIsNone(guard._pre_tool_call('terminal', {'command': self.INCIDENT}))
        self.assertEqual(lookup.call_args_list, [])
        self.assertNotIn(['kill'], guard._segments(self.INCIDENT.split("'")[1]))

    def test_quoted_kill_word_in_substitution_is_not_a_command(self):
        for cmd in ('echo "$(grep -E "a|kill|b" f)"', 'echo "`grep -E "a|kill|b" f`"',
                    'x=$(grep -c "kill|launchctl" log); echo $x'):
            with patch.object(guard, 'protected_processes', return_value={}) as lookup:
                guard._pre_tool_call('terminal', {'command': cmd})
            self.assertEqual(lookup.call_args_list, [], cmd)

    def test_substitution_bodies_still_checked(self):
        with self.fake_ssh():
            for template in ("ssh example.com 'echo $(kill {gw})'", 'ssh example.com kill {gw}',
                             'echo $(kill -9 {gw})', 'echo "x $(kill {gw})"', 'echo `kill {gw}`',
                             "ssh example.com 'echo \"$(kill -USR2 {gw})\"'"):
                self.hermes(self.command(template), True)
            self.hermes(self.command("ssh example.com 'echo $(kill {other})'"), False)
        self.assertTrue(guard.check_shell('echo $(systemctl restart hermes-gateway)'))
        self.assertTrue(guard.check_shell('echo "`hermes gateway restart`"'))

    def test_remote_discovery_tolerates_slow_proxy_connect(self):
        with self.fake_ssh(connect_delay=4.0):
            rows = guard.protected_processes(([], 'example.com'))
        self.assertIn(self.child.pid, rows)

    def test_remote_discovery_failure_logs_one_line(self):
        with self.fake_ssh(connect_delay=30.0), \
                patch.object(guard, 'REMOTE_CONNECT_TIMEOUT', 1), \
                self.assertLogs(guard.logger, level='WARNING') as logs:
            self.assertEqual(guard.protected_processes(([], 'example.com')), {})
        self.assertEqual(len(logs.records), 1)
        rec = logs.records[0]
        self.assertIsNone(rec.exc_info)
        self.assertIn('PID discovery failed open', rec.getMessage())
        self.assertIn('rc=255', rec.getMessage())
        self.assertIn('banner exchange', rec.getMessage())

    def test_execute_code(self):
        v = guard._pre_tool_call('execute_code', {'code': self.command('import os,signal; os.kill({gw}, signal.SIGUSR2)')})
        self.assertEqual(v['action'], 'block')

for name, template, denied in CASES:
    for path in ('hermes', 'claude'):
        def test(self, template=template, denied=denied, path=path):
            getattr(self, path)(self.command(template), denied)
        setattr(Signals, 'test_' + path + '_' + name, test)

# The methods belong to Signals; the last loop-local binding is not a free
# pytest test (it requires unittest's self).
del test


def test_generated_case_inventory():
    expected = {'test_' + path + '_' + name
                for name, _, _ in CASES for path in ('hermes', 'claude')}
    assert expected <= set(vars(Signals))
    assert len(expected) == len(CASES) * 2
    assert 'test' not in globals()
