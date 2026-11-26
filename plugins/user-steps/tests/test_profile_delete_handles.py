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


def test_decision_inbox_does_not_recreate_deleted_profile(tmp_path):
    home = _deleted_profile(tmp_path)
    cards = _load('profile_delete_cards', 'cards.py')
    with pytest.raises(FileNotFoundError):
        cards.DecisionInbox(home / 'user-steps' / 'decision-inbox.sqlite3')
    assert not home.exists()


def test_decision_inbox_schema_closes_every_connection(tmp_path, monkeypatch):
    cards = _load('profile_delete_cards_close', 'cards.py')
    TrackedConnection.opened = []
    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: real(*a, **{**kw, 'factory': TrackedConnection}))
    inbox = cards.DecisionInbox(tmp_path / 'inbox.sqlite3')
    assert TrackedConnection.opened
    assert all(c.closed for c in TrackedConnection.opened)
    c = real(inbox.path)
    try:
        assert c.execute("select count(*) from sqlite_master where name='deliveries'").fetchone()[0] == 1
    finally:
        c.close()


def test_decision_inbox_schema_failure_closes_connection(tmp_path, monkeypatch):
    cards = _load('profile_delete_cards_failure', 'cards.py')
    TrackedConnection.opened = []

    class Broken(TrackedConnection):
        def execute(self, sql, *args):
            if sql.startswith('CREATE TABLE'):
                raise sqlite3.OperationalError('fixture schema failure')
            return super().execute(sql, *args)

    real = sqlite3.connect
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: real(*a, **{**kw, 'factory': Broken}))
    with pytest.raises(sqlite3.OperationalError, match='fixture schema failure'):
        cards.DecisionInbox(tmp_path / 'inbox.sqlite3')
    assert TrackedConnection.opened
    assert all(c.closed for c in TrackedConnection.opened)


def test_router_inflight_recovery_does_not_resurrect_profile(tmp_path, monkeypatch):
    """Real router lifespan/outbox enters while live; deletion interrupts the admitted pass."""
    import asyncio
    import shutil

    constants = _fence()
    home = tmp_path / '.hermes' / 'profiles' / 'inflight'
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('x: 1\n')
    monkeypatch.setenv('HERMES_HOME', str(home))
    api = _load('profile_delete_router', 'dashboard/plugin_api.py')
    runtime_core = api._load('core')
    store = runtime_core.ActionStore()
    own = {'storedSessionId': 's1'}
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
    attempted, reconciled = [], []
    original_inbox = api._load('cards').DecisionInbox

    def inbox(*args, **kwargs):
        attempted.append(True)
        try:
            return original_inbox(*args, **kwargs)
        finally:
            finished.set()

    def pending():
        entered.set()
        assert resume.wait(10), 'test did not release admitted pass'
        return [dict(own)]

    monkeypatch.setattr(store, 'pending_owners', pending)
    monkeypatch.setattr(store, 'reconcile', lambda *a, **kw: reconciled.append(True) or [])
    monkeypatch.setattr(store, 'backfill_legacy', lambda *a, **kw: None)
    monkeypatch.setattr(runtime_core, 'validate_owner', lambda o: None)
    monkeypatch.setattr(api._load('cards'), 'DecisionInbox', inbox)
    router = api.create_router(action_store=store, owner_resolver=lambda s: dict(own))

    async def exercise():
        try:
            async with router.lifespan_context(None):
                assert await asyncio.to_thread(entered.wait, 10)
                constants.mark_named_profile_deleted(home)
                shutil.rmtree(home)
                resume.set()
                assert await asyncio.to_thread(finished.wait, 10), 'delivery receiver was never reached'
        finally:
            resume.set()
            router.stop_delivery()

    asyncio.run(exercise())
    assert attempted, 'test missed the real receiver construction'
    assert not reconciled, 'delivery must be refused after retirement'
    assert not home.exists()
