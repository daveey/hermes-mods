"""Pure effective-holder selection shared with read-only urgency observers."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('effective_turn_guard', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def ticket(n, files, acquired=None):
    rec = {'pr': n, 'files': files, 'expires': 10000, 'head': 'a' * 40}
    if acquired is not None:
        rec['acquired'] = acquired
    return (f'refs/land-turn/pr-{n}', rec)


def selected(turns, eligible=lambda ref, rec: True, check=lambda: None):
    return [rec['pr'] for _, rec in g.effective_turns(turns, eligible, check)]


def test_order_and_nontransitive_waiters_do_not_reserve_area():
    turns = [ticket(3, ['y'], 3), ticket(2, ['x', 'y'], 2), ticket(1, ['x'], 1)]
    seen = []
    assert selected(turns, lambda ref, rec: seen.append(rec['pr']) or True) == [1, 3]
    assert seen == [1, 3]


def test_yielded_or_closed_holder_does_not_block_waiter():
    assert selected([ticket(1, ['x'], 1), ticket(2, ['x'], 2)],
                    lambda ref, rec: rec['pr'] != 1) == [2]


def test_legacy_acquisition_and_equal_time_pr_order():
    acquired = 10000 - g._TURN_TTL
    assert selected([ticket(3, ['x'], acquired), ticket(2, ['x'])]) == [2]


def test_owner_metadata_never_filters_blockers():
    first = ticket(1, ['x'], 1)
    second = ticket(2, ['x'], 2)
    second[1].update(owner_task='t_target', owner_board='default')
    assert selected([second, first]) == [1]
    first[1].update(owner_task='t_foreign', owner_board='other')
    assert selected([second, first]) == [1]


def test_eligibility_exception_never_promotes_waiter():
    def unavailable(ref, rec):
        raise RuntimeError('missing evidence')
    with pytest.raises(RuntimeError, match='missing evidence'):
        selected([ticket(1, ['x'], 1), ticket(2, ['x'], 2)], unavailable)


def test_deadline_checked_after_eligibility_before_yield():
    calls = []
    def check():
        calls.append('check')
        if len(calls) == 3:
            raise TimeoutError('late result')
    def eligible(ref, rec):
        calls.append('read')
        return True
    with pytest.raises(TimeoutError, match='late result'):
        selected([ticket(1, ['x'], 1)], eligible, check)
    assert calls == ['check', 'read', 'check']


def test_selector_is_lazy_after_first_holder():
    seen = []
    gen = g.effective_turns([ticket(2, ['y'], 2), ticket(1, ['x'], 1)],
                            lambda ref, rec: seen.append(rec['pr']) or True, lambda: None)
    assert next(gen)[1]['pr'] == 1
    assert seen == [1]
    gen.close()


def test_workflow_holder_blocks_ordinary_ticket_in_reverse_direction():
    seen = []
    turns = [ticket(1, ['.github/workflows/ci.yml'], 1),
             ticket(2, ['home/bin/example.py'], 2)]
    assert selected(turns, lambda ref, rec: seen.append(rec['pr']) or True) == [1]
    assert seen == [1]


@pytest.fixture
def hold_snapshot(monkeypatch):
    """Keep selection/deadlines real; replace only snapshot and holder IO."""
    monkeypatch.setattr(g, '_prefetch_holders', lambda *args: None)
    monkeypatch.setattr(g, '_turn_yielded', lambda *args: False)
    monkeypatch.setattr(g, 'time', type('Clock', (), {'monotonic': staticmethod(lambda: 100.0)})())
    return lambda turns: monkeypatch.setattr(g, '_live_turns', lambda *args: turns)


def test_merge_caller_stops_at_own_ticket_before_younger_connected_reads(hold_snapshot, monkeypatch):
    # The x/y waiter connects y to us, but cannot reserve y: an eager caller
    # would unnecessarily read the third holder after already admitting us.
    hold_snapshot([ticket(1, ['x'], 1), ticket(2, ['x', 'y'], 2), ticket(3, ['y'], 3)])
    reads = []
    deletes = []
    monkeypatch.setattr(g, '_turn_holder_data', lambda n, *args: reads.append(n) or {'state': 'CLOSED'})
    monkeypatch.setattr(g, '_turn_delete', lambda ref, *args: deletes.append(ref))
    assert g._turn_hold_refusal(1, {'state': 'OPEN', 'files': [{'path': 'x'}]}, 'example/repo', None, {}) is None
    assert reads == []
    assert deletes == []


@pytest.mark.parametrize('remaining', [0.25, 0.5, 0.501])
def test_merge_selector_preserves_canonical_deadline_reserve(hold_snapshot, monkeypatch, remaining):
    hold_snapshot([ticket(1, ['x'], 1)])
    yielded = []
    monkeypatch.setattr(g, '_turn_yielded', lambda *args: yielded.append(True) or False)
    token = g._PREFLIGHT_DEADLINE.set(100.0 + remaining)
    try:
        args = (1, {'state': 'OPEN', 'files': [{'path': 'x'}]}, 'example/repo', None, {})
        if remaining <= 0.5:
            with pytest.raises(g._Unreadable, match='deadline exceeded'):
                g._turn_hold_refusal(*args)
            assert yielded == []
        else:
            assert g._turn_hold_refusal(*args) is None
            assert yielded == [True]
    finally:
        g._PREFLIGHT_DEADLINE.reset(token)


@pytest.mark.parametrize('state', ['OPEN', 'CLOSED'])
def test_late_holder_read_refuses_before_eligibility_side_effects(hold_snapshot, monkeypatch, state):
    hold_snapshot([ticket(1, ['x'], 1), ticket(2, ['x'], 2)])
    now = [100.0]
    monkeypatch.setattr(g.time, 'monotonic', lambda: now[0])
    effects = []
    def read(*args):
        now[0] = 131.0
        return {'state': state}
    monkeypatch.setattr(g, '_turn_holder_data', read)
    monkeypatch.setattr(g, '_turn_yielded', lambda *args: effects.append('yielded') or False)
    monkeypatch.setattr(g, '_turn_delete', lambda *args: effects.append('delete'))
    with pytest.raises(g._Unreadable, match='deadline exceeded'):
        g._turn_hold_refusal(2, {'state': 'OPEN', 'files': [{'path': 'x'}]}, 'example/repo', None, {})
    assert effects == []


@pytest.mark.parametrize('source', ['runtime', 'command'])
@pytest.mark.parametrize('field,value,accepted', [
    ('HERMES_KANBAN_BOARD', 'bad board!', False),
    ('HERMES_KANBAN_BOARD', 'b' * 128, True),
    ('HERMES_KANBAN_BOARD', 'b' * 129, False),
    ('HERMES_KANBAN_TASK', 't' * 128, True),
    ('HERMES_KANBAN_TASK', 't' * 129, False),
])
def test_owner_identifiers_validate_charset_and_length(monkeypatch, source, field, value, accepted):
    for key in ('HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK', 'HERMES_KANBAN_BOARD'):
        monkeypatch.delenv(key, raising=False)
    env = {'HERMES_KANBAN_TASK': 't_01aa', 'HERMES_KANBAN_BOARD': 'default', field: value}
    expected = {'owner_task': env['HERMES_KANBAN_TASK'], 'owner_board': env['HERMES_KANBAN_BOARD']}
    if not accepted:
        if field == 'HERMES_KANBAN_TASK':
            expected = {}
        else:
            expected.pop('owner_board')
    if source == 'runtime':
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        env = {}
    assert g._turn_owner(env) == expected
