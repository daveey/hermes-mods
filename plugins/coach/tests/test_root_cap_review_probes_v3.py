"""Independent v3 review probes. Fixture homes only; never execute a config write."""
import json
import pytest
from test_root_cap_guard import w, KEY, PERM, SET8, SURFACES
from test_root_cap_activation import Steps

@pytest.mark.parametrize('surface', SURFACES)
def test_latest_different_value_supersedes_older_permission(w, surface):
    w.register()
    newer = f'I authorize changing root {KEY} from 7 to 9 permanently for this rollout.'
    assert w.blocked(surface, SET8, texts=[PERM, newer]), 'old value 8 authorization survived newer explicit value 9 decision'

@pytest.mark.parametrize('text', [
    "'I approve freezing root kanban.max_in_progress at 7 (spec.md).'",
    'Did I approve freezing root kanban.max_in_progress at 7 (spec.md)?',
])
def test_non_decision_cannot_register(w, text):
    with pytest.raises(w.rc.RootCapError):
        w.register(approval_message_id=w.message(text))


@pytest.mark.parametrize('legacy', [False, True])
def test_action_operation_requires_native_authority_and_owner_source_ref(w, tmp_path, legacy):
    st = Steps(w, tmp_path)
    if legacy:
        st.sql('update operations set body=? where id=?',
               json.dumps({k: v for k, v in st.operation.items() if k != 'binding'}),
               st.operation['operationId'])
    # The real store accepts agent-created requests; deciding the store in Python is not proof of a native human row.
    assert not st.request['scope']['sourceRefs']
    assert w.sdb.execute('select count(*) from messages').fetchone()[0] == 0
    source = st.source()
    with pytest.raises(w.rc.RootCapError):
        w.rc._verify_operation(source['authority'], source['mapping'], source['owner_card'], w.root)
