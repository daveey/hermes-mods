"""A no-soak rule is not an answer to waiting for the precondition a test needs (t_01a1c7d033912ae9).

The live failure (11-10, ask-gate.jsonl 21:12:45 and 23:35:34): David's 11-01 message "i dont want to wait for any
soaking, if something is waiting for a soak, run the check immediately and complete" was cited as already_answered
for "wait for a real Claude overload to prove the switch" and for a natural-exhaustion waiver ask. Agents then read the
gate's ruling as "David forbade the precondition wait" and t_7e2ee45c re-reconciled for 69 runs. Code now keeps such
an ask when the repeat evidence is only the soak rule; an answer about the precondition itself still holds it.
"""
import json

import pytest

from conftest import llm_returning

NO_SOAK = ("i dont want to wait for any soaking, if something is waiting for a soak, run the check immediately "
           "and complete")
OVERLOAD = ("The one thing that's your call is whether to reopen the model-balancing card and wait for a real Claude "
            "overload to prove the switch.")
NATURAL = "Do you want me to keep the incident open and wait for natural Claude exhaustion before the Gmail proof?"


def _log(tmp_path):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _run(gate, llm, ask, users, *, structured=False):
    jm = gate.judge_mod
    return jm.judge([ask], tools_text="", user_msgs=list(users), card_status={}, ledger_text="", caps="",
                    reply=ask, llm=jm.structured_llm(llm, "") if structured else llm)[0]


def _stub(quote, request, reason="David's standing decision not to wait for soaks already declines natural waiting"):
    return llm_returning([("already_answered", reason, "", {
        "source": "messages", "source_quote": quote, "request_quote": request,
        "explanation": "He said not to wait."})])


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("ask,quoted", [(OVERLOAD, "wait for a real Claude overload"),
                                         (NATURAL, "wait for natural Claude exhaustion")])
def test_precondition_wait_held_only_on_no_soak_rule_is_kept(gate, tmp_path, structured, ask, quoted):
    v = _run(gate, _stub("i dont want to wait for any soaking", quoted), ask, [NO_SOAK], structured=structured)
    assert v["verdict"] == "ok", v
    assert any(e.get("kind") == "precondition_kept" for e in _log(tmp_path))


def test_answer_about_the_precondition_itself_still_holds(gate, tmp_path):
    said = "Don't wait for the Claude overload, close the incident as NO_PROOF."
    v = _run(gate, _stub(said, "wait for a real Claude overload", reason="He said close it as NO_PROOF"),
             OVERLOAD, [said])
    assert v["verdict"] == "already_answered", v
    assert not any(e.get("kind") == "precondition_kept" for e in _log(tmp_path))


def test_an_actual_soak_ask_still_holds(gate, tmp_path):
    ask = "Should I keep the 48 h soak running before I close the retention card?"
    v = _run(gate, _stub("i dont want to wait for any soaking", "keep the 48 h soak running"), ask, [NO_SOAK])
    assert v["verdict"] == "already_answered", v


@pytest.mark.parametrize("ask,evidence,expected", [
    (OVERLOAD, NO_SOAK, True),
    (NATURAL, "no observation window after deploys", True),
    ("Should I wait until the quota is exhausted again?", NO_SOAK, True),
    ("Wait for the precondition (pool held) and then run the proof?", NO_SOAK, True),
    ("Should I keep soaking it for 48 h?", NO_SOAK, False),          # the ask is itself a soak
    (OVERLOAD, "He wants low-stakes decisions made without asking.", False),  # not soak evidence
    ("Should I merge PR 12 now?", NO_SOAK, False),                    # not a wait
])
def test_classifier(gate, ask, evidence, expected):
    assert gate.judge_mod.is_precondition_wait(ask, evidence) is expected
