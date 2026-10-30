"""service-owner-guard: one test per entry path. Fixtures only; no real service is ever touched."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "claude_hook.py"
CARD = "t_abc123"
OWNER = CARD + "@macbook"
LABEL = "com.example.owned-svc"


def load():
    spec = importlib.util.spec_from_file_location("service_owner_guard", PLUGIN / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.work = self.home / "svcwork"
        self.work.mkdir()
        (self.work / "OWNER.md").write_text(f"card: {OWNER}\nwhy: fixture\n")
        agents = self.home / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        import plistlib
        (agents / f"{LABEL}.plist").write_bytes(plistlib.dumps(
            {"Label": LABEL, "WorkingDirectory": str(self.work), "ProgramArguments": ["/bin/sleep", "1"]}))
        (agents / "com.example.free-svc.plist").write_bytes(plistlib.dumps(
            {"Label": "com.example.free-svc", "ProgramArguments": ["/bin/sleep", "1"]}))
        self.log = self.home / "guard.log"
        env = {"HOME": str(self.home), "SERVICE_OWNER_GUARD_LOG": str(self.log)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("HERMES_KANBAN_TASK", None)
        os.environ.pop("SERVICE_OWNER_GUARD", None)
        self.g = load()
        # no real launchctl/systemctl: `print` and `show` fail unless a test stubs them
        real = self.g.run_cmd
        self.g.run_cmd = lambda argv, timeout=5: (1, "") if argv[0] in ("launchctl", "systemctl") else real(argv, timeout)

    def verdict(self, cmd, tool="terminal"):
        r = self.g._pre_tool_call(tool, {"command": cmd} if tool == "terminal" else {"code": cmd})
        return r["message"] if r and r.get("action") == "block" else None


class HermesHook(Base):
    def test_bootout_refused_then_allowed_with_approval(self):
        msg = self.verdict(f"launchctl bootout gui/501/{LABEL}")
        self.assertIn(f"card {OWNER}", msg)
        self.assertIn(f"owner-approved={CARD}", msg)
        self.assertIsNone(self.verdict(f"launchctl bootout gui/501/{LABEL}  # owner-approved={CARD}"))
        self.assertIn("refus", self.log.read_text())

    def test_wrong_approval_id_still_refused(self):
        self.assertIsNotNone(self.verdict(f"launchctl bootout gui/501/{LABEL} # owner-approved=t_ffff"))

    def test_own_card_allowed(self):
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": CARD}):
            self.assertIsNone(self.verdict(f"launchctl bootout gui/501/{LABEL}"))

    def test_no_owner_allowed_but_disable_needs_checked_none(self):
        self.assertIsNone(self.verdict("launchctl bootout gui/501/com.example.free-svc"))
        self.assertIn("owner-checked=none", self.verdict("launchctl disable gui/501/com.example.free-svc"))
        self.assertIsNone(self.verdict("launchctl disable gui/501/com.example.free-svc # owner-checked=none"))

    def test_unrelated_commands_allowed(self):
        for cmd in ("launchctl list", "echo launchctl bootout x", "git status", "launchctl print gui/501/x"):
            self.assertIsNone(self.verdict(cmd), cmd)


class ClaudeHook(Base):
    def run_hook(self, cmd):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                              capture_output=True, timeout=20, env=dict(os.environ))

    def test_deny_json_then_allow(self):
        r = self.run_hook(f"launchctl bootout gui/501/{LABEL}")
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn(f"card {OWNER}", out["permissionDecisionReason"])
        r = self.run_hook(f"launchctl bootout gui/501/{LABEL} # owner-approved={CARD}")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))

    def test_install_idempotent(self):
        s = self.home / "settings.json"
        s.write_text(json.dumps({"hooks": {"PreToolUse": []}}))
        spec = importlib.util.spec_from_file_location("sog_hook", HOOK)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.assertTrue(hook.install(s))
        self.assertFalse(hook.install(s))
        self.assertEqual(len(json.loads(s.read_text())["hooks"]["PreToolUse"]), 1)


class SshWrapped(Base):
    CMD = f"ssh mini 'launchctl disable gui/501/{LABEL}'"

    def test_remote_owner_refused(self):
        calls = []

        def remote(ssh_prefix, spec):
            calls.append((ssh_prefix, spec))
            return {"status": "found", "owner": OWNER, "source": "/x/OWNER.md"}
        self.g.remote_lookup = remote
        msg = self.verdict(self.CMD)
        self.assertIn(f"card {OWNER}", msg)
        self.assertEqual(calls[0][0][-1], "mini")
        self.assertEqual(calls[0][1]["label"], LABEL)

    def test_unreachable_disable_needs_checked_none(self):
        self.g.remote_lookup = lambda p, s: {"status": "failed", "error": "timeout"}
        self.assertIn("owner-checked=none", self.verdict(self.CMD))
        self.assertIsNone(self.verdict(self.CMD + " # owner-checked=none"))
        self.assertIn("failed", self.log.read_text())

    def test_unreachable_bootout_allowed_and_logged(self):
        self.g.remote_lookup = lambda p, s: {"status": "failed", "error": "timeout"}
        self.assertIsNone(self.verdict(f"ssh mini 'launchctl bootout gui/501/{LABEL}'"))

    def test_real_ssh_unreachable_is_bounded(self):
        # no stub: a real ssh to an invalid host must fail fast (< 9 s), not hang
        g = load()
        t = time.time()
        res = g.remote_lookup(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "nohost.invalid"],
                              {"kind": "launchd", "label": LABEL, "domain": "gui/501"})
        self.assertEqual(res["status"], "failed")
        self.assertLess(time.time() - t, 9)


class RemoteScript(Base):
    def test_shipped_script_runs_standalone_and_finds_owner(self):
        spec = {"kind": "launchd", "label": LABEL, "domain": "gui/501"}
        res = subprocess.run(["python3", "-", json.dumps(spec)], input=self.g.remote_script(spec), text=True,
                             capture_output=True, timeout=20, env=dict(os.environ))
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual((out["status"], out["owner"]), ("found", OWNER))


class ReloadsAndRestarts(Base):
    def test_reload_pair_allowed(self):
        p = self.home / f"Library/LaunchAgents/{LABEL}.plist"
        self.assertIsNone(self.verdict(f"launchctl bootout gui/501/{LABEL} && launchctl bootstrap gui/501 {p}"))
        self.assertIsNone(self.verdict(f"launchctl unload {p}; launchctl load {p}"))

    def test_bootout_then_bootstrap_of_other_label_refused(self):
        self.assertIsNotNone(self.verdict(
            f"launchctl bootout gui/501/{LABEL} && launchctl bootstrap gui/501 /x/com.other.plist"))

    def test_kickstart_enable_bootstrap_allowed(self):
        for cmd in (f"launchctl kickstart -k gui/501/{LABEL}", f"launchctl enable gui/501/{LABEL}",
                    f"launchctl bootstrap gui/501 /x/{LABEL}.plist", "systemctl --user restart foo.service"):
            self.assertIsNone(self.verdict(cmd), cmd)


class LocalKill(Base):
    def spawn(self, cwd):
        p = subprocess.Popen(["sleep", "60"], cwd=cwd)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        time.sleep(0.5)
        return p

    def test_owned_refused_own_card_and_unmarked_allowed(self):
        p = self.spawn(self.work)
        msg = self.verdict(f"kill {p.pid}")
        self.assertIn(f"card {OWNER}", msg)
        self.assertIn(f"{p.pid}", msg)
        self.assertIsNotNone(self.verdict(f"kill -9 {p.pid}"))
        self.assertIsNone(self.verdict(f"kill {p.pid} # owner-approved={CARD}"))
        with mock.patch.dict(os.environ, {"HERMES_KANBAN_TASK": CARD}):
            self.assertIsNone(self.verdict(f"kill {p.pid}"))
        free = self.home / "free"
        free.mkdir()
        q = self.spawn(free)
        self.assertIsNone(self.verdict(f"kill {q.pid}"))
        self.assertIsNone(self.verdict(f"kill -0 {p.pid}"))

    def test_ancestor_process_cwd_marker_counts(self):
        free = self.home / "free2"
        free.mkdir()
        parent = subprocess.Popen(
            [sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen(['sleep','60'], cwd=sys.argv[1]); time.sleep(60)",
             str(free)], cwd=self.work)
        self.addCleanup(lambda: (subprocess.run(["pkill", "-P", str(parent.pid)]), parent.kill(), parent.wait()))
        time.sleep(1)
        child = int(subprocess.run(["pgrep", "-P", str(parent.pid)], capture_output=True, text=True).stdout.split()[0])
        self.assertIn(f"card {OWNER}", self.verdict(f"kill {child}"))  # child's cwd is unmarked; its parent's is marked


class Systemd(Base):
    def test_owned_unit_stop_refused(self):
        seen = []

        def run(argv, timeout=5):
            seen.append(argv)
            if argv[:2] == ["systemctl", "--user"] and "show" in argv:
                return 0, f"Environment=FOO=1 HERMES_OWNER_CARD={OWNER}\nWorkingDirectory=/nonexistent\nExecStart=\n"
            return (1, "") if argv[0] in ("launchctl", "systemctl") else real(argv, timeout)
        real = self.g.run_cmd
        self.g.run_cmd = run
        msg = self.verdict("systemctl --user stop my-collector.service")
        self.assertIn(f"card {OWNER}", msg)
        self.assertTrue(any("show" in a and "Environment,WorkingDirectory,ExecStart" in " ".join(a) for a in seen))
        self.assertIsNone(self.verdict(f"systemctl --user stop my-collector.service # owner-approved={CARD}"))
        self.assertIsNone(self.verdict("systemctl --user start my-collector.service"))


class ExecuteCode(Base):
    def test_python_subprocess_bootout_refused(self):
        code = f'import subprocess\nsubprocess.run(["launchctl","bootout","gui/501/{LABEL}"])\n'
        self.assertIn(f"card {OWNER}", self.verdict(code, tool="execute_code"))
        self.assertIsNone(self.verdict(code + f"# owner-approved={CARD}\n", tool="execute_code"))
        self.assertIsNone(self.verdict('import subprocess\nsubprocess.run(["launchctl","list"])\n', tool="execute_code"))

    def test_nested_tool_call_batch(self):
        r = self.g._pre_tool_call("tool_call", {"calls": [
            {"name": "terminal", "arguments": {"command": f"launchctl bootout gui/501/{LABEL}"}}]})
        self.assertEqual(r["action"], "block")


if __name__ == "__main__":
    unittest.main()
