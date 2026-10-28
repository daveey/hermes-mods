"""Integration with fork core; set HERMES_CORE_SOURCE to its checkout to run."""
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import pytest
from test_closeout import plugin, core


@pytest.fixture
def origin(tmp_path, monkeypatch):
    source = os.environ.get('HERMES_CORE_SOURCE')
    if not source:
        pytest.skip('set HERMES_CORE_SOURCE for core SQLite integration')
    monkeypatch.syspath_prepend(source)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub'))
    monkeypatch.setenv('HERMES_MAIL_CONFIG', str(tmp_path / 'mail.json'))
    ledger = importlib.import_module('hermes_cli.kanban_origin_ledger')
    activity = importlib.import_module('hermes_cli.kanban_session_activity')
    adapter = importlib.import_module('hermes_cli.kanban_origin_adapter')
    profiles = importlib.import_module('hermes_cli.profiles')
    constants = importlib.import_module('hermes_constants')
    monkeypatch.setattr(constants, 'get_hermes_home', lambda: tmp_path)
    monkeypatch.setattr(adapter, '_gateway', lambda _: 'origin')
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    replica = tmp_path / 'hub.db'
    path = tmp_path / 'kanban' / 'origin-ledger.db'
    binding = {'binding_id': 'binding-0001', 'version': 1,
               'owner': {'gateway': 'owner', 'profile': 'work', 'board': 'build', 'task_id': 't_same'},
               'origin': {'gateway': 'origin', 'profile': 'default', 'session': 'chat'},
               'task_record_key': 'work/build/t_same', 'publication_version': 3}
    owner = sqlite3.connect(tmp_path / 'owner.db')
    owner.executescript('''
        CREATE TABLE tasks (id TEXT, status TEXT, completed_at REAL, current_run_id INTEGER,
                            goal_mode INTEGER, completion_contract TEXT);
        INSERT INTO tasks VALUES ('t_same','running',NULL,NULL,0,'local-only');
        CREATE TABLE origin_creation_intents (task_id TEXT, binding_json TEXT);
        CREATE TABLE task_events (id INTEGER, task_id TEXT, kind TEXT, run_id INTEGER,
                                  created_at REAL, payload TEXT);
        CREATE TABLE task_runs (id INTEGER, task_id TEXT, status TEXT, outcome TEXT, ended_at REAL);
    ''')
    owner.execute('INSERT INTO origin_creation_intents VALUES (?,?)',
                  ('t_same', json.dumps({k: v for k, v in binding.items() if k != 'publication_version'})))
    owner.commit()
    with sqlite3.connect(replica) as db:
        db.executescript('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, data TEXT);'
                         'CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);')

    def publish(status, version, age=0):
        now = time.time() - age
        owner.execute('UPDATE tasks SET status=?,completed_at=?', (status, now if status == 'done' else None))
        if status == 'done':
            owner.execute('DELETE FROM task_events')
            owner.execute('DELETE FROM task_runs')
            owner.execute("INSERT INTO task_events VALUES (1,'t_same','completed',1,?,'{}')", (now,))
            owner.execute("INSERT INTO task_runs VALUES (1,'t_same','done','completed',?)", (now,))
        task = {**binding['owner'], 'id': 't_same', 'status': status, 'origin_bindings': [binding]}
        task = ledger.observe_owner_task(owner, task, now=now)
        owner.commit()
        with sqlite3.connect(replica) as db:
            db.execute('DELETE FROM replica')
            db.execute('INSERT INTO replica VALUES (?,?,?,?,?)',
                       ('owner', 'tasks', binding['task_record_key'], version, json.dumps(task)))
            db.execute('INSERT INTO replica VALUES (?,?,?,?,?)', ('owner', 'gateway', 'info', version,
                       json.dumps({'gateway': 'owner', 'task_scopes': [
                           {'profile': 'work', 'board': 'build', 'state': 'ok',
                                         'observed_at': now}]})))  # core releases only on a fresh per-scope heartbeat
            db.execute("INSERT OR REPLACE INTO meta VALUES ('last_ok',?)", (str(now),))
    publish('running', 3)
    ledger.accept_binding(path=path, replica=replica, binding=binding, request_id='request-0001',
                          sender='gw:owner', gateway='origin', profile='default', resolve_session=lambda _: 'chat')
    local = tmp_path / 'kanban.db'
    with sqlite3.connect(local) as db:
        db.executescript("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, session_id TEXT);"
                         "INSERT INTO tasks VALUES ('t_same','Local done','done','other');")
    monkeypatch.setattr(activity, '_board_db_paths', lambda: [local])
    monkeypatch.setattr(activity, '_hub_db_path', lambda: replica)
    monkeypatch.setattr(activity, '_legacy_remote_links', lambda: [])
    monkeypatch.setattr(plugin, 'board_paths', lambda: [local])
    try:
        yield publish, replica
    finally:
        owner.close()


