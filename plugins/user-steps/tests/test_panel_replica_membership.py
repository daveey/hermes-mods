"""Production config -> Remote.membership_capture -> Mirror._panel_stamp.

All SQLite inputs are disposable; only installation paths and the clock are
injected. Membership/configuration/capture are the actual production adapters.
"""
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

import pytest

from test_kanban_mirror import OWNER, fixture
from test_panel_membership import sources, NOW, GW  # noqa: F401
from test_withdrawal import ROOT


@pytest.fixture
def replica_panel(tmp_path, monkeypatch, sources):
    pytest.importorskip('hermes_cli.kanban_chat_links')
    from hermes_cli import kanban_db, kanban_db_sync, kanban_sync_client, profiles
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: OWNER['profile'])
    from hermes_state import SessionDB
    import hermes_constants
    import time
    s = sources
    home = tmp_path / 'home'
    home.mkdir()
    key = home / 'fixture.key'
    key.write_text('disposable-fixture-key')
    (home / 'config.yaml').write_text(
        'kanban:\n  sync:\n    links_reader: replica\n    interval_seconds: 10\n'
        f'    gateway: {GW}\n    url: https://example.com\n    key_file: {key}\n')
    (home / 'kanban.db').touch()  # actual board discovery, not injected board_paths
    monkeypatch.setattr(kanban_db, 'kanban_home', lambda: home)
    monkeypatch.setattr(hermes_constants, 'get_hermes_home', lambda: home)
    monkeypatch.setattr(time, 'time', lambda: NOW)
    assert kanban_db_sync.sync_config()['links_reader'] == 'replica'
    shadow = kanban_sync_client.shadow_path('default')
    shadow.parent.mkdir(parents=True)
    def sql(query, args=()):
        with closing(sqlite3.connect(shadow)) as conn, conn:
            return conn.execute(query, args).fetchall()
    sql('CREATE TABLE kanban_sync_state(pulled_at REAL)')
    sql('INSERT INTO kanban_sync_state VALUES (?)', (NOW,))
    sql('CREATE TABLE task_chat_links(task_id TEXT,chat_gateway TEXT,chat_profile TEXT,chat_session TEXT,kind TEXT,released INTEGER)')
    sql('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,created_on TEXT,title TEXT,'
        'assignee TEXT,created_at INTEGER,started_at INTEGER,priority INTEGER,block_kind TEXT,human_request TEXT)')
    ledger = home / 'kanban' / 'origin-ledger.db'
    s.paths['ledger'].rename(ledger)
    s.paths['ledger'] = ledger
    state = SessionDB(home / 'state.db')
    state.create_session(session_id=OWNER['storedSessionId'], source='cli', model='test')
    root = tmp_path / 'mirror'
    root.mkdir()
    mirror, store, _, _ = fixture(root)
    mirror.remote.gateway = lambda: GW
    monkeypatch.setattr(mirror.core, '_state_db', lambda: home / 'state.db')
    glob = mirror.remote.membership_capture.__func__.__globals__
    view = glob['load_file'](ROOT.parent / 'hub-sync' / 'hubview.py')
    monkeypatch.setattr(view, 'db_path', lambda: s.paths['replica'])
    def link(tid='t_ledger', *, profile=OWNER['profile'], gateway=GW, session=None):
        sql('INSERT INTO task_chat_links VALUES (?,?,?,?,?,0)',
            (tid, gateway, profile, session or OWNER['storedSessionId'], 'created'))
        sql('INSERT OR REPLACE INTO tasks(id,status,created_on,title) VALUES (?,?,?,?)',
            (tid, 'blocked', 'remote', 'Ask'))
    calls = []
    monkeypatch.setattr(mirror.remote, 'reconcile', lambda owner: calls.append(owner))
    # Local boards are independent of this remote-only memo.
    monkeypatch.setattr(mirror, '_reconcile_local', lambda *args: None)
    from hermes_cli.fact_contract import Scope, Fact
    from hermes_cli.fact_store import FactStore
    from hermes_cli.fact_reducers import ReducerEngine
    from hermes_cli.fact_remote_links import LinksFacts, use
    scope = Scope('fixture-install', OWNER['profile'])
    facts = FactStore()
    engine = ReducerEngine(facts, scope)
    handle = SimpleNamespace(v1=SimpleNamespace(snapshot=lambda _: facts.snapshot(scope)))
    binding = LinksFacts(handle, scope, ('fixture-origin',))
    def publish(cards):
        engine.publish([Fact(binding.key, {'cards': cards, 'origin_rows': []},
                             observed_at=NOW, valid_until=NOW + 90, health='fresh')], now=NOW)
    publish([])
    try:
        with use(binding):
            from hermes_cli import profiles, kanban_session_activity
            assert kanban_sync_client.load_identity().gateway == GW
            assert profiles.get_active_profile_name() == OWNER['profile']
            assert kanban_session_activity._remote_links(stale_ok=True) == []
            yield SimpleNamespace(s=s, home=home, sql=sql, link=link, mirror=mirror,
                                  stamp=lambda: mirror._panel_stamp(OWNER), calls=calls,
                                  run=lambda: mirror.reconcile_panel(OWNER), publish=publish)
    finally:
        state.close()


