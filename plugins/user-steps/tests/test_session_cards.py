"""GET /session-cards: the session panel's card feed, finished cards included."""
from test_withdrawal import ROOT, OWNER, load_module, env


def client(tmp_path, reader):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    store, _ = env(tmp_path)
    api = load_module('session_cards_api', ROOT / 'dashboard' / 'plugin_api.py')
    app = FastAPI()
    app.include_router(api.create_router(
        ledger_dir=tmp_path / 'ledger', state_db=tmp_path / 'missing-state.db', action_store=store,
        owner_resolver=lambda session: dict(OWNER, storedSessionId=session), kanban_boards=[],
        session_tasks=reader))
    return TestClient(app)


def test_session_cards_passes_the_lineage_and_returns_done_cards(tmp_path):
    seen = []
    rows = [{'id': 't_1', 'status': 'done'}, {'id': 't_2', 'status': 'running'}]
    c = client(tmp_path, lambda ids: seen.append(ids) or rows)
    r = c.get('/session-cards', params={'ids': 's1, s0,,'})
    assert r.status_code == 200 and r.json() == {'tasks': rows, 'includes': ['done', 'archived']}
    assert seen == [['s1', 's0']]


def test_session_cards_empty_ids_reads_nothing(tmp_path):
    c = client(tmp_path, lambda ids: (_ for _ in ()).throw(AssertionError('read')))
    assert c.get('/session-cards', params={'ids': ' , '}).json() == {'tasks': [], 'includes': ['done', 'archived']}


def test_default_reader_asks_core_for_listed_plus_done(tmp_path, monkeypatch):
    import sys
    import types
    calls = []
    fake = types.ModuleType('hermes_cli.kanban_session_activity')
    fake.LISTED_STATUSES = ('todo', 'running', 'blocked')
    fake.session_tasks = lambda ids, statuses, stale_ok=False: calls.append((ids, statuses, stale_ok)) or []
    pkg = sys.modules.get('hermes_cli') or types.ModuleType('hermes_cli')
    monkeypatch.setitem(sys.modules, 'hermes_cli', pkg)
    monkeypatch.setattr(pkg, 'kanban_session_activity', fake, raising=False)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', fake)
    c = client(tmp_path, None)
    assert c.get('/session-cards', params={'ids': 's1'}).json() == {'tasks': [], 'includes': ['done', 'archived']}
    assert calls == [(['s1'], ('todo', 'running', 'blocked', 'done', 'archived'), True)]


def test_default_reader_unions_released_terminal_history_without_hiding_live(tmp_path, monkeypatch):
    import sys
    import types
    from copy import deepcopy
    row = dict(id='t_1', gateway='owner-a', profile='default', board='work',
               origin_gateway='origin', origin_profile='default', session='tip', origin_session='s1',
               status='done', unfinished=False, stale=False)
    unknown = dict(row, status='unknown', reported_status='done', unfinished=True)
    history = [row, dict(row), dict(row, gateway='owner-b'),
               dict(row, id='released-done'), dict(row, id='released-done'),
               dict(row, profile='other-profile'),
               dict(row, id='archived', status='archived'),
               dict(row, id='other-lineage', session='elsewhere', origin_session='elsewhere'),
               dict(row, id='unverified', unfinished=True),
               dict(row, id='stale', status='unknown', reported_status='done', unfinished=True),
               dict(row, id='unqualified', gateway=None)]
    before = deepcopy(history)
    fake = types.ModuleType('hermes_cli.kanban_session_activity')
    fake.LISTED_STATUSES = ('running',)
    fake.session_tasks = lambda *a, **kw: [unknown]
    fake.remote_links_and_origin_rows = lambda: ([], history)
    pkg = sys.modules.get('hermes_cli') or types.ModuleType('hermes_cli')
    monkeypatch.setitem(sys.modules, 'hermes_cli', pkg)
    monkeypatch.setattr(pkg, 'kanban_session_activity', fake, raising=False)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', fake)
    result = client(tmp_path, None).get('/session-cards', params={'ids': 's1'}).json()
    tasks = result['tasks']
    assert tasks[0] == unknown  # terminal history never replaces a live unknown obligation
    assert [(r['gateway'], r['id'], r['status']) for r in tasks] == [
        ('owner-a', 't_1', 'unknown'), ('owner-b', 't_1', 'done'),
        ('owner-a', 'released-done', 'done'), ('owner-a', 't_1', 'done'), ('owner-a', 'archived', 'archived')]
    assert tasks[1]['session_id'] == 'tip'
    assert history == before


def test_history_failure_preserves_live_feed(tmp_path, monkeypatch):
    import sys
    import types
    fake = types.ModuleType('hermes_cli.kanban_session_activity')
    fake.LISTED_STATUSES = ('running',)
    live = [{'id': 't_live', 'status': 'unknown', 'unfinished': True}]
    fake.session_tasks = lambda *a, **kw: live
    def fail():
        raise OSError('ledger unavailable')
    fake.remote_links_and_origin_rows = fail
    pkg = sys.modules.get('hermes_cli') or types.ModuleType('hermes_cli')
    monkeypatch.setitem(sys.modules, 'hermes_cli', pkg)
    monkeypatch.setattr(pkg, 'kanban_session_activity', fake, raising=False)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', fake)
    assert client(tmp_path, None).get('/session-cards', params={'ids': 's1'}).json()['tasks'] == live


def _fake_core(monkeypatch, history_reader):
    import sys
    import types
    fake = types.ModuleType('hermes_cli.kanban_session_activity')
    fake.LISTED_STATUSES = ('running',)
    fake.session_tasks = lambda *a, **kw: []
    fake.remote_links_and_origin_rows = history_reader
    pkg = sys.modules.get('hermes_cli') or types.ModuleType('hermes_cli')
    monkeypatch.setitem(sys.modules, 'hermes_cli', pkg)
    monkeypatch.setattr(pkg, 'kanban_session_activity', fake, raising=False)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_session_activity', fake)


def test_history_read_is_stale_ok_on_cores_that_accept_it(tmp_path, monkeypatch):
    # The route is stale-only: a strict history read waits on core's global projection lock (bfe75689
    # regression, 7 s session-cards on the hub). Cores with the kwarg must get stale_ok=True every request.
    calls = []
    _fake_core(monkeypatch, lambda stale_ok=False: calls.append(stale_ok) or ([], []))
    c = client(tmp_path, None)
    for _ in range(2):
        assert c.get('/session-cards', params={'ids': 's1'}).status_code == 200
    assert calls == [True, True]


def test_history_read_stays_strict_on_older_cores(tmp_path, monkeypatch):
    calls = []
    _fake_core(monkeypatch, lambda: calls.append('strict') or ([], []))
    c = client(tmp_path, None)
    for _ in range(2):
        assert c.get('/session-cards', params={'ids': 's1'}).status_code == 200
    assert calls == ['strict', 'strict']


def test_stale_history_failure_is_not_retried_strict(tmp_path, monkeypatch):
    # A cold-miss failure under stale_ok keeps the live feed; it never falls back to a second, strict read.
    calls = []
    live = [{'id': 't_live', 'status': 'running'}]

    def reader(stale_ok=False):
        calls.append(stale_ok)
        raise OSError('projection failed')

    _fake_core(monkeypatch, reader)
    import sys
    sys.modules['hermes_cli.kanban_session_activity'].session_tasks = lambda *a, **kw: live
    assert client(tmp_path, None).get('/session-cards', params={'ids': 's1'}).json()['tasks'] == live
    assert calls == [True]