def test_real_projection_completion_and_stale_collision(origin, tmp_path, monkeypatch):
    publish, replica = origin
    led = core.Ledger(tmp_path / 'closeout.db')
    ids = plugin.open_card_ids('chat')
    assert ids == ['owner/work/build/t_same']
    core.record_turn_end(led, 'chat', ids, plugin.read_cards)
    assert core.closeout_key(led.get('chat'), plugin.read_cards(ids)) is None
    publish('done', 4)
    assert plugin.open_card_ids('chat') == []  # core's open projection omits the release
    cards = plugin.read_cards(ids)
    assert cards[ids[0]]['unfinished'] is False
    assert core.closeout_key(led.get('chat'), cards)
    with sqlite3.connect(replica) as db:
        db.execute("UPDATE meta SET v='1' WHERE k='last_ok'")
    cards = plugin.read_cards(ids)
    assert cards[ids[0]]['status'] == 'unknown'
    assert core.closeout_key(led.get('chat'), cards) is None
    core.record_turn_end(led, 'chat', [], plugin.read_cards)
    assert led.get('chat')['batch'] == ids and not led.get('chat')['covered']
    publish('done', 5)
    key = core.closeout_key(led.get('chat'), plugin.read_cards(ids))
    assert key
    core.record_turn_end(led, 'chat', [], plugin.read_cards, observed_sig=key)
    assert led.get('chat')['batch'] == []


def test_missing_replica_never_uses_local_done(origin, tmp_path):
    _, replica = origin
    ids = plugin.open_card_ids('chat')
    replica.unlink()
    assert plugin.open_card_ids('chat') == ids
    assert core.closeout_key({'batch': ids}, plugin.read_cards(ids)) is None
    assert core.closeout_key({'batch': ['t_same']}, plugin.read_cards(['t_same'])) is None


def test_mixed_legacy_inventory_preserves_collision_and_human_ask(origin, monkeypatch):
    from hermes_cli import kanban_session_activity as activity
    legacy = {'id': 't_same', 'key': 'legacy/work/build/t_same', 'gateway': 'legacy',
              'profile': 'work', 'board': 'build', 'remote': True, 'via': 'shepherd',
              'session': 'chat', 'origin_gateway': 'origin', 'origin_profile': 'default',
              'status': 'unknown', 'unfinished': True, 'stale': True}
    monkeypatch.setattr(activity, '_legacy_remote_links', lambda: [legacy])
    # Release the receipt so only the unrelated historical association remains open.
    origin[0]('done', 4)
    assert plugin.open_card_ids('chat') == [legacy['key']]
    assert core.closeout_key({'batch': ['t_same']}, plugin.read_cards(['t_same'])) is None
    assert core.closeout_key({'batch': [legacy['key']]}, plugin.read_cards([legacy['key']])) is None
    legacy.update(status='blocked', block_kind='needs_input', stale=False)
    cards = plugin.read_cards([legacy['key']])
    assert core.needs_human(cards[legacy['key']])
    assert core.closeout_key({'batch': [legacy['key']]}, cards)


def test_superseded_legacy_unknown_does_not_block_verified_release(origin, monkeypatch):
    from hermes_cli import kanban_session_activity as activity
    legacy = {'id': 't_same', 'key': 'owner/work/build/t_same', 'gateway': 'owner',
              'profile': 'work', 'board': 'build', 'remote': True, 'via': 'shepherd',
              'session': 'chat', 'origin_gateway': 'origin', 'origin_profile': 'default',
              'status': 'unknown', 'unfinished': True, 'stale': True}
    monkeypatch.setattr(activity, '_legacy_remote_links', lambda: [legacy])
    origin[0]('done', 4)
    assert plugin.open_card_ids('chat') == []
    cards = plugin.read_cards([legacy['key']])
    assert core.closeout_key({'batch': [legacy['key']]}, cards)
