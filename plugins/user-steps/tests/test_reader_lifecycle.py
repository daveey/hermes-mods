"""Reader ownership ends explicitly, not when SQLite connections are GC'd."""
import importlib.util
import sqlite3
import sys
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_kanban_mirror import OWNER, core
from test_kanban_mirror_keyed import keyed, settle, board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'reader.db'
    with closing(sqlite3.connect(path)) as c, c:
        c.execute('CREATE TABLE sample(value)')
        c.execute('INSERT INTO sample VALUES (1)')
    return path


def closed(c):
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        c.execute('SELECT 1')


def test_retirement_closes_idle_readers_and_never_retains_again(db):
    mod = load('lifecycle_mirror', ROOT / 'kanban_mirror.py')
    with mod._kept(db) as c:
        assert c.execute('SELECT value FROM sample').fetchone()[0] == 1
    dispose = getattr(mod, 'close_pool', lambda: None)
    dispose()
    closed(c)
    dispose()
    with mod._kept(db) as later:
        assert later.execute('SELECT value FROM sample').fetchone()[0] == 1
    closed(later)
    assert not mod._POOL


def test_retirement_during_open_drains_after_rollback(db, monkeypatch):
    mod = load('opening_mirror', ROOT / 'kanban_mirror.py')
    opening, resume = threading.Event(), threading.Event()
    original = mod._open_ro
    opened = []

    def slow_open(path):
        c = original(path)
        opened.append(c)
        opening.set()
        assert resume.wait(5)
        return c

    monkeypatch.setattr(mod, '_open_ro', slow_open)

    def read():
        with mod._kept(db) as c:
            c.execute('BEGIN')
            assert c.execute('SELECT value FROM sample').fetchone()[0] == 1
            # Nested reads after retirement must not deadlock or retain.
            with mod._kept(db) as nested:
                assert nested.execute('SELECT 1').fetchone()[0] == 1

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        try:
            assert opening.wait(5)
            mod.close_pool()
        finally:
            resume.set()
        future.result(timeout=5)
    assert not mod._POOL
    for c in opened:
        closed(c)


def test_dashboard_replacement_closes_previous_owner(db, tmp_path, monkeypatch):
    root = tmp_path / 'plugin'
    shutil.copytree(ROOT, root)
    api = load('lifecycle_api', root / 'dashboard/plugin_api.py')
    monkeypatch.delitem(sys.modules, '_user_steps_kanban_mirror', raising=False)
    try:
        for i in range(3):
            old = api._load('kanban_mirror')
            with old._kept(db) as c:
                assert c.execute('SELECT 1').fetchone()[0] == 1
            path = root / 'kanban_mirror.py'
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
            new = api._load('kanban_mirror')
            assert new is not old
            closed(c)
            assert not old._POOL
    finally:
        sys.modules.pop('_user_steps_kanban_mirror', None)


def test_repeated_agent_unload_closes_only_owned_package(db, monkeypatch):
    from types import SimpleNamespace
    api = load('independent_api', ROOT / 'dashboard/plugin_api.py')
    # Use the real loader so the cached owner carries its reload stamp. An
    # unstamped synthetic module is legitimately retired by a dashboard retry.
    monkeypatch.setitem(sys.modules, '_user_steps_kanban_mirror', None)
    dashboard = api._load('kanban_mirror')
    with dashboard._kept(db) as independent:
        pass
    try:
        for i in range(3):
            name = f'lifecycle_plugin_{i}'
            spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py')
            plugin = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, name, plugin)
            try:
                spec.loader.exec_module(plugin)
                monkeypatch.setattr(plugin, '_require_publication_contract', lambda: None)
                monkeypatch.setattr(plugin, '_register_clarify_early', lambda: None)
                callbacks = []
                plugin.register(SimpleNamespace(on_unload=callbacks.append,
                    register_hook=lambda *a, **k: None, register_tool=lambda **k: None))
                # Model a dashboard retry pass while the agent is registered.
                assert api._load('kanban_mirror') is dashboard
                mirror = importlib.import_module(name + '.kanban_mirror')
                with mirror._kept(db) as c:
                    pass
                for callback in reversed(callbacks):
                    callback()
                closed(c)
                assert not mirror._POOL
                assert independent.execute('SELECT 1').fetchone()[0] == 1
            finally:
                for key in list(sys.modules):
                    if key.startswith(name + '.'):
                        sys.modules.pop(key)
    finally:
        dashboard.close_pool()


