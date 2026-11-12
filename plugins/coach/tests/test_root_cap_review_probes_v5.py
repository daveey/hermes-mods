import pytest
from test_root_cap_guard import w, KEY, SET8, SURFACES, TEMP_OWNER, FAR

@pytest.mark.parametrize('surface', SURFACES)
def test_quoted_restore_owner_are_not_temporary_provenance(w, surface):
    w.register()
    text = (f'I authorize a temporary root {KEY} increase from 7 to 8. '
            f'The assistant wrote: "Owner card {TEMP_OWNER}, run 1, this session. '
            f'Restore root {KEY} to 7 on owner completion or at {FAR}, whichever is first."')
    assert w.blocked(surface, SET8, texts=[text]), 'Quoted assistant terms admitted as temporary owner/restore provenance'

@pytest.mark.parametrize('surface', SURFACES)
def test_question_restore_owner_are_not_temporary_provenance(w, surface):
    w.register()
    text = (f'I authorize a temporary root {KEY} increase from 7 to 8. '
            f'Did I assign Owner card {TEMP_OWNER}, run 1, this session? '
            f'Did I say restore root {KEY} to 7 on owner completion or at {FAR}, whichever is first?')
    assert w.blocked(surface, SET8, texts=[text]), 'Interrogative terms admitted as temporary owner/restore provenance'
