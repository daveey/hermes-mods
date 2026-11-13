"""agent-521fd3b5d4: the coach keeps a turn's tool log in memory only, so a turn that resumed after an update pause (core
writes a user-role resume note; a new gateway process judges the reply) had an empty tool log. A reply that named "the
review card" (no id) fell back to the cards this turn's calls touched (reply_check.tail_card_ids -> turn_card_ids),
found none, and report_only scored owner_source none: a real hand-off filed as dropped work.

turn_start now rebuilds the cut turn's tool log from state.db when the user message is core's resume note and the
session's in-memory log is empty (AgentCoach._rehydrate_resumed_turn)."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"t_resume_{name}", HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rules = _mod("agent_rules")
rc = _mod("reply_check")
sh = _mod("self_heal")

SID = "20261111_213321_be8afc"
CARD = "t_01a1cf3bcfe1f5b9"
OLD_CARD = "t_0000aaaabbbbcccc"
USER_ASK = "close out the billing card and tell me what is left"
PAUSE_NOTE = ("[System note: Your previous turn was interrupted mid-run — it paused at a step boundary for a Hermes "
              "update. Check the current state before redoing anything, then finish the task. The interrupted "
              "request was:]\n\n" + USER_ASK)
REPLY = ("Closed out. Three billing questions stay open (rate, cap, refund). The review card settles these before "
         "any dispatch.")
COMMENT_RESULT = json.dumps({"ok": True, "comment_id": 121, "task": {
    "id": CARD, "status": "running", "assignee": "default",
    "runs": [{"id": 24330, "status": "running", "outcome": None, "summary": "live worker run"}]}})


class _Log:
    def __init__(self):
        self.rows = []

    def log(self, row):
        self.rows.append(row)


def _db(path, rows):
    db = sqlite3.connect(path)
    db.execute("create table messages (id integer primary key autoincrement, session_id text, role text, "
               "content text, tool_call_id text, tool_calls text, tool_name text, timestamp real, "
               "active integer not null default 1)")
    for i, r in enumerate(rows):
        db.execute("insert into messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp)"
                   " values (?, ?, ?, ?, ?, ?, ?)",
                   (r.get("sid", SID), r["role"], r.get("content"), r.get("tool_call_id"),
                    json.dumps(r["tool_calls"]) if r.get("tool_calls") else None, r.get("tool_name"), 1000.0 + i))
    db.commit()
    db.close()
    return path


def _call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _paused_turn(note_persisted=True):
    rows = [
        # an earlier, finished turn: its calls must never leak into the resumed turn
        {"role": "user", "content": "check the old card"},
        {"role": "assistant", "content": "", "tool_calls": [_call("c0", "kanban_show", {"task_id": OLD_CARD})]},
        {"role": "tool", "tool_call_id": "c0", "tool_name": "kanban_show", "content": f"status: done {OLD_CARD}"},
        {"role": "assistant", "content": "Old card is done."},
        # the paused turn
        {"role": "user", "content": USER_ASK},
        {"role": "assistant", "content": "", "tool_calls": [
            _call("c1", "tool_call", {"calls": [{"name": "cross_task", "arguments": {
                "action": "comment", "task_id": CARD, "body": "billing evidence: rate, cap, refund"}}]})]},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "tool_call", "content": COMMENT_RESULT},
        {"role": "assistant", "content": "", "tool_calls": [_call("c2", "terminal", {"command": "git status -sb"})]},
        {"role": "tool", "tool_call_id": "c2", "tool_name": "terminal", "content": "## main...origin/main"},
        # another session's rows never count
        {"sid": "other", "role": "assistant", "content": "", "tool_calls": [
            _call("x9", "kanban_show", {"task_id": "t_ffffeeeeddddcccc"})]},
    ]
    if note_persisted:
        rows.append({"role": "user", "content": PAUSE_NOTE})
    return rows


@pytest.fixture(autouse=True)
def _no_boards(monkeypatch):
    monkeypatch.setenv("COACH_BOARD_DBS", "")
    monkeypatch.setenv("COACH_SHADOW_DBS", "")


def _fresh(tmp_path, rows):
    coach = rules.AgentCoach(signals=_Log())
    coach.state_db = _db(tmp_path / "state.db", rows)
    return coach


@pytest.mark.parametrize("persisted", [True, False])
def test_resumed_turn_rebuilds_tool_log_from_state_db(tmp_path, persisted):
    coach = _fresh(tmp_path, _paused_turn(note_persisted=persisted))
    coach.turn_start(SID, PAUSE_NOTE)
    _ask, log = coach.turn_context(SID)
    assert any(CARD in l for l in log), log
    assert not any(OLD_CARD in l or "t_ffffeeeeddddcccc" in l for l in log), log
    assert any(l.startswith("-> terminal(git status -sb") for l in log), log
    assert rc.turn_card_ids(log) == [CARD]
    assert rc.owner_source(REPLY, log, user_ask=USER_ASK) != "none"
    assert any(CARD in l for l in coach.turn_rich(SID))
    assert coach.resume_info(SID) == {"resumed_turn": True, "rehydrated": len(log)}
    ev = [r for r in coach.signals.rows if r.get("event") == "turn_log_rehydrated"]
    assert ev and ev[0]["session_id"] == SID and ev[0]["lines"] == len(log) and ev[0]["cards"] == [CARD], ev
    # replaying the cut turn queues no hints and fires no per-call signals for it
    assert coach.take(SID) is None
    assert coach._s(SID)["turn_tools"] == 0


def test_normal_new_turn_still_starts_empty(tmp_path):
    coach = _fresh(tmp_path, _paused_turn())
    coach.turn_start(SID, "what about the refund question?")
    assert coach.turn_context(SID)[1] == []
    assert coach.resume_info(SID) == {}
    assert not [r for r in coach.signals.rows if r.get("event") == "turn_log_rehydrated"]


def test_same_process_resume_keeps_its_live_log(tmp_path):
    """The process that ran the paused turn still holds the real log: turn_start clears it as for any turn, but a
    resume note rebuilds it from state.db, so the judged log is never empty either way."""
    coach = _fresh(tmp_path, _paused_turn())
    coach.turn_start(SID, USER_ASK)
    coach.observe(SID, "terminal", {"command": "echo hi"}, "hi")
    coach.turn_start(SID, PAUSE_NOTE)
    assert rc.turn_card_ids(coach.turn_context(SID)[1]) == [CARD]


def test_unreadable_state_db_fails_open(tmp_path):
    coach = rules.AgentCoach(signals=_Log())
    coach.state_db = tmp_path / "missing" / "state.db"
    coach.turn_start(SID, PAUSE_NOTE)
    assert coach.turn_context(SID)[1] == []
    assert coach.resume_info(SID) == {"resumed_turn": True, "rehydrated": 0}


def test_rehydrated_log_is_capped(tmp_path):
    rows = [{"role": "user", "content": USER_ASK}]
    for i in range(80):
        rows.append({"role": "assistant", "content": "", "tool_calls": [
            _call(f"c{i}", "terminal", {"command": f"echo {i}"})]})
        rows.append({"role": "tool", "tool_call_id": f"c{i}", "tool_name": "terminal", "content": str(i)})
    coach = _fresh(tmp_path, rows)
    coach.turn_start(SID, PAUSE_NOTE)
    log = coach.turn_context(SID)[1]
    assert len(log) == rules.REHYDRATE_MAX_LINES
    assert log[-1].startswith("-> terminal(echo 79")


def test_resume_note_matches_self_heal():
    for text in (PAUSE_NOTE, "  " + PAUSE_NOTE, USER_ASK, "[System note: something else]", "", None):
        assert rules.is_resume_note(text) == sh.is_resume_note(text), text


def test_report_only_scored_records_resume_fields(tmp_path):
    rows = []

    def llm(system, prompt):
        return json.dumps({"handoff": 0, "confirm": 0, "unverified": 0, "unwatched": 0, "report_only": 0.95,
                           "evidence": "billing questions left to the review card"})

    rc.check(REPLY + " Not fixed yet.", USER_ASK, [], llm, thresholds={"report_only": 0.7},
             confirm=lambda: (0.9, "left open"), confirm_floor=0.5, only=["report_only"], audit=rows.append,
             card_status=lambda c: "ready", resume={"resumed_turn": True, "rehydrated": 0})
    scored = [r for r in rows if r.get("event") == "report_only_scored"]
    assert scored and scored[0]["resumed_turn"] is True and scored[0]["rehydrated"] == 0, rows


def test_report_only_nudge_asks_for_the_card_id():
    assert "by its id (t_" in rc.NUDGES["report_only"]


def test_resumed_outcome_with_no_rehydration_files_a_coach_defect():
    sig = _mod("signals")
    n = {"session_id": SID, "rule": "report_only", "evidence": "billing left open", "p": 0.9, "nudge_id": "n1"}
    r = {"rule": "report_only", "resolved": False, "p": 0.9, "evidence": "The review card settles these",
         "resumed_turn": True, "rehydrated": 0, "src": "confirm"}
    assert sig.resume_lost_outcome(r)
    s = sig.resume_lost_signal(n, r, 1000.0)
    assert s["component"] == "coach" and s["fp"].startswith("coach-")
    assert sig.resume_lost_signal({**n, "session_id": "other"}, r, 1.0)["fp"] == s["fp"]  # one card, not per session
    assert not sig.resume_lost_outcome({**r, "rehydrated": 4})
    assert not sig.resume_lost_outcome({**r, "resumed_turn": False})
    assert not sig.resume_lost_outcome({**r, "resolved": True})
