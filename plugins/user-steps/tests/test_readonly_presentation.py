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


def test_mirror_views_batch_owners_and_close_before_presentation(tmp_path, monkeypatch):
    from test_kanban_mirror import fixture
    mirror, store, _, _ = fixture(tmp_path)
    mirror.reconcile(OWNER)
    other = dict(OWNER, storedSessionId='second-session')
    store.create(other, dict(request(), owner=other))
    foreign = dict(OWNER, profile='other-profile')
    store.create(foreign, dict(request(), owner=foreign))
    owners = [OWNER, other]
    expected = [mirror.present(r) for own in owners
                for r in store.views(own, include_withdrawn=True)]
    mirror.store = core.ActionStore(store.path, read_only=True)
    opened, closed = [], []
    original = sqlite3.connect

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
    present = mirror.present

    def checked(*args, **kwargs):
        assert opened == closed
        return present(*args, **kwargs)

    monkeypatch.setattr(mirror, 'present', checked)
    assert mirror.views(owners, include_withdrawn=True) == expected
    assert len(opened) == 1
    assert len(expected) == 2


@pytest.mark.parametrize('retiring,corrupt', [(False, False), (True, False), (True, True)])
def test_materialized_remote_presentation_preserves_binding_and_delivery(tmp_path, monkeypatch, retiring, corrupt):
    from test_remote_card_state_machine import setup, depart
    mirror, store, row, old, _ = setup(tmp_path)
    monkeypatch.setattr(mirror.remote, '_age', lambda op: 4000)
    if retiring:
        depart(mirror, row, departure=False)
    if corrupt:
        with store._tx() as c:
            c.execute("UPDATE kanban_projections SET request='{}'")
    expected = [mirror.present(r) for r in store.views(OWNER, include_withdrawn=True)]
    mirror.store = core.ActionStore(store.path, read_only=True)
    original = sqlite3.connect
    handles = []

    def connect(*args, **kwargs):
        c = original(*args, **kwargs)
        handles.append(c)
        return c

    monkeypatch.setattr(core.sqlite3, 'connect', connect)
    present = mirror.present

    def checked(*args, **kwargs):
        assert len(handles) == 1
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            handles[0].execute('SELECT 1')
        return present(*args, **kwargs)

    monkeypatch.setattr(mirror, 'present', checked)
    actual = mirror.views([OWNER], include_withdrawn=True)
    assert actual == expected
    assert len(handles) == 1
    if retiring and not corrupt:
        assert actual[0]['delivery']['unconfirmed'] is True
    if corrupt:
        assert 'presentation' not in actual[0]


@pytest.mark.parametrize('broken', ['missing-operation', 'wrong-request', 'wrong-session', 'missing-display'])
def test_materialized_remote_missing_or_mismatched_evidence_falls_back(tmp_path, broken):
    import json
    from test_remote_card_state_machine import setup, depart
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row, departure=False)
    with store._tx() as c:
        if broken == 'missing-display':
            c.execute('DROP TABLE kanban_projection_display')
        elif broken == 'missing-operation':
            c.execute('DELETE FROM operations')
        elif broken == 'wrong-session':
            c.execute("UPDATE operations SET session='other-session'")
        else:
            body = store._operation(c, OWNER, op['operationId'])['operation']
            body['requestId'] = 'another-request'
            c.execute('UPDATE operations SET body=?', (json.dumps(body),))
    expected = store.view(OWNER, old['requestId'])
    mirror.store = core.ActionStore(store.path, read_only=True)
    assert mirror.views([OWNER], include_withdrawn=True) == [expected]


def test_materialized_plain_store_needs_no_mirror_tables(tmp_path):
    from test_kanban_mirror import fixture
    mirror, _, _, _ = fixture(tmp_path)
    path = tmp_path / 'plain.sqlite3'
    store = core.ActionStore(path)
    r = store.create(OWNER, request())
    mirror.store = core.ActionStore(path, read_only=True)
    assert mirror.views([OWNER]) == [store.view(OWNER, r['requestId'])]
    assert mirror.views([]) == []


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
    c = sqlite3.connect(tmp_path / 'state.db')
    c.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, source TEXT)')
    c.execute("INSERT INTO sessions(id) VALUES ('session-a')")
    c.commit()
    c.close()
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
    original = core._ro_connect
    opened = []

    def connect(path, **kwargs):
        c = original(path, **kwargs)
        opened.append((Path(path).name, c))
        return c

    monkeypatch.setattr(core, '_ro_connect', connect)
    assert api.panel_requests(tmp_path, 'session-a', ['session-a'], reconcile=False) == [expected]
    assert [name for name, _ in opened] == ['state.db', 'actions.sqlite3']
    for _, c in opened:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            c.execute('SELECT 1')
