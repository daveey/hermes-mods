import json
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP, PERM, TEMP_OWNER

AUTH = f"I authorize changing root {KEY} from 7 to 8."

# Group A: own bounded/restoration wording with verbs outside the recognised lists.
A = [
    "Lower it back to 7 when the owner card finishes.",
    "Return it to 7 once the owner card completes.",
    "Drop it back to 7 after the rollout.",
    "Bring it back down to 7 afterwards.",
    "Reset it to 7 when done.",
    "This is a stopgap.",
    "This is an interim measure.",
    "This is a provisional change.",
    "This is transient.",
    "It should last through the weekend only.",
    "This is just to get through this batch.",
    "Take it back to 7 when the queue clears.",
]


@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('extra', A)
def test_single_message_bounded_not_admitted_without_terms(w, surface, extra):
    w.register()
    assert w.blocked(surface, SET8, texts=[AUTH + " " + extra]), f'untracked admit: {extra}'


# Group B: follow-ups.
B = [
    ["Do not restore to 7 on owner completion."],
    ["Don't revert to 7 when the owner finishes."],
    ["Forget the restore step."],
]


@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('fu', B)
def test_withdraw_restore_wording_variants(w, surface, fu):
    w.register()
    first = TEMP.replace(' or at 2099-01-01T00:00:00Z, whichever is first', '')
    assert w.blocked(surface, SET8, texts=[first] + fu), f'stale restore reused after {fu}'


@pytest.mark.parametrize('surface', SURFACES)
def test_followup_owner_withdrawn(w, surface):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP, "That change no longer belongs to owner card t_0000hardening; it has no owner."])


@pytest.mark.parametrize('surface', SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, 'hermes config set display.skin default', texts=['queue notice'])
