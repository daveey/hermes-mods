"""Presentation readers must neither initialize stores nor hold SQL during projection."""
import sqlite3

import pytest
from test_decisions import core, OWNER, request


def test_readonly_store_never_initializes_or_creates(tmp_path, monkeypatch):
    path = tmp_path / 'absent' / 'actions.sqlite3'
    monkeypatch.setattr(core.ActionStore, '_ensure_wal', lambda self: pytest.fail('WAL initialization'))
    reader = core.ActionStore(path, read_only=True)
    assert not path.parent.exists()
    with pytest.raises(sqlite3.OperationalError):
        reader.views(OWNER)
    assert not path.parent.exists()


def test_materialized_views_close_before_projection_and_preserve_lifecycle(tmp_path, monkeypatch):
    path = tmp_path / 'actions.sqlite3'
    writer = core.ActionStore(path)
    first = writer.create(OWNER, request(), created_at=1000)
    second = writer.create(OWNER, request(), created_at=1001)
    writer.withdraw(OWNER, second['requestId'], by='agent', reason='moot')
    expected = [writer.view(OWNER, r['requestId']) for r in (first, second)]
    reader = core.ActionStore(path, read_only=True)
    original = sqlite3.connect
    opened, closed = [], []

    class Connection(sqlite3.Connection):
        def close(self):
            closed.append(self)
            super().close()

    def connect(*args, **kwargs):
        assert kwargs.get('uri') and 'mode=ro' in str(args[0])
        c = original(*args, **kwargs, factory=Connection)
        opened.append(c)
        return c

    monkeypatch.setattr(core.sqlite3, 'connect', connect)
    project = reader._project_view

    def checked(row):
        assert opened == closed
        return project(row)

    monkeypatch.setattr(reader, '_project_view', checked)
    assert reader.views(OWNER, include_withdrawn=True) == expected
    assert len(opened) == 1
    assert reader.views(dict(OWNER, storedSessionId='other')) == []
    assert reader.views(OWNER) == expected[:1]
    with pytest.raises(sqlite3.OperationalError):
        reader.create(OWNER, request())


def test_panel_read_does_not_reconcile_or_initialize(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    root = Path(core.__file__).parent

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    api = load('readonly_panel_api', root / 'dashboard' / 'plugin_api.py')
    mirror = load('readonly_panel_mirror', root / 'kanban_mirror.py')
    monkeypatch.setattr(api, '_load', lambda name: {'core': core, 'kanban_mirror': mirror}[name])
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    monkeypatch.setattr(core, '_row', lambda *args: ('session-a', None, None, 'desktop'))
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(core, 'kanban_boards', lambda: {})
    assert api.panel_requests(tmp_path, 'session-a', ['session-a'], reconcile=False) == []
    assert not (tmp_path / 'user-steps').exists()
    writer = core.ActionStore()
    r = writer.create(OWNER, request())
    mirror.Mirror(core, writer)  # producer alone initializes projection tables
    expected = writer.view(OWNER, r['requestId'])
    monkeypatch.setattr(core.ActionStore, '_ensure_wal', lambda self: pytest.fail('reader initialized WAL'))
    monkeypatch.setattr(core.ActionStore, '_tx', lambda self: pytest.fail('reader opened write transaction'))
    monkeypatch.setattr(mirror.Mirror, 'reconcile', lambda *args: pytest.fail('reader reconciled'))
    assert api.panel_requests(tmp_path, 'session-a', ['session-a'], reconcile=False) == [expected]
