"""A deleted named profile is never recreated by user-steps writers, and schema setup closes its connections.

Real-core fence tests skip when the core on the path predates mkdir_under_hermes_home.
"""
import importlib.util
import sqlite3
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load('profile_delete_core', 'core.py')


def _fence():
    constants = pytest.importorskip('hermes_constants')
    if not hasattr(constants, 'mkdir_under_hermes_home'):
        pytest.skip('core predates the named-profile mkdir fence')
    return constants


def _deleted_profile(tmp_path, name='gone'):
    """<root>/.hermes/profiles/<name> that existed, was deleted and tombstoned."""
    constants = _fence()
    profiles = tmp_path / '.hermes' / 'profiles'
    home = profiles / name
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('x: 1\n')
    constants.mark_named_profile_deleted(home)
    import shutil
    shutil.rmtree(home)
    assert not home.exists()
    return home


class TrackedConnection(sqlite3.Connection):
    opened = []

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.closed = False
        TrackedConnection.opened.append(self)

    def close(self):
        self.closed = True
        super().close()


def test_action_store_schema_setup_closes_every_connection(tmp_path, monkeypatch):
    TrackedConnection.opened = []
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: real(*a, **{**kw, 'factory': TrackedConnection}))
    core.ActionStore(tmp_path / 'actions.sqlite3')
    assert TrackedConnection.opened, 'tracking saw no connection'
    assert [c for c in TrackedConnection.opened if not c.closed] == []
    # Schema committed before close.
    c = real(tmp_path / 'actions.sqlite3')
    try:
        assert c.execute("select count(*) from sqlite_master where name='requests'").fetchone()[0] == 1
    finally:
        c.close()


def test_action_store_schema_failure_still_closes(tmp_path, monkeypatch):
    TrackedConnection.opened = []
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: real(*a, **{**kw, 'factory': TrackedConnection}))
    monkeypatch.setattr(core.ActionStore, '_ensure_wal', lambda self: None)
    original = core.ActionStore._connect

    def broken(self):
        c = original(self)
        c.execute('CREATE TABLE lifecycle (namespace TEXT)')  # makes ALTER path run, then fail below
        c.execute('CREATE VIEW requests AS SELECT 1')  # CREATE TABLE IF NOT EXISTS requests is a no-op; revisions follows
        c.execute('CREATE TABLE revisions (x)')
        c.execute('CREATE TABLE aliases (ref TEXT PRIMARY KEY, x)')
        c.execute('CREATE TABLE operations (id TEXT PRIMARY KEY, x)')
        c.execute('CREATE TABLE chat_retirements (x)')
        c.execute('CREATE TABLE clarification_upgrades (x)')
        c.execute('CREATE TABLE withdrawal_history (x)')  # INSERT SELECT with the wrong columns raises
        return c
    monkeypatch.setattr(core.ActionStore, '_connect', broken)
    with pytest.raises(sqlite3.Error):
        core.ActionStore(tmp_path / 'actions.sqlite3')
    assert [c for c in TrackedConnection.opened if not c.closed] == []


def test_action_store_does_not_recreate_deleted_profile(tmp_path):
    home = _deleted_profile(tmp_path)
    with pytest.raises(FileNotFoundError):
        core.ActionStore(home / 'user-steps' / 'actions.sqlite3')
    assert not home.exists()


def test_delivery_lease_and_ledger_do_not_recreate_deleted_profile(tmp_path):
    live = tmp_path / 'live.sqlite3'
    store = core.ActionStore(live)
    home = _deleted_profile(tmp_path)
    store.path = home / 'user-steps' / 'actions.sqlite3'
    with pytest.raises(FileNotFoundError):
        with store.delivery_lease('o', 'op'):
            pass
    with pytest.raises(FileNotFoundError):
        with core.Ledger(home / 'user-steps').edit('root'):
            pass
    assert not home.exists()


def test_trace_does_not_recreate_deleted_profile(tmp_path, monkeypatch):
    home = _deleted_profile(tmp_path)
    plugin = _load_plugin()
    monkeypatch.setenv('HERMES_HOME', str(home))
    plugin._trace(event='x')
    assert not home.exists()


def _load_plugin():
    import sys
    name = 'profile_delete_user_steps'
    spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_pinned_home_poller_stops_without_recreating_deleted_profile(tmp_path, monkeypatch):
    home = _deleted_profile(tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    runtime = _load('profile_delete_outbox', 'outbox.py')
    calls = []
    token = runtime.Token()
    worker = runtime.acquire('gone', token, lambda: calls.append('recover'), lambda: calls.append('retry'), 1)
    worker.thread.join(5)
    assert not worker.thread.is_alive()
    assert calls == [] and 'gone' not in runtime._WORKERS
    assert not home.exists()


def test_poller_stops_when_profile_is_deleted_between_passes(tmp_path, monkeypatch):
    constants = _fence()
    profiles = tmp_path / '.hermes' / 'profiles'
    home = profiles / 'soon'
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('x: 1\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    runtime = _load('profile_delete_outbox2', 'outbox.py')
    calls, first = [], threading.Event()
    token = runtime.Token()
    worker = runtime.acquire('soon', token, first.set, lambda: calls.append('retry'), 1)
    assert first.wait(3)
    constants.mark_named_profile_deleted(home)
    import shutil
    shutil.rmtree(home)
    worker.wake.set()
    worker.thread.join(5)
    assert not worker.thread.is_alive()
    assert calls == [] and 'soon' not in runtime._WORKERS


def test_poller_runs_normally_for_live_profile(tmp_path, monkeypatch):
    home = tmp_path / '.hermes' / 'profiles' / 'live'
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('x: 1\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    runtime = _load('profile_delete_outbox3', 'outbox.py')
    got = threading.Event()
    token = runtime.Token()
    worker = runtime.acquire('live', token, got.set, lambda: None, 1)
    try:
        assert got.wait(3)
        assert worker.thread.is_alive()
    finally:
        assert runtime.release(worker, token)
