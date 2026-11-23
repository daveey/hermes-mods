"""Native hints are admission, not a replacement publisher or action writer."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from test_feed import make


def load():
    spec = importlib.util.spec_from_file_location('panel_keyed_test', Path(__file__).parents[1] / 'dashboard/keyed_hints.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_keyed_refresh_changes_only_named_session_and_preserves_recovery():
    f, r, pushes, _ = make()
    f.changes(['S1', 'S2'])
    before = f.rev
    r.reads.clear()
    r.links['S1'][0]['title'] = 'changed title'
    f.queue_keyed({'S1'})
    f.refresh_keyed()
    assert 't_dddddd4' not in r.reads
    assert set(pushes[-1]['bundles']) == {'S1'}
    assert pushes[-1]['prev'] == before
    reset = f.changes(['S1', 'S2'], epoch='old-epoch')
    assert reset['reset'] and set(reset['bundles']) == {'S1', 'S2'}
    assert reset['bundles']['S1']['tasks'][0]['title'] == 'changed title'


def test_empty_hint_never_wakes_feed():
    f, _, _, _ = make()
    f._wake.clear()
    f.queue_keyed({'not-watched'})
    assert not f._wake.is_set()


def test_native_hints_keyed_read_and_fixed_reconcile_clock(tmp_path, monkeypatch):
    native = pytest.importorskip('tui_gateway.notification_loaded_f')
    remote = pytest.importorskip('tui_gateway.kanban_remote_notices')
    pytest.importorskip('tui_gateway.kanban_remote_notices_keyed')
    path = tmp_path / 'hub.db'
    with sqlite3.connect(path) as c:
        c.executescript('CREATE TABLE meta(k TEXT,v TEXT); CREATE TABLE replica(coll TEXT,gw TEXT,key TEXT,seq INTEGER,data TEXT); CREATE INDEX replica_poll_metadata ON replica(coll,seq,gw,key);')
        c.executemany('INSERT INTO meta VALUES (?,?)', [('gateway','local'),('cursor','2'),('head','2')])
        c.execute('INSERT INTO replica VALUES (?,?,?,?,?)', ('tasks','remote','default/default/t_aaaaaa1',1,json.dumps({'id':'t_aaaaaa1'})))
    monkeypatch.setattr(remote, 'hub_db_path', lambda: path)
    monkeypatch.setattr(remote, '_linked_chats', lambda task, me: [('default', 'S1')])
    now = [0.0]
    hints = load().KeyedHints(clock=lambda: now[0])
    held = {'S1': {'lineage': {'S1'}, 'refs': {'t_aaaaaa1'}}, 'S2': {'lineage': {'S2'}, 'refs': set()}}
    assert hints.sessions(held, 'default') == {'S1'}
    assert hints.sessions(held, 'default') == set()
    now[0] = 29
    assert not hints.due()
    now[0] = 30
    assert hints.due()
    assert not hints.due()
    f, r, _, _ = make()
    f.changes(['S1', 'S2'])
    r.links['S2'][0]['title'] = 'missed wake'
    f.queue_keyed(set(f.interest))
    f.refresh_keyed()
    assert f.bundles['S2']['bundle']['tasks'][0]['title'] == 'missed wake'
    with sqlite3.connect(path) as c:
        c.execute('DROP INDEX replica_poll_metadata')
    with pytest.raises(RuntimeError, match='index unavailable'):
        hints.sessions(held, 'default')


def test_keyed_hidden_session_retains_visible_only_debt():
    from test_feed import feedmod
    f, r, _, _ = make()
    f.changes(['S1', 'S2'])
    lane = feedmod.BoardLane(f, lambda: r)
    r.reads.clear()
    f.queue_keyed({'S2'})
    f.refresh_keyed()
    assert r.reads == []
    assert 'S2' in f._board_dirty
    assert not lane.pending
    lane.close()


def test_keyed_failure_preserves_retry_and_last_good(monkeypatch):
    f, r, _, _ = make()
    f.changes(['S1'])
    before = f.bundles['S1']['bundle']
    monkeypatch.setattr(f, 'compute', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('source unavailable')))
    f.queue_keyed({'S1'})
    f.refresh_keyed()
    assert f.bundles['S1']['bundle'] == before
    assert 'S1' in f._retry
    assert f._hydrate_retry_at is not None


def test_keyed_deferred_canonical_writer_retains_obligation():
    f, r, _, _ = make()
    f.changes(['S1'])
    r.reconcile_scoped = True
    r.reconcile_sources = lambda sessions: set(sessions)
    f.queue_keyed({'S1'})
    f.refresh_keyed()
    assert 'S1' in f._reconcile_deferred
    assert f._reconcile_retry_at is not None


def test_off_retains_legacy_watcher_wakes(monkeypatch, tmp_path):
    from test_routes import load as load_api
    api = load_api('_panel_keyed_off', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
    monkeypatch.setenv('HERMES_FACTS_PANEL', '0')
    f, _, _, _ = make()
    class Watch:
        stale = False
        calls = 0
        changed_paths = {tmp_path / 'hub.db-wal'}
        def set_paths(self, *args, **kwargs):
            pass
        def wait(self, timeout):
            self.calls += 1
            if self.calls == 2:
                f.stopped.set()
            return True
        def close(self):
            pass
    api._watch(f, Watch(), lambda: [tmp_path / 'hub.db'])
    assert f._wake.is_set() and f._board.is_set() and f._decide.is_set()
    assert not getattr(f, '_keyed_pending', set())
