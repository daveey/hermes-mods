"""Terminal-answer integration without network or live state."""
import sqlite3
import time

import pytest


ASK = "Could you cancel the Example subscription?"


@pytest.mark.parametrize("answer", ["The subscription was cancelled.", "The account was revoked.", "That action is impossible on this platform."])
def test_direct_answer_persists_and_replaces_after_nudge_budget(gate, answer):
    gate.ledger().set_open_asks("origin", [ASK])
    gate.pre_llm_call(session_id="origin", user_message=answer)
    assert gate.ledger().relevant("origin")[0]["retired"]
    result = gate.pre_final_gate(session_id="origin", final_response=ASK, attempt=100)
    assert result and result.get("replace")
    assert ASK not in result["replace"]
    assert not result.get("hold")


def test_retired_terminal_step_never_nudges_execution(gate):
    ask = "Please run `sudo example-tool` on the mini."
    gate.ledger().add(ask, "The step was completed.", session="origin")
    result = gate.pre_final_gate(session_id="origin", final_response=ask, attempt=0)
    assert result and result.get("replace")
    assert "nudge" not in result


def test_old_retirement_reaches_both_consumers(gate):
    gate.ledger().add(ASK, "The subscription was cancelled.", session="origin", ts=time.time() - 90 * 86400)
    assert "cancelled" in gate.core.l1_block("origin", gate.ledger())
    assert gate.run_judge("origin", [ASK], [])[0]["verdict"] == "already_answered"


def test_retirement_does_not_match_other_account_or_action(gate, monkeypatch):
    gate.ledger().add(ASK, "The subscription was cancelled.", session="origin")
    monkeypatch.setattr(gate, "LLM", lambda *a: '{"asks":[{"i":0,"verdict":"ok"}]}')
    for sid, ask in [("unrelated", ASK), ("origin", "Could you cancel the Different subscription?"),
                     ("origin", "Could you download the Example subscription invoice?")]:
        assert gate.run_judge(sid, [ask], [])[0]["verdict"] == "ok"


def test_compression_inherits_but_subagent_does_not(gate):
    with sqlite3.connect(gate.core.home() / "state.db") as c:
        c.execute("create table sessions(id text, parent_session_id text, end_reason text, source text)")
        c.executemany("insert into sessions values(?,?,?,?)", [
            ("origin", None, "compression", "cli"), ("compressed", "origin", None, "cli"),
            ("child", "origin", None, "subagent")])
    gate.ledger().add(ASK, "The subscription was cancelled.", session="origin", ts=1)
    assert gate.ledger().relevant("compressed")[0]["retired"]
    assert gate.ledger().relevant("child") == []


def test_model_paraphrase_cannot_create_terminal_fact(gate, monkeypatch):
    gate.ledger().set_open_asks("origin", [ASK])
    monkeypatch.setattr(gate.judge_mod, "classify_answer", lambda *a: [
        {"topic": ASK, "answer": "The subscription was cancelled."}])
    gate.pre_llm_call(session_id="origin", user_message="No, it is not cancelled.")
    assert not any(r["retired"] for r in gate.ledger().relevant("origin"))


@pytest.mark.parametrize("message", [
    "The Different subscription was cancelled.",
    'The notice says "the subscription was cancelled".',
    "The subscription was cancelled; the invoice still needs review.",
])
def test_unbound_terminal_clause_cannot_retire_pending_action(gate, monkeypatch, message):
    gate.ledger().set_open_asks("origin", [ASK])
    monkeypatch.setattr(gate.judge_mod, "classify_answer", lambda *a: [
        {"topic": ASK, "answer": "The subscription was cancelled."}])
    gate.pre_llm_call(session_id="origin", user_message=message)
    assert not any(r["retired"] for r in gate.ledger().relevant("origin"))


def test_identity_preserves_punctuation_and_long_topics(gate):
    first = "Could you revoke a.b@example.com?"
    second = "Could you revoke a-b@example.com?"
    gate.ledger().add(first, "The account was revoked.", session="origin")
    rows = gate.ledger().relevant("origin")
    assert gate.core.retired_matches([first], rows)
    assert not gate.core.retired_matches([second], rows)
    long_ask = "Could you cancel " + "Example " * 60 + "subscription?"
    gate.ledger().add(long_ask, "The subscription was cancelled.", session="origin")
    assert gate.core.retired_matches([long_ask], gate.ledger().relevant("origin"))
