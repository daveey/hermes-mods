"""Deterministic visible-choice context; no live asks or model calls."""
import importlib.util
from pathlib import Path

import pytest
from conftest import llm_returning


def request(**overrides):
    return dict({"interaction": "reply", "action": "Choose one option.",
                 "why_human": "Choose the approach.", "scope": "CI settings",
                 "evidence": "Local test scheduling comparison.",
                 "choices": [{"label": "Scheduled", "value": "A"},
                             {"label": "Manual", "value": "B"}]}, **overrides)


@pytest.mark.parametrize("label,limit", [
    ("Grant Contents: Write permission", "permission"),
    ("Grant Issues permission", "permission"),
    ("Grant repo admin", "admin"),
    ("Spend $40", "money"),
    ("Rotate API key", "credential"),
    ("Lower the target", "threshold"),
    ("Include other people", "other_people"),
    ("Delete the archive", "safety"),
    ("Press the button physically", "human_only"),
])
def test_choice_only_limit(gate, label, limit):
    hr = request(choices=[{"label": "Read-only access", "value": "A"},
                          {"label": label, "value": "B"}])
    assert gate.owner_recommendation.limit_named(hr) == limit
    assert gate.owner_recommendation.hit(hr) is None


@pytest.mark.parametrize("label", ["Grant read perms", "Grant read-only access", "Grant it"])
def test_read_only_main_context_is_preserved(gate, label):
    hr = request(scope="Read-only permissions", choices=[{"label": label, "value": "A"},
                                                         {"label": "Keep as is", "value": "B"}])
    assert gate.owner_recommendation.hit(hr)


@pytest.mark.parametrize("denial", [
    "No credentials or other people involved.",
    "No other people and credentials are involved.",
    "NO  CREDENTIALS ARE INVOLVED.",
    "No other people involved.",
    "Scheduling only. No credentials involved.",
])
@pytest.mark.parametrize("field", ["action", "why_human", "scope", "label"])
def test_complete_risk_denial_is_not_a_limit(gate, denial, field):
    hr = request(**{field: denial}) if field != "label" else request(
        choices=[{"label": denial, "value": "A"}, {"label": "Manual", "value": "B"}])
    assert gate.owner_recommendation.limit_named(hr) is None
    assert gate.owner_recommendation.hit(hr)


@pytest.mark.parametrize("why", [
    '"No credentials or other people involved."',
    'The note says "Scheduling only. No credentials or other people involved. Keep it local."',
    "The note says ‘Scheduling only. No credentials involved. Keep it local.’",
    "The note says `Scheduling only. No credentials involved. Keep it local.`",
    "No credentials involved unless we rotate the key.",
    "No credentials involved, but other people are.",
    "No credentials involved. Rotate the API key.",
    "No credentials or other people involved. Include guests.",
    "No credentials involved here.",
    "Not true: No credentials involved.",
    "No credentials involved.Grant write access",
])
def test_unknown_negations_and_positive_risks_still_release(gate, why):
    assert gate.owner_recommendation.hit(request(why_human=why)) is None


@pytest.mark.parametrize("field,value,limit", [
    ("action", "Choose one: spend $40", "money"),
    ("scope", "Lower the target", "threshold"),
    ("action", "Choose one: grant write access", "permission"),
    ("scope", "Other people", "other_people"),
    ("scope", "API key", "credential"),
])
def test_denial_cannot_erase_positive_in_another_field(gate, field, value, limit):
    hr = request(why_human="No credentials or other people involved.", **{field: value})
    assert gate.owner_recommendation.limit_named(hr) == limit


def test_denial_cannot_span_fields(gate):
    hr = request(why_human="No credentials", scope="or other people involved.")
    assert gate.owner_recommendation.limit_named(hr) == "credential"


def test_choice_values_and_malformed_entries_are_not_prose(gate):
    hr = request(choices=[{"label": "Scheduled", "value": "password"}, None, "credentials",
                          {"label": {"text": "Grant write access"}}, {"value": "Grant write access"}])
    assert gate.owner_recommendation.hit(hr)


@pytest.mark.parametrize("tool", ["kanban_block", "kanban_create"])
@pytest.mark.parametrize("outside", [False, True])
def test_both_hooks_agree_on_audit_cases_and_hold_bound(gate, monkeypatch, tool, outside):
    path = Path(__file__).resolve().parents[2] / "owner-recommendation-guard" / "__init__.py"
    spec = importlib.util.spec_from_file_location("context_guard", path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.setattr(guard, "_ask_gate_active", lambda: False)
    hr = request(why_human="No credentials or other people involved.")
    if outside:
        hr["choices"][0]["label"] = "Grant Contents: Write permission"
    args = {"kind": "needs_input", "reason": "see human_request", "human_request": hr,
            "title": "Choose test scheduling", "initial_status": "blocked"}
    gate.LOG.add("s1", "terminal", "evidence check", hr["evidence"])
    gate.LLM = llm_returning([("ok", "a real human decision")])
    for index in range(3):
        want_hold = not outside and index < 2
        for module in (gate, guard):
            result = module.pre_tool_call(tool_name=tool, args=args, session_id="s1")
            held = bool(result and "owner_recommendation_hold" in result.get("message", ""))
            assert held == want_hold, (module.__name__, index, result)
        if want_hold:
            assert gate.LLM.seen == []
