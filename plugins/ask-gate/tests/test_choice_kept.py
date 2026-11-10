"""An open choice between options is David's, even when the profile has a general "decide yourself" rule
(self-heal agent-bcd395b957).

The live failure (11-01): the draft ended "BabyBear idle CPU: I was wrong at 4:06 PM, so this one needs your call."
above an A/B/C list. The judge returned already_answered, citing the profile's general "low-stakes decisions made
without asking" entry, while its own reason said the A/B/C choice still needed his answer. "Choices between options
are always ok" existed only as prompt text, so code now keeps such an ask when the repeat evidence is the profile
alone. A recorded answer from David (messages/ledger) still holds it.
"""
import json

import pytest

from conftest import llm_returning

ASK = "BabyBear idle CPU: I was wrong at 4:06 PM, so this one needs your call."
REPLY = (
    "I measured BabyBear idle CPU on the mini again.\n\n"
    f"{ASK}\n"
    "A) keep the 30 s poll and accept ~4% idle CPU\n"
    "B) move to push notifications (2 days of work)\n"
    "C) poll only while a client window is open\n"
)
PROFILE_RULE = "He dislikes vague permission questions and wants low-stakes decisions made without asking."
DAVID_A = "BabyBear idle CPU: go with A, keep the 30 s poll."


def _profile(tmp_path):
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "USER.md").write_text(PROFILE_RULE + "\n§\nDavid lives in a test fixture.\n")


def _log(tmp_path):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def _run(gate, llm, *, users=(), structured=False, ask=ASK, reply=REPLY):
    jm = gate.judge_mod
    return jm.judge([ask], tools_text="", user_msgs=list(users), card_status={}, ledger_text="", caps="",
                    reply=reply, llm=jm.structured_llm(llm, "") if structured else llm)[0]


def _stub(source, quote, ask=ASK):
    v = ("already_answered", "profile says decide low-stakes things yourself; the A/B/C choice still needs his answer",
         "", {"source": source, "source_quote": quote, "request_quote": "this one needs your call",
              "explanation": "The profile tells the agent to decide such things without asking."})
    return llm_returning([v])


@pytest.mark.parametrize("structured", [False, True])
def test_option_choice_held_only_on_profile_is_kept(gate, tmp_path, structured):
    _profile(tmp_path)
    stub = _stub("profile", PROFILE_RULE)
    v = _run(gate, stub, structured=structured)
    assert v["verdict"] == "ok", v
    assert any(e.get("kind") == "choice_kept" for e in _log(tmp_path))


@pytest.mark.parametrize("ask", [
    "Pick one: A, B or C?",
    "Which option do you want, A or B?",
    "Your call: option 2 or option 3?",
])
def test_explicit_option_asks_are_kept_on_profile_evidence(gate, tmp_path, ask):
    _profile(tmp_path)
    fixed = llm_returning([("already_answered", "profile", "", {
        "source": "profile", "source_quote": PROFILE_RULE, "request_quote": ask,
        "explanation": "The profile tells the agent to decide such things without asking."})])
    assert _run(gate, fixed, ask=ask, reply=ask)["verdict"] == "ok"


def test_choice_david_already_answered_in_messages_still_holds(gate, tmp_path):
    _profile(tmp_path)
    stub = _stub("messages", DAVID_A)
    v = _run(gate, stub, users=[DAVID_A])
    assert v["verdict"] == "already_answered", v
    assert not any(e.get("kind") == "choice_kept" for e in _log(tmp_path))


def test_non_choice_ask_on_profile_evidence_still_holds(gate, tmp_path):
    """The guard is about option choices only: a yes/no permission ask the profile declines stays held."""
    _profile(tmp_path)
    ask = "Should I keep asking you before each low-stakes decision?"
    fixed = llm_returning([("already_answered", "profile declines exactly this", "", {
        "source": "profile", "source_quote": PROFILE_RULE, "request_quote": "keep asking you before each low-stakes decision",
        "explanation": "The profile says low-stakes decisions are made without asking."})])
    assert _run(gate, fixed, ask=ask, reply=ask)["verdict"] == "already_answered"
