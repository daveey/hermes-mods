"""Real replica SQL bookkeeping must not trigger per-chat hydration."""
import copy
import sqlite3
from types import SimpleNamespace

import pytest
from test_feed import make
from test_source_coverage import api_module


def test_replica_pull_carries_observation_without_hydration(tmp_path, monkeypatch):
    links = pytest.importorskip('hermes_cli.kanban_chat_links')
    from hermes_cli import kanban_session_activity as ksa, kanban_db_sync as sync
    from hermes_cli import profiles
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    monkeypatch.setattr(sync, 'sync_config', lambda: {'links_reader': 'replica'})
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', lambda **kw: ([], []))
    path = tmp_path / 'replica.db'
    with sqlite3.connect(path) as c:
        c.executescript('''
            CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, assignee TEXT,
                created_at INTEGER, started_at INTEGER, priority INTEGER, block_kind TEXT,
                human_request TEXT, created_on TEXT);
            CREATE TABLE task_chat_links (uid TEXT, task_id TEXT, chat_gateway TEXT, chat_profile TEXT,
                chat_session TEXT, kind TEXT, released INTEGER, created_at INTEGER);
            CREATE TABLE kanban_sync_state (board_id TEXT PRIMARY KEY, cursor INTEGER,
                gateway_id TEXT, hlc_last TEXT, mode TEXT, applying INTEGER, pending_run_uid TEXT, pulled_at REAL);
            INSERT INTO kanban_sync_state VALUES ('default',0,'local','','shadow',0,NULL,1000);
        ''')
        for n in (1, 2):
            c.execute("INSERT INTO tasks VALUES (?,?,'running','worker',1,NULL,0,NULL,NULL,'remote')",
                      (f'card{n}', f'title{n}'))
            c.execute("INSERT INTO task_chat_links VALUES (?,?,'local','default',?,'created',0,1)",
                      (f'link{n}', f'card{n}', f'S{n}'))
    original = links.replica_cards
    f, r, pushed, clock = make()
    clock['now'] = 1000
    monkeypatch.setattr(links, 'replica_cards', lambda ids, gateway, statuses, **kw:
                        original(ids, gateway, statuses, replicas=[('default', path)], now=clock['now'], **kw))
    adapter = object.__new__(api_module().CoreReaders)
    adapter.ksc = SimpleNamespace(load_identity=lambda: SimpleNamespace(gateway='local'))
    held = {s: {'lineage': {s}, 'refs': set()} for s in ('S1', 'S2')}
    r.external_stamps = lambda: r.stamp
    r.external_capture = adapter.external_capture
    # Optional adapter hooks: the baseline has neither, so RED is behavioral.
    for name in ('external_semantic', 'advance_external'):
        if hasattr(adapter, name):
            setattr(r, name, getattr(adapter, name))
    r.linked = lambda ids: original(ids, 'local', ('running',), replicas=[('default', path)],
                                  now=clock['now'], origin_cards=[])
    r.done = lambda ids: []
    r.card = lambda *args: None
    calls = []
    compute = f.compute
    def counted(session, **kw):
        calls.append(session)
        return compute(session, **kw)
    f.compute = counted
    f.watch(['S1', 'S2'])
    f.tick()
    initial = copy.deepcopy(f.bundles['S1']['bundle'])
    calls.clear()
    # A hydration begun before the pull must not overwrite the carried clock.
    delayed = copy.deepcopy(initial)
    delayed['computed_seq'] = next(f._seq)
    for i in range(1, 11):
        clock['now'] = 1000 + i
        with sqlite3.connect(path) as c:
            c.execute('UPDATE kanban_sync_state SET pulled_at=?', (clock['now'],))
        r.stamp += 1  # unrelated external write exposes the new replica observation
        f.tick()
    assert calls == [], 'unchanged semantic replica input must not hydrate every chat'
    after = f.bundles['S1']['bundle']
    assert after['tasks'][0]['synced_at'] == 1010
    assert after['computed_at'] == initial['computed_at']
    assert after['computed_seq'] == initial['computed_seq']
    f._publish({'S1': delayed})
    assert f.bundles['S1']['bundle']['tasks'][0]['synced_at'] == 1010
    with sqlite3.connect(path) as c:
        c.execute("UPDATE tasks SET title='changed receipt' WHERE id='card1'")
    r.stamp += 1
    f.tick()
    assert calls == ['S1']
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'changed receipt'
    # A real freshness transition is still an input change, not erased bookkeeping.
    calls.clear()
    clock['now'] += 10000
    f.watch(['S1', 'S2'])
    r.stamp += 1
    f.tick()
    assert sorted(calls) == ['S1', 'S2']
