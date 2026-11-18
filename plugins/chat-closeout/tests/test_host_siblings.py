"""Pre-read row invalidation under active siblings; real covered board and transcript readers."""
import sqlite3
import pytest
from test_closeout import plugin
from test_host_idle_skip import clock, spy, unlinking, world, revision_board, tick, entry, T0


@pytest.mark.parametrize('change', ['card', 'transcript', 'row', 'unseen'])
@pytest.mark.parametrize('clock_row', [False, True])
def test_sibling_change_reads_only_its_own_batch(world, spy, clock, monkeypatch, change, clock_row, request):
    if clock_row:
        request.getfixturevalue('unlinking')
    revision_board(world)
    unseen = {}
    monkeypatch.setattr(plugin, '_unseen_counts', lambda: dict(unseen))
    typing = lambda: clock.wall - 10
    for _ in range(4):
        tick(spy, typing)
        clock.wall += 30
    batches, sources = [], []
    real_read, real_source = plugin.read_cards, plugin._session_source
    def read(ids, *a, **kw):
        batches.extend(ids)
        return real_read(ids, *a, **kw)
    def source(sid):
        sources.append(sid)
        return real_source(sid)
    monkeypatch.setattr(plugin, 'read_cards', read)
    monkeypatch.setattr(plugin, '_session_source', source)
    if change == 'card':
        with sqlite3.connect(world['board']) as conn:
            conn.execute("UPDATE tasks SET title='sibling changed' WHERE id='t_2'")
    elif change == 'transcript':
        with sqlite3.connect(world['state']) as conn:
            conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                         "VALUES ('c2', 'user', 'hello', 200, 1)")
    elif change == 'row':
        world['led'].write('c2', retry_after=1.0)
    else:
        unseen['c2'] = 1
    spy['reset']()
    tick(spy, typing)
    assert set(batches) == {'t_2'}
    assert sources == ['c2']
    assert world['work']['reconcile_done'] == ['c2']
    if clock_row:
        assert [w for w in spy['writes'] if w[0] == 'c4'] == [('c4', ['unlinked'])]
        assert entry(world)['observed'] == 120.0
    else:
        assert spy['writes'] == []


def test_only_expired_sibling_is_read(world, spy, clock, monkeypatch):
    revision_board(world)
    base = T0 - T0 % plugin.wi.FLOOR_S
    clock.wall = base
    typed = {'c1': base - 100, 'c2': base - 10, 'c3': base - 10}
    for _ in range(3):
        plugin.wake_tick(wake=lambda *a, **k: 'busy', last_user=lambda ids: typed[ids[0]])
        clock.wall += 30
    batches = []
    real_read = plugin.read_cards
    def read(ids, *a, **kw):
        batches.extend(ids)
        return real_read(ids, *a, **kw)
    monkeypatch.setattr(plugin, 'read_cards', read)
    clock.wall = base + 210
    spy['reset']()
    plugin.wake_tick(wake=lambda *a, **k: 'busy', last_user=lambda ids: typed[ids[0]])
    assert set(batches) == {'t_1'}
    assert world['work']['reconcile_done'] == ['c1']


def test_clock_health_moves_when_last_fresh_unrelated_inventory_row_disappears(unlinking, spy, clock, monkeypatch):
    world = unlinking
    revision_board(world)
    inventory = [{'id': 'unrelated', 'stale': False}]
    monkeypatch.setattr(plugin, 'remote_cards', lambda: list(inventory))
    # Use inventory only for global health; fixture boards remain authoritative for the batch.
    real_read = plugin.read_cards
    monkeypatch.setattr(plugin, 'read_cards', lambda ids, **kw: real_read(ids, **{**kw, 'remote_inventory': lambda: []}))
    for _ in range(4):
        tick(spy, lambda: clock.wall - 10)
        clock.wall += 30
    observed = entry(world)['observed']
    inventory[:] = [{'id': 'unrelated', 'stale': True}]
    spy['reset']()
    tick(spy, lambda: clock.wall - 10)
    assert 'c4' in world['work']['reconcile_done']
    assert entry(world)['observed'] == observed
    inventory[:] = [{'id': 'unrelated', 'stale': False}]
    clock.wall += 30
    spy['reset']()
    tick(spy, lambda: clock.wall - 10)
    assert 'c4' in world['work']['reconcile_done']


def test_residual_and_injected_passes_leave_host_memos_untouched(world, spy, clock):
    revision_board(world)
    for _ in range(3):
        tick(spy, lambda: clock.wall - 10)
        clock.wall += 30
    before = dict(plugin._wi['host_pre_rows'])
    memo = plugin._state['host_memo']
    plugin.wake_tick(only={'c3'}, wake=lambda *a, **k: 'busy')
    assert plugin._wi['host_pre_rows'] == before
    assert plugin._state['host_memo'] == memo
    plugin.wake_tick(now=clock.wall, wake=lambda *a, **k: 'busy', last_user=lambda ids: clock.wall - 10)
    assert plugin._wi['host_pre_rows'] == before
    assert plugin._state['host_memo'] == memo
