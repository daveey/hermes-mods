"""Owner variants must trigger without incidental legacy owner nouns."""
import importlib.util
from pathlib import Path

import pytest


_spec = importlib.util.spec_from_file_location(
    "t_relay_owner_variants", Path(__file__).resolve().parents[1] / "relay_repeat.py"
)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


@pytest.mark.parametrize("qualifier", ["cloud", "pool", "remote", "kanban", "background"])
@pytest.mark.parametrize("noun", ["worker", "agent", "session"])
def test_new_owner_with_fresh_activity(qualifier, noun, monkeypatch):
    # Background is a legacy positive control; the other four qualifiers must
    # depend on the added alternative. No 'the session-start' or card ID here.
    monkeypatch.setenv("COACH_RELAY_REPEAT", "on")
    phrase = "is tidying the follow-ups now"
    reply = f"A {qualifier} {noun} {phrase}."
    assert rr.relay_owner(reply) == {"cards": [], "phrase": phrase}

    state = {"last_reply": reply}
    result = rr.assess(state, 2, "Any update?")
    assert result is not None
    assert result["kind"] == "relay"
    assert result["cards"] == []
    assert result["phrase"] == phrase
    assert state["relay_repeat_turn"] == 2
    assert rr.assess(state, 2, "Any update?") is None


@pytest.mark.parametrize("qualifier", ["cloud", "pool", "remote", "kanban", "background"])
def test_owner_without_unfinished_activity_is_not_a_relay(qualifier):
    assert rr.relay_owner(f"A {qualifier} worker finished the follow-ups.") is None


def test_fresh_activity_without_an_owner_is_not_a_relay():
    assert rr.relay_owner("I am tidying the follow-ups now.") is None
