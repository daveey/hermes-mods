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
    assert r.status_code == 200 and r.json() == {'tasks': rows, 'includes': ['done']}
    assert seen == [['s1', 's0']]


def test_session_cards_empty_ids_reads_nothing(tmp_path):
    c = client(tmp_path, lambda ids: (_ for _ in ()).throw(AssertionError('read')))
    assert c.get('/session-cards', params={'ids': ' , '}).json() == {'tasks': [], 'includes': ['done']}


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
    assert c.get('/session-cards', params={'ids': 's1'}).json() == {'tasks': [], 'includes': ['done']}
    assert calls == [(['s1'], ('todo', 'running', 'blocked', 'done'), True)]
