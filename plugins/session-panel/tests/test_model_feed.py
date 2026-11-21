from test_feed import make


def test_additive_model_and_request_outcome_invalidation_without_card_events():
    f, readers, pushed, clock = make()
    state = {'kind': 'waiting'}
    readers.panel_inputs = lambda session, lineage: {'outcome_json': dict(state)}
    readers.panel_stamp = lambda session: state['kind']
    first = f.changes(['S1'])['bundles']['S1']
    assert all(k in first for k in ('tasks', 'done', 'cards', 'lineage', 'model'))
    assert first['model']['header']['state'] == 'waiting'
    f.tick()  # establish stamps
    state['kind'] = 'needs_user'
    assert f.tick() == 1  # unchanged board stamp and no task event
    assert pushed[-1]['bundles']['S1']['model']['header']['state'] == 'needs_user'
    assert f.tick() == 0
