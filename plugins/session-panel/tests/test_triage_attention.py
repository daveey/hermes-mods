"""Overdue visual attention is not human attention or execution permission."""
import copy
import pytest
pytest.importorskip('hermes_cli.kanban_triage_attention')
from test_model import model, task, bundle, human, canonical_outcome, NOW


def aged(age=601, *, remote=False, entry=None, **kw):
    row = task(status='triage', remote=remote, source='replica' if remote else 'live',
               block_kind='decision_pending', created_at=1, **kw)
    events = [{'kind': 'created', 'created_at': 1, 'payload': {'status': 'ready'}},
              {'kind': 'decision_retained', 'created_at': NOW - age if entry is None else entry},
              {'kind': 'decision_retained', 'created_at': NOW - 1}]
    return bundle(row, outcome_json=canonical_outcome('waiting'),
                  cards={row['id']: {'detail': {'task': dict(row), 'events': events}}})


@pytest.mark.parametrize('remote', [False, True])
@pytest.mark.parametrize('age,red', [(599, False), (600, False), (601, True), (6 * 86400, True)])
def test_triage_age_changes_attention_not_actor_or_authority(age, red, remote):
    b = aged(age, remote=remote)
    before = copy.deepcopy(b)
    result = model.reduce_panel(b, now=NOW, wire=True)
    row = result['sections']['stuck' if red else 'waiting'][0]
    assert (row.get('attention') == 'triage_overdue') is red
    assert 'agent' in row['detail']
    assert not result['sections']['needs_you']
    assert result['header']['status'] == b['outcome_json']['status']
    assert (result['header'].get('attention') == 'triage_overdue') is red
    assert b == before
    assert not any(a['kind'] == 'decide' for a in row['actions'])


@pytest.mark.parametrize('entry', [None, 'yesterday', -1, float('nan'), float('inf'), NOW + 20])
def test_unknown_entry_never_fabricates_age(entry):
    b = aged()
    b['cards']['t_abc123']['detail']['events'][1]['created_at'] = entry
    result = model.reduce_panel(b, now=NOW)
    assert not result['sections']['stuck']
    assert 'entry time unavailable' in result['sections']['waiting'][0]['detail']


def test_live_human_ask_is_red_before_threshold():
    b = aged(1, human_request=human())
    b['tasks'][0]['block_kind'] = 'needs_input'
    b['cards']['t_abc123']['detail']['task']['block_kind'] = 'needs_input'
    result = model.reduce_panel(b, now=NOW)
    assert len(result['sections']['needs_you']) == 1
    assert not result['sections']['stuck']


def test_wire_revision_only_changes_at_semantic_boundary_and_clears_on_exit():
    b = aged(599)
    first = model.reduce_panel(b, now=NOW, wire=True)
    boundary = model.reduce_panel(b, now=NOW + 1, wire=True)
    red = model.reduce_panel(b, now=NOW + 2, wire=True)
    assert first['revision'] == boundary['revision']
    assert first['revision'] != red['revision']
    assert red['revision'] == model.reduce_panel(b, now=NOW + 3, wire=True)['revision']
    b['tasks'][0]['status'] = 'ready'
    b['cards']['t_abc123']['detail']['task']['status'] = 'ready'
    assert 'attention' not in model.reduce_panel(b, now=NOW + 3)['header']


def test_feed_crosses_threshold_with_unchanged_adapters_and_no_reads():
    from test_feed import make, detail
    f, readers, _, clock = make()
    row = {'id': 't_aaaaaa1', 'status': 'triage', 'board': 'default', 'session_id': 'S1', 'block_kind': 'decision_pending'}
    readers.links['S1'] = [row]
    readers.cards[row['id']] = detail(row['id'], 'triage', events=[{'kind': 'decision_retained', 'created_at': 401}])
    f.changes(['S1'])
    reads = list(readers.reads)
    revision = f.rev
    clock['now'] = 1001
    f.beat()
    assert f.rev == revision
    clock['now'] = 1002
    f.beat()
    assert f.rev > revision
    held = f.bundles['S1']['bundle']
    assert held['model']['header']['attention'] == 'triage_overdue'
    assert held['computed_at'] == 1000
    assert readers.reads == reads
    revision = f.rev
    clock['now'] = 1003
    f.beat()
    assert f.rev == revision


def _serving_feed():
    """The production bundle shape: CoreReaders supplies the serving gateway; local session_tasks rows carry none."""
    from test_feed import make, detail, Readers

    class Serving(Readers):
        def panel_inputs(self, session, lineage):
            return {'gateway': 'serving-gw', 'profile': 'default'}

    readers = Serving()
    row = {'id': 't_aaaaaa1', 'status': 'triage', 'board': 'default', 'session_id': 'S1',
           'block_kind': 'decision_pending'}
    readers.links['S1'] = [row]
    readers.cards[row['id']] = detail(row['id'], 'triage', events=[{'kind': 'decision_retained', 'created_at': 401}])
    f, _, _, clock = make()
    f.readers = readers
    return f, clock


