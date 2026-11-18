"""scheduled_kill: hold the first kill of a background job still sleeping toward its scheduled time.

Self-heal agent-d483b8fda1: the agent scheduled `secs=$(( $(TZ=America/Los_Angeles date -d 'tomorrow 00:01' +%s)
- $(date +%s) )); sleep $secs; <post>` at 21:11 PST, polled it (`sleeping 10185`), decided by mental arithmetic that
the wait should be ~50 minutes, and killed the valid job in the same batch as the date check that refuted it.
"""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parents[1]

INCIDENT_CMD = ("cd ~/code/pr/twitter/bin && secs=$(( $(TZ=America/Los_Angeles date -d 'tomorrow 00:01' +%s) "
                "- $(date +%s) )); echo \"sleeping $secs\"; sleep $secs; python3 ~/code/pr/twitter/bin/"
                "bsky_post_reply.py --card --variant 'B' at://x <<'BEOF'\nreply text\nBEOF")
START = 1_800_000_000.0


def load():
    spec = importlib.util.spec_from_file_location("scheduled_kill_test", HERE / "scheduled_kill.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Proc:
    def __init__(self, command, output="", started_at=START, exited=False):
        self.command, self.output_buffer, self.started_at, self.exited = command, output, started_at, exited


class Reg:
    def __init__(self, **procs):
        self.procs = procs

    def get(self, sid):
        return self.procs.get(sid)


class ScheduledKillTests(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.logs = []
        self.held = set()

    def check(self, reg, sid="proc_a", now=START + 8, session="s1", tool="process_manage", action="kill"):
        return self.mod.check(self.held, session, tool, {"action": action, "session_id": sid}, self.logs.append,
                              registry=reg, now=now)

    def test_incident_kill_is_held_once_with_wake_time(self):
        reg = Reg(proc_a=Proc(INCIDENT_CMD, "sleeping 10185\n"))
        msg = self.check(reg)
        self.assertIsNotNone(msg)
        self.assertIn("scheduled_kill", msg)
        self.assertIn("2 h 49 m", msg)  # 10185 s after start, 8 s elapsed -> 10177 s left
        self.assertIn(f"@{int(START + 10185)}", msg)
        self.assertEqual(self.logs[-1]["rule"], "scheduled_kill")
        # the same kill again goes through: never loops
        self.assertIsNone(self.check(reg))
        self.assertEqual(self.logs[-1]["event"], "scheduled_kill_repeat")

    def test_literal_sleep_uses_command(self):
        reg = Reg(proc_a=Proc("sleep 3600; ./post.sh", ""))
        msg = self.check(reg, now=START + 600)
        self.assertIn("50 m", msg)

    def test_variable_sleep_without_echo_is_held_without_time(self):
        reg = Reg(proc_a=Proc("s=$(( $(date -d 'tomorrow 09:00' +%s) - $(date +%s) )); sleep $s; ./run", ""))
        msg = self.check(reg)
        self.assertIsNotNone(msg)
        self.assertIn("wake time unknown", msg)

    def test_allows(self):
        cases = {
            "past wake (now doing the work)": Reg(proc_a=Proc("sleep 60; ./build", "", started_at=START - 120)),
            "exited": Reg(proc_a=Proc(INCIDENT_CMD, "sleeping 10185\n", exited=True)),
            "no sleep": Reg(proc_a=Proc("./train.py --epochs 9", "")),
            "poll loop": Reg(proc_a=Proc("while ! gh run view 1 | grep -q done; do sleep 30; done", "")),
            "short literal sleep": Reg(proc_a=Proc("sleep 5; ./x", "")),
            "unknown job": Reg(),
        }
        for name, reg in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(self.check(reg))

    def test_other_actions_and_tools_ignored(self):
        reg = Reg(proc_a=Proc(INCIDENT_CMD, "sleeping 10185\n"))
        self.assertIsNone(self.check(reg, action="poll"))
        self.assertIsNone(self.check(reg, tool="terminal"))

    def test_fail_open(self):
        class Boom:
            def get(self, sid):
                raise RuntimeError("x")
        self.assertIsNone(self.check(Boom()))

    def test_wired_into_pre_tool_call(self):
        src = (HERE / "__init__.py").read_text()
        self.assertIn('_load("scheduled_kill")', src)


if __name__ == "__main__":
    unittest.main()
