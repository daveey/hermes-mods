import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "__init__.py"
spec = importlib.util.spec_from_file_location("backend_restart_guard", PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

WRAP = "python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- "


class GuardTests(unittest.TestCase):
    def blocked(self, tool, **args):
        r = guard._pre_tool_call(tool, args)
        return bool(r) and r["action"] == "block"

    def test_bare_restart_refused_and_names_wrapper(self):
        for cmd in (
            "systemctl --user restart hermes-always-on-dashboard.service",
            "systemctl --user restart hermes-gateway.service hermes-always-on-dashboard.service",
            "systemctl --user stop hermes-gateway",
            "cd /tmp && systemctl --user restart hermes-always-on-dashboard",
            "sudo systemctl restart hermes-gateway.service",
            "bash -c 'systemctl --user restart hermes-always-on-dashboard.service'",
            "bash -lc 'systemctl --user restart hermes-always-on-dashboard.service'",
            "git status\nsystemctl --user restart hermes-gateway.service",
            "ssh -i ~/.ssh/test -o ConnectTimeout=8 user@example.com 'systemctl --user restart hermes-gateway.service'",
            "bash -c 'systemctl --user restart hermes-gateway.service' hermes-sync pause-turns",
            "ssh ubuntu@hub systemctl --user restart hermes-gateway.service",
            "echo done; systemctl --user kill hermes-gateway.service",
            "pkill -f 'hermes serve'",
            "hermes gateway restart",
        ):
            with self.subTest(cmd=cmd):
                r = guard._pre_tool_call("terminal", {"command": cmd})
                self.assertEqual(r["action"], "block")
                self.assertIn("pause-turns", r["message"])

    def test_wrapped_restart_allowed(self):
        for cmd in (
            WRAP + "systemctl --user restart hermes-always-on-dashboard.service",
            "~/.hermes/sync-runner/hermes-sync pause-turns -- systemctl --user restart hermes-gateway.service",
            WRAP + "systemctl --user restart hermes-gateway.service hermes-always-on-dashboard.service && echo ok",
        ):
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked("terminal", command=cmd))

    def test_unrelated_commands_allowed(self):
        for cmd in (
            "systemctl --user status hermes-gateway.service",
            "systemctl --user restart some-other.service",
            "systemctl --user list-units 'hermes*'",
            "journalctl --user -u hermes-gateway.service | grep restart",
            "git commit -m 'systemctl --user restart hermes-gateway'",
            "echo systemctl restart hermes-gateway",
        ):
            with self.subTest(cmd=cmd):
                self.assertFalse(self.blocked("terminal", command=cmd))

    def test_other_entry_paths(self):
        bad = "import subprocess; subprocess.run(['systemctl','--user','restart','hermes-gateway.service'])"
        self.assertTrue(self.blocked("execute_code", code=bad))
        self.assertFalse(self.blocked("execute_code", code=bad + "  # via pause-turns"))
        nested = {"calls": [{"name": "terminal", "arguments": {"command": "systemctl --user stop hermes-gateway"}}]}
        self.assertTrue(self.blocked("tool_call", **nested))
        self.assertTrue(self.blocked("process_manage", data="systemctl --user restart hermes-gateway\n"))

    def test_off_switch(self):
        import os
        os.environ["HERMES_BACKEND_RESTART_GUARD"] = "off"
        try:
            self.assertFalse(self.blocked("terminal", command="systemctl --user restart hermes-gateway"))
        finally:
            del os.environ["HERMES_BACKEND_RESTART_GUARD"]


if __name__ == "__main__":
    unittest.main()
