import pytest
from test_root_cap_guard import w, KEY, PERM, SET8, SURFACES

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('text', [
    "'" + PERM + "'",
    '‘' + PERM + '’',
    f'Did I authorize changing root {KEY} from 7 to 8 permanently for this rollout',
])
def test_nondecision_cannot_override(w, surface, text):
    w.register()
    assert w.blocked(surface, SET8, texts=[text]), repr(text)

@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('text', [
    'The assistant wrote: "Cancel that."',
    "'Cancel that.'",
])
def test_reported_cancel_does_not_withdraw_user_decision(w, surface, text):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM, text]), repr(text)

@pytest.mark.parametrize('surface', SURFACES)
def test_native_decision_control(w, surface):
    w.register()
    assert not w.blocked(surface, SET8, texts=[PERM])
    assert w.blocked(surface, SET8, texts=[PERM, 'Cancel that.'])
    assert not w.blocked(surface, 'hermes config set display.skin default')
