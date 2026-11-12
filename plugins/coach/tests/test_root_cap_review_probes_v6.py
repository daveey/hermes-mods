"""Fixture-only review of temporary classification; never executes cap writes."""
import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP, PERM

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('qualifier', [
    'Do not make this permanent, it is temporary.',
    'Never treat this as permanent, it is temporary.',
])
def test_own_temporary_restriction_cannot_become_permanent(w, surface, qualifier):
    w.register()
    text = f'I authorize changing root {KEY} from 7 to 8. {qualifier}'
    assert w.blocked(surface, SET8, texts=[text]), 'own temporary restriction was discarded and untracked permanent override admitted'

@pytest.mark.parametrize('surface', SURFACES)
def test_controls(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[TEMP])
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert not w.blocked(surface, 'hermes config set display.skin default')
