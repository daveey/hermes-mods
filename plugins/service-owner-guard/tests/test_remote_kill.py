"""Remote process checks only read process state; no kill command is executed."""
import ast
import io
import json
import os
import platform
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from test_guard import Base, CARD, OWNER, HOOK


class RemoteKill(Base):
    def stub(self):
        self.calls = []
        def lookup(prefix, spec):
            self.calls.append((prefix, spec))
            return {"status": "found", "owner": OWNER, "source": "/fixture/OWNER.md", "label": "4242"}
        self.g.remote_lookup = lookup

    def script_result(self, spec, run):
        ns = {"__name__": "__remote_fixture__"}
        script = self.g.remote_script(spec)
        output = io.StringIO()
        with mock.patch.object(platform, "system", return_value="Darwin"), mock.patch.object(sys, "argv", ["-", json.dumps(spec)]), mock.patch.object(subprocess, "run", run), redirect_stdout(output):
            exec(compile(script, "<remote>", "exec"), ns)
        return json.loads(output.getvalue())

    def process_fixture(self, argv, **kwargs):
        self.seen.append(argv)
        if argv[0] == "pgrep":
            return subprocess.CompletedProcess(argv, 0, "4242\n", "")
        if argv[:2] == ["ps", "-Ao"]:
            return subprocess.CompletedProcess(argv, 0, "4242 1 0.0 1024 00:10 collector\n", "")
        if argv[0] == "lsof":
            return subprocess.CompletedProcess(argv, 0, "n" + str(self.work) + "\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "")

    def test_pkill_pgrep_flags_resolved_on_remote(self):
        self.seen = []
        self.g.remote_lookup = lambda p, s: self.script_result(s, self.process_fixture)
        self.assertIsNotNone(self.verdict("ssh mini 'pkill -f -u 501 collector'"))
        self.assertIn(["pgrep", "-f", "-u", "501", "collector"], self.seen)

    def test_pkill_signal_and_selection_flags(self):
        for flags in ('-9 -f', '-TERM -f', '--signal TERM -f', '-n -f', '-o -f', '-l -f'):
            with self.subTest(flags=flags):
                self.seen = []
                self.g.remote_lookup = lambda p, s: self.script_result(s, self.process_fixture)
                self.assertIsNotNone(self.g.check("ssh mini 'pkill " + flags + " collector'"))
                expected = flags.split()
                if expected[0] in ('-9', '-TERM'):
                    expected = expected[1:]
                elif expected[0] == '--signal':
                    expected = expected[2:]
                self.assertIn(['pgrep'] + expected + ['collector'], self.seen)

    def test_ssh_stdin_suppressing_flags_removed(self):
        self.stub()
        self.assertIsNotNone(self.g.check("ssh -n -f mini 'kill 4242'"))
        self.assertNotIn('-n', self.calls[0][0])
        self.assertNotIn('-f', self.calls[0][0])

    def test_approval_of_one_pid_does_not_approve_other_owner(self):
        self.seen = []
        def run(argv, **kwargs):
            if argv[:2] == ['ps', '-Ao']:
                return subprocess.CompletedProcess(argv, 0, '4242 1 0 1024 00:10 a\n4243 1 0 1024 00:10 b\n', '')
            if argv[:2] == ['ps', '-E']:
                return subprocess.CompletedProcess(argv, 0, '4242 a HERMES_OWNER_CARD=' + OWNER + '\n4243 b HERMES_OWNER_CARD=t_def456@remote\n', '')
            return self.process_fixture(argv, **kwargs)
        self.g.remote_lookup = lambda p, s: self.script_result(s, run)
        msg = self.g.check("ssh mini 'kill 4242 4243' # owner-approved=" + CARD)
        self.assertIsNotNone(msg)
        self.assertIn('card t_def456@remote', msg)

    def test_multiple_kill_pids_one_ssh_lookup(self):
        self.calls = []
        self.g.remote_lookup = lambda p, s: self.calls.append(s) or {'status': 'none'}
        self.assertIsNone(self.g.check("ssh mini 'kill 1610 1611 1612'"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['pids'], [1610, 1611, 1612])

    def test_killall_exact_name_on_remote(self):
        self.seen = []
        self.g.remote_lookup = lambda p, s: self.script_result(s, self.process_fixture)
        self.assertIsNotNone(self.verdict("ssh mini 'killall -TERM collector'"))
        self.assertIn(["pgrep", "-x", "collector"], self.seen)

    def test_real_unroutable_lookup_allowed_and_logged(self):
        self.g.REMOTE_TIMEOUT = 1
        self.assertIsNone(self.verdict("ssh -o ConnectTimeout=1 192.0.2.1 'kill 4242'"))
        self.assertTrue(self.log.exists(), "remote kill must attempt lookup and log its failure")
        self.assertIn("lookup failed", self.log.read_text())
        self.assertIn("allowed", self.log.read_text())

    def test_unowned_incident_shape_checks_pid_without_running_command(self):
        calls = []
        self.g.remote_lookup = lambda p, s: calls.append(s) or {"status": "none"}
        cmd = "ssh mini 'lsof -nP -iTCP:23374; kill -TERM 1610; sleep 25; launchctl print gui/501/com.example.server'"
        self.assertIsNone(self.verdict(cmd))
        self.assertEqual([s["pid"] for s in calls], [1610])

    def test_claude_stdin_deny_json(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("hook_fixture", HOOK)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.stub()
        payload = {"tool_name": "Bash", "tool_input": {"command": "ssh mini 'kill 4242'"}}
        output = io.StringIO()
        with mock.patch.object(hook, "load_guard", return_value=self.g), mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), mock.patch.object(sys, "argv", [str(HOOK)]), redirect_stdout(output):
            self.assertEqual(hook.main(), 0)
        self.assertTrue(output.getvalue(), "owned ssh kill must emit denial JSON")
        out = json.loads(output.getvalue())["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn(OWNER, out["permissionDecisionReason"])

    def test_python39_script_pid_lookup(self):
        spec = {"kind": "pids", "verb": "kill", "pid": 4242}
        script = self.g.remote_script(spec)
        ast.parse(script, feature_version=(3, 9))
        self.seen = []
        res = self.script_result(spec, self.process_fixture)
        self.assertEqual(res.get("owner"), OWNER)
        r = subprocess.run(["/usr/bin/python3", "-c", "import sys; compile(sys.stdin.read(), '<remote>', 'exec')"], input=script, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_remote_nesting_each_entry(self):
        self.stub()
        cases = [
            ("terminal", {"command": "bash -c \"ssh mini 'kill 4242'\""}),
            ("execute_code", {"code": "subprocess.run(['ssh', 'mini', 'kill 4242'])"}),
            ("tool_call", {"calls": [{"name": "terminal", "arguments": {"command": "ssh mini 'kill 4242'"}}]}),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool):
                self.assertIsNotNone(self.g.classify(tool, args))

    def test_probes_do_not_lookup(self):
        with mock.patch.object(self.g, "remote_lookup", side_effect=AssertionError("probe looked up")):
            for args in ("-0 4242", "-l", "-s 0 4242", "-n 0 4242"):
                self.assertIsNone(self.g.check("ssh mini 'kill " + args + "'"))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'requires real Linux /proc')
    def test_linux_proc_owner_environment_and_cwd(self):
        def lookup(pid):
            spec = {"kind": "pids", "verb": "kill", "pid": pid}
            r = subprocess.run([sys.executable, "-", json.dumps(spec)], input=self.g.remote_script(spec), text=True, capture_output=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        free = self.home / 'free'
        free.mkdir()
        for cwd, env in [(self.work, dict(os.environ)), (free, dict(os.environ, HERMES_OWNER_CARD=OWNER))]:
            with subprocess.Popen(['sleep', '60'], cwd=cwd, env=env) as child:
                try:
                    res = lookup(child.pid)
                    self.assertEqual(res.get('owner'), OWNER, res)
                    self.assertIn('OWNER.md' if cwd == self.work else 'env', res['source'])
                finally:
                    child.terminate()
                    child.wait()
        with subprocess.Popen([sys.executable, '-c', "import subprocess,sys; p=subprocess.Popen(['sleep','60'], cwd=sys.argv[1]); print(p.pid, flush=True); p.wait()", str(free)], cwd=self.work, stdout=subprocess.PIPE, text=True) as parent:
            pid = int(parent.stdout.readline())
            try:
                res = lookup(pid)
                self.assertEqual(res.get('owner'), OWNER, res)
                self.assertIn('OWNER.md', res['source'])
            finally:
                # Only the test's own fixture child; never a discovered service.
                os.kill(pid, 15)
                parent.wait(timeout=10)

    def test_kill_refused_override_and_own_card(self):
        self.stub()
        cmd = "ssh mini 'kill 4242'"
        self.assertIsNotNone(self.verdict(cmd))
        self.assertIn("card " + OWNER, self.verdict(cmd))
        self.assertIn("/fixture/OWNER.md", self.verdict(cmd))
        self.assertIsNone(self.verdict(cmd + " # owner-approved=" + CARD))
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": CARD}):
            self.assertIsNone(self.verdict(cmd))
        self.assertEqual(self.calls[0][1]["pid"], 4242)
        self.assertEqual(self.calls[0][1]["kind"], "pids")
        self.assertIn("override owner-approved", self.log.read_text())
