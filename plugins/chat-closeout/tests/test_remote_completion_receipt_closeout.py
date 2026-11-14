"""Owner-published receipt -> real core remote producer -> plugin.remote_cards -> real close-out prompt.

The owner observation hash comes from core's ``observe_owner_task`` (never hand-built). Needs the core checkout
(HERMES_CORE_SOURCE); no publisher repo is required. Stale rows stay historical, never a release or authorization.
"""
import importlib
import json
import os
import sqlite3
import time

import pytest
from test_closeout import core, plugin

T0 = 1_800_000_000.0
REF = '@task:build@owner/t_same'


def receipt(disposition='rejected'):
    return {'version': 1, 'items': [{
        'scope': 'bounded assessment', 'disposition': disposition,
        'evidence': [{'ref': 'artifact:report', 'observed_at': '2026-01-01T00:00:00Z', 'verification': 'reported'}],
        'remaining': [{'scope': 'enforcement', 'state': 'deferred', 'owner': None, 'reason': 'No execution authorized.'}]}]}


def compact(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'))


@pytest.fixture
def world(tmp_path, monkeypatch):
    source = os.environ.get('HERMES_CORE_SOURCE')
    if not source:
        pytest.skip('set HERMES_CORE_SOURCE for core SQLite integration')
    monkeypatch.syspath_prepend(source)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub'))
    monkeypatch.setenv('HERMES_MAIL_CONFIG', str(tmp_path / 'mail.json'))
    now = [T0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    ledger = importlib.import_module('hermes_cli.kanban_origin_ledger')
    ksa = importlib.import_module('hermes_cli.kanban_session_activity')
    adapter = importlib.import_module('hermes_cli.kanban_origin_adapter')
    profiles = importlib.import_module('hermes_cli.profiles')
    constants = importlib.import_module('hermes_constants')
    importlib.import_module('hermes_cli.kanban_completion_receipt_projection')
    import hermes_cli.kanban_completion_receipt_projection as proj
    if not hasattr(proj, 'remote_fields'):
        pytest.skip('core lacks remote completion receipt transport')
    monkeypatch.setattr(constants, 'get_hermes_home', lambda: tmp_path)
    monkeypatch.setattr(adapter, '_gateway', lambda _: 'origin')
    monkeypatch.setattr(ksa, '_self_gateway', lambda _: 'origin')
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    monkeypatch.setattr(ksa, '_LINKS_MAX_AGE_S', 0.0)
    binding = {'binding_id': 'binding-0001', 'version': 1,
               'owner': {'gateway': 'owner', 'profile': 'work', 'board': 'build', 'task_id': 't_same'},
               'origin': {'gateway': 'origin', 'profile': 'default', 'session': 'chat'},
               'task_record_key': 'work/build/t_same', 'publication_version': 3}
    owner = sqlite3.connect(tmp_path / 'owner.db')
    owner.executescript('''
        CREATE TABLE tasks (id TEXT, status TEXT, completed_at REAL, current_run_id INTEGER,
                            goal_mode INTEGER, completion_contract TEXT);
        INSERT INTO tasks VALUES ('t_same','done',1000,NULL,0,'local-only');
        CREATE TABLE origin_creation_intents (task_id TEXT, binding_json TEXT);
        CREATE TABLE task_events (id INTEGER, task_id TEXT, kind TEXT, run_id INTEGER, created_at REAL, payload TEXT);
        CREATE TABLE task_runs (id INTEGER, task_id TEXT, status TEXT, outcome TEXT, ended_at REAL, summary TEXT);
        INSERT INTO task_events VALUES (7,'t_same','completed',4,1000,'{}');
        INSERT INTO task_runs VALUES (4,'t_same','done','completed',1000,'Assessment rejected');
    ''')
    owner.execute('INSERT INTO origin_creation_intents VALUES (?,?)',
                  ('t_same', json.dumps({k: v for k, v in binding.items() if k != 'publication_version'})))
    owner.commit()
    replica = tmp_path / 'hub.db'
    with sqlite3.connect(replica) as db:
        db.executescript('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER,'
                         ' data TEXT, updated_at INTEGER, PRIMARY KEY (gw, coll, key));'
                         'CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);'
                         "INSERT INTO meta VALUES ('gateway', 'origin');")
    local = tmp_path / 'kanban.db'
    with sqlite3.connect(local) as db:
        db.execute('CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, session_id TEXT)')
    monkeypatch.setattr(ksa, '_board_db_paths', lambda: [local])
    monkeypatch.setattr(ksa, '_hub_db_path', lambda: replica)
    monkeypatch.setattr(plugin, 'board_paths', lambda: [local])
    ksa._reset_cache_for_tests()
    plugin._gate_reset()
    core._structural_reads.clear()

    def publish(value, version=3, *, at=T0):
        source = {'kind': 'task_run', 'ref': REF, 'event_id': 7, 'event_uid': 'event-original', 'run_id': 4,
                  'run_uid': 'run-original', 'completed_at': 1000}
        task = {**binding['owner'], 'id': 't_same', 'status': 'done', 'title': 'Assessment',
                'origin_bindings': [binding], 'completion_receipt': value, 'completion_receipt_source': source}
        owner.execute('BEGIN')
        task = ledger.observe_owner_task(owner, task, now=at)  # the real owner-observation hash
        owner.rollback()
        with sqlite3.connect(replica) as db:
            db.execute('INSERT OR REPLACE INTO replica VALUES (?,?,?,?,?,?,?)',
                       ('owner', 'tasks', binding['task_record_key'], version, version, json.dumps(task), int(at)))
            db.execute('INSERT OR REPLACE INTO replica VALUES (?,?,?,?,?,?,?)', (
                'owner', 'gateway', 'info', version, version + 100, json.dumps({'gateway': 'owner', 'task_scopes': [
                    {'profile': 'work', 'board': 'build', 'state': 'ok', 'observed_at': at}]}), int(at)))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_ok',?)", (str(at),))
        return task, source

    publish(receipt())
    ledger.accept_binding(path=tmp_path / 'kanban' / 'origin-ledger.db', replica=replica, binding=binding,
                          request_id='request-0001', sender='gw:owner', gateway='origin', profile='default',
                          resolve_session=lambda _: 'chat')
    try:
        yield {'now': now, 'publish': publish, 'local': local, 'replica': replica}
    finally:
        owner.close()
        plugin._gate_reset()
        core._structural_reads.clear()
        ksa._reset_cache_for_tests()


def closeout(cards):
    by_key = {c['key']: c for c in cards}
    ids = sorted(by_key)
    return by_key, core.closeout_prompt({'batch': ids}, by_key)


def test_fresh_remote_receipt_reaches_closeout_prompt_with_source(world):
    world['now'][0] = T0 + 1
    cards = [c for c in plugin.remote_cards() if c.get('via') == 'origin']
    assert len(cards) == 1
    card = cards[0]
    assert card['completion_receipt'] == receipt()
    assert card['stale'] is False and card['unfinished'] is False
    _, prompt = closeout(cards)
    assert compact(receipt()) in prompt
    assert compact(card['completion_receipt_source']) in prompt
    assert REF in prompt and 'event-original' in prompt and 'run-original' in prompt
    assert 'Deferred work is not authorized' in prompt
    assert "evidence" in prompt and '"verification":"reported"' in prompt


def test_waiting_source_consumes_real_remote_producer(world, tmp_path, monkeypatch):
    """Unlike replay's empty remote inventory, route a real owner observation through waiting."""
    world['now'][0] = T0 + 1
    rows = plugin.remote_cards()
    assert len(rows) == 1 and rows[0]['completion_receipt'] == receipt()
    key = rows[0]['key']
    led = core.Ledger(tmp_path / 'closeout.db')
    led.write('chat', batch=[key], open=[])
    monkeypatch.setattr(plugin, 'ledger', lambda: led)
    monkeypatch.setattr(plugin, 'state_dbs', lambda: [])
    monkeypatch.setattr(plugin, 'session_proof', lambda *a: False)
    monkeypatch.delenv('CHAT_CLOSEOUT_OFF', raising=False)
    real_remote = plugin.remote_cards
    calls = []

    def remote():
        result = real_remote()
        calls.append(result)
        return result

    monkeypatch.setattr(plugin, 'remote_cards', remote)  # spy, not a substitute producer
    plugin._wi_reset()
    try:
        plugin._state.pop('waiting_cache', None)
        assert plugin.waiting_source() == {'chat': [key]}
        assert len(calls) == 1
        assert calls[0][0]['completion_receipt'] == receipt()
        world['now'][0] = T0 + 1000
        plugin._state.pop('waiting_cache', None)
        assert plugin.waiting_source() == {}
        assert len(calls) == 2
        assert calls[-1][0]['stale'] is True
    finally:
        plugin._wi_reset()
        plugin._state.pop('waiting_cache', None)


def test_stale_remote_receipt_stays_historical_not_released_or_authorized(world):
    world['now'][0] = T0 + 1000
    cards = [c for c in plugin.remote_cards() if c.get('via') == 'origin']
    card = cards[0]
    assert card['stale'] is True and card['unfinished'] is True and card['status'] == 'unknown'
    assert not card.get('released', False)
    assert not core.is_settled(card)  # a stale receipt never settles/closes the obligation
    _, prompt = closeout(cards)
    assert compact(receipt()) in prompt and compact(card['completion_receipt_source']) in prompt
    assert 'historical completion receipt' in prompt
    assert 'Deferred work is not authorized' in prompt


def test_changed_receipt_without_reobservation_is_dropped_from_prompt(world):
    task, _ = world['publish'](receipt(), version=4)
    task['completion_receipt'] = receipt('delivered')  # tampered after the owner hashed it
    with sqlite3.connect(world['replica']) as db:
        db.execute("UPDATE replica SET data=?, version=5 WHERE coll='tasks'", (json.dumps(task),))
    world['now'][0] = T0 + 1
    cards = [c for c in plugin.remote_cards() if c.get('via') == 'origin']
    assert all('completion_receipt' not in c for c in cards)
    _, prompt = closeout(cards)
    assert 'artifact acceptance: unspecified' in prompt
    assert compact(receipt('delivered')) not in prompt
