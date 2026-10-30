"""Only JSON is passed to the hook: no payload is ever executed."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

HOOK = Path(__file__).resolve().parents[1] / "claude_hook.py"
RESTART = "systemctl --user restart hermes-always-on-dashboard.service"
WRAP = "python3 ~/.hermes/sync-runner/hermes-sync pause-turns --timeout 180 -- "
# Same command structure as the incident, with public-safe host/path/metrics code.
REMOTE = """cd ~/.hermes/hermes-agent && .venv/bin/python - <<EOF
print('probe')
EOF
cd ~/code/hermes-mods && git fetch -q && git merge --ff-only -q origin/main && git log --oneline -1 && """


def invoke(command, **extra):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
    payload.update(extra)
    env = dict(os.environ, HERMES_BACKEND_RESTART_GUARD="on")
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          text=True, capture_output=True, env=env, timeout=5)


class ClaudeHookTests(unittest.TestCase):
    def test_blocked_entry_paths(self):
        for command in (
            RESTART,
            "git status --short\n" + RESTART,
            "bash -c " + shlex.quote(RESTART),
            "bash -lc " + shlex.quote(RESTART),
            "ssh user@example.com " + shlex.quote(REMOTE + RESTART),
            "ssh -i ~/.ssh/test -o ConnectTimeout=8 user@example.com " + shlex.quote(RESTART),
            "ssh -p 2222 user@example.com " + shlex.quote("bash -lc " + shlex.quote(RESTART)),
        ):
            with self.subTest(command=command):
                result = invoke(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("pause-turns --timeout 180 --", result.stderr)

    def test_wrapped_equivalents(self):
        for command in (
            WRAP + RESTART,
            "bash -c " + shlex.quote(WRAP + RESTART),
            "ssh user@example.com " + shlex.quote(REMOTE + WRAP + RESTART),
            "ssh -i ~/.ssh/test user@example.com " + shlex.quote(WRAP + RESTART),
        ):
            with self.subTest(command=command):
                self.assertEqual(invoke(command).returncode, 0)

    def test_unrelated_allowed(self):
        for command in (
            "git fetch -q && git merge --ff-only origin/main",
            "ssh user@example.com 'git status --short'",
            "systemctl --user status hermes-always-on-dashboard.service",
            "ssh -o ConnectTimeout=8 user@example.com 'systemctl --user show hermes-gateway'",
            "git commit -m " + shlex.quote(RESTART),
            "echo " + shlex.quote(WRAP + RESTART),
            "git commit -m " + shlex.quote("document guard\n" + RESTART),
        ):
            with self.subTest(command=command):
                self.assertEqual(invoke(command).returncode, 0)

    def test_wrapper_mention_does_not_exempt_bare_restart(self):
        for command in (
            "bash -c " + shlex.quote(RESTART) + " hermes-sync pause-turns",
            "ssh user@example.com " + shlex.quote(WRAP + RESTART + "; " + RESTART),
            WRAP + RESTART + " && " + RESTART,
        ):
            with self.subTest(command=command):
                self.assertEqual(invoke(command).returncode, 2)

    def test_off_switch(self):
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({
            "tool_name": "Bash", "tool_input": {"command": RESTART}
        }), text=True, capture_output=True,
            env=dict(os.environ, HERMES_BACKEND_RESTART_GUARD="off"), timeout=5)
        self.assertEqual(result.returncode, 0)

    def test_non_bash_and_post_tool_are_ignored(self):
        self.assertEqual(invoke(RESTART, tool_name="Read").returncode, 0)
        self.assertEqual(invoke(RESTART, hook_event_name="PostToolUse").returncode, 0)

    def test_install_through_settings_symlink_with_null_hooks(self):
        spec = importlib.util.spec_from_file_location("claude_backend_hook_link", HOOK)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "managed.json"
            link = Path(tmp) / "settings.json"
            real.write_text(json.dumps({"hooks": None, "theme": "dark"}))
            link.symlink_to(real)
            self.assertTrue(module.install(link))
            self.assertTrue(link.is_symlink())
            data = json.loads(real.read_text())
            self.assertEqual(data["theme"], "dark")
            self.assertEqual(len(data["hooks"]["PreToolUse"]), 1)

    def test_install_preserves_settings_and_is_idempotent(self):
        spec = importlib.util.spec_from_file_location("claude_backend_hook", HOOK)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            before = {"permissions": {"allow": ["Read"]}, "hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 other.py"}]}
            ]}}
            settings.write_text(json.dumps(before))
            self.assertTrue(module.install(settings))
            installed = json.loads(settings.read_text())
            self.assertEqual(installed["permissions"], before["permissions"])
            self.assertEqual(installed["hooks"]["PreToolUse"][0], before["hooks"]["PreToolUse"][0])
            self.assertEqual(len(installed["hooks"]["PreToolUse"]), 2)
            self.assertFalse(module.install(settings))
            self.assertEqual(json.loads(settings.read_text()), installed)
            alias = Path(tmp) / "linked_hook.py"
            alias.symlink_to(HOOK)
            installed["hooks"]["PreToolUse"][-1]["hooks"][0]["command"] = "python3 " + shlex.quote(str(alias))
            settings.write_text(json.dumps(installed))
            self.assertFalse(module.install(settings))
            self.assertEqual(json.loads(settings.read_text()), installed)


if __name__ == "__main__":
    unittest.main()