def test_replica_production_adapter_unchanged_chat_hits_memo(replica_panel):
    p = replica_panel
    p.s.obligation()
    p.link()
    stamp = p.stamp()
    assert stamp is not None
    p.run()
    p.run()
    assert len(p.calls) == 1
    assert p.mirror.panel_remote_unchanged
    # Other origin/profile/chat rows and healthy pull refreshes are not semantic.
    p.link('t_other', session='another-chat')
    p.link('t_other_profile', profile='elsewhere')
    p.link('t_other_gateway', gateway='elsewhere')
    p.sql('UPDATE kanban_sync_state SET pulled_at=?', (NOW - 1,))
    assert p.stamp() == stamp
    p.run()
    assert len(p.calls) == 1


@pytest.mark.parametrize('change', ['arrival', 'removal', 'release', 'content'])
def test_shadow_membership_changes_invalidate(replica_panel, change):
    p = replica_panel
    p.s.obligation()
    if change != 'arrival':
        p.link()
    before = p.stamp()
    assert before
    p.run()
    if change == 'arrival':
        p.link()
    elif change == 'removal':
        p.sql('DELETE FROM task_chat_links')
    elif change == 'release':
        p.sql('UPDATE task_chat_links SET released=1')
    else:
        p.sql("UPDATE tasks SET status='running'")
    assert p.stamp() != before
    p.run()
    assert len(p.calls) == 2
    assert not p.mirror.panel_remote_unchanged


@pytest.mark.parametrize('change', ['unbound_arrival', 'removed_task', 'stale', 'future', 'nan',
                                   'owner', 'binding', 'profile', 'unknown', 'missing_table', 'missing_pull'])
def test_replica_unknown_inputs_return_none_and_never_hit(replica_panel, change):
    p = replica_panel
    p.s.obligation()
    p.link()
    assert p.stamp()
    p.run()
    if change == 'unbound_arrival':
        p.link('t_unbound')
    elif change == 'removed_task':
        p.sql('DELETE FROM tasks')
    elif change in ('stale', 'future', 'nan'):
        value = {'stale': NOW - 10000, 'future': NOW + 1, 'nan': float('nan')}[change]
        p.sql('UPDATE kanban_sync_state SET pulled_at=?', (value,))
    elif change == 'owner':
        p.sql("UPDATE tasks SET created_on='wrong-owner'")
    elif change in ('binding', 'profile'):
        raw = p.s.sql('ledger', 'SELECT binding_json FROM origin_obligations')[0][0]
        binding = json.loads(raw)
        if change == 'binding':
            binding['task_record_key'] = 'wrong/default/t_ledger'
        else:
            binding['origin']['profile'] = 'another-profile'
        p.s.sql('ledger', 'UPDATE origin_obligations SET binding_json=?', (json.dumps(binding),))
    elif change == 'unknown':
        p.sql("UPDATE tasks SET status='unknown'")
    elif change == 'missing_table':
        p.sql('DROP TABLE task_chat_links')
    else:
        p.sql('DELETE FROM kanban_sync_state')
    assert p.stamp() is None
    p.run()
    p.run()
    assert len(p.calls) == 3
    assert not p.mirror.panel_remote_unchanged


