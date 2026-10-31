"""Runtime-written user turns are never David's answers (L4 ledger provenance).

Every runtime notice below was recorded in a live ledger as "David answered" before this fix: 89 rows across two
hosts, each telling later turns "do not ask these again" about asks he never saw. They arrive as user-role
messages, so text shape and the row's display_kind are the only provenance signals.
"""
import json

import pytest

ASK = "Want me to rotate the key?"

NOTICES = [
    "[chat-closeout] Work this chat started is now waiting on the user. Nothing else waits on this: no other card "
    "depends on it. Approve or decline the spend. Unblock = approve; archive = no rerun.",
    "[chat-closeout] Every kanban card this chat handed work to has settled. This turn is the close-out: done.",
    "\u23f8 [hub:default] @default Kanban t_1 blocked: approve or decline the key rotation, yes or no",
    "[Message from another Hermes session \"Some chat\" (@session:default@hub/2026)]\n\nDone, already handled.",
    "[System note: Your previous turn was interrupted mid-run. Continue with the next step.] ok, done",
    "[owner-wake] Card(s) this chat owns need you: approve the rotation, yes or no.",
    "[ASYNC DELEGATION BATCH COMPLETE] 2 subagents finished: done, approved.",
    "[STILL IN PROGRESS \u2014 this is the active request, restated after the compaction boundary]\nyes, go ahead",
    "[OUT-OF-BAND USER MESSAGE \u2014 a direct message]\n\u2714 [default] @default Kanban t_1 done \u2014 ok\n"
    "[/OUT-OF-BAND USER MESSAGE]",
]


def _answering_model(gate):
    calls = []

    def model(system, user):
        calls.append(user)
        return json.dumps({"answered": [{"i": 0, "topic": "rotate the key", "answer": "yes"}]})
    gate.LLM = model
    return calls


@pytest.mark.parametrize("notice", NOTICES)
def test_runtime_notice_text_is_not_recorded_as_an_answer(gate, notice):
    gate.ledger().set_open_asks("s1", [ASK])
    calls = _answering_model(gate)
    gate.pre_llm_call(session_id="s1", user_message=notice, conversation_history=[], platform="tui")
    assert gate.ledger().recent(3600) == []
    assert gate.ledger().open_asks("s1")
    assert calls == []
    assert gate.LOG.users("s1") == []


@pytest.mark.parametrize("kind", ["kanban_notice", "auto_continue"])
def test_runtime_display_kind_turn_is_not_recorded_even_without_bracket_text(gate, kind):
    text = "Card t_1 is waiting on you: approve it, yes or no"
    gate.ledger().set_open_asks("s1", [ASK])
    calls = _answering_model(gate)
    hist = [{"role": "assistant", "content": ASK}, {"role": "user", "content": text, "display_kind": kind}]
    gate.pre_llm_call(session_id="s1", user_message=text, conversation_history=hist, platform="tui")
    assert gate.ledger().recent(3600) == []
    assert gate.ledger().open_asks("s1")
    assert calls == []
    assert gate.LOG.users("s1") == []


@pytest.mark.parametrize("kind", [None, "", "steer"])
def test_typed_answer_is_still_recorded(gate, kind):
    text = "yes, go ahead and rotate it"
    gate.ledger().set_open_asks("s1", [ASK])
    _answering_model(gate)
    row = {"role": "user", "content": text}
    if kind is not None:
        row["display_kind"] = kind
    gate.pre_llm_call(session_id="s1", user_message=text,
                      conversation_history=[{"role": "assistant", "content": ASK}, row], platform="tui")
    assert [a["answer"] for a in gate.ledger().recent(3600)] == [text]
    assert text in gate.LOG.users("s1")


def test_typed_answer_after_an_earlier_runtime_row_is_still_recorded(gate):
    text = "yes, rotate it"
    gate.ledger().set_open_asks("s1", [ASK])
    _answering_model(gate)
    hist = [{"role": "user", "content": "Card t_1 needs you", "display_kind": "kanban_notice"},
            {"role": "assistant", "content": ASK}]
    gate.pre_llm_call(session_id="s1", user_message=text, conversation_history=hist, platform="tui")
    assert [a["answer"] for a in gate.ledger().recent(3600)] == [text]
    assert gate.LOG.users("s1") == [text]


def test_wrapped_steer_answer_is_still_recorded(gate):
    gate.ledger().set_open_asks("s1", [ASK])
    _answering_model(gate)
    msg = "[OUT-OF-BAND USER MESSAGE \u2014 a direct message]\nyes, rotate it\n[/OUT-OF-BAND USER MESSAGE]"
    gate.pre_llm_call(session_id="s1", user_message=msg, conversation_history=[], platform="tui")
    assert [a["answer"] for a in gate.ledger().recent(3600)] == ["yes, rotate it"]


def test_notice_rows_written_by_a_pre_fix_process_stay_invisible(gate):
    led = gate.ledger()
    led.add("Rerun the controls", "[chat-closeout] Work this chat started is now waiting on the user.", session="s1")
    led.add("Approve the spend", "\u23f8 [hub:default] @default Kanban t_1 blocked: approve", session="s1")
    led.add("Rotate the key", "yes, rotate it", session="s1")
    assert [r["answer"] for r in led.relevant("s1")] == ["yes, rotate it"]
    block = gate.core.l1_block("s1", led, db_paths=[])
    assert "Rotate the key" in block
    assert "Rerun the controls" not in block and "Approve the spend" not in block


def test_retired_notice_row_does_not_retire_the_ask(gate):
    import sqlite3
    led = gate.ledger()
    led.add("Cancel the subscription", "[Message from another Hermes session] the subscription was cancelled",
            session="s1", retire=False)
    c = sqlite3.connect(led.path)
    c.execute("insert into retired_answers(answer_id) select id from answered")
    c.commit()
    c.close()
    assert led.retired_for("s1", ["Cancel the subscription"]) == []
    led.add("Cancel the subscription", "the subscription was cancelled", session="s1")
    assert [r["answer"] for r in led.retired_for("s1", ["Cancel the subscription"])] == [
        "the subscription was cancelled"]
