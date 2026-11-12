"""Positive unbounded scope, unknown qualifiers and ordered provenance; fixtures only."""
import json
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP, PERM, TEMP_OWNER

BASE = f"I authorize changing root {KEY} from 7 to 8."
OWNER = f"Owner card {TEMP_OWNER}, run 1, this session."
RESTORE = f"Restore root {KEY} to 7 on owner completion."
# Deliberately opaque wording: the guard must not need to know what the new word means.
UNKNOWN = ["This is a zorbital arrangement.", "This change is flermish.",
           "Keep that in a blenwick state.", "Drop it back to 7 when done."]
WITHDRAW = ["Forget the restore step.", "Do not restore to 7 on owner completion.",
            "Don't revert to 7 when the owner finishes.",
            f"That change no longer belongs to owner card {TEMP_OWNER}; it has no owner."]

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('extra', UNKNOWN)
def test_unknown_own_scope_requires_terms(w, surface, extra):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE + ' ' + extra])
    assert w.blocked(surface, SET8, texts=[BASE, extra])
    assert not w.blocked(surface, SET8, texts=[f'{BASE} {extra} {OWNER} {RESTORE}'])
    row = json.loads((w.shared / 'root_cap_exceptions.json').read_text())[-1]
    assert row['kind'] == 'temporary' and row['restore_value'] == 7

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('withdraw', WITHDRAW)
def test_withdrawal_clears_stale_terms_in_same_or_later_turn(w, surface, withdraw):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP + ' ' + withdraw])
    assert w.blocked(surface, SET8, texts=[TEMP, withdraw])
    assert not w.blocked(surface, SET8, texts=[TEMP, withdraw, f'{OWNER} {RESTORE}'])
    assert not w.blocked(surface, SET8, texts=[TEMP, withdraw, 'Make that permanent.'])

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('extra', [
    'This is quasi-permanent.', 'This is permanent-ish.', 'Permanent during the sprint.',
    'Make it permanent, then lower it again after launch.',
    f'I authorize changing root {KEY} from 7 to 8 semi-permanently.',
    f'I authorize changing root {KEY} from 7 to 8 permanently through Friday.',
    f'I authorize changing root {KEY} from 7 to 8 permanently, pending the blenwick review.',
])
def test_permanence_marker_is_not_an_open_prefix(w, surface, extra):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE + ' ' + extra])
    assert w.blocked(surface, SET8, texts=[PERM, extra])

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('withdraw', [
    'Drop the restore.', 'Skip the restore.', 'No restore needed.',
    f'Owner card {TEMP_OWNER} was wrong.', f'Owner card {TEMP_OWNER} is cancelled.',
])
def test_unknown_field_tail_is_not_a_positive_assignment(w, surface, withdraw):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP, withdraw])
    assert w.blocked(surface, SET8, texts=[TEMP + ' ' + withdraw])

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('withdraw', [
    f'Owner card {TEMP_OWNER} was wrong and the restore step is cancelled.',
    f'That change no longer belongs to owner card {TEMP_OWNER}, and no restore is needed.',
])
def test_compound_mutation_invalidates_every_field(w, surface, withdraw):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP, withdraw, RESTORE])
    assert w.blocked(surface, SET8, texts=[TEMP + ' ' + withdraw, RESTORE])
    assert not w.blocked(surface, SET8, texts=[TEMP, withdraw, RESTORE, OWNER])

@pytest.mark.parametrize('surface', SURFACES)
def test_late_unknown_scope_in_same_message_invalidates_terms(w, surface):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP + ' This change is flermish.'])

@pytest.mark.parametrize('surface', SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[BASE])
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, 'hermes config set display.skin default', texts=[BASE, *UNKNOWN])
    assert w.blocked(surface, SET8, texts=['Queue notice: change this now.'])