def test_serving_gateway_local_row_crosses_once_then_returns_to_normal_cadence():
    f, clock = _serving_feed()
    f.changes(['S1'])
    held = f.bundles['S1']['bundle']
    assert held['gateway'] == 'serving-gw' and 'gateway' not in held['tasks'][0]
    assert f.idle_wait() == pytest.approx(1.0, abs=0.01)  # wakes at the 600 s crossing (entered 401)
    clock['now'] = 1002
    f.beat()
    held = f.bundles['S1']['bundle']
    assert held['model']['header']['attention'] == 'triage_overdue'
    assert held['model']['sections']['stuck'][0]['key'].startswith('serving-gw:')
    assert model.triage_deadline(held, 1002) is None
    waits = []
    for step in range(5):
        clock['now'] = 1003 + step
        f.beat()
        waits.append(f.idle_wait())
    assert waits == [f.heartbeat_s] * 5, waits


@pytest.mark.parametrize('age', [599, 601])
def test_triage_deadline_keys_local_rows_by_the_serving_gateway(age):
    b = aged(age)
    b['gateway'] = 'serving-gw'
    row = b['tasks'][0]
    for field in ('gateway', 'remote'):
        row.pop(field, None)
    row['source'] = 'live'
    b['model'] = model.reduce_panel(b, now=NOW)
    if age > 600:
        assert b['model']['header']['attention'] == 'triage_overdue'
        assert model.triage_deadline(b, NOW) is None
    else:
        assert model.triage_deadline(b, NOW) == pytest.approx(NOW + 1, abs=1e-3)


def test_feed_never_rearms_a_crossed_deadline_it_already_repainted(monkeypatch):
    """Defence in depth: even a deadline the model keeps reporting re-wakes the feed once, not every 0.2 s."""
    from test_feed import make, feedmod
    f, _, _, clock = make()
    f.changes(['S1'])
    import types
    real = feedmod._model()
    stub = types.SimpleNamespace(**{k: getattr(real, k) for k in dir(real) if not k.startswith('__')})
    stub.triage_clocks = lambda bundle: [(400.0, 1000.5)]
    monkeypatch.setattr(feedmod, '_model', lambda: stub)
    assert f.idle_wait() == pytest.approx(0.5)
    clock['now'] = 1001
    f.beat()
    assert [f.idle_wait() for _ in range(3)] == [f.heartbeat_s] * 3


def test_idle_waits_derive_triage_clocks_once_per_held_bundle(monkeypatch):
    """The loop's idle_wait/beat never re-execute the reducer for a bundle the producer has not replaced.

    Re-deriving on every loop turn (under the feed lock) slowed source publishes on loaded CI hosts
    (test_selective_producer's <1 s publish bound); a replaced bundle is still derived, so a new triage
    entry arms its wake.
    """
    from test_feed import make, feedmod
    f, _, _, clock = make()
    f.changes(['S1'])
    import types
    real = feedmod._model()
    calls = []
    stub = types.SimpleNamespace(**{k: getattr(real, k) for k in dir(real) if not k.startswith('__')})
    stub.triage_clocks = lambda bundle: calls.append(bundle) or [(400.0, 1000.5)]
    loads = []
    monkeypatch.setattr(feedmod, '_model', lambda: loads.append(1) or stub)
    for _ in range(5):
        assert f.idle_wait() == pytest.approx(0.5)
    f.beat()
    assert len(calls) == 1 and len(loads) == 1
    with f.lock:
        held = f.bundles['S1']
        held['bundle'] = {**held['bundle']}  # the producer replaces a bundle, never mutates it
    assert f.idle_wait() == pytest.approx(0.5)
    assert len(calls) == 2
    with f.lock:
        f.bundles.pop('S1')
    f.idle_wait()
    assert f._triage_memo == {}  # a dropped chat releases its held bundle


@pytest.mark.parametrize('age', [599, 601])
def test_an_off_list_shared_blocker_group_schedules_its_own_crossing(age):
    """Shared-blocker parents are not in bundle['tasks']; their group still arms the bounded next-threshold wake."""
    from test_model import task, bundle
    parent = task('t_abcdef', status='triage', title='Shared stage')
    parent.pop('triage_entered_at', None)
    events = [{'kind': 'decision_retained', 'created_at': NOW - age, 'payload': {}}]
    b = bundle(task('t_111111', status='todo', upstream=parent), task('t_222222', status='todo', upstream=parent),
               cards={parent['id']: {'detail': {'task': parent, 'events': events}}})
    b['model'] = model.reduce_panel(b, now=NOW)
    group = next(g for g in b['model']['groups'] if g.get('id') == parent['id'])
    if age > 600:
        assert group.get('attention') == 'triage_overdue'
        assert model.triage_deadline(b, NOW) is None
    else:
        assert group.get('attention') != 'triage_overdue'
        assert model.triage_deadline(b, NOW) == pytest.approx(NOW + 1, abs=1e-3)
