"""Exercise the real remote stamp adapter through panel memoization."""
from contextlib import closing
import sys
from pathlib import Path
from types import ModuleType
import sqlite3

import pytest

from test_kanban_mirror import OWNER, fixture


def setup_adapter(tmp_path, monkeypatch):
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
    calls = []
    monkeypatch.setattr(mirror.remote, 'reconcile', lambda owner: calls.append(owner))
    mirror.reconcile(OWNER)  # settle initial local ActionStore projection first
    calls.clear()
    return mirror, hub, state, revision, calls


def test_panel_real_stamp_absent_wal_is_valid_and_changes_invalidate(tmp_path, monkeypatch):
    mirror, hub, state, revision, calls = setup_adapter(tmp_path, monkeypatch)
    mirror.reconcile_panel(OWNER)
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 1
    assert mirror.panel_remote_unchanged
    Path(str(hub) + '-wal').write_bytes(b'new committed source')
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 2
    assert not mirror.panel_remote_unchanged


@pytest.mark.parametrize('source', ['hub', 'state', 'store'])
def test_panel_real_stamp_missing_required_db_and_recovery(tmp_path, monkeypatch, source):
    mirror, hub, state, revision, calls = setup_adapter(tmp_path, monkeypatch)
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
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 3
    assert mirror.panel_remote_unchanged


@pytest.mark.parametrize('wal', [False, True])
def test_panel_real_stamp_stat_error_cannot_vouch_unchanged(tmp_path, monkeypatch, wal):
    mirror, hub, state, revision, calls = setup_adapter(tmp_path, monkeypatch)
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


def test_panel_real_sqlite_wal_update_survives_stat_failure(tmp_path, monkeypatch):
    mirror, hub, state, revision, calls = setup_adapter(tmp_path, monkeypatch)
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
def test_panel_real_stamp_unknown_activity_is_not_success(tmp_path, monkeypatch, unknown):
    mirror, hub, state, revision, calls = setup_adapter(tmp_path, monkeypatch)
    revision[0] = unknown
    mirror.reconcile_panel(OWNER)
    mirror.reconcile_panel(OWNER)
    assert len(calls) == 2
    assert not mirror.panel_remote_unchanged