def test_projection_results_survive_retirement_write_and_reopen(keyed):
    k = keyed
    settle(k)
    before = k.store.requests(OWNER)
    k.mod['close_pool']()
    k.mirror.reconcile(OWNER)
    assert k.store.requests(OWNER) == before
    assert not k.mod['_POOL']
    fresh = load('fresh_mirror', ROOT / 'kanban_mirror.py')
    mirror = fresh.Mirror(core, k.store, boards=k.mirror.boards, backend=k.mirror.backend)
    try:
        mirror.reconcile(OWNER)
        assert k.store.requests(OWNER) == before
        board(k, "UPDATE tasks SET status='ready'")
        mirror.reconcile(OWNER)
        assert k.store.requests(OWNER) == []
    finally:
        fresh.close_pool()


def test_failed_dashboard_import_keeps_old_owner(db, tmp_path, monkeypatch):
    root = tmp_path / 'plugin'
    shutil.copytree(ROOT, root)
    api = load('failed_api', root / 'dashboard/plugin_api.py')
    monkeypatch.delitem(sys.modules, '_user_steps_kanban_mirror', raising=False)
    old = api._load('kanban_mirror')
    try:
        with old._kept(db) as c:
            pass
        (root / 'kanban_mirror.py').write_text('raise RuntimeError("broken candidate")\n')
        with pytest.raises(RuntimeError, match='broken candidate'):
            api._load('kanban_mirror')
        assert sys.modules['_user_steps_kanban_mirror'] is old
        with old._kept(db) as again:
            assert again is c
            assert again.execute('SELECT 1').fetchone()[0] == 1
    finally:
        old.close_pool()
        sys.modules.pop('_user_steps_kanban_mirror', None)


def test_active_read_survives_retirement_and_error(db):
    mod = load('active_mirror', ROOT / 'kanban_mirror.py')
    with pytest.raises(ValueError, match='caller'):
        with mod._kept(db) as c:
            c.execute('BEGIN')
            mod.close_pool()  # same-thread unload must not wait on itself
            assert c.execute('SELECT value FROM sample').fetchone()[0] == 1
            raise ValueError('caller')
    closed(c)
    assert not mod._POOL


def test_old_core_registration_without_unload_is_compatible(monkeypatch):
    plugin = load('older_core_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin, '_require_publication_contract', lambda: None)
    monkeypatch.setattr(plugin, '_register_clarify_early', lambda: None)
    hooks = []
    plugin.register(SimpleNamespace(register_hook=lambda *a, **k: hooks.append(a),
                                    register_tool=lambda **k: None))
    assert len(hooks) == 3


def test_queued_borrower_cannot_retain_detached_entry(db):
    mod = load('queued_mirror', ROOT / 'kanban_mirror.py')
    with mod._kept(db):
        pass
    entry = mod._POOL[str(db)]
    waiting = threading.Event()

    class ObservedLock:
        def __init__(self):
            self.lock = threading.Lock()

        def acquire(self, blocking=True):
            return self.lock.acquire(blocking=blocking)

        def release(self):
            self.lock.release()

        def __enter__(self):
            waiting.set()
            self.lock.acquire()

        def __exit__(self, *exc):
            self.lock.release()

    entry.lock = ObservedLock()
    entry.lock.acquire()

    def read():
        with mod._kept(db) as c:
            assert c.execute('SELECT 1').fetchone()[0] == 1
        return c

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(read)
        try:
            assert waiting.wait(5)
            mod.close_pool()
        finally:
            entry.lock.release()
        c = future.result(timeout=5)
    closed(c)
    assert entry.conn is None
    assert not mod._POOL
