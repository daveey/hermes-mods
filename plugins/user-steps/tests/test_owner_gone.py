"""Deleted-chat retirement uses positive local owner-store evidence only."""
import sqlite3

import pytest

from test_withdrawal import complete_request, core, decision, OWNER


@pytest.fixture
def owner_env(tmp_path, monkeypatch):
    from hermes_cli import profiles

    profile_home = tmp_path / 'owner-profile'
    profile_home.mkdir()
    db = profile_home / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY)')
        c.execute('INSERT INTO sessions VALUES (?)', (OWNER['storedSessionId'],))
    monkeypatch.setattr(core, 'home', lambda: tmp_path / 'wrong-profile')
    monkeypatch.setattr(core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(profiles, 'profile_exists', lambda name: name == OWNER['profile'])
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: profile_home)
    store = core.ActionStore(tmp_path / 'actions.sqlite3')
    ledger = core.Ledger(tmp_path / 'ledger', db)
    return store, ledger, db


def remove_owner(db):
    with sqlite3.connect(db) as c:
        c.execute('DELETE FROM sessions')


def reconcile(env, owner=OWNER):
    store, ledger, _ = env
    return core.reconcile_moot(store, ledger, owner, boards=[])


def test_deleted_owner_withdrawn_without_decision(owner_env):
    store, _, db = owner_env
    r = store.create(OWNER, complete_request())
    remove_owner(db)
    result = reconcile(owner_env)
    assert len(result) == 1
    assert result[0]['state'] == 'withdrawn'
    assert result[0]['withdrawal']['by'] == 'system'
    assert result[0]['withdrawal']['reason'] == 'its chat no longer exists'
    assert 'resolution' not in result[0]
    assert store.view(OWNER, r['requestId']) == result[0]
    with sqlite3.connect(store.path) as c:
        assert c.execute('SELECT count(*) FROM operations').fetchone()[0] == 0
    assert reconcile(owner_env) == []


def test_present_owner_kept(owner_env):
    store, _, _ = owner_env
    r = store.create(OWNER, complete_request())
    assert reconcile(owner_env) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'schema', 'permission'])
def test_unreadable_owner_store_kept(owner_env, monkeypatch, failure):
    store, _, db = owner_env
    r = store.create(OWNER, complete_request())
    remove_owner(db)
    if failure == 'missing':
        db.unlink()
    elif failure == 'corrupt':
        db.write_bytes(b'not sqlite')
    elif failure == 'schema':
        with sqlite3.connect(db) as c:
            c.execute('DROP TABLE sessions')
    else:
        monkeypatch.setattr(core, '_ro_connect', lambda *a, **kw: (_ for _ in ()).throw(PermissionError('denied')))
    assert reconcile(owner_env) == []
    assert store.get(OWNER, r['requestId'])['state'] == 'open'
    if failure == 'missing':
        assert not db.exists()


def test_locked_owner_store_kept(owner_env):
    store, _, db = owner_env
    r = store.create(OWNER, complete_request())
    remove_owner(db)
    blocker = sqlite3.connect(db)
    try:
        blocker.execute('BEGIN EXCLUSIVE')
        assert reconcile(owner_env) == []
        assert store.get(OWNER, r['requestId'])['state'] == 'open'
    finally:
        blocker.rollback()
        blocker.close()


def test_recorded_decision_unchanged_when_owner_deleted(owner_env):
    store, _, db = owner_env
    r = store.create(OWNER, complete_request())
    store.decide(OWNER, decision(r))
    before = store.view(OWNER, r['requestId'])
    remove_owner(db)
    assert reconcile(owner_env) == []
    assert store.view(OWNER, r['requestId']) == before


@pytest.mark.parametrize('field,value', [('profile', 'unknown'), ('gatewayId', 'another-gateway')])
def test_unknown_owner_namespace_kept(owner_env, field, value):
    store, _, db = owner_env
    owner = dict(OWNER, **{field: value})
    r = store.create(owner, dict(complete_request(), owner=owner))
    remove_owner(db)
    assert reconcile(owner_env, owner) == []
    assert store.get(owner, r['requestId'])['state'] == 'open'


def test_card_bound_request_unchanged_when_owner_deleted(owner_env):
    store, _, db = owner_env
    ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', id='t_12345678', board='default')
    r = store.create(OWNER, complete_request(aliases=[ref]))
    before = store.view(OWNER, r['requestId'])
    remove_owner(db)
    assert reconcile(owner_env) == []
    assert store.view(OWNER, r['requestId']) == before


