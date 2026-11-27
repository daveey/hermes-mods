"""Source-review regressions: no model-selected executable SSH options or skill imports."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

from test_guard import Base, CARD, LABEL, PLUGIN


class SourceReview(Base):
    def fake_ssh(self):
        bindir = self.home / "bin"
        bindir.mkdir()
        log = self.home / "ssh.json"
        ssh = bindir / "ssh"
        ssh.write_text("#!" + sys.executable + "\nimport json,sys\nfrom pathlib import Path\n"
                       + "Path(" + repr(str(log)) + ").write_text(json.dumps(sys.argv[1:]))\n"
                       + "print('{\"status\": \"none\"}')\n")
        ssh.chmod(0o700)
        return bindir, log

    def test_model_ssh_executable_options_never_reach_guard_transport(self):
        bindir, log = self.fake_ssh()
        cases = ["-o 'ProxyCommand=echo PWNED'", "-oProxyCommand=echo_PWNED",
                 "-o 'LocalCommand=echo PWNED' -o PermitLocalCommand=yes",
                 "-o 'KnownHostsCommand=echo PWNED'", "-F /fixture/untrusted-config",
                 "-J untrusted-proxy", "-E /fixture/output", "-S /fixture/socket"]
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
            for opts in cases:
                with self.subTest(opts=opts):
                    log.unlink(missing_ok=True)
                    msg = self.g.check(f"ssh {opts} box systemctl stop foo.service")
                    self.assertFalse(log.exists(), "untrusted transport must not be invoked: "
                                     + (log.read_text() if log.exists() else ""))
                    self.assertIsNotNone(msg, "unsafe lookups must not fail open")
                    self.assertIn("SSH", msg)

    def test_ssh_destination_option_injection_never_runs(self):
        bindir, log = self.fake_ssh()
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
            self.assertIsNotNone(self.g.check("ssh -- -oProxyCommand=echo_PWNED 'systemctl stop foo'"))
        self.assertFalse(log.exists())

    def test_claude_pretooluse_rejects_unsafe_ssh_without_lookup(self):
        bindir, log = self.fake_ssh()
        payload = {"tool_name": "Bash", "tool_input": {
            "command": "ssh -oProxyCommand=echo_PWNED box 'systemctl stop foo'"}}
        env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
        result = subprocess.run([sys.executable, str(PLUGIN / "claude_hook.py")],
                                input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(log.exists(), "Claude permission hook invoked untrusted SSH")
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")

    def test_unsafe_ssh_is_blocked_on_every_hook_entry(self):
        command = "ssh -oProxyCommand=echo_PWNED box 'kill 4242'"
        entries = [("terminal", {"command": command}),
                   ("execute_code", {"code": "unused = " + repr(command)}),
                   ("tool_call", {"calls": [{"name": "terminal", "arguments": {"command": command}}]}),
                   ("terminal", {"command": "cat <<'EOF'\n" + command + "\nEOF"})]
        with mock.patch.object(self.g, "remote_lookup", return_value={"status": "none"}) as remote:
            for tool, args in entries:
                with self.subTest(tool=tool, args=args):
                    result = self.g._pre_tool_call(tool, args)
                    self.assertIsNotNone(result)
                    self.assertEqual(result["action"], "block")
            remote.assert_not_called()

    def test_safe_ssh_transport_preserves_target(self):
        calls = []
        self.g.remote_lookup = lambda p, s: calls.append((p, s)) or {"status": "found", "owner": CARD}
        for options in ["-p 2222 -l you -i /fixture/key", "-vp2222 -lyou -i/fixture/key",
                        "-o Port=2222 -o User=you -o IdentityFile=/fixture/key"]:
            with self.subTest(options=options):
                self.assertIsNotNone(self.g.check(f"ssh {options} box 'kill 4242'"))
                self.assertEqual(calls[-1][0][-1], "box")
                self.assertEqual(calls[-1][1]["pid"], 4242)
                self.assertIn("2222", " ".join(calls[-1][0]))

    def test_malformed_or_unknown_ssh_options_fail_closed(self):
        for options in ["-Z", "-p bad", "-p 0", "-p 65536", "-o User=-oops",
                        "-o ConnectTimeout=99", "-o BatchMode=no", "-o IdentitiesOnly=no"]:
            with self.subTest(options=options), mock.patch.object(self.g, "remote_lookup") as remote:
                self.assertIsNotNone(self.g.check(f"ssh {options} box 'systemctl stop foo'"))
                remote.assert_not_called()

    def test_direct_lookup_rejects_unsafe_transport(self):
        act = {"tool": "systemctl", "verb": "stop", "unit": "foo", "user": False,
               "host": {"dest": "box", "opts": ["-oProxyCommand=echo_PWNED"]}}
        with mock.patch.object(self.g, "remote_lookup") as remote:
            self.assertEqual(self.g._lookup(act)["status"], "failed")
            remote.assert_not_called()

    def test_direct_lookup_rejects_malformed_values(self):
        cases = [("box", ["-o"]), ("box", ["--"]), ("box", ["not-an-option"]),
                 ("box", ["-o", "Malformed"]), ("box", ["-i", "-bad"]),
                 ("box", ["-i", "bad\npath"]), ("", []), ("box\n-oProxyCommand=x", [])]
        for dest, opts in cases:
            with self.subTest(dest=dest, opts=opts):
                act = {"tool": "systemctl", "unit": "foo", "user": False,
                       "host": {"dest": dest, "opts": opts}}
                with mock.patch.object(self.g, "remote_lookup") as remote:
                    self.assertEqual(self.g._lookup(act)["status"], "failed")
                    remote.assert_not_called()

    def test_safe_config_and_flags_are_canonicalized(self):
        calls = []
        self.g.remote_lookup = lambda p, s: calls.append(p) or {"status": "none"}
        self.assertIsNone(self.g.check("ssh -4ntf -o 'User you' -o IdentitiesOnly=yes "
                                      "-o BatchMode=yes -o ConnectTimeout=1 box 'kill 4242'"))
        self.assertEqual(calls[0], ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                                    "-4", "-o", "user=you", "-o", "identitiesonly=yes",
                                    "-o", "batchmode=yes", "-o", "connecttimeout=1", "box"])

    def assert_skill_not_executed(self, fallback):
        root = self.home / "copy"
        plugin = root / "plugins/service-owner-guard"
        shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__"))
        base = self.home / "code/hermes-mods" if fallback else root
        skill = base / "skills/devops/machine-investigate/scripts/collect.py"
        skill.parent.mkdir(parents=True)
        marker = self.home / "executed-skill"
        skill.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\n")
        spec = importlib.util.spec_from_file_location("isolated_guard", plugin / "__init__.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        try:
            result = guard.local_lookup({"kind": "launchd", "label": LABEL, "domain": "gui/501"})
        except Exception as exc:
            result = {"error": repr(exc)}
        self.assertFalse(marker.exists(), "guard executed mutable collect.py")
        self.assertEqual(result.get("owner"), CARD + "@macbook", result)
        script = guard.remote_script({"kind": "pids", "pid": 4242})
        self.assertNotIn("executed-skill", script)

    def test_repo_skill_collect_is_never_executed(self):
        self.assert_skill_not_executed(False)

    def test_home_fallback_collect_is_never_executed(self):
        self.assert_skill_not_executed(True)
