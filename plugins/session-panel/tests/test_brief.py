"""Shared brief parsing uses synthetic, public-safe requests only."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("test_session_panel_brief", Path(__file__).parents[1] / "brief.py")
brief = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brief)


def test_scope_options_and_recommendation():
    parsed = brief.parse_brief({"scope": (
        "Situation: The service is stopped and needs a decision.\nOptions:\n"
        "B) Wait: no restart today\nD) Restart — takes one minute\n"
        "Recommend: D because it restores the service."),
        "why_human": "Tried: checked the logs. Only you can: approve the restart."})
    assert parsed["situation"] == "The service is stopped and needs a decision."
    assert parsed["options"] == [
        {"letter": "B", "name": "Wait", "detail": "No restart today"},
        {"letter": "D", "name": "Restart", "detail": "Takes one minute"}]
    assert parsed["recommend_letter"] == "D"
    assert parsed["reason"] == "it restores the service."
    assert parsed["only_you"] == "Only you can: approve the restart."
    assert parsed["tried"] == "checked the logs."


def test_choice_labels_keep_their_own_letters_not_positions():
    parsed = brief.parse_brief({"choices": [
        {"label": "D: Restart (recommended)", "value": "restart"},
        {"label": "B: Wait", "value": "wait"}]})
    assert parsed["options"] == [
        {"letter": "D", "name": "Restart", "detail": ""},
        {"letter": "B", "name": "Wait", "detail": ""}]
    assert parsed["recommend_letter"] == "D"


def test_unlettered_choices_use_user_steps_positional_badges():
    parsed = brief.parse_brief({"choices": [{"label": "Wait", "value": "wait"}]})
    assert parsed["options"] == [{"letter": "A", "name": "Wait", "detail": ""}]
    assert parsed["recommend_letter"] == ""


@pytest.mark.parametrize("recommend,letter,reason", [
    ("Option B — lower cost", "B", "lower cost"),
    ("A, B both work", "", "A, B both work"),
    ("A and B both work", "", "A and B both work"),
    ("I recommend B", "", "I recommend B"),
    ("Roll back because it is safe", "", "Roll back because it is safe"),
])
def test_recommendation_only_accepts_a_single_leading_letter(recommend, letter, reason):
    parsed = brief.parse_brief({"scope": "Recommend: " + recommend})
    assert (parsed["recommend_letter"], parsed["reason"]) == (letter, reason)


@pytest.mark.parametrize("why,line,tried", [
    ("Only you can: sign in.", "Only you can: sign in.", ""),
    ("Tried: checked twice.", "Agent tried: checked twice.", ""),
    ("Needs your password; only you can sign in.", "Why you: Needs your password; only you can sign in.", ""),
    ("", "", ""),
])
def test_why_split(why, line, tried):
    parsed = brief.parse_brief({"why_human": why})
    assert parsed["only_you"] == line
    assert parsed["tried"] == tried


def test_sections_compatibility_and_contract_stops():
    scope = ("## Situation\r\nThe service stopped after a failed update.\r\n"
             "- Options:\n- A) Apply plan B: restart\n1. stop\n2. start\n- B) Wait\n"
             "- Recommend: A - restores the service quickly.\nDEVICE: test device")
    parts = brief.sections(scope)
    assert parts["recommend"] == "A - restores the service quickly."
    assert brief.options(parts["options"]) == ["A) Apply plan B: restart 1. stop 2. start", "B) Wait"]


def test_empty_and_wrong_type_inputs():
    for value in (None, "", [], {}):
        parsed = brief.parse_brief(value)
        assert parsed["situation"] == ""
        assert parsed["options"] == []
        assert parsed["only_you"] == ""