def test_hygiene_is_permitted_withdrawal_actor(owner_env):
    store, ledger, _ = owner_env
    r = store.create(OWNER, complete_request())
    view = core.withdraw_request(store, ledger, OWNER, r['requestId'], by='hygiene', reason='duplicate ask')
    assert view['state'] == 'withdrawn'
    assert view['withdrawal']['by'] == 'hygiene'


# ---- other deleted chats: a deleted chat never reads its own queue, so a surviving chat's read retires them ----

GONE = dict(OWNER, storedSessionId='session-gone')
KEPT = dict(OWNER, storedSessionId='session-kept')


def add_sessions(db, *sids):
    with sqlite3.connect(db) as c:
        c.executemany('INSERT OR IGNORE INTO sessions(id) VALUES (?)', [(s,) for s in sids])


def ask(store, owner, **kw):
    return store.create(owner, dict(complete_request(**kw), owner=dict(owner)))


def test_reconcile_retires_other_deleted_owner_but_returns_only_supplied_owner(owner_env):
    store, _, db = owner_env
    add_sessions(db, KEPT['storedSessionId'])
    mine = ask(store, OWNER)
    gone = ask(store, GONE)
    kept = ask(store, KEPT)
    assert reconcile(owner_env) == []
    assert store.get(OWNER, mine['requestId'])['state'] == 'open'
    assert store.get(KEPT, kept['requestId'])['state'] == 'open'
    view = store.view(GONE, gone['requestId'])
    assert view['state'] == 'withdrawn'
    assert view['withdrawal']['by'] == 'system' and view['withdrawal']['reason'] == 'its chat no longer exists'
    assert 'resolution' not in view
    assert reconcile(owner_env) == []
    assert store.view(GONE, gone['requestId']) == view


def test_other_deleted_owner_decided_and_card_bound_kept(owner_env):
    store, _, _ = owner_env
    ref = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', id='t_87654321', board='default')
    bound = ask(store, GONE, aliases=[ref])
    decided = ask(store, GONE)
    store.decide(GONE, decision(decided))
    before = [store.view(GONE, r['requestId']) for r in (bound, decided)]
    assert reconcile(owner_env) == []
    assert [store.view(GONE, r['requestId']) for r in (bound, decided)] == before


@pytest.mark.parametrize('field,value', [('profile', 'other-profile'), ('gatewayId', 'another-gateway')])
def test_other_namespace_deleted_owner_untouched(owner_env, monkeypatch, field, value):
    from hermes_cli import profiles

    store, _, _ = owner_env
    monkeypatch.setattr(profiles, 'profile_exists', lambda name: True)  # even an existing foreign profile
    foreign = dict(GONE, **{field: value})
    r = ask(store, foreign)
    assert reconcile(owner_env) == []
    assert store.get(foreign, r['requestId'])['state'] == 'open'


@pytest.mark.parametrize('failure', ['missing', 'schema', 'permission', 'identity'])
def test_other_deleted_owner_kept_when_evidence_unreadable(owner_env, monkeypatch, failure):
    store, _, db = owner_env
    r = ask(store, GONE)
    if failure == 'missing':
        db.unlink()
    elif failure == 'schema':
        with sqlite3.connect(db) as c:
            c.execute('DROP TABLE sessions')
    elif failure == 'permission':
        monkeypatch.setattr(core, '_ro_connect', lambda *a, **kw: (_ for _ in ()).throw(PermissionError('denied')))
    else:
        monkeypatch.setattr(core, 'server_owner', lambda sid: (_ for _ in ()).throw(core.ActionError('no id', 503)))
    assert reconcile(owner_env) == []
    assert store.get(GONE, r['requestId'])['state'] == 'open'


