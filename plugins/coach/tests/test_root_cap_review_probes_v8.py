"""Independent bounded-intent probes; callback fixtures only, no config execution."""
import json
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, PERM, TEMP

BASE = f"I authorize changing root {KEY} from 7 to 8."

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('restriction', [
    'This change is for only 10 minutes.',
    'This change is for just 10 minutes.',
    'This is not temporary, but restore it on owner completion.',
    'This is not temporary, but it expires tomorrow.',
])
def test_bound_without_own_provenance_refuses(w, surface, restriction):
    w.register()
    assert w.blocked(surface, SET8, texts=[BASE + ' ' + restriction]), restriction

@pytest.mark.parametrize('surface', SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, 'hermes config set display.skin default', texts=[BASE + ' This change is for only 10 minutes.'])
