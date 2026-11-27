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


WHY = 'Event watch on rollout owners; backstop at nightly service rollout 10:30Z'
WATCHED = ['t_0000000000000001', 't_0000000000000002', 't_0000000000000003', 't_0000000000000004']
CHECK = ('test "$(ssh example-gateway "sqlite kanban \\"select exists(select 1 from task_comments where id>100 '
         "and task_id in ('" + "','".join(WATCHED) + "'))\\\"\")\" = 1")
UNTIL = 'SCHEDULED_UNTIL=2026-11-22T10:30:00+00:00'  # NOW + 17h 20m
TIMER = '17h 20m left ({} 2026-11-22 02:30 PST)'


def _hydrated_row(api, board, monkeypatch, tid, replica, **kw):
    """The card as the panel reads it: real CoreReaders detail (live or replica adapter) -> a session row."""
    path, conn = board
    detail = readers_for(api, path, monkeypatch)._detail(conn, tid, 'default', replica)
    detail['owner'] = 'remote' if replica else None
    row = task(tid, status=detail['task']['status'], wait=detail['task'].get('wait') or {}, gateway='remote' if replica else 'hub', **kw)
    b = bundle(row, cards={tid: {'board': 'default', 'detail': detail}})
    b.update(gateway='hub', profile='default')
    return detail, b


def _only_row(b):
    return next(r for rows in model.reduce_panel(b, now=NOW)['sections'].values() for r in rows)


@pytest.mark.parametrize('replica', [False, True])
def test_screenshot_row_event_watch_with_clock_backstop(api, board, monkeypatch, replica):
    """Reported shape: awaiting clock + a wake_check watching four owner cards."""
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    tid = kanban_db.create_task(conn, title='Shepherd no-pin checks', initial_status='running')
    assert kanban_db.schedule_task(conn, tid, reason=f'{UNTIL} awaiting natural hub update then live measurements',
                                   awaiting={'kind': 'clock', 'why': WHY}, wake_check=CHECK, check_every=900)
    monkeypatch.setattr('time.time', lambda: NOW)
    detail, b = _hydrated_row(api, board, monkeypatch, tid, replica)
    assert detail['task']['wait']['prerequisite'] == WHY and detail['task']['wait']['watching'] == WATCHED
    row = _only_row(b)
    where = ' on remote' if replica else ' on hub'
    assert row['detail'].startswith('waiting on Event watch on rollout')
    assert row['detail'].endswith(' · watching 4 cards · 5m · ' + TIMER.format('backstop') + where)
    assert row['wait_reason'] == WHY and row['wait_kind'] == 'clock'
    assert row['wait_next_actor'] == 'cards ' + ', '.join(WATCHED) + ' (event watch)'
    assert f'Waiting reason: {WHY}' in row['details']
    assert not any('Next actor: the clock' in d for d in row['details'])
    assert row['gateway'] == ('remote' if replica else 'hub')


@pytest.mark.parametrize('replica', [False, True])
def test_screenshot_row_clock_park_behind_an_open_parent(api, board, monkeypatch, replica):
    """Reported shape: a clock park on a card that also has an unfinished parent."""
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    parent = kanban_db.create_task(conn, title='Mini rollout owner', initial_status='running')
    tid = kanban_db.create_task(conn, title='No-pin supervision', parents=[parent], initial_status='running')
    assert kanban_db.schedule_task(conn, tid, reason=f'{UNTIL} normal mini rollout', clock_only=True,
                                   awaiting={'kind': 'clock', 'why': 'normal mini rollout, then natural runtime proof'})
    monkeypatch.setattr('time.time', lambda: NOW)
    detail, b = _hydrated_row(api, board, monkeypatch, tid, replica)
    row = _only_row(b)
    assert row['detail'].startswith('waiting on normal mini rollout, then natur')
    assert TIMER.format('until') in row['detail']  # the canonical clock is not replaced by the parent
    assert row['wait_reason'] == 'normal mini rollout, then natural runtime proof'
    assert row['wait_next_actor'] == 'the clock (timed check)'


@pytest.mark.parametrize('replica', [False, True])
def test_recovery_clears_the_reason_through_the_real_reader(api, board, monkeypatch, replica):
    from hermes_cli import kanban_continuation
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    tid = kanban_db.create_task(conn, title='Parked then woken', initial_status='running')
    assert kanban_db.schedule_task(conn, tid, reason=f'{UNTIL} nightly rollout window', clock_only=True,
                                   awaiting={'kind': 'clock', 'why': WHY})
    monkeypatch.setattr('time.time', lambda: NOW)
    assert _only_row(_hydrated_row(api, board, monkeypatch, tid, replica)[1])['wait_reason'] == WHY
    assert kanban_continuation.wake_due_continuations(conn, now=NOW + 10 * 86400) == [tid]
    detail, b = _hydrated_row(api, board, monkeypatch, tid, replica)
    assert detail['task']['status'] != 'scheduled' and detail['task']['wait'] == {}
    row = _only_row(b)
    assert 'wait_reason' not in row and 'wait_next_actor' not in row
    assert 'Event watch' not in str(row['detail']) and not any('Event watch' in d for d in row['details'])


