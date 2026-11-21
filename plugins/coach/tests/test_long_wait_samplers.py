"""long_wait_pending_reply / live_chat_sleep: silent foreground samplers in a decision-button turn.

Incident (hermes-hub Desktop session 20261121_045343_355be8): the user picked a speed-up option with a decision
button. The agent then ran two foreground samplers with nothing said first: a shell `for ... sleep 5; done` (80 s)
and a heredoc `while time.time() < end: ... time.sleep(1)` (240 s). The user saw a frozen chat and asked why it was
stalled. Neither rule fired: the decision boilerplate contains "after" ("Owner receipt: after validating ..."), which
asks_delay read as the user asking for a delay, and the Python sleep form was not matched at all.
"""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent

DECISION = ('Canonical user decision. Act only on the frozen revision below. Never repeat an operation already '
            'executed.\n{"effect":"answer","executionAuthorized":false,"revisionText":"Dispatcher tick 15s→5s"}\n'
            'Owner receipt: after validating, call action_request {"receipt": {"operationId": "x"}}. After acting, '
            'phase "applied".')
SHELL_SAMPLER = {"command": "cd ~/.hermes; for i in $(seq 1 16); do python3 -c 'print(1)'; sleep 5; done | uniq",
                 "timeout": 120}
PY_SAMPLER = {"command": "cd ~/.hermes; python3 - <<'EOF'\nimport time\nseen=[];end=time.time()+240\n"
                         "while time.time()<end:\n  seen.append(1)\n  time.sleep(1)\nprint(seen)\nEOF",
              "timeout": 300}


def _load(name):
    spec = importlib.util.spec_from_file_location(f"coach_lws_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


plugin = _load("__init__")
core = plugin._load("core")
long_wait = plugin._load("long_wait")


@pytest.fixture()
def chat(tmp_path, monkeypatch):
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_CRON_JOB_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_LESSONS", "0")
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None)
    return store, hooks


def _block(hooks, args, sid="s1"):
    out = hooks["pre_tool_call"](tool_name="terminal", args=dict(args), session_id=sid)
    return out if out and out.get("action") == "block" else None


def test_decision_boilerplate_is_not_a_delay_ask():
    assert long_wait.asks_delay(DECISION) is False
    assert long_wait.asks_delay("check it again in 5 minutes") is True
    assert long_wait.asks_delay("wait for CI then merge") is True


@pytest.mark.parametrize("args", [SHELL_SAMPLER, PY_SAMPLER], ids=["shell-loop", "python-loop"])
def test_incident_sampler_in_decision_turn_is_held_once(chat, args):
    store, hooks = chat
    hooks["pre_llm_call"](session_id="s1", user_message=DECISION, platform="desktop", parent_session_id="")
    out = _block(hooks, args)
    assert out and "long_wait_pending_reply" in out["message"] and "background=true" in out["message"]
    rows = [json.loads(x) for x in (store.root / "log.jsonl").read_text().splitlines()]
    held = [r for r in rows if r.get("event") == "agent_block" and r.get("rule") == "long_wait_pending_reply"]
    assert held and held[-1]["secs"] is None  # unbounded loop, logged as JSON-safe null
    assert _block(hooks, args) is None  # once per turn: never loops


def test_real_loops_count_and_prose_for_does_not():
    loops = ["for n in 15 18 20; do gh run view $n; sleep 2; done",
             "while ps -p 123 >/dev/null; do sleep 2; done",
             "cd x && until curl -sf localhost; do sleep 3; done",
             "timeout 60 bash -c 'while ps -p 9 >/dev/null 2>&1; do sleep 2; done'",
             "python3 - <<'EOF'\nfor i in range(9):\n    poll()\n    time.sleep(10)\nEOF"]
    not_loops = ['hermes kanban comment t_1 "re-scoped for rows 4-6" | tail -1; sleep 2',
                 'gh pr comment 1 --body "answers for each point; see below" && sleep 3',
                 "for c in a b; do hermes kanban assign $c; done; sleep 4"]
    for cmd in loops:
        assert long_wait.terminal_wait({"command": cmd}) == float("inf"), cmd
    for cmd in not_loops:
        assert (long_wait.terminal_wait({"command": cmd}) or 0) < 60, cmd


