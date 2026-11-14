"""Reload ownership tests use only fixture databases and a fake core registry."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest


def load(monkeypatch, tmp_path, *, modern=True):
    core = ModuleType('hermes_cli')
    registry = ModuleType('hermes_cli.kanban_generation')
    hooks = []
    registry.register_reader_cache = lambda hook: hooks.append(hook) if hook not in hooks else None
    if modern:
        registry.unregister_reader_cache = lambda hook: hooks.remove(hook)
    core.kanban_generation = registry
    monkeypatch.setitem(sys.modules, 'hermes_cli', core)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_generation', registry)
    for key in ('HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKER_TASK', 'HERMES_MANAGED_WORKER'):
        monkeypatch.delenv(key, raising=False)
    modules, callbacks, connections = [], [], []
    for index in range(3):
        spec = importlib.util.spec_from_file_location(f'sweep_unload_{index}', Path(__file__).parents[1] / '__init__.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod, '_home', lambda: tmp_path)
        monkeypatch.setattr(mod, 'WATCH_INTERVAL_S', 3600)
        def connect(path, **kwargs):
            con = sqlite3.connect(path, check_same_thread=False)
            connections.append(con)
            return con
        monkeypatch.setattr(mod, '_ro_connect', connect)
        mod.start_reconciler(SimpleNamespace(on_unload=callbacks.append))
        modules.append(mod)
    db = tmp_path / 'fixture.db'
    con = sqlite3.connect(db)
    try:
        con.execute('create table fixture(value)')
        con.commit()
    finally:
        con.close()
    return modules, callbacks, connections, hooks, str(db)


@pytest.mark.parametrize('modern', [True, False])
def test_multiple_scopes_retire_observers(monkeypatch, tmp_path, modern):
    modules, callbacks, connections, hooks, db = load(monkeypatch, tmp_path, modern=modern)
    try:
        for mod in modules:
            first = mod._sweep_db_stamp(db)
            assert mod._sweep_db_stamp(db) == first
        assert len(connections) == 3
    finally:
        for callback in callbacks:
            callback()
    assert all(not mod._SWEEP_DBS for mod in modules)
    if modern:
        assert hooks == []
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute('select 1')
    for mod in modules:
        with pytest.raises(mod._SweepBoardBusy):
            mod._sweep_db_stamp(db)
    for callback in callbacks:
        callback()  # unload is idempotent


def test_unload_fences_inflight_open(monkeypatch, tmp_path):
    modules, callbacks, connections, hooks, db = load(monkeypatch, tmp_path)
    mod = modules[0]
    opened, release = threading.Event(), threading.Event()
    original = mod._ro_connect
    errors = []
    def blocked(*args, **kwargs):
        con = original(*args, **kwargs)
        opened.set()
        assert release.wait(5)
        return con
    monkeypatch.setattr(mod, '_ro_connect', blocked)
    def observe():
        try:
            mod._sweep_db_stamp(db)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=observe)
    thread.start()
    try:
        assert opened.wait(5)
        for callback in callbacks:
            callback()
        assert hooks == []
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], mod._SweepBoardBusy)
    assert not mod._SWEEP_DBS
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute('select 1')
