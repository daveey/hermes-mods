"""One closed source snapshot for overlapping panel lineages."""
import sqlite3

import pytest
from test_decisions import core, OWNER, request
from test_withdrawal import ROOT, load_module


@pytest.mark.parametrize('extra_owners', [0, 510])
def test_many_requests_share_closed_snapshots_and_preserve_scopes(tmp_path, monkeypatch, extra_owners):
    api = load_module('many_panel_api', ROOT / 'dashboard' / 'plugin_api.py')
    mirror = load_module('many_panel_mirror', ROOT / 'kanban_mirror.py')
    monkeypatch.setattr(api, '_load', lambda name: core if name == 'core' else mirror)
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    c = sqlite3.connect(tmp_path / 'state.db')
    c.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY)')
    extra = [f'extra-{i}' for i in range(extra_owners)]
    c.executemany('INSERT INTO sessions VALUES (?)', [(s,) for s in ('left', 'right', 'parent', *extra)])
    c.commit()
    c.close()
    store = core.ActionStore()
    expected = {}
    for sid in ('left', 'right', 'parent', 'deleted'):
        owner = dict(OWNER, storedSessionId=sid)
        expected[sid] = store.create(owner, dict(request(), owner=owner))
    foreign = dict(OWNER, profile='foreign', storedSessionId='parent')
    store.create(foreign, dict(request(), owner=foreign))
    original = sqlite3.connect
    opened, closed = [], []

    class Connection(sqlite3.Connection):
        def close(self):
            closed.append(self)
            super().close()

    def connect(*args, **kwargs):
        assert kwargs.get('uri') and 'mode=ro' in str(args[0])
        c = original(*args, **kwargs, factory=Connection)
        c.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        opened.append(c)
        return c

    project = core.ActionStore._project_view

    def checked(self, row):
        assert opened == closed
        return project(row)

    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(core.ActionStore, '_project_view', checked)
    monkeypatch.setattr(mirror.Mirror, 'reconcile', lambda *a, **k: pytest.fail('reader reconciled'))
    actual = api.panel_requests_many(tmp_path, {
        'left': ['parent', 'left', 'parent'], 'right': ['right', 'parent'],
        'deleted': ['parent'], **{sid: [] for sid in extra},
    })
    assert len(opened) == 2
    assert opened == closed
    assert [r['requestId'] for r in actual['left']] == [expected[s]['requestId'] for s in ('parent', 'left')]
    assert [r['requestId'] for r in actual['right']] == [expected[s]['requestId'] for s in ('right', 'parent')]
    assert isinstance(actual['deleted'], core.ActionError)
    assert actual['deleted'].status == 404
    # Consumers must not mutate another lineage's shared parent presentation.
    actual['left'][0]['title'] = 'changed'
    assert actual['right'][1]['title'] != 'changed'


def test_many_requests_absent_store_is_empty_but_unreadable_state_raises(tmp_path, monkeypatch):
    api = load_module('empty_many_panel_api', ROOT / 'dashboard' / 'plugin_api.py')
    monkeypatch.setattr(api, '_load', lambda name: core)
    monkeypatch.setattr(core, 'home', lambda: tmp_path)
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    with pytest.raises(sqlite3.OperationalError):
        api.panel_requests_many(tmp_path, {'left': []})
    assert not (tmp_path / 'state.db').exists()
    c = sqlite3.connect(tmp_path / 'state.db')
    c.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY)')
    c.execute("INSERT INTO sessions VALUES ('left')")
    c.commit()
    c.close()
    assert api.panel_requests_many(tmp_path, {'left': []}) == {'left': []}
    assert not (tmp_path / 'user-steps').exists()
