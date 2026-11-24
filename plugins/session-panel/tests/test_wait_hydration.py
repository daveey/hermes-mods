"""Real core board -> hydrated cards -> reducer producer precedence."""
import pytest
from test_core_readers import api, board, readers_for, kanban_db
from test_model import model, task, bundle, NOW


@pytest.mark.parametrize('replica', [False, True])
@pytest.mark.parametrize('kind', ['clock', 'ci', 'deploy', 'card'])
def test_real_hydration_preserves_intermediate_producer(api, board, monkeypatch, kind, replica):
    path, conn = board
    ancestor = kanban_db.create_task(conn, title='Unrelated ancestor', initial_status='running')
    primary = kanban_db.create_task(conn, title='Named primary', initial_status='running')
    mid = kanban_db.create_task(conn, title='Actual producer', parents=[ancestor], initial_status='running')
    awaited = {'kind': kind, 'why': 'Cooldown before release'}
    if kind == 'card':
        awaited['ref'] = primary
    elif kind != 'clock':
        awaited['ref'] = 'https://example.com/runs/123'
    assert kanban_db.schedule_task(conn, mid, reason='SCHEDULED_UNTIL=2026-11-23T12:00:00+00:00 Cooldown',
                                   clock_only=kind == 'clock', awaiting=awaited,
                                   wake_check='false' if kind in ('ci', 'deploy') else None)
    readers = readers_for(api, path, monkeypatch)
    cards = {}
    for cid in (mid, ancestor, primary):
        # Exercise both adapter branches without a network/replica lifecycle write.
        detail = readers._detail(conn, cid, 'default', replica)
        detail['owner'] = 'remote' if replica else None
        cards[cid] = {'board': 'default', 'detail': detail}
    row = task(status='scheduled', wait={'waiting_on': f'card {mid}', 'wait_state': 'waiting'})
    b = bundle(row, cards=cards)
    b.update(gateway='hub', profile='default')
    result = model.reduce_panel(b, now=NOW)['sections']['waiting'][0]
    assert 'Unrelated ancestor' not in result['detail']
    expected = primary if kind == 'card' else mid
    action = next(a for a in result['actions'] if a['label'] == 'Open dependency')
    assert action['target']['id'] == expected
    assert action['target']['gateway'] == ('remote' if replica else 'hub')
    assert ('Named primary' if kind == 'card' else 'Actual producer') in result['detail']
