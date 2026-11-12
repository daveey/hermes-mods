"""Reviewer probe (v13, contract lens): restore-trigger withdrawal wording outside the v13 regex families.
Fixtures only; no config command runs. Same harness as test_root_cap_expiry_state.py."""
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, FAR

OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
TEMP_EXPIRY = f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} Restore root {KEY} to 7 at {FAR}."
TEMP_ONE = f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} Restore root {KEY} to 7 on owner completion."

EXPIRY_WITHDRAWALS = [
    "Expiry cancelled.", "Expiry: none.", "No more expiry.", "Scrap expiry.", "Cancel expiry.",
    "Forget the 2099 deadline.", "Cancel the time limit.", "Drop the time limit.", "The time-box is gone.",
    "The cutoff is cancelled.", "The end date is withdrawn.", "Disregard the date.",
    "The rollback time no longer applies.", "Never mind the timer.", "Without any expiry.",
]
OWNER_COMPLETION_WITHDRAWALS = [
    "Owner completion no longer triggers it.", "Owner completion is not a trigger.",
    "Don't tie it to owner completion.", "Forget owner completion.", "The completion trigger is cancelled.",
]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", EXPIRY_WITHDRAWALS)
def test_expiry_withdrawal_wording(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later]), later


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", OWNER_COMPLETION_WITHDRAWALS)
def test_owner_completion_withdrawal_wording(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_ONE, later]), later


@pytest.mark.parametrize("surface", SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY])
    assert not w.blocked(surface, SET8, texts=[TEMP_ONE])
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[TEMP_EXPIRY, "Expiry cancelled."])
    assert w.blocked(surface, SET8, texts=["The queue is full, headroom is available."])
