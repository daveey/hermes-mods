"""Repeat-ask hold (self-heal agent-31a66d274a).

The live failure: four long replies over ~43 h each ended with a "Still waiting on you" list that restated the same
two optional asks (revoke a leaked OpenAI key in ~/.bash_profile; yes/no on secret hiding). The judge passes a true,
unanswered ask as ok every time, so nothing counted how often this chat had already shown it. A 3rd showing of the
same topic within 48 h is now held before the judge runs, unless it brings a new fact (status, deadline, cost).
"""
import json
import time

import pytest

FINDINGS = "I patched the export config and ran its tests: 12 passed."
KEY_1 = "Still waiting on you: revoke the OpenAI key in ~/.bash_profile (it leaked into the chat)."
KEY_2 = "Needs you: rotate the leaked OpenAI key in ~/.bash_profile."
KEY_3 = "Still waiting on you: revoke the OpenAI key from ~/.bash_profile."
HIDE_1 = "Still waiting on you: say yes or no on turning on secret hiding (redactions)."
KEY_NEW_FACT = ("Needs you: revoke the OpenAI key in ~/.bash_profile. OpenAI emailed that it will disable the key "
                "on 11-02 and it has already been charged $40 of usage.")
OTHER = "Want me to merge PR 12 into main?"


def ok_llm(system, user):
    """A judge that passes every ask (the real judge did this for every restatement)."""
    return json.dumps({"asks": [{"i": i, "verdict": "ok", "reason": "true and unanswered", "how": ""}
                                for i in range(12)]})


def _log(tmp_path):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def g(gate, monkeypatch):
    """The gate with a judge that passes everything and a clock that advances 2 h per delivered showing (the live
    restatements were hours apart; repeats minutes apart while the user works a step live are not held)."""
    gate.LLM = ok_llm
    real = time.time
    clock = {"off": -10 * 3600}
    monkeypatch.setattr(gate.core.time, "time", lambda: real() + clock["off"])
    gate._clock = clock
    return gate


def _show(g, text, session="s1"):
    out = g.pre_final_gate(session_id=session, final_response=f"{FINDINGS}\n\n{text}", attempt=0)
    assert out is None, out
    g._clock["off"] += 2 * 3600
    return out


def test_first_ask_passes_and_is_recorded_as_shown(g):
    _show(g, KEY_1)
    assert len(g.ledger().shown_rows("s1")) >= 1


def test_third_same_topic_ask_is_held_with_count_and_default_advice(g, tmp_path):
    _show(g, KEY_1)
    _show(g, KEY_2)  # 2nd showing is still allowed
    held = g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_3}", attempt=0)
    assert held and held.get("hold") is True, held
    nudge = held["nudge"]
    assert "2 times" in nudge
    assert "Don't restate" in nudge
    assert "silence means no" in nudge
    assert "bash_profile" in nudge  # names the repeated ask
    assert any(e.get("kind") == "ask_repeat_held" for e in _log(tmp_path))


def test_repeat_inside_a_list_is_held_but_a_new_item_is_not_named(g):
    _show(g, KEY_1 + "\n" + HIDE_1)
    _show(g, KEY_2 + "\n" + HIDE_1)
    draft = f"{FINDINGS}\n\nStill waiting on you:\n- Revoke the OpenAI key in ~/.bash_profile.\n- {OTHER}"
    held = g.pre_final_gate(session_id="s1", final_response=draft, attempt=0)
    assert held and held.get("hold"), held
    assert "bash_profile" in held["nudge"]
    assert "merge PR 12" not in held["nudge"]


def test_repeat_with_a_new_fact_passes(g):
    _show(g, KEY_1)
    _show(g, KEY_2)
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_NEW_FACT}", attempt=0) is None


def test_different_topic_passes_after_repeats(g):
    _show(g, KEY_1)
    _show(g, KEY_2)
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{OTHER}", attempt=0) is None


def test_different_identifier_is_a_different_topic(g):
    _show(g, "Want me to merge PR 10 into main?")
    _show(g, "Want me to merge PR 11 into main?")
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{OTHER}", attempt=0) is None


def test_other_chat_does_not_count(g):
    _show(g, KEY_1, session="other")
    _show(g, KEY_2, session="other")
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_3}", attempt=0) is None


def test_old_showings_outside_48h_do_not_count(g):
    _show(g, KEY_1)
    _show(g, KEY_2)
    g._clock["off"] += 50 * 3600
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_3}", attempt=0) is None


def test_user_asking_about_it_is_not_a_restatement(g):
    _show(g, KEY_1)
    _show(g, KEY_2)
    g.LOG.set_users("s1", ["what was that about the OpenAI key in bash_profile?"])
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_3}", attempt=0) is None


def test_hold_budget_spent_delivers(g):
    _show(g, KEY_1)
    _show(g, KEY_2)
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_3}", attempt=2) is None


def test_held_draft_is_not_counted_as_shown(g):
    _show(g, KEY_1)
    _show(g, KEY_2)
    before = len(g.ledger().shown_rows("s1"))
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_3}", attempt=0).get("hold")
    assert len(g.ledger().shown_rows("s1")) == before


def test_live_step_restated_minutes_apart_is_not_held(g):
    """Guidance while the user works a step live ("tap X once more") is not a nag: replay false holds."""
    for _ in range(2):
        assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_1}", attempt=0) is None
        g._clock["off"] += 300
    assert g.pre_final_gate(session_id="s1", final_response=f"{FINDINGS}\n\n{KEY_3}", attempt=0) is None


def test_same_topic_helper_is_conservative(g):
    c = g.core
    assert c.same_topic(KEY_1, KEY_2)
    assert c.same_topic(HIDE_1, "Yes or no on secret hiding.")
    assert not c.same_topic(KEY_1, HIDE_1)
    assert not c.same_topic("Want me to merge PR 10 into main?", OTHER)
    assert not c.same_topic("Can you check the build?", "Can you check the email?")