@pytest.mark.parametrize('replica', [False, True])
@pytest.mark.parametrize('kind', ['ci', 'deploy', 'card'])
def test_named_producer_parks_keep_their_line_and_carry_the_reason(api, board, monkeypatch, kind, replica):
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    producer = kanban_db.create_task(conn, title='Named producer', initial_status='running')
    tid = kanban_db.create_task(conn, title='Waits on a producer', initial_status='running')
    ref = producer if kind == 'card' else 'https://example.com/runs/123'
    assert kanban_db.schedule_task(conn, tid, reason=f'{UNTIL} producer', awaiting={'kind': kind, 'ref': ref, 'why': 'exact-head result lands'},
                                   wake_check='false' if kind != 'card' else None)
    monkeypatch.setattr('time.time', lambda: NOW)
    detail, b = _hydrated_row(api, board, monkeypatch, tid, replica)
    assert detail['task']['wait']['prerequisite'] == 'exact-head result lands'
    row = _only_row(b)
    assert row['wait_reason'] == 'exact-head result lands'
    assert 'Waiting reason: exact-head result lands' in row['details']
    assert row['wait_next_actor'] == f'{kind} {ref}'
    if kind != 'card':
        assert row['wait_kind'] == kind and row['detail'].startswith('waiting on CI' if kind == 'ci' else 'waiting on')
        assert 'reason unavailable' not in row['detail']


def test_same_card_id_on_two_owners_keeps_each_owners_reason(api, board, monkeypatch):
    """Replica owner scoping: the reason and next actor come from each row's own wait, so two gateways' rows
    with one id never share them. (The older 'Blocking reason' Details line reads the id-keyed card and is not
    covered here.)"""
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    tid = kanban_db.create_task(conn, title='Owner-qualified', initial_status='running')
    assert kanban_db.schedule_task(conn, tid, reason=f'{UNTIL} nightly rollout window', clock_only=True,
                                   awaiting={'kind': 'clock', 'why': 'hub reason'})
    monkeypatch.setattr('time.time', lambda: NOW)
    detail, b = _hydrated_row(api, board, monkeypatch, tid, True)
    other = task(tid, status='scheduled', gateway='mini', title='Other owner',
                 wait={'wait_state': 'waiting', 'waiting_on': 'the clock until 2026-11-22 10:30 UTC',
                       'waiting_since': NOW - 300})
    b['tasks'].append(other)
    rows = {r['gateway']: r for r in model.reduce_panel(b, now=NOW)['sections']['waiting']}
    assert rows['remote']['wait_reason'] == 'hub reason'
    assert 'wait_reason' not in rows['mini'] and 'reason unavailable' in rows['mini']['detail']
    assert 'Waiting reason: unavailable (the park recorded none)' in rows['mini']['details']
    assert 'Waiting reason: hub reason' not in rows['mini']['details']


@pytest.mark.parametrize('field,value', [('gateway', 'another-owner'), ('board', 'another-board'), ('profile', 'another-profile')])
def test_hydrated_wait_rejects_conflicting_owner(api, board, monkeypatch, field, value):
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    tid = kanban_db.create_task(conn, title='Owner-qualified', initial_status='running')
    assert kanban_db.schedule_task(conn, tid, reason=f'{UNTIL} private prerequisite', clock_only=True,
                                   awaiting={'kind': 'clock', 'why': 'private prerequisite'})
    monkeypatch.setattr('time.time', lambda: NOW)
    detail, b = _hydrated_row(api, board, monkeypatch, tid, True)
    # A second owner's list row does not publish a wait. The id-keyed hydration
    # belongs to the first owner, not to this row.
    b['tasks'][0].pop('wait', None)
    b['tasks'][0][field] = value
    row = _only_row(b)
    assert 'private prerequisite' not in str(row)
    assert 'wait_reason' not in row


def test_hydrated_wait_accepts_serving_gateway_alias(api, board, monkeypatch):
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    tid = kanban_db.create_task(conn, title='Alias-qualified', initial_status='running')
    assert kanban_db.schedule_task(conn, tid, reason=f'{UNTIL} shared prerequisite', clock_only=True,
                                   awaiting={'kind': 'clock', 'why': 'shared prerequisite'})
    monkeypatch.setattr('time.time', lambda: NOW)
    detail, b = _hydrated_row(api, board, monkeypatch, tid, False)
    b['gateway_aliases'] = ['install:example']
    b['tasks'][0].pop('wait', None)
    b['tasks'][0]['gateway'] = 'install:example'
    assert _only_row(b)['wait_reason'] == 'shared prerequisite'


@pytest.mark.parametrize('replica', [False, True])
def test_real_retained_agent_transition_names_decision_owner(api, board, monkeypatch, replica):
    from hermes_cli import kanban_retain
    from gateway.session_context import scoped_current_session_id
    support = pytest.importorskip('tests.hermes_cli.test_kanban_containment_actor')
    path, conn = board
    monkeypatch.setattr('time.time', lambda: NOW - 300)
    tid, _, session = support.bind(conn, monkeypatch)
    with scoped_current_session_id(session):
        result = kanban_retain.retain(conn, board='default', reason='scope_stopped',
                                     summary='Owner instructed a bounded scope stop',
                                     next_step='Owning chat checks the remaining scope',
                                     release_condition='Owning chat records its decision',
                                     evidence=['disposable fixture owner instruction'])
    assert result['terminal_for_run'] and not result['dependency_satisfied']
    monkeypatch.setattr('time.time', lambda: NOW)
    detail, b = _hydrated_row(api, board, monkeypatch, tid, replica)
    assert detail['task']['status'] == 'triage'
    panel = model.reduce_panel(b, now=NOW)
    assert not panel['sections']['needs_you']
    row = _only_row(b)
    assert 'decide' in row['detail'] or 'decision' in row['detail']
    assert 'Owning chat checks the remaining scope' in str(row['details'])
