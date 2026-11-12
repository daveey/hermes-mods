"""user-chat-probe-guard: a restart-proof backend launched on the production Hermes home is refused.

Regression for self-heal gateway-9aaf151a15 (Nov 11, 2026): a restart proof (card t_01a1c8d8a5c1f725) started a
disposable `HERMES_DESKTOP=1 hermes serve --isolated` backend on the hub with the real ~/.hermes home. `--isolated`
isolates the server endpoint, not the stored chats: the backend's startup-resume sweep adopted David's real
interrupted chat, then the proof's planned `kill -TERM` of that PID interrupted his turn. A restart/fixture backend
must run on a disposable HERMES_HOME (`HERMES_HOME=$(mktemp -d)`).
"""
import importlib.util
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("user_chat_probe_guard_serve", PLUGIN / "__init__.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

ENV_KEYS = ("HERMES_HOME", "HERMES_PROBE_REAL_CHAT_OK", "HERMES_USER_CHAT_PROBE_GUARD", "HERMES_SHARED_HOME_SERVE_OK")


class SharedHomeServeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        # the caller's own (production) home; no state.db needed for this check
        os.environ["HERMES_HOME"] = str(self.tmp / "prod" / ".hermes")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def call(self, tool, **args):
        return guard._pre_tool_call(tool_name=tool, args=args, session_id="")

    def assertBlocked(self, tool, **args):
        r = self.call(tool, **args)
        self.assertIsNotNone(r, f"not blocked: {args}")
        self.assertEqual(r["action"], "block")
        self.assertIn("HERMES_HOME=$(mktemp -d)", r["message"])
        self.assertIn("--isolated", r["message"])
        self.assertIn("gateway-9aaf151a15", r["message"])
        return r

    def assertAllowed(self, tool, **args):
        self.assertIsNone(self.call(tool, **args), f"blocked: {args}")

    # ── the incident and its launch routes ──
    def test_incident_remote_wrapper_launch_refused(self):
        # the proof launched the backend on the hub through hub-admin with the real home
        self.assertBlocked("terminal", command=(
            "hub-admin 'cd ~ && HERMES_DESKTOP=1 nohup ~/.local/bin/hermes serve --isolated --host 127.0.0.1 "
            "--port 0 > /tmp/liveproof-a.log 2>&1 &'"))

    def test_shared_home_shell_launches_refused(self):
        for cmd in (
            "HERMES_DESKTOP=1 hermes serve --isolated --host 127.0.0.1 --port 0",
            "hermes serve --isolated --port 0 &",
            "env HERMES_DESKTOP=1 hermes serve --port 0",  # Desktop-owned shape, inherits the home
            "HERMES_HOME=~/.hermes HERMES_DESKTOP=1 hermes serve --isolated",
            "HERMES_HOME=$HOME/.hermes hermes serve --isolated",
            "HERMES_HOME=\"$HERMES_HOME\" hermes serve --isolated",
            "HERMES_HOME=/home/ubuntu/.hermes/profiles/coach hermes serve --isolated",
            f"HERMES_HOME={os.environ['HERMES_HOME']} hermes serve --isolated",  # the caller's own home
            "nohup python3 -m hermes_cli.main serve --isolated --port 0 &",
            "~/.hermes/hermes-agent/venv/bin/python ~/.hermes/hermes-agent/hermes_cli/main.py serve --isolated",
            "hermes --profile coach serve --isolated",
            "hermes dashboard --isolated --no-open",
            "bash -lc 'HERMES_DESKTOP=1 hermes serve --isolated'",
            "ssh -o BatchMode=yes ubuntu@hermes-hub 'HERMES_DESKTOP=1 hermes serve --isolated --port 0'",
            "ssh hermes-hub HERMES_DESKTOP=1 hermes serve --isolated",
            "hub-admin 'HERMES_HOME=$HOME/.hermes hermes serve --isolated'",
        ):
            with self.subTest(cmd=cmd):
                self.assertBlocked("terminal", command=cmd)

    def test_remote_launch_ignores_local_disposable_env(self):
        # a disposable HERMES_HOME in THIS process says nothing about the remote host's home
        os.environ["HERMES_HOME"] = str(self.tmp / "disposable")
        self.assertBlocked("terminal", command="hub-admin 'HERMES_DESKTOP=1 hermes serve --isolated'")

    def test_python_launch_refused(self):
        self.assertBlocked("execute_code", code=textwrap.dedent("""
            import os, subprocess
            env = {**os.environ, "HERMES_DESKTOP": "1"}
            p = subprocess.Popen(["hermes", "serve", "--isolated", "--host", "127.0.0.1", "--port", "0"], env=env)
        """))
        # the incident's liveproof.py shape: HOME constant is the real home, passed as HERMES_HOME
        self.assertBlocked("execute_code", code=textwrap.dedent("""
            import os, subprocess
            from pathlib import Path
            HOME = Path.home() / ".hermes"
            env = dict(os.environ, HERMES_DESKTOP="1", HERMES_HOME=str(HOME))
            subprocess.Popen(["hermes", "serve", "--isolated", "--port", "0"], env=env)
        """))
        self.assertBlocked("execute_code", code=(
            "from hermes_tools import terminal\n"
            "terminal(\"hub-admin 'HERMES_DESKTOP=1 hermes serve --isolated --port 0'\")\n"))

    def test_script_file_launch_refused(self):
        script = self.tmp / "liveproof.py"
        script.write_text(textwrap.dedent("""
            import os, subprocess
            HOME = os.path.expanduser("~/.hermes")
            def launch():
                env = {**os.environ, "HERMES_DESKTOP": "1", "HERMES_HOME": HOME}
                return subprocess.Popen(["hermes", "serve", "--isolated", "--port", "0"], env=env)
        """))
        self.assertBlocked("terminal", command=f"cd {self.tmp} && python3 {script} launch")
        self.assertBlocked("terminal", command=f"python3 -u {script}")

    # ── allowed ──
    def test_disposable_home_launches_allowed(self):
        for cmd in (
            "HERMES_HOME=$(mktemp -d) HERMES_DESKTOP=1 hermes serve --isolated --port 0",
            "home=$(mktemp -d); HERMES_HOME=\"$home\" hermes serve --isolated",
            "export HERMES_HOME=/tmp/proof-home-t_x; hermes serve --isolated --port 0",
            "env HERMES_HOME=/home/ubuntu/.hermes/cache/scratch/t_x-home HERMES_DESKTOP=1 hermes serve --isolated",
            "hub-admin 'HERMES_HOME=$(mktemp -d -p ~/.hermes/cache/scratch) HERMES_DESKTOP=1 hermes serve --isolated'",
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed("terminal", command=cmd)
        self.assertAllowed("execute_code", code=textwrap.dedent("""
            import os, subprocess, tempfile
            home = tempfile.mkdtemp(prefix="proof-home-")
            env = {**os.environ, "HERMES_DESKTOP": "1", "HERMES_HOME": home}
            subprocess.Popen(["hermes", "serve", "--isolated", "--port", "0"], env=env)
        """))
        script = self.tmp / "liveproof_ok.py"
        script.write_text(textwrap.dedent("""
            import os, subprocess, tempfile
            HOME = tempfile.mkdtemp()
            subprocess.Popen(["hermes", "serve", "--isolated"], env={**os.environ, "HERMES_HOME": HOME})
        """))
        self.assertAllowed("terminal", command=f"python3 {script}")

    def test_unrelated_commands_allowed(self):
        for cmd in (
            "hermes serve",  # a normal production backend, not a restart-proof shape
            "hermes dashboard",
            "hermes serve --help",
            "systemctl --user restart hermes-dashboard",
            "grep -n 'serve --isolated' ~/.hermes/logs/gui.log",
            "echo HERMES_DESKTOP=1 hermes serve --isolated",
            "git commit -m 'hermes serve --isolated needs a disposable home'",
            "hub-admin 'tail -n 50 ~/.hermes/logs/gui.log'",
            "python3 /nonexistent/script.py",
            "hermes profile create x --isolated-auth",
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed("terminal", command=cmd)

    def test_opt_out_and_off_switch(self):
        cmd = "HERMES_DESKTOP=1 hermes serve --isolated"
        self.assertAllowed("terminal", command=f"HERMES_SHARED_HOME_SERVE_OK=1 {cmd}")
        os.environ["HERMES_SHARED_HOME_SERVE_OK"] = "1"
        self.assertAllowed("terminal", command=cmd)
        os.environ.pop("HERMES_SHARED_HOME_SERVE_OK")
        os.environ["HERMES_USER_CHAT_PROBE_GUARD"] = "off"
        self.assertAllowed("terminal", command=cmd)


if __name__ == "__main__":
    unittest.main()