def test_python_sleep_forms_are_counted():
    assert long_wait.terminal_wait({"command": "python3 -c 'import time; time.sleep(90)'"}) == 90
    assert long_wait.terminal_wait({"command": "python3 -c 'import asyncio; asyncio.run(asyncio.sleep( 75 ))'"}) == 75
    assert long_wait.terminal_wait(PY_SAMPLER) == float("inf")
    assert long_wait.terminal_wait({**PY_SAMPLER, "background": True}) is None


def test_short_and_background_samplers_pass(chat):
    _, hooks = chat
    hooks["pre_llm_call"](session_id="s1", user_message=DECISION, platform="desktop", parent_session_id="")
    assert _block(hooks, {"command": "python3 -c 'import time; time.sleep(5)'"}) is None
    assert _block(hooks, {**PY_SAMPLER, "background": True}) is None


def test_kanban_worker_keeps_foreground_samplers(chat, monkeypatch):
    _, hooks = chat
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_x")
    hooks["pre_llm_call"](session_id="s1", user_message=DECISION, platform="desktop", parent_session_id="")
    assert _block(hooks, PY_SAMPLER) is None


# Review (PR 322): `do` on its own line is valid shell, and the loop is just as unbounded. The first form of
# _LOOP_SLEEP needed `do` on the header line, so these samplers summed to 5 s and both rules let them run.
NEWLINE_DO = {"for": "for i in $(seq 1 16)\ndo\n  echo sample\n  sleep 5\ndone",
              "while": "while ps -p 123 >/dev/null\ndo\n  echo sample\n  sleep 5\ndone",
              "until": "cd x && until curl -sf localhost:8080/health\n\ndo\n  sleep 5\ndone"}


class _Clock:
    t = 1_794_460_000.0

    def __call__(self):
        return self.t


@pytest.mark.parametrize("kind", sorted(NEWLINE_DO))
def test_newline_before_do_sampler_is_held_before_any_reply(chat, kind):
    store, hooks = chat
    hooks["pre_llm_call"](session_id="s1", user_message=DECISION, platform="desktop", parent_session_id="")
    out = _block(hooks, {"command": NEWLINE_DO[kind], "timeout": 120})
    assert out and "long_wait_pending_reply" in out["message"], kind
    rows = [json.loads(x) for x in (store.root / "log.jsonl").read_text().splitlines()]
    assert [r["secs"] for r in rows if r.get("event") == "agent_block"] == [None]


@pytest.mark.parametrize("kind", sorted(NEWLINE_DO))
def test_newline_before_do_sampler_is_held_after_a_stale_status(chat, monkeypatch, kind):
    _, hooks = chat
    clock = _Clock()
    monkeypatch.setattr(long_wait, "now", clock, raising=False)
    hooks["pre_llm_call"](session_id="s1", user_message=DECISION, platform="desktop", parent_session_id="")
    hooks["on_interim_message"](session_id="s1", text="Sampling the dispatcher tick now.")
    clock.t += 400  # the status line is long stale: live_chat_sleep owns this call
    out = _block(hooks, {"command": NEWLINE_DO[kind], "timeout": 120})
    assert out and "live_chat_sleep" in out["message"] and "loop-with-sleep" in out["message"], kind


def test_newline_before_do_keeps_prose_and_sleep_after_done_out():
    for cmd in NEWLINE_DO.values():
        assert long_wait.terminal_wait({"command": cmd}) == float("inf"), cmd
    not_loops = ["for c in a b\ndo\n  hermes kanban assign $c\ndone\nsleep 4",
                 "while read -r l\ndo\n  echo $l\ndone < f.txt; sleep 3",
                 'git commit -m "notes for later\ndo the rest tomorrow" && sleep 2',
                 'echo "waiting for CI"\ndo_check; sleep 5']
    for cmd in not_loops:
        assert (long_wait.terminal_wait({"command": cmd}) or 0) < 60, cmd
