"""Canonical external capture is shared, strict and scoped before reduction."""
import sys
from types import ModuleType, SimpleNamespace

import pytest
from test_source_coverage import api_module


def test_external_capture_partitions_canonical_rows_and_preserves_evidence(monkeypatch):
    api = api_module()
    calls = []
    rows = [{'id': 'one', 'session': 'old-a', 'status': 'running', 'synced_at': 12},
            {'id': 'two', 'origin_session': 'B', 'released': True},
            {'id': 'parent', 'session': 'other', 'status': 'ready'}]
    def read(*, stale_ok):
        calls.append(stale_ok)
        return rows[:1], rows[1:]
    package = ModuleType('hermes_cli')
    package.kanban_session_activity = SimpleNamespace(remote_links_and_origin_rows=read)
    package.kanban_db_sync = SimpleNamespace(sync_config=lambda: {})
    package.kanban_chat_links = SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'hermes_cli', package)
    r = object.__new__(api.CoreReaders)
    held = {'A': {'lineage': {'A', 'old-a'}, 'refs': {'parent'}},
            'B': {'lineage': {'B'}, 'refs': set()}}
    first = r.external_capture(held)
    assert calls == [False]
    assert len(first['A']) == 2 and len(first['B']) == 1
    rows.append({'id': 'unrelated', 'session': 'elsewhere'})
    assert r.external_capture(held) == first
    rows[0]['synced_at'] = 13
    second = r.external_capture(held)
    assert second['A'] != first['A'] and second['B'] == first['B']


def test_external_capture_held_reference_is_owner_qualified(monkeypatch):
    api = api_module()
    owner = dict(id='parent', gateway='owner-a', profile='default', board='work')
    rows = [dict(owner, session='elsewhere', title='wanted'),
            dict(owner, gateway='owner-b', session='elsewhere', title='not ours'),
            dict(owner, profile='other', session='elsewhere'),
            dict(owner, board='other', session='elsewhere')]
    package = ModuleType('hermes_cli')
    package.kanban_session_activity = SimpleNamespace(
        remote_links_and_origin_rows=lambda **kw: (rows, []))
    package.kanban_db_sync = SimpleNamespace(sync_config=lambda: {})
    package.kanban_chat_links = SimpleNamespace()
    monkeypatch.setitem(sys.modules, 'hermes_cli', package)
    r = object.__new__(api.CoreReaders)
    held = {'A': {'lineage': {'A'}, 'refs': {'parent'},
                  'bundle': {'tasks': [owner], 'done': [], 'cards': {}}}}
    first = r.external_capture(held)
    assert len(first['A']) == 1
    rows[1]['title'] = 'unrelated update'
    assert r.external_capture(held) == first
    rows[0]['title'] = 'changed parent'
    assert r.external_capture(held) != first
    # Hydrated parents carry scope on the record, not always on detail.task.
    held['A']['bundle'] = {'tasks': [], 'done': [], 'cards': {
        'parent': {**owner, 'detail': {'task': {'id': 'parent'}}}}}
    assert len(r.external_capture(held)['A']) == 1
    # Old snapshots lacking coordinates stay conservative, not silently empty.
    held['A']['bundle']['cards']['parent'] = {'detail': {'task': {'id': 'parent'}}}
    assert len(r.external_capture(held)['A']) == 4


def test_feed_passes_hydrated_scope_to_external_capture():
    from test_feed import make
    f, r, _, _ = make()
    r.cards['t_bbbbbb2'].update(gateway='owner-a', profile='default')
    f.watch(['S1'])
    f.tick()
    captures = []
    def capture(held):
        captures.append(held)
        return {s: () for s in held}
    r.external_capture = capture
    r.external_stamps = lambda: r.stamp
    r.stamp += 1
    f.tick()
    refs = api_module().CoreReaders._external_refs(captures[0]['S1'])
    assert refs['t_bbbbbb2'] == [dict(gateway='owner-a', profile='default', board='default')]


def test_failed_external_capture_does_not_consume_any_cursor():
    from test_feed import make
    f, r, _, t = make()
    r.external_stamps = lambda: r.stamp
    r.external_capture = lambda held: {s: () for s in held}
    f.watch(['S1'])
    f.tick()
    old = (f._external_stamps, f._external_captures, f._stamps, f._cursor)
    r.stamp += 1
    def fail(held):
        raise RuntimeError('capture unavailable')
    r.external_capture = fail
    with pytest.raises(RuntimeError):
        f.tick()
    assert (f._external_stamps, f._external_captures, f._stamps, f._cursor) == old
    assert 'S1' in f._retry
    assert f._admission_retry_at > t['now']
