"""unblock_card_hold: a blocked card's human_request must carry the generate-unblock-card brief.

Incident: 2026-11-20 session summary showed card t_01a1f8b56d1a69fc ("Reply A or B in this chat. A = authorize one
bounded repair run against the current Mac Desktop (PID 5198 ...)") with no situation, consequences or
recommendation. The skills try-to-unblock / generate-unblock-card hold the reasoning; this rule checks the output.
"""
import json

import pytest
from conftest import llm_returning

INCIDENT = dict(
    action=("Reply A or B in this chat. A = authorize one bounded repair run against the current Mac Desktop "
            "(PID 5198; you start it with a debug inspector, or tell me an inspector already exists). "
            "B = close acceptance with the documented gaps."),
    why_human=("The proven app process is gone and no inspector is available. Getting one means restarting "
               "Desktop or changing its launch settings, which is outside my approved scope."),
    interaction="approval",
    scope="PR1697 Mac delivery acceptance",
    evidence="ps: no process 5198; inspector port 9229 closed",
)

GOOD = dict(
    action="Choose how to finish verifying the fix. Unblock = A (one supervised test run). Archive = B.",
    why_human=("Tried: the original test app exited, so I can't attach to it; a Desktop restart with debugging is "
               "outside this card's approval. Only you can: allow a Desktop restart, or accept less proof."),
    interaction="approval",
    scope=("Situation: The fix for the Mac app's idle screen is merged and installed. Three of four checks passed; "
           "the last needs a debug connection to the running app, which has closed.\n"
           "Options:\n"
           "A) One supervised test run — restart Desktop with debugging (~1 min, chats reload). Reversible.\n"
           "B) Accept with the known gap — lowers the acceptance criteria to 3 of 4 checks. Nothing restarts.\n"
           "Recommend: A — one minute of restart buys the only check covering the bug you hit."),
    choices=[{"label": "A: one supervised test run (recommended)", "value": "A"},
             {"label": "B: accept with the known gap", "value": "B"}],
    evidence="ps: no process 5198; inspector port 9229 closed",
)

PHYSICAL = dict(
    action="Plug the ring charger into the MacBook. Unblock = I resume the pairing test. Archive = drop it.",
    why_human="Tried: BLE scan from the Mac and the hub; the ring is asleep. Only you can: physically plug it in.",
    interaction="physical",
    scope=("Situation: The pairing test needs the ring awake and it has been asleep since last night.\n"
           "Recommend: plug it in when you are next at the desk; the test takes two minutes after that."),
    evidence="ble-scan: 0 devices matching ring",
)


@pytest.fixture(autouse=True)
def _brief_on(monkeypatch):
    monkeypatch.setenv("ASK_GATE_UNBLOCK_BRIEF", "1")


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("kind") == kind] if p.exists() else []


def _block(gate, hr, session="s1", task=None):
    gate.LOG.add(session, "terminal", "evidence check", hr["evidence"])
    gate.LLM = llm_returning([("ok", "a real human decision")] * 4)
    args = {"kind": "needs_input", "reason": "see human_request", "human_request": hr}
    if task:
        args["task_id"] = task
    return gate.pre_tool_call(tool_name="kanban_block", session_id=session, args=args)


def test_incident_is_missing_every_part(gate):
    gaps = gate.unblock_card_quality.missing(INCIDENT)
    assert any("Tried" in g for g in gaps)
    assert any("Situation" in g for g in gaps)
    assert any("Recommend" in g for g in gaps)
    assert any("Options" in g for g in gaps)


def test_good_brief_passes(gate):
    assert gate.unblock_card_quality.missing(GOOD) == []


def test_physical_step_needs_no_options(gate):
    assert gate.unblock_card_quality.missing(PHYSICAL) == []


def test_markdown_labels_are_recognised(gate):
    hr = dict(GOOD, scope=GOOD["scope"].replace("Situation:", "**Situation:**").replace("Recommend:", "- **Recommend**:"))
    assert gate.unblock_card_quality.missing(hr) == []


def test_options_from_choices_when_no_option_lines(gate):
    scope = GOOD["scope"].split("Options:")[0] + "Options: see the buttons.\nRecommend: A — cheapest full proof of the fix."
    assert gate.unblock_card_quality.missing(dict(GOOD, scope=scope)) == []
    assert any("Options" in g for g in gate.unblock_card_quality.missing(dict(GOOD, scope=scope, choices=None)))


@pytest.mark.parametrize("labels,count", [(("A", "B"), 0), (("A (recommended)", "B (recommended)"), 2)])
def test_exactly_one_recommended_choice(gate, labels, count):
    hr = dict(GOOD, choices=[{"label": l, "value": str(i)} for i, l in enumerate(labels)])
    assert any(f"{count} choices" in g for g in gate.unblock_card_quality.missing(hr))


def test_empty_recommend_is_missing(gate):
    hr = dict(GOOD, scope=GOOD["scope"].rsplit("Recommend:", 1)[0] + "Recommend: A")
    assert any("Recommend" in g for g in gate.unblock_card_quality.missing(hr))


def test_incident_block_is_held_before_the_judge(gate, tmp_path):
    r = _block(gate, INCIDENT, task="t_01a1f8b56d1a69fc")
    assert r and r["action"] == "block"
    assert "unblock_card_hold" in r["message"]
    assert "try-to-unblock" in r["message"] and "generate-unblock-card" in r["message"]
    assert gate.LLM.seen == []
    assert _events(tmp_path, "unblock_card_hold")


def test_blocked_create_is_held(gate):
    gate.LOG.add("s1", "terminal", "evidence check", INCIDENT["evidence"])
    gate.LLM = llm_returning([("ok", "fine")])
    r = gate.pre_tool_call(tool_name="kanban_create", session_id="s1", args={
        "title": "PR1697 Mac delivery", "initial_status": "blocked", "human_request": INCIDENT})
    assert r and "unblock_card_hold" in r["message"]


def test_good_brief_is_not_held_by_this_rule(gate, tmp_path):
    r = _block(gate, GOOD, task="t_good")
    assert r is None or "unblock_card_hold" not in r.get("message", "")
    assert not _events(tmp_path, "unblock_card_hold")


def test_hold_is_bounded_then_released(gate, tmp_path):
    for _ in range(2):
        r = _block(gate, INCIDENT, task="t_bound")
        assert r and "unblock_card_hold" in r["message"]
    r = _block(gate, INCIDENT, task="t_bound")
    assert r is None or "unblock_card_hold" not in r.get("message", "")
    assert _events(tmp_path, "unblock_card_released")


def test_rule_error_fails_open(gate, tmp_path, monkeypatch):
    monkeypatch.setattr(gate.unblock_card_quality, "hit", lambda r: 1 / 0)
    r = _block(gate, INCIDENT, task="t_err")
    assert r is None or "unblock_card_hold" not in r.get("message", "")
    assert _events(tmp_path, "unblock_card_error")
