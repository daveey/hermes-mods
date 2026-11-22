"""Event-driven producers cannot consume display caches awaiting an unwatched async completion."""
import pytest

from test_core_readers import api, board, readers_for
from test_reader_batch import graph
from test_feed import make


@pytest.mark.parametrize('fallback', ['single', 'failed', 'exception', 'membership'])
def test_producer_fallback_reads_strict_projection(graph, monkeypatch, fallback):
    r, local, remote, ksa = graph
    stale, current = [{'id': 'old'}], [{'id': 'new'}]
    monkeypatch.setattr(ksa, 'session_tasks', lambda ids, statuses, *, stale_ok=False: stale if stale_ok else current)
    with r.batch(['S0'] if fallback == 'single' else ['S0', 'S1']):
        if fallback == 'failed':
            r._batch_scope()['local_failed'] = True
        elif fallback == 'exception':
            monkeypatch.setattr(ksa, '_read_session_tasks', lambda *a: (_ for _ in ()).throw(RuntimeError('read failed')))
        elif fallback == 'membership':
            monkeypatch.setattr(ksa, '_read_session_tasks', lambda *a: [])
        assert r._session_tasks(['S0'], ('ready',)) == current


@pytest.mark.parametrize('replica', [False, True])
def test_batched_remote_inventory_never_uses_display_cache(graph, monkeypatch, replica):
    from hermes_cli import kanban_db_sync as sync, kanban_chat_links as links
    r, local, remote, ksa = graph
    stale, current = [{'id': 'old'}], []
    monkeypatch.setattr(sync, 'sync_config', lambda: {'links_reader': 'replica' if replica else 'legacy'})
    seen = []
    def origin(*, stale_ok=False):
        seen.append(('origin', stale_ok))
        return stale if stale_ok else current
    def cards(ids, statuses, stale_ok=False):
        seen.append(('cards', stale_ok))
        return stale if stale_ok else current
    def selected(ids, gateway, statuses, *, stale_ok=False, origin_cards=None):
        seen.append(('selected', stale_ok))
        return []
    monkeypatch.setattr(ksa, '_remote_links', origin)
    monkeypatch.setattr(ksa, '_remote_cards', cards)
    monkeypatch.setattr(links, 'replica_cards', selected)
    scope = {'ids': ['S0', 'S1'], 'remote': {}}
    # Read through the producer entry; final core obligation policy remains exercised.
    r._remote_inventory(scope, ['S0'], ('ready',))
    assert seen and all(not stale_ok for _, stale_ok in seen), seen


def test_remote_inventory_error_fallback_is_strict(graph, monkeypatch):
    r, local, remote, ksa = graph
    monkeypatch.setattr(ksa, '_remote_links', lambda **k: (_ for _ in ()).throw(RuntimeError('projection error')))
    monkeypatch.setattr(ksa, '_remote_cards', lambda ids, statuses, stale_ok=False: ['old'] if stale_ok else ['new'])
    assert r._remote_inventory({'ids': ['S0', 'S1'], 'remote': {}}, ['S0'], ('ready',)) == ['new']


def test_terminal_history_producer_is_strict(graph, monkeypatch):
    r, local, remote, ksa = graph
    seen = []
    def history(*, stale_ok=False):
        seen.append(stale_ok)
        return [], []
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', history)
    monkeypatch.setattr(r, '_session_tasks', lambda *args: [])
    assert r.done(['S0']) == []
    assert seen == [False]


def test_watch_and_invalidation_inventory_includes_remote_sources(api, board, tmp_path, monkeypatch):
    from hermes_cli import kanban_db
    from hermes_constants import get_hermes_home
    path, _ = board
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub'))
    monkeypatch.setattr(kanban_db, 'kanban_home', lambda: tmp_path / 'boards-home')
    r = readers_for(api, path, monkeypatch)
    expected = {tmp_path / 'hub' / 'hub.db', get_hermes_home() / 'kanban' / 'origin-ledger.db',
                tmp_path / 'boards-home' / 'origin-work.db'}
    assert expected <= set(r.watch_paths()), 'ledger and legacy snapshot are different stores'
    before = r.stamps()
    for source in sorted(expected):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'source changed')
        after = r.stamps()
        assert after != before, source
        before = after
    r.release()


def test_external_only_write_invalidates_held_bundle_without_board_event():
    f, r, emitted, clock = make()
    r.external_stamps = lambda: (r.external,)
    r.external = 0
    raw_stamps = r.stamps
    r.stamps = lambda: (raw_stamps(), r.external)
    f.changes(['S1'])
    f.tick()
    r.reads.clear()
    r.external += 1
    f.tick()
    assert r.reads, 'remote source wake must not be swallowed by unchanged board/task cursors'
