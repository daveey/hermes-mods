"""live_chat_sleep (self-heal agent-57d0cd6941 / agent-9dd87a311d): a long foreground wait in a live chat whose user
has had no fresh status.

Incident (hermes-hub Desktop session 20261112_040336_f74a3e): the user said "fix the hub paths issue RIGHT NOW".
The agent sent one interim line early ("Now the replay over real hub cards..."), made ~25 more tool calls, saw
`hermes-core-wt ship` defer on a quota brake, and its next call was a FOREGROUND `date +%s; sleep 200; ... ship`
with nothing said first. Two minutes later the user wrote "why are you not answering me" and stopped the turn.

long_wait_pending_reply (long_wait.py) stays quiet once any interim text was sent this turn, and a loop whose single
sleep is < 60 s never adds up to its threshold. live_chat_sleep covers both: a foreground wait of >= 60 s (a loop
with sleep is unbounded) in a live chat whose last status is older than STALE_S is refused once per turn. Kanban
workers, cron and subagents keep foreground sleeps.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent

ASK = "fix the hub paths issue RIGHT NOW. dont make a card, just do it inline"
INCIDENT = {"command": "date +%s; sleep 200; cd ~/code/_wt/hub-paths && ~/.hermes/bin/hermes-core-wt ship hub-paths",
            "timeout": 400}
LOOP = {"command": "for i in 1 2; do gh run view 123 --json status; sleep 30; done"}


def _load(name):
    spec = importlib.util.spec_from_file_location(f"coach_lcs_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


plugin = _load("__init__")
core = plugin._load("core")
long_wait = plugin._load("long_wait")


class Clock:
    def __init__(self):
        self.t = 1_794_460_000.0

    def __call__(self):
        return self.t


@pytest.fixture()
def chat(tmp_path, monkeypatch):
    for k in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKER_TASK", "HERMES_CRON_JOB_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))  # no state.db unless a test writes one
    monkeypatch.setenv("COACH_LESSONS", "0")
    clock = Clock()
    monkeypatch.setattr(long_wait, "now", clock, raising=False)
    store = core.Store(tmp_path / "coach")
    _, hooks = plugin.build(store=store, checkout=None)
    return store, hooks, clock


def _rows(store):
    p = store.root / "log.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def _block(hooks, args, sid="s1"):
    out = hooks["pre_tool_call"](tool_name="terminal", args=dict(args), session_id=sid)
    return out if out and out.get("action") == "block" else None


def _incident_turn(hooks, clock, sid="s1", platform="desktop", parent=""):
    hooks["pre_llm_call"](session_id=sid, user_message=ASK, platform=platform, parent_session_id=parent)
    hooks["on_interim_message"](session_id=sid, text="Now the replay over real hub cards, then I ship it.")
    clock.t += 400  # ~25 tool calls later, nothing else said


def test_incident_stale_status_then_long_sleep_is_blocked(chat):
    store, hooks, clock = chat
    _incident_turn(hooks, clock)
    out = _block(hooks, INCIDENT)
    assert out, "a 200 s foreground sleep 400 s after the last status line must be refused"
    msg = out["message"]
    assert "live_chat_sleep" in msg and "background=true" in msg and "notify=true" in msg
    assert "waiting for" in msg and "until when" in msg
    rows = [r for r in _rows(store) if r.get("event") == "agent_block" and r.get("rule") == "live_chat_sleep"]
    assert rows and rows[-1]["session_id"] == "s1" and rows[-1]["secs"] == 200
    # held once per turn: the same call again goes through (logged), so it can never loop
    assert _block(hooks, INCIDENT) is None
    assert any(r.get("event") == "live_chat_sleep_repeat" for r in _rows(store))


def test_fresh_status_right_before_the_wait_passes(chat):
    _, hooks, clock = chat
    _incident_turn(hooks, clock)
    hooks["on_interim_message"](session_id="s1", text="Fix is ready; ship is deferred by a quota brake until 05:11.")
    clock.t += 5
    assert _block(hooks, INCIDENT) is None


def test_fresh_status_in_state_db_passes_when_the_interim_hook_lags(chat, tmp_path):
    _, hooks, clock = chat
    _incident_turn(hooks, clock)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
               "content TEXT, timestamp REAL)")
    db.executemany("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                   [("s1", "user", ASK, clock.t - 400), ("s1", "assistant", "Now the replay...", clock.t - 400),
                    ("s1", "assistant", "Ship deferred until 05:11; waiting.", clock.t - 3),
                    ("s2", "user", ASK, clock.t - 400), ("s2", "assistant", "Now the replay...", clock.t - 400)])
    db.commit()
    db.close()
    assert _block(hooks, INCIDENT) is None
    _incident_turn(hooks, clock, sid="s2")  # s2's newest assistant text in state.db is 800 s old
    assert _block(hooks, INCIDENT, sid="s2")


def test_background_and_short_sleeps_pass(chat):
    _, hooks, clock = chat
    _incident_turn(hooks, clock)
    assert _block(hooks, {**INCIDENT, "background": True}) is None
    assert _block(hooks, {"command": "sleep 5; curl -s localhost:8080/health"}) is None


def test_loop_with_sleep_is_unbounded_and_blocked(chat):
    store, hooks, clock = chat
    _incident_turn(hooks, clock)
    assert _block(hooks, LOOP)
    # and before any reply at all, where long_wait_pending_reply's 60 s sum misses it (one sleep 30)
    hooks["pre_llm_call"](session_id="s9", user_message=ASK, platform="desktop")
    assert _block(hooks, LOOP, sid="s9")
    assert [r["secs"] for r in _rows(store) if r.get("rule") == "live_chat_sleep"] == [None, None]


def test_first_wait_before_any_reply_stays_long_wait_pending_reply(chat):
    store, hooks, _ = chat
    hooks["pre_llm_call"](session_id="s1", user_message=ASK, platform="desktop")
    out = _block(hooks, INCIDENT)
    assert out and "long_wait_pending_reply" in out["message"]
    # its repeat goes through: one redirect per turn, not two rules stacking
    assert _block(hooks, INCIDENT) is None
    assert not [r for r in _rows(store) if r.get("rule") == "live_chat_sleep"]


def test_kanban_cron_and_subagents_keep_foreground_sleep(chat, monkeypatch):
    _, hooks, clock = chat
    _incident_turn(hooks, clock, sid="k1", platform="kanban")
    assert _block(hooks, INCIDENT, sid="k1") is None
    _incident_turn(hooks, clock, sid="c1", platform="cron")
    assert _block(hooks, INCIDENT, sid="c1") is None
    _incident_turn(hooks, clock, sid="sub", parent="parent-session")
    assert _block(hooks, INCIDENT, sid="sub") is None
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_example")
    _incident_turn(hooks, clock, sid="k2")
    assert _block(hooks, INCIDENT, sid="k2") is None


def test_asked_delay_and_unknown_session_pass(chat):
    _, hooks, clock = chat
    hooks["pre_llm_call"](session_id="d", user_message="wait 3 minutes then check the hub", platform="desktop")
    hooks["on_interim_message"](session_id="d", text="ok")
    clock.t += 400
    assert _block(hooks, INCIDENT, sid="d") is None
    assert _block(hooks, INCIDENT, sid="never-started") is None
