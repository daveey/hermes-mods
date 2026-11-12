import json
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, PERM, FAR
TEMP_EXPIRY = (f'I authorize a temporary root {KEY} increase from 7 to 8. '
               f'Owner card {TEMP_OWNER}, run 1, this session. '
               f'Restore root {KEY} to 7 at {FAR}.')
@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('later', ['The expiry is cancelled.', 'The expiry no longer applies.',
                                  'Do not use the expiry anymore.',
                                  'The expiry is now 2000-01-01T00:00:00Z.'])
def test_expiry_mutation_invalidates_old_only_trigger(w, surface, later):
    w.register()
    assert w.blocked(surface, SET8, texts=[TEMP_EXPIRY, later]), 'stale expiry retained: '+later
@pytest.mark.parametrize('surface', SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP_EXPIRY])
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, 'hermes config set display.skin default', texts=[TEMP_EXPIRY,'The expiry is cancelled.'])
