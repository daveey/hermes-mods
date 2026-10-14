"""Agent channel: each rule fires on its trigger, stays quiet otherwise, and reaches the model via the hooks."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rules = load("coach_rules_test", ROOT / "agent_rules.py")
core = load("coach_core_test_a", ROOT / "core.py")
plugin = load("coach_plugin_test", ROOT / "__init__.py")

FAIL = json.dumps({"output": "boom", "exit_code": 1})
OK = json.dumps({"output": "fine", "exit_code": 0})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def coach(tmp_path, checkout=None, clock=None):
    return rules.AgentCoach(checkout, signals=core.Store(tmp_path / "coach"), clock=clock or Clock())


def term(c, cmd, result=OK, sid="s1", **args):
    c.observe(sid, "terminal", {"command": cmd, **args}, result)


# ── repeat_failure ──
def test_repeat_failure_fires_on_third_identical_failure(tmp_path):
    c = coach(tmp_path)
    term(c, "pytest -q tests", FAIL)
    term(c, "pytest  -q tests", FAIL)  # whitespace-normalized: same command
    assert c.take("s1") is None
    term(c, "pytest -q tests", FAIL)
    hint = c.take("s1")
    assert "failed 3 times" in hint and "hypothesis" in hint and "pytest -q tests" in hint


def test_repeat_failure_resets_on_success_and_fires_once(tmp_path):
    c = coach(tmp_path)
    term(c, "make", FAIL)
    term(c, "make", FAIL)
    term(c, "make", OK)
    term(c, "make", FAIL)
    assert c.take("s1") is None
    for _ in range(5):
        term(c, "make", FAIL)
    assert "failed" in c.take("s1")
    term(c, "make", FAIL)
    assert c.take("s1") is None  # once per key per session


def test_execute_code_and_status_error_count_as_failures(tmp_path):
    c = coach(tmp_path)
    for _ in range(3):
        c.observe("s1", "execute_code", {"code": "print(1/0)"}, json.dumps({"error": "ZeroDivisionError"}))
    assert "failed 3 times" in c.take("s1")


# ── poll_loop ──
def test_sleep_loop_fires_immediately(tmp_path):
    c = coach(tmp_path)
    term(c, "while ! curl -s localhost:8080; do sleep 5; done")
    assert "background=true" in c.take("s1")


def test_three_sleep_polls_in_window_fire(tmp_path):
    c = coach(tmp_path)
    term(c, "sleep 30 && tail log")
    term(c, "ls")
    c.observe("s1", "process_manage", {"action": "poll", "session_id": "p1"}, "{}")
    assert c.take("s1") is None
    term(c, "sleep 60; cat status")
    assert "polling" in c.take("s1")


def test_short_sleep_is_not_polling(tmp_path):
    c = coach(tmp_path)
    for _ in range(5):
        term(c, "sleep 1 && echo hi")
    assert c.take("s1") is None


# ── no_checkpoint ──
def test_forty_tool_calls_in_one_turn(tmp_path):
    c = coach(tmp_path)
    c.turn_start("s1")
    for i in range(39):
        c.observe("s1", "read_file", {"path": f"/x/{i}"}, "{}")
    assert c.take("s1") is None
    c.observe("s1", "read_file", {"path": "/x/40"}, "{}")
    assert "40 tool calls" in c.take("s1")
    c.turn_start("s1")  # a new user turn resets the counter
    for i in range(39):
        c.observe("s1", "read_file", {"path": f"/y/{i}"}, "{}")
    assert c.take("s1") is None


# ── live_checkout ──
def test_live_checkout_edit_and_git_commit(tmp_path):
    live = tmp_path / "hermes-agent"
    (live / "agent").mkdir(parents=True)
    c = coach(tmp_path, checkout=live.resolve())
    c.observe("s1", "patch", {"path": str(tmp_path / "elsewhere.py")}, "{}")
    c.observe("s1", "read_file", {"path": str(live / "agent" / "x.py")}, "{}")
    assert c.take("s1") is None
    c.observe("s1", "write_file", {"path": str(live / "agent" / "x.py")}, "{}")
    assert "hermes-core-wt" in c.take("s1")
    c2 = coach(tmp_path, checkout=live.resolve())
    term(c2, f"cd {live} && git status")
    assert c2.take("s1") is None
    term(c2, f"git -C {live} commit -am wip")
    assert "hermes-core-wt" in c2.take("s1")


# ── signals for the user channel ──
def test_subagents_long_jobs_and_bug_signatures_are_recorded(tmp_path):
    clk = Clock()
    c = coach(tmp_path, clock=clk)
    st = c.signals
    c.observe("s1", "delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}, {"goal": "c"}]}, "{}")
    c.observe("s1", "delegate_task", {"goal": "d"}, "{}")
    assert st.session("s1")["subagents"] == 4
    term(c, "./train.sh", result=json.dumps({"session_id": "proc_1"}), background=True)
    c.observe("s1", "process_manage", {"action": "poll", "session_id": "proc_1"}, "{}")
    assert not st.session("s1").get("long_job")
    clk.t += 601
    c.observe("s1", "process_manage", {"action": "wait", "session_id": "proc_1"}, "{}")
    assert "background job for 10 min" in st.session("s1")["long_job"]
    c.observe("s2", "terminal", {"command": "make all", "timeout": 1800}, OK)
    assert "timeout 1800" in st.session("s2")["long_job"]
    c.observe("s3", "cross_session", {"action": "send"}, json.dumps({"error": "chat is open in a window 42"}),
              status="error", error_message="chat is open in a window 42")
    c.observe("s3", "read_file", {"path": "/nope"}, json.dumps({"error": "not found"}), status="error",
              error_message="not found")  # agent mistake, not a Hermes bug
    bugs = st.bugs(0)
    assert len(bugs) == 1 and bugs[0]["sig"] == "cross_session: chat is open in a window <n>"


def test_traceback_from_hermes_code_is_a_bug_signature():
    tb = ('Traceback (most recent call last):\n  File "/Users/me/.hermes/hermes-agent/tools/x.py", line 3, in f\n'
          'KeyError: \'abc\'')
    assert rules.bug_signature("terminal", tb, "ok", None, None) == "terminal: KeyError: 'abc'"
    assert rules.bug_signature("terminal", "Traceback ... File \"/tmp/mine.py\"\nValueError", "error", None, None) is None


# ── delivery through the real hook callbacks ──
def test_hooks_attach_hint_to_tool_result_and_count_turns(tmp_path):
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None)
    assert hooks["pre_llm_call"](session_id="s1", user_message="still not working") is None
    for _ in range(3):
        hooks["post_tool_call"](tool_name="terminal", args={"command": "npm test"}, result=FAIL, session_id="s1",
                                status="error")
        out = hooks["transform_tool_result"](tool_name="terminal", result=FAIL, session_id="s1")
    assert json.loads(out)["coach_hint"].startswith("The same command has now failed 3 times")
    assert hooks["transform_tool_result"](tool_name="terminal", result=FAIL, session_id="s1") is None
    plain = rules.attach_hint("plain text", "do X")
    assert plain.endswith("[coach] do X")
    s = store.session("s1")
    assert s["turns"] == 1 and s["still_broken"] == 1
    log = [json.loads(line) for line in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert log[-1]["event"] == "agent_hint" and log[-1]["rule"] == "repeat_failure"


def test_pending_hint_rides_the_next_turn_context(tmp_path):
    _, hooks = plugin.build(store=core.Store(tmp_path / "coach"), checkout=None)
    hooks["post_tool_call"](tool_name="terminal", args={"command": "while true; do sleep 9; done"}, result=OK,
                            session_id="s1")
    ctx = hooks["pre_llm_call"](session_id="s1", user_message="ok")
    assert ctx and ctx["context"].startswith("[coach] You are polling")


def test_still_broken_matcher():
    yes = ["still not working", "same error as before", "it didn't work", "nope, still broken", "doesn't load",
           "didnt work  [Image attached at: /x.png]",
           "[OUT-OF-BAND USER MESSAGE — from the user]\nstill broken\n[/OUT-OF-BAND USER MESSAGE]"]
    no = ["please add a test for login", "works now, thanks", "[IMPORTANT: Background process done] still failing",
          "[System note: Your previous turn was interrupted mid-run] not working", 
          "make it not crash on empty input"]
    assert all(core.is_still_broken(t) for t in yes)
    assert not any(core.is_still_broken(t) for t in no)
