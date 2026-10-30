"""user-chat-probe-guard: a one-shot `hermes chat --resume <id>` into a person's chat is refused.

Regression for self-heal agent-93a55211b1: a fix worker proved its fix with
`hermes chat --resume 20261026_000514_23ef49 --oneshot -q "... Reply exactly ROUTE_INSTALLED_23ef49"`, which left a
test prompt and a bare token as the newest turns of David's real Desktop chat.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("user_chat_probe_guard", PLUGIN / "__init__.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

DESKTOP = "20261026_000514_23ef49"
CLI = "20261025_073850_55b17f"
KANBAN = "20261030_010101_kan001"
ONESHOT = "20261030_010102_one001"
COPY = "20261030_010103_cpy001"
OWN = "20261030_010104_own001"
THROWAWAY = "20261013_231523_c989ce"
EMPTY = "20261030_010105_emp001"


def make_home(tmp: str) -> Path:
    home = Path(tmp)
    con = sqlite3.connect(home / "state.db")
    con.execute("create table sessions (id text primary key, source text, title text, model_config text, "
                "started_at real, parent_session_id text)")
    rows = [
        (DESKTOP, "desktop", "add alpha to this slack workspace", None, 1.0),
        (CLI, "cli", "phone calls", None, 2.0),
        (KANBAN, "kanban", "worker", None, 3.0),
        (ONESHOT, "oneshot", None, None, 4.0),
        (COPY, "oneshot", "probe copy", json.dumps({"_probe_copy_of": DESKTOP}), 5.0),
        (OWN, "desktop", "the caller's own chat", None, 6.0),
    ]
    con.executemany("insert into sessions (id, source, title, model_config, started_at) values (?,?,?,?,?)", rows)
    con.execute("create table messages (id integer primary key autoincrement, session_id text, role text, "
                "content text)")
    con.executemany("insert into messages (session_id, role, content) values (?,?,?)", [
        (DESKTOP, "user", "add alpha to this slack workspace"), (DESKTOP, "assistant", "Start the test, or skip?"),
        (CLI, "user", "can BB answer calls?"), (OWN, "user", "hi"),
        (THROWAWAY, "user", "Isolated transport test. Do not use tools. Reply exactly: READY"),
        (THROWAWAY, "assistant", "READY"),
    ])
    con.execute("insert into sessions (id, source, title, started_at) values (?,?,?,?)",
                (THROWAWAY, "desktop", "Session Mentions live test", 7.0))
    con.execute("insert into sessions (id, source, title, started_at) values (?,?,?,?)",
                (EMPTY, "desktop", None, 8.0))
    con.commit()
    con.close()
    return home


class GuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = make_home(self._tmp.name)
        self._env = {k: os.environ.get(k) for k in ("HERMES_HOME", "HERMES_PROBE_REAL_CHAT_OK",
                                                    "HERMES_USER_CHAT_PROBE_GUARD")}
        os.environ["HERMES_HOME"] = str(self.home)
        os.environ.pop("HERMES_PROBE_REAL_CHAT_OK", None)
        os.environ.pop("HERMES_USER_CHAT_PROBE_GUARD", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def call(self, tool, session_id="", **args):
        return guard._pre_tool_call(tool_name=tool, args=args, session_id=session_id)

    def assertBlocked(self, tool, session_id="", **args):
        r = self.call(tool, session_id=session_id, **args)
        self.assertIsNotNone(r, f"not blocked: {args}")
        self.assertEqual(r["action"], "block")
        self.assertIn("hermes sessions branch", r["message"])
        self.assertIn("hermes sessions delete", r["message"])
        return r

    def assertAllowed(self, tool, session_id="", **args):
        self.assertIsNone(self.call(tool, session_id=session_id, **args), f"blocked: {args}")

    # ── the incident ──
    def test_incident_command_is_refused(self):
        r = self.assertBlocked("terminal", command=(
            f"hermes chat --resume {DESKTOP} --oneshot -q 'Routing installed-code acceptance only. Do not run tools "
            "or resume old work. Reply exactly ROUTE_INSTALLED_23ef49.'"))
        self.assertIn(DESKTOP, r["message"])
        self.assertIn(f"hermes sessions branch {DESKTOP}", r["message"])

    def test_probe_forms_into_user_chats_refused(self):
        for cmd in (
            f"hermes chat -r {DESKTOP} -q hi",
            f"hermes chat --resume={DESKTOP} --query hi",
            f"hermes chat --resume {CLI} --query-file /tmp/q.txt",
            f"echo hi | hermes chat --resume {CLI} --query-file -",
            f"hermes --resume {DESKTOP} -z 'hi'",
            f"hermes -r {DESKTOP} chat -Q -q hi",
            f"cd /tmp && HOME=/home/x timeout 60 hermes chat --resume {DESKTOP} --oneshot -q hi",
            f"~/.local/bin/hermes chat --resume 23ef49 -q hi",  # id suffix, as cards quote it
            f"bash -lc 'hermes chat --resume {DESKTOP} -q hi'",
            f"hermes chat --continue 'phone calls' -q hi",
        ):
            with self.subTest(cmd=cmd):
                self.assertBlocked("terminal", command=cmd)

    def test_execute_code_and_nested_tool_call_refused(self):
        self.assertBlocked("execute_code", code=(
            "from hermes_tools import terminal\n"
            f"terminal(\"hermes chat --resume {DESKTOP} --oneshot -q 'Reply exactly OK'\")"))
        self.assertBlocked("execute_code", code=(
            "import subprocess\n"
            f"subprocess.run(['hermes', 'chat', '--resume', '{DESKTOP}', '-q', 'hi'])"))
        self.assertBlocked("tool_call", calls=[{"name": "terminal", "arguments": {
            "command": f"hermes chat --resume {CLI} -q hi"}}])

    # ── allowed ──
    def test_probe_into_copy_oneshot_or_worker_session_allowed(self):
        for sid in (COPY, ONESHOT, KANBAN):
            with self.subTest(sid=sid):
                self.assertAllowed("terminal", command=f"hermes chat --resume {sid} --oneshot -q hi")

    def test_agent_opened_test_session_allowed(self):
        # first user message is itself a probe (Session Mentions transport tests), or no user message yet
        for sid in (THROWAWAY, EMPTY):
            with self.subTest(sid=sid):
                self.assertAllowed("terminal", command=f"hermes chat --resume {sid} --oneshot -q 'Reply exactly OK_2'")

    def test_branch_copy_route_allowed(self):
        self.assertAllowed("terminal", command=f"hermes sessions branch {DESKTOP}")
        self.assertAllowed("terminal", command=f"hermes sessions delete --yes {COPY}")

    def test_interactive_resume_allowed(self):
        self.assertAllowed("terminal", command=f"hermes chat --resume {DESKTOP}")
        self.assertAllowed("terminal", command=f"hermes --resume {DESKTOP}")

    def test_own_session_allowed(self):
        self.assertAllowed("terminal", session_id=OWN, command=f"hermes chat --resume {OWN} -q hi")
        self.assertBlocked("terminal", session_id=OWN, command=f"hermes chat --resume {DESKTOP} -q hi")

    def test_unknown_session_and_unrelated_commands_allowed(self):
        for cmd in (
            "hermes chat --resume 20991231_000000_nosuch -q hi",  # not in this host's store: can't judge
            "hermes chat -q hi",  # a new session, nobody's chat
            "hermes -z hi",
            f"git commit -m 'hermes chat --resume {DESKTOP} -q hi'",
            f"echo hermes chat --resume {DESKTOP} -q hi",
            f"grep -r 'chat --resume {DESKTOP}' ~/.hermes/logs",
            "hermes sessions list",
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed("terminal", command=cmd)

    def test_opt_out(self):
        self.assertAllowed("terminal", command=f"HERMES_PROBE_REAL_CHAT_OK=1 hermes chat --resume {DESKTOP} -q hi")
        os.environ["HERMES_PROBE_REAL_CHAT_OK"] = "1"
        self.assertAllowed("terminal", command=f"hermes chat --resume {DESKTOP} -q hi")
        os.environ.pop("HERMES_PROBE_REAL_CHAT_OK")
        os.environ["HERMES_USER_CHAT_PROBE_GUARD"] = "off"
        self.assertAllowed("terminal", command=f"hermes chat --resume {DESKTOP} -q hi")

    def test_missing_store_never_breaks_a_call(self):
        os.environ["HERMES_HOME"] = str(self.home / "nope")
        self.assertAllowed("terminal", command=f"hermes chat --resume {DESKTOP} -q hi")


class ClaudeHookTests(unittest.TestCase):
    """The Claude Code Bash adapter runs the same classifier (exit 2 = block)."""

    def run_hook(self, command, env):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        return subprocess.run([sys.executable, str(PLUGIN / "claude_hook.py")], input=json.dumps(payload),
                              capture_output=True, text=True, env=env, timeout=30)

    def test_hook_blocks_and_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(tmp)
            env = {**os.environ, "HERMES_HOME": str(home)}
            env.pop("HERMES_PROBE_REAL_CHAT_OK", None)
            r = self.run_hook(f"hermes chat --resume {DESKTOP} --oneshot -q hi", env)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("hermes sessions branch", r.stderr)
            self.assertEqual(self.run_hook(f"hermes chat --resume {COPY} --oneshot -q hi", env).returncode, 0)


if __name__ == "__main__":
    unittest.main()
