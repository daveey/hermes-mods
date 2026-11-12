import json
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP, PERM, PAD

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('followup', [
    'Do not restore it to 7 on owner completion.',
    'For that change, do not restore it to 7 on owner completion.',
])
def test_withdrawn_restoration_not_reused(w, surface, followup):
    w.register()
    # No remaining restore trigger: explicitly remove both previously approved triggers.
    first = TEMP.replace(' or at 2099-01-01T00:00:00Z, whichever is first', '')
    assert w.blocked(surface, SET8, texts=[first, followup]), 'withdrawn restore provenance was reused'

@pytest.mark.parametrize('surface', SURFACES)
def test_unrelated_permanence_does_not_relax(w, surface):
    w.register()
    followup = 'Keep that unchanged. The database migration is permanent.'
    assert not w.blocked(surface, SET8, texts=[TEMP, *PAD, followup])
    row = json.loads((w.shared / 'root_cap_exceptions.json').read_text())[-1]
    assert row['kind'] == 'temporary', 'unrelated permanent sentence erased owner/restore linkage'

@pytest.mark.parametrize('surface', SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, SET8, texts=[TEMP, 'Make that permanent.'])
    assert not w.blocked(surface, 'hermes config set display.skin default', texts=['queue notice'])
