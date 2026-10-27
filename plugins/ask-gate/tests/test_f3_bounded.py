"""Bounded context must not truncate deterministic retirement enforcement."""
import sqlite3

from test_f3_pipeline import ASK


def seed(gate):
    led = gate.ledger()
    led.add(ASK, "The subscription was cancelled.", session="origin", ts=1)
    for n in range(30):
        led.add(f"Could you finish example step {n}?", "The step was completed.", session="origin", ts=n+2)
    return led


def test_context_bounds_terminal_records(gate):
    led = seed(gate)
    rows = led.relevant("origin", limit=3)
    assert len(rows) == 3
    assert all(r["retired"] for r in rows)


def test_zero_context_limit_is_empty(gate):
    assert seed(gate).relevant("origin", limit=0) == []


def test_hidden_old_retirement_still_suppresses_delivery(gate):
    led = seed(gate)
    assert ASK not in [r["topic"] for r in led.relevant("origin")]
    result = gate.pre_final_gate(session_id="origin", final_response=ASK, attempt=100)
    assert result and result.get("replace")
    assert ASK not in result["replace"]


def test_candidate_lookup_normalizes_whitespace_not_identifiers(gate):
    led = seed(gate)
    rows = led.retired_for("origin", ["Could you  cancel the Example subscription?", ASK.lower()])
    matches = gate.core.retired_matches(["Could you  cancel the Example subscription?", ASK.lower()], rows)
    assert set(matches) == {0}
    assert led.retired_for("other", [ASK]) == []


def test_mixed_context_and_candidate_latest_selection(gate):
    led = seed(gate)
    for n in range(10):
        led.add(f"Question {n}", "Perhaps later", session="origin")
    led.add("Old question", "Perhaps later", session="origin", ts=1)
    rows = led.relevant("origin", limit=3)
    assert sum(r["retired"] for r in rows) == 3
    assert sum(not r["retired"] for r in rows) == 3
    assert "Old question" not in [r["topic"] for r in rows]
    led.add(ASK, "The account was revoked.", session="origin", ts=99)
    led.add(ASK, "The step was completed.", session="origin", ts=99)
    matches = led.retired_for("origin", [ASK, ASK.replace("you ", "you  ")])
    assert len(matches) == 1
    assert matches[0]["answer"] == "The step was completed."


def test_hidden_retirement_judge_mixed_indices(gate, monkeypatch):
    seed(gate)
    other = "Could you check the invoice?"
    def judge(asks, **kwargs):
        assert asks == [other]
        return [{"i": 0, "verdict": "ok"}]
    monkeypatch.setattr(gate.judge_mod, "judge", judge)
    result = gate.run_judge("origin", [other, ASK], [])
    assert [(v["i"], v["verdict"]) for v in result] == [(0, "ok"), (1, "already_answered")]


def test_candidate_lookup_preserves_punctuation(gate):
    led = gate.ledger()
    ask = "Could you revoke a.b@example.com?"
    led.add(ask, "The account was revoked.", session="origin")
    assert led.retired_for("origin", [ask])
    assert not led.retired_for("origin", [ask.replace("a.b", "a-b")])


def test_scope_does_not_depend_on_sql_variable_limit(gate, monkeypatch):
    led = seed(gate)
    original = led._conn
    def limited():
        c = original()
        c.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 16)
        return c
    monkeypatch.setattr(led, "_conn", limited)
    monkeypatch.setattr(gate.core, "answer_lineage", lambda s: [s] + [f"p{i}" for i in range(40)])
    assert led.relevant("origin", limit=2)
    assert led.retired_for("origin", [ASK])
    led.add("Ancestor action", "The step was completed.", session="p39")
    assert led.retired_for("origin", ["Ancestor action"])
    led.record_workflow_directive("p39", "Cancel this workflow.")
    assert led.workflow_stopped("origin")
    led.record_workflow_directive("p0", "Resume this workflow.")
    assert not led.workflow_stopped("origin")