def test_other_owner_sweep_reads_store_and_state_db_once(owner_env, monkeypatch):
    store, _, db = owner_env
    add_sessions(db, KEPT['storedSessionId'])
    for o in (GONE, GONE, KEPT, KEPT):
        ask(store, o)
    opens = []
    real = core._ro_connect
    monkeypatch.setattr(core, '_ro_connect', lambda *a, **kw: opens.append(a[0]) or real(*a, **kw))
    looked = []
    real_mc = store.moot_candidates
    monkeypatch.setattr(store, 'moot_candidates', lambda o: looked.append(o['storedSessionId']) or real_mc(o))
    reconcile(owner_env)
    assert opens == [db]  # one grouped state.db read; the present supplied owner has no candidates
    assert looked == ['session-gone', OWNER['storedSessionId']]  # only the deleted chat gets a candidate read
    assert {r['state'] for r in store.views(GONE, include_withdrawn=True)} == {'withdrawn'}
    assert {r['state'] for r in store.views(KEPT, include_withdrawn=True)} == {'open'}


def full_sessions_db(db, *sids):
    db.unlink()
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT, end_reason TEXT, source TEXT)')
        c.executemany('INSERT INTO sessions(id) VALUES (?)', [(s,) for s in sids])


def test_get_requests_for_existing_observer_retires_deleted_owner(owner_env, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_withdrawal import ROOT, load_module

    _, _, db = owner_env
    api = load_module('owner_gone_api', ROOT / 'dashboard' / 'plugin_api.py')
    live = api._load('core')  # the hot-loaded core the route really calls
    monkeypatch.setattr(live, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    monkeypatch.setattr(live, 'home', lambda: tmp_path / 'wrong-profile')
    store = live.ActionStore(tmp_path / 'router-actions.sqlite3')
    observer = dict(OWNER, storedSessionId='observer')
    full_sessions_db(db, 'observer', 'fixture-owner', KEPT['storedSessionId'])
    fixture = dict(OWNER, storedSessionId='fixture-owner')
    doomed = store.create(fixture, dict(complete_request(), owner=dict(fixture)))
    kept = store.create(KEPT, dict(complete_request(), owner=dict(KEPT)))
    foreign = dict(fixture, gatewayId='another-gateway')
    elsewhere = store.create(foreign, dict(complete_request(), owner=dict(foreign)))
    cards = load_module('owner_gone_cards', ROOT / 'cards.py')
    app = FastAPI()
    app.include_router(api.create_router(ledger_dir=tmp_path / 'ledger', state_db=db, action_store=store,
                                         receiver=cards.DecisionInbox(tmp_path / 'inbox.db'),
                                         kanban_boards=lambda: []))
    client = TestClient(app)
    # The fixture chat is deleted: its own read is refused before any reconciliation...
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM sessions WHERE id='fixture-owner'")
    assert client.get('/requests', params={'session': 'fixture-owner'}).status_code == 404
    assert store.get(fixture, doomed['requestId'])['state'] == 'open'
    # ...so one GET from an existing observer chat retires it, and lists only the observer's own queue.
    response = client.get('/requests', params={'session': 'observer', 'include': 'withdrawn'})
    assert response.status_code == 200 and response.json()['requests'] == []
    assert store.view(fixture, doomed['requestId'])['withdrawal']['reason'] == 'its chat no longer exists'
    assert store.get(KEPT, kept['requestId'])['state'] == 'open'
    assert store.get(foreign, elsewhere['requestId'])['state'] == 'open'
    assert store.views(observer, include_withdrawn=True) == []


def test_pre_llm_call_for_existing_chat_retires_deleted_owner(owner_env, tmp_path, monkeypatch):
    from test_withdrawal import ROOT, load_module

    _, _, db = owner_env
    hermes_home = tmp_path / 'hook-home'
    hermes_home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(hermes_home))
    monkeypatch.delenv('HERMES_KANBAN_TASK', raising=False)
    plugin = load_module('owner_gone_hook_plugin', ROOT / '__init__.py')
    monkeypatch.setattr(plugin.core, 'home', lambda: hermes_home)
    monkeypatch.setattr(plugin.core, 'kanban_root', lambda: hermes_home)
    monkeypatch.setattr(plugin.core, 'server_owner', lambda sid: dict(OWNER, storedSessionId=sid))
    store = plugin.core.ActionStore()
    add_sessions(db, 'observer')
    fixture = dict(OWNER, storedSessionId='fixture-owner')
    doomed = store.create(fixture, dict(complete_request(), owner=dict(fixture)))
    note = (plugin.pre_llm_call(session_id='observer', platform='desktop') or {}).get('context') or ''
    assert doomed['requestId'] not in note
    assert store.view(fixture, doomed['requestId'])['withdrawal']['reason'] == 'its chat no longer exists'
