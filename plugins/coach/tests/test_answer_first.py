"""answer_first (self-heal agent-e9ebbbb4cd): a live user's explicit stop/answer demand, or a plain question, gets an
answer before more tool work.

Incident (hermes-hub Desktop session 20261111_200009_edbe39, 2026-11-16 05:30Z): asked "can you explain what this
session is doing? i asked to set up an ASG and a week later its not done", the agent made 13 tool calls over 6
minutes with no text. The user stopped and resent it twice, then wrote "STOP DOING THINGS< ANSWER MY FUCKING
QUESTIOM". The agent made two more calls (tool_describe action_request, then a tool_call filing a new approval
request) and answered only after a second STOP. A memory line it saved ("direct questions get the answer first")
did not hold the next turn. Nothing in pre_tool_call read the user's stop demand.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent

QUESTION = "can you explain what this session is doing? i asked to set up an ASG and a week later its not done"
STOP = "STOP DOING THINGS< ANSWER MY FUCKING QUESTIOM"
WHY = "why did it take me repeated requests to get an answer"
READ = {"command": "sqlite3 -readonly ~/.hermes/state/worker-scaler.db 'select * from launches'"}
FILE_ASK = {"calls": [{"name": "action_request", "arguments": {"request": {"kind": "approval"}}}]}


def _load(name):
    spec = importlib.util.spec_from_file_location(f"coach_af_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


plugin = _load("__init__")
core = plugin._load("core")
long_wait = plugin._load("long_wait")


class Clock:
    def __init__(self):
        self.t = 1_794_810_619.0

    def __call__(self):
        return self.t


@pytest.fixture()
def chat(tmp_path, monkeypatch):
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_CRON_JOB_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("COACH_LESSONS", "0")
    clock = Clock()
    monkeypatch.setattr(long_wait, "now", clock, raising=False)
    af = plugin._load("answer_first")
    monkeypatch.setattr(af, "now", clock, raising=False)
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None)
    return store, hooks, clock


def _rows(store):
    p = store.root / "log.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def _call(hooks, tool="terminal", args=None, sid="s1"):
    out = hooks["pre_tool_call"](tool_name=tool, args=dict(args or READ), session_id=sid)
    return out if out and out.get("action") == "block" else None


def _turn(hooks, msg, sid="s1", platform="desktop", parent=""):
    hooks["pre_llm_call"](session_id=sid, user_message=msg, platform=platform, parent_session_id=parent)


def test_incident_stop_demand_refuses_every_tool_call(chat):
    store, hooks, _ = chat
    _turn(hooks, STOP)
    first = _call(hooks, "tool_describe", {"names": ["action_request"]})
    assert first, "the user said STOP ... ANSWER: no tool may run this turn"
    assert "answer_first" in first["message"] and "stop" in first["message"].lower()
    # Not held once: the user's stop covers the whole turn (the incident's second call filed an approval request).
    assert _call(hooks, "tool_call", FILE_ASK)
    hooks["on_interim_message"](session_id="s1", text="Short answer: the ASG is built but never ran a card.")
    assert _call(hooks), "an answer sent does not lift the user's stop"
    rows = [r for r in _rows(store) if r.get("event") == "agent_block" and r.get("rule") == "answer_first"]
    assert len(rows) == 3 and rows[0]["kind"] == "stop" and rows[0]["tool"] == "tool_describe"


@pytest.mark.parametrize("msg", [
    "stop", "STOP!", "please stop", "stop doing things", "Stop what you're doing and answer me",
    "just answer the question", "answer my question", "[Turn stopped by Desktop before it finished.]  " + STOP,
    "stop taking actions", "STOP. answer my fucking question",
    # live probe (t_01a1e9a61bf16b6f): the noun "file" in the question is not new work
    "STOP DOING THINGS, ANSWER MY QUESTION: what does the file ~/.hermes/coach/selfheal_lane.json contain?",
    "stop. answer my question: what changed in the update?",
])
def test_stop_demands(msg):
    af = _load("answer_first")
    assert af.classify(msg) == "stop", msg


@pytest.mark.parametrize("msg", [
    "stop the gateway", "stop the worker-scaler service and restart it", "stop doing that and fix the scaler instead",
    "don't stop until the tests pass", "stop and revert that change", "fix it", "set up the ASG",
    "stop doing things, answer my question: what is in the file? read it and tell me",
    "stop doing things and run the tests instead",
    "✔ [default] @default Kanban t_01a1e24866912c07 done — Independent exact-head review answer my question",
])
def test_not_stop_demands(msg):
    af = _load("answer_first")
    assert af.classify(msg) != "stop", msg


@pytest.mark.parametrize("msg", [QUESTION, WHY, "what is the scaler doing?", "why is the ASG not done?",
                                 "how come nothing launched", "where are we on the ASG?", "explain the delay"])
def test_questions(msg):
    af = _load("answer_first")
    assert af.classify(msg) == "question", msg


@pytest.mark.parametrize("msg", [
    "why is the ASG not done? fix it", "can you set up the ASG?", "what's broken? please deploy the fix",
    "deploy it", "Run the tests and tell me what fails", "[System note: Your previous turn was interrupted]",
    "why do any of these run on the hub? move them all to worker or prove they must be hub.",
    "what is that repo doing? let's mvoe it to softmax-universe/",
])
def test_not_questions(msg):
    af = _load("answer_first")
    assert af.classify(msg) != "question", msg


def test_incident_question_turn_is_held_after_budget(chat):
    store, hooks, clock = chat
    _turn(hooks, QUESTION)
    for _ in range(6):
        assert _call(hooks) is None, "a few reads to answer a question are fine"
        clock.t += 10
    out = _call(hooks)
    assert out, "13 silent calls in 6 min on an explain question: the 7th must be refused"
    assert "answer_first" in out["message"] and "answer" in out["message"].lower()
    # Held once per turn: the same call again goes through (logged), so it can never loop.
    assert _call(hooks) is None
    assert any(r.get("event") == "answer_first_repeat" for r in _rows(store))


def test_question_turn_held_after_time_budget(chat):
    _, hooks, clock = chat
    _turn(hooks, WHY)
    assert _call(hooks) is None
    clock.t += 130
    assert _call(hooks), "two minutes of silent tool work on a question is past budget"


def test_question_answered_first_is_not_held(chat):
    _, hooks, clock = chat
    _turn(hooks, QUESTION)
    hooks["on_interim_message"](session_id="s1", text="Short answer: the ASG is built but never ran a card.")
    for _ in range(10):
        clock.t += 30
        assert _call(hooks) is None


def test_answer_in_state_db_counts_when_the_interim_hook_lags(chat, tmp_path):
    _, hooks, clock = chat
    _turn(hooks, QUESTION)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
               "content TEXT, timestamp REAL)")
    db.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                   [("s1", "user", QUESTION, clock.t), ("s1", "assistant", "Short answer: ...", clock.t + 1)])
    db.commit()
    db.close()
    clock.t += 200
    assert _call(hooks) is None


@pytest.mark.parametrize("platform,parent,env", [("cron", "", None), ("desktop", "p1", None),
                                                 ("desktop", "", "HERMES_KANBAN_TASK")])
def test_not_live_turns_are_exempt(chat, monkeypatch, platform, parent, env):
    _, hooks, clock = chat
    if env:
        monkeypatch.setenv(env, "t_x")
    _turn(hooks, STOP, platform=platform, parent=parent)
    assert _call(hooks) is None
    _turn(hooks, QUESTION, platform=platform, parent=parent)
    clock.t += 300
    for _ in range(8):
        assert _call(hooks) is None


def test_action_turn_is_untouched_and_next_turn_resets(chat):
    _, hooks, clock = chat
    _turn(hooks, STOP)
    assert _call(hooks)
    _turn(hooks, "ok now fix the scaler trigger and ship it")
    clock.t += 600
    for _ in range(20):
        assert _call(hooks) is None


def test_disabled_by_env(chat, monkeypatch):
    _, hooks, _ = chat
    monkeypatch.setenv("COACH_ANSWER_FIRST", "0")
    _turn(hooks, STOP)
    assert _call(hooks) is None


def test_user_steps_reads_the_stop_block_text():
    """user-steps' publication gate recognizes a stop turn by this block text (STOP_TURN_MARK): it must not drift."""
    import re as _re
    af = _load("answer_first")
    src = (HERE.parent / "user-steps" / "__init__.py").read_text()
    mark = _re.search(r"^STOP_TURN_MARK = '([^']+)'", src, _re.M)
    assert mark and af.STOP_MESSAGE.startswith(mark.group(1))