def test_new_board_is_not_covered_by_old_capture(replica_panel):
    from hermes_cli import kanban_db_sync
    p = replica_panel
    before = p.stamp()
    assert before
    link_boards = p.home / 'kanban' / kanban_db_sync.LINKS_DIR
    link_boards.mkdir()
    (link_boards / 'arrived.db').touch()
    assert p.stamp() is None  # no readable/fresh shadow for the newly discovered board


def test_actual_core_replica_reader_matches_qualified_shadow(replica_panel):
    from hermes_cli import kanban_session_activity, kanban_origin_ledger
    p = replica_panel
    p.s.obligation()
    p.link()
    origin = kanban_origin_ledger.project(p.s.paths['ledger'], replica=p.s.paths['replica'],
                                        gateway=GW, profile=OWNER['profile'], now=NOW)
    assert origin and origin[0]['status'] == 'blocked'
    p.publish(origin)
    cards = kanban_session_activity._remote_cards([OWNER['storedSessionId']], ('blocked', 'triage'), True)
    assert [(c['gateway'], c['profile'], c['board'], c['id']) for c in cards] == [
        ('remote', 'default', 'default', 't_ledger')]
    assert cards[0]['source'] == 'replica'
    assert not cards[0]['stale']
    assert p.stamp()


def test_retained_display_publication_is_also_semantic(replica_panel):
    from hermes_cli import kanban_origin_ledger
    p = replica_panel
    p.s.obligation()
    p.link()
    p.run()  # a healthy but older empty facts projection
    p.run()
    assert len(p.calls) == 1
    before = p.stamp()
    origin = kanban_origin_ledger.project(p.s.paths['ledger'], replica=p.s.paths['replica'],
                                        gateway=GW, profile=OWNER['profile'], now=NOW)
    p.publish(origin)  # no source DB change: only the retained display caught up
    assert p.stamp() not in (None, before)
    p.run()
    assert len(p.calls) == 2
    p.run()
    assert len(p.calls) == 2


@pytest.mark.parametrize('change', ['identity', 'identity_unavailable', 'active_profile',
                                   'display_unknown', 'display_malformed', 'display_expired'])
def test_effective_production_reader_fences(replica_panel, monkeypatch, change):
    p = replica_panel
    assert p.stamp()
    if change == 'identity':
        p.mirror.remote.gateway = lambda: 'wrong-gateway'
    elif change == 'identity_unavailable':
        (p.home / 'fixture.key').unlink()
    elif change == 'active_profile':
        from hermes_cli import profiles
        monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'other')
    elif change == 'display_expired':
        import time
        monkeypatch.setattr(time, 'time', lambda: NOW + 91)
    else:
        p.publish([{'id': 't_ledger', 'session': OWNER['storedSessionId'], 'status': 'unknown'}]
                  if change == 'display_unknown' else False)
    assert p.stamp() is None


@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('table,column', [('tasks', 'assignee'), ('task_chat_links', 'kind')])
def test_incomplete_shadow_schema_is_unknown_even_when_empty(replica_panel, populated, table, column):
    p = replica_panel
    if populated:
        p.s.obligation()
        p.link()
    assert p.stamp()
    p.sql(f'ALTER TABLE {table} DROP COLUMN {column}')
    assert p.stamp() is None
