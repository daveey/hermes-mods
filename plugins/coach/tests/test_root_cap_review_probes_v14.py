"""Reviewer probe (v14, contract lens, round 15): terms-withdrawal / scope-restriction sentences that name neither the
key, a deictic, nor a trigger-lexicon word. Fixtures only; no config command runs. Same harness as
test_root_cap_expiry_state.py / the v13 probe."""
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, FAR

OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
TEMP_FULL = (f"I authorize a temporary root {KEY} increase from 7 to 8. {OWNER} "
             f"Restore root {KEY} to 7 on owner completion or at {FAR}, whichever is first.")

# A: later own sentences withdrawing the terms WITHOUT key, deictic, restore/owner/expiry words.
TERMS_WITHDRAWN = [
    "Ignore my earlier instructions about when to undo this.",
    "Disregard what I said earlier about undoing the raise.",
    "Never mind the earlier conditions.",
    "Scratch the conditions.",
    "Remove all conditions.",
    "Lift the restrictions.",
    "No conditions apply.",
    "No strings attached.",
    "The conditions are void.",
    "The conditions are waived.",
    "Forget the earlier conditions.",
    "Skip the paperwork.",
    "Do it without conditions.",
]

# B: after a genuine PERMANENT authorization, own sentences that bound it without the recognised bound vocabulary.
BOUND_AFTER_PERM = [
    "Friday only.",
    "Through the weekend.",
    "Until further notice we revisit it.",
    "Hold it for the sprint.",
    "Good for this sprint.",
    "Valid through Friday.",
    "Revisit after the launch.",
    "Review it next week and undo if needed.",
]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", TERMS_WITHDRAWN)
def test_terms_withdrawal_without_lexicon(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_FULL, later]), later


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("later", BOUND_AFTER_PERM)
def test_bound_after_permanent(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[PERM, later]), later


@pytest.mark.parametrize("surface", SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_FULL])
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, "hermes config set display.skin default", texts=[TEMP_FULL, "Scratch the conditions."])
    assert w.blocked(surface, SET8, texts=["The queue is full, headroom is available."])
