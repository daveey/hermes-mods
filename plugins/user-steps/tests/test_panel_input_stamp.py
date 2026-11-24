"""Exercise the real remote stamp adapter through panel memoization."""
from contextlib import closing
import gc
import sys
from pathlib import Path
from types import ModuleType
import sqlite3

import pytest

from test_kanban_mirror import OWNER, fixture


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    mirror, store, _, _ = fixture(tmp_path)
    remote_globals = mirror.remote.input_stamp.__func__.__globals__
    hub = tmp_path / 'hub.db'
    state = tmp_path / 'state.db'
    hub.touch()
    state.touch()
    revision = [('revision', 1)]
    try:
        from hermes_cli import kanban_session_activity as activity
    except ImportError:
        activity = ModuleType('hermes_cli.kanban_session_activity')
        parent = ModuleType('hermes_cli')
        parent.kanban_session_activity = activity
        monkeypatch.setitem(sys.modules, 'hermes_cli', parent)
        monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', activity)
    monkeypatch.setattr(activity, '_activity_fingerprint', lambda: revision[0], raising=False)
    monkeypatch.setattr(mirror.core, '_state_db', lambda: state)
    # Only source location and activity revision are controlled; input_stamp and
    # filesystem metadata inspection remain the real production implementation.
    view = remote_globals['load_file'](Path(remote_globals['__file__']).resolve().parents[1] / 'hub-sync' / 'hubview.py')
    monkeypatch.setattr(view, 'db_path', lambda: hub)
    monkeypatch.setitem(remote_globals, 'load_file', lambda _: view)
    # Legacy physical-stamp adapter contract remains covered independently of
    # the production semantic capture (which requires real membership stores).
    monkeypatch.setattr(mirror, '_panel_stamp', lambda _: mirror.remote.input_stamp(store.path))
    calls = []
    monkeypatch.setattr(mirror.remote, 'reconcile', lambda owner: calls.append(owner))
    mirror.reconcile(OWNER)  # settle initial local ActionStore projection first
    calls.clear()
    # Keep SQLite's WAL identity stable for the no-source-change assertions.
    # ActionStore initialization can leave a connection awaiting cyclic GC; its
    # last-close checkpoint otherwise changes DB/WAL metadata midway through a
    # panel call. That is real invalidation, not a spurious remote invocation.
    # An idle (not in-transaction) keeper prevents only last-close housekeeping;
    # real commits, missing/stat errors and their recovery remain observable.
    with closing(sqlite3.connect(store.path)) as keeper:
        keeper.execute('SELECT count(*) FROM requests').fetchone()
        yield mirror, hub, state, revision, calls


def test_panel_real_stamp_absent_wal_is_valid_and_changes_invalidate(adapter, tmp_path, monkeypatch):
    mirror, hub, state, revision, calls = adapter
    mirror.reconcile_panel(OWNER)
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 1
    assert mirror.panel_remote_unchanged
    Path(str(hub) + '-wal').write_bytes(b'new committed source')
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 2
    assert not mirror.panel_remote_unchanged


@pytest.mark.parametrize('source', ['hub', 'state', 'store'])
def test_panel_real_stamp_missing_required_db_and_recovery(adapter, tmp_path, monkeypatch, source):
    mirror, hub, state, revision, calls = adapter
    path = {'hub': hub, 'state': state, 'store': mirror.store.path}[source]
    # Avoid removing an open ActionStore: emulate exactly its missing stat.
    original = Path.stat
    missing = [True]
    def stat(p, *args, **kwargs):
        if p == path and missing[0]:
            raise FileNotFoundError(str(p))
        return original(p, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    mirror.reconcile_panel(OWNER)
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 2
    assert not mirror.panel_remote_unchanged
    missing[0] = False
    mirror.reconcile_panel(OWNER)
    gc.collect()  # force the CI last-close/checkpoint trigger inside the fixture
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 3
    assert mirror.panel_remote_unchanged


def test_panel_real_store_commit_still_invalidates_with_keeper(adapter):
    mirror, hub, state, revision, calls = adapter
    mirror.reconcile_panel(OWNER)
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 1
    with closing(sqlite3.connect(mirror.store.path)) as writer:
        writer.execute('CREATE TABLE source_change(value INTEGER)')
        writer.commit()
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 2
    assert not mirror.panel_remote_unchanged


def test_panel_real_checkpoint_is_a_source_stamp_change(adapter, monkeypatch):
    mirror, hub, state, revision, calls = adapter
    with closing(sqlite3.connect(hub)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE source(value INTEGER)')
        conn.commit()
        before = mirror.remote.input_stamp(mirror.store.path)
        def checkpoint(owner):
            calls.append(owner)
            if len(calls) == 1:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        monkeypatch.setattr(mirror.remote, 'reconcile', checkpoint)
        mirror.reconcile_panel(OWNER)
        assert mirror.remote.input_stamp(mirror.store.path) != before
        mirror.reconcile_panel(OWNER)
        assert len(calls) == 2  # metadata changed during the prior pass: revisit
        mirror.reconcile_panel(OWNER)
        assert len(calls) == 2
        assert mirror.panel_remote_unchanged


@pytest.mark.parametrize('wal', [False, True])
def test_panel_real_stamp_stat_error_cannot_vouch_unchanged(adapter, tmp_path, monkeypatch, wal):
    mirror, hub, state, revision, calls = adapter
    target = Path(str(hub) + '-wal') if wal else hub
    target.write_bytes(b'first')
    original = Path.stat
    def stat(p, *args, **kwargs):
        if p == target:
            raise PermissionError(str(p))
        return original(p, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    mirror.reconcile_panel(OWNER)
    target.write_bytes(b'second committed source')
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 2
    assert not mirror.panel_remote_unchanged


def test_panel_real_sqlite_wal_update_survives_stat_failure(adapter, tmp_path, monkeypatch):
    mirror, hub, state, revision, calls = adapter
    with closing(sqlite3.connect(hub)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE source(value INTEGER)')
        conn.execute('INSERT INTO source VALUES (1)')
        conn.commit()
        observed = []
        monkeypatch.setattr(mirror.remote, 'reconcile',
                            lambda owner: observed.append(conn.execute('SELECT value FROM source').fetchone()[0]))
        original = Path.stat
        wal = Path(str(hub) + '-wal')
        assert wal.exists()
        def stat(p, *args, **kwargs):
            if p == wal:
                raise PermissionError(str(p))
            return original(p, *args, **kwargs)
        monkeypatch.setattr(Path, 'stat', stat)
        mirror.reconcile_panel(OWNER)
        conn.execute('UPDATE source SET value=2')
        conn.commit()
        mirror.reconcile_panel(OWNER)
        assert observed == [1, 2]
        assert not mirror.panel_remote_unchanged


@pytest.mark.parametrize('unknown', [None, (), (None,), ('revision', (None, 1))])
def test_panel_real_stamp_unknown_activity_is_not_success(adapter, tmp_path, monkeypatch, unknown):
    mirror, hub, state, revision, calls = adapter
    revision[0] = unknown
    mirror.reconcile_panel(OWNER)
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 2
    assert not mirror.panel_remote_unchanged
