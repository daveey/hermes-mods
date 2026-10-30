"""Owner wake: an agent-owned park wakes the owning chat once per premise; orphaned chats route to the supervisor."""
import importlib.util
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_owner_wake_tests', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
ow = plugin.owner_wake

NOW = 1_793_140_000.0


def card(key, session='chat', premise='blocked:transient:aaa', *, agent=True, certain=True, state='waiting',
         gateway=None, title='T'):
    return {'session': session, 'key': key, 'id': key.rsplit('/', 1)[-1], 'title': title, 'gateway': gateway,
            'wait': {'agent_owned': agent, 'agent_owned_certain': certain, 'wait_state': state,
                     'premise': premise if agent else None, 'waiting_on': 'an agent to clear a transient block',
                     'waiting_since': NOW - 60}}


class Harness:
    def __init__(self, tmp_path, *, chats=None, results=('started',)):
        self.log = ow.WakeLog(tmp_path / 'owner-wake.db')
        self.chats = {'chat': {'archived': False, 'end_reason': None, 'source': 'desktop'}} if chats is None else chats
        self.results = list(results)
        self.wakes = []
        self.routes = []

    def wake(self, sid, text, **kw):
        self.wakes.append((sid, text, kw))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    def supervise(self, cards, sid, why):
        self.routes.append(([c['key'] for c in cards], sid, why))
        return f't_sup{len(self.routes)}'

    def tick(self, cards, now=NOW, last_user=0.0):
        return ow.tick(cards=cards, wake=self.wake, log=self.log, chat_info=self.chats.get,
                       supervise=self.supervise, last_user=lambda _sid: last_user, now=now)


def test_wakes_owning_chat_once_per_premise(tmp_path):
    h = Harness(tmp_path)
    out = h.tick([card('hub/default/default/t_159f0063', gateway='hub', title='self-hosted release route')])
    assert [o['result'] for o in out] == ['started'] and len(h.wakes) == 1
    sid, text, kw = h.wakes[0]
    assert sid == 'chat' and '[owner-wake]' in text and 't_159f0063' in text and 'on hub' in text
    assert kw['display_kind'] == 'kanban_notice' and kw['display_metadata']['owner_wake'] is True
    # Same card, same premise (a re-park on the same reason): never again.
    assert h.tick([card('hub/default/default/t_159f0063', gateway='hub')], now=NOW + 7200) == []
    assert len(h.wakes) == 1
    # A new premise on the same card is a new wake.
    assert h.tick([card('hub/default/default/t_159f0063', premise='triage:bbb')], now=NOW + 7300)
    assert len(h.wakes) == 2


def test_one_wake_covers_every_due_card_of_a_chat(tmp_path):
    h = Harness(tmp_path)
    h.tick([card('default/t_a'), card('default/t_b', premise='triage:x')])
    assert len(h.wakes) == 1
    assert sorted(h.wakes[0][2]['display_metadata']['task_ids']) == ['default/t_a', 'default/t_b']


def test_rate_limit_per_chat_per_hour(tmp_path):
    h = Harness(tmp_path)
    for i in range(ow.MAX_WAKES_PER_CHAT_HOUR + 2):
        h.tick([card(f'default/t_{i}', premise=f'p{i}')], now=NOW + i * 60)
    assert len(h.wakes) == ow.MAX_WAKES_PER_CHAT_HOUR
    # The capped cards were not marked fired: they wake once the hour has passed.
    h.tick([card(f'default/t_{ow.MAX_WAKES_PER_CHAT_HOUR}', premise=f'p{ow.MAX_WAKES_PER_CHAT_HOUR}')],
           now=NOW + 3700)
    assert len(h.wakes) == ow.MAX_WAKES_PER_CHAT_HOUR + 1


@pytest.mark.parametrize('chat,why', [
    ({'archived': True, 'end_reason': None, 'source': 'desktop'}, 'archived'),
    ({'archived': False, 'end_reason': 'startup_orphan_reap', 'source': 'desktop'}, 'reaped (startup_orphan_reap)'),
    (None, 'gone'),
])
def test_archived_reaped_or_gone_chat_routes_to_supervisor(tmp_path, chat, why):
    h = Harness(tmp_path, chats={} if chat is None else {'chat': chat})
    out = h.tick([card('hub/default/default/t_4c3070a4'), card('hub/default/default/t_159f0063', premise='p2')])
    assert h.wakes == []
    assert h.routes == [(['hub/default/default/t_4c3070a4', 'hub/default/default/t_159f0063'], 'chat', why)]
    assert out[0]['route'] == 'supervisor' and out[0]['supervisor_task'] == 't_sup1'
    h.tick([card('hub/default/default/t_4c3070a4')], now=NOW + 60)
    assert len(h.routes) == 1  # once per premise on this route too


def test_supervisor_routes_are_capped_per_hour(tmp_path):
    h = Harness(tmp_path, chats={})
    for i in range(ow.MAX_SUPERVISOR_ROUTES_HOUR + 3):
        h.tick([card(f'default/t_{i}', session=f'gone{i}', premise=f'p{i}')], now=NOW + i)
    assert len(h.routes) == ow.MAX_SUPERVISOR_ROUTES_HOUR


def test_missing_on_wake_routes_to_supervisor(tmp_path):
    h = Harness(tmp_path, results=('missing',))
    h.tick([card('default/t_a')])
    assert len(h.wakes) == 1 and h.routes == [(['default/t_a'], 'chat', 'gone')]


def test_busy_wake_releases_the_claim_for_a_later_tick(tmp_path):
    h = Harness(tmp_path, results=('busy', 'started'))
    assert h.tick([card('default/t_a')])[0]['result'] == 'busy'
    assert h.tick([card('default/t_a')], now=NOW + 30)[0]['result'] == 'started'
    assert len(h.wakes) == 2 and h.tick([card('default/t_a')], now=NOW + 60) == []


def test_user_typing_defers_without_consuming_the_premise(tmp_path):
    h = Harness(tmp_path)
    assert h.tick([card('default/t_a')], last_user=NOW - 10) == []
    assert h.tick([card('default/t_a')], now=NOW + 300, last_user=NOW - 10)
    assert len(h.wakes) == 1


def test_not_agent_owned_or_unstalled_legacy_waits_do_not_wake(tmp_path):
    h = Harness(tmp_path)
    assert h.tick([card('default/t_ci', agent=False),
                   card('default/t_probe', premise='scheduled:probe:x', certain=False, state='waiting')]) == []
    assert h.tick([card('default/t_probe', premise='scheduled:probe:x', certain=False, state='stalled')])
    assert len(h.wakes) == 1


def test_worker_sessions_are_never_woken(tmp_path):
    h = Harness(tmp_path, chats={'chat': {'archived': False, 'end_reason': None, 'source': 'kanban'}})
    assert h.tick([card('default/t_a')]) == [] and h.wakes == [] and h.routes == []


def test_no_backend_means_no_chat_wake_but_orphans_still_route(tmp_path):
    h = Harness(tmp_path, chats={'live': {'archived': False, 'end_reason': None, 'source': 'desktop'}})
    out = ow.tick(cards=[card('default/t_a', session='live'), card('default/t_b', session='gone')], wake=None,
                  log=h.log, chat_info=h.chats.get, supervise=h.supervise, last_user=lambda _s: 0.0, now=NOW)
    assert [o['session_id'] for o in out] == ['gone'] and h.wakes == []


def test_two_backends_claim_one_premise_once(tmp_path):
    a, b = Harness(tmp_path), Harness(tmp_path)
    a.tick([card('default/t_a')])
    b.tick([card('default/t_a')])
    assert len(a.wakes) + len(b.wakes) == 1


def test_chat_info_follows_the_compression_tip(tmp_path):
    db = tmp_path / 'state.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, archived INTEGER, end_reason TEXT, source TEXT)')
        c.executemany('INSERT INTO sessions VALUES (?,?,?,?)', [
            ('old', 0, 'compression', 'desktop'), ('tip', 0, None, 'desktop'), ('arch', 1, None, 'desktop')])
    info = ow.chat_info_from(lambda: [db], lambda sid: 'tip' if sid == 'old' else None)
    assert info('old') == {'archived': False, 'end_reason': None, 'source': 'desktop'}
    assert info('arch')['archived'] is True and info('nope') is None


# ---- integration with the fork core (HERMES_CORE_SOURCE): real board, real parked_waits, real supervisor card ----
core_src = os.environ.get('HERMES_CORE_SOURCE')


@pytest.mark.skipif(not core_src, reason='set HERMES_CORE_SOURCE to the fork checkout')
def test_integration_parked_card_wakes_chat_and_archived_chat_gets_a_supervisor_card(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, core_src)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'kanban.db'))
    # Isolated fixture board only; inherited worker DB/identity must never reach it.
    for key in ('HERMES_KANBAN_TASK', 'HERMES_DELEGATED_CHILD_CONTEXT', 'HERMES_DELEGATED_CHILD_OWNER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    monkeypatch.setenv('CHAT_CLOSEOUT_OWNER_WAKE_DB', str(tmp_path / 'ow.db'))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli.kanban_db_connect import connect_closing
    ksa._reset_cache_for_tests()
    with connect_closing() as conn:
        live = kb.create_task(conn, title='scratch live', session_id='s_live')
        orphan = kb.create_task(conn, title='scratch orphan', session_id='s_arch')
        for tid in (live, orphan):
            conn.execute("UPDATE tasks SET status='blocked', block_kind='capability' WHERE id=?", (tid,))
            kb._append_event(conn, tid, 'blocked', {'kind': 'capability', 'reason': 'hub role lacks ec2:Describe*'})
        conn.commit()
    chats = {'s_live': {'archived': False, 'end_reason': None, 'source': 'desktop'},
             's_arch': {'archived': True, 'end_reason': None, 'source': 'desktop'}}
    woke = []
    out = plugin.owner_wake_tick(wake=lambda sid, text, **kw: woke.append(sid) or 'started',
                                 chat_info=chats.get, last_user=lambda _s: 0.0)
    assert woke == ['s_live']
    routed = [o for o in out if o['route'] == 'supervisor']
    assert len(routed) == 1 and routed[0]['cards'] == [f'default/{orphan}']
    with connect_closing() as conn:
        sup = kb.get_task(conn, routed[0]['supervisor_task'])
    assert sup.status == 'ready' and sup.assignee == 'default' and orphan in (sup.body or '')
    # Second pass: nothing new.
    assert plugin.owner_wake_tick(wake=lambda *a, **k: 'started', chat_info=chats.get, last_user=lambda _s: 0.0) == []
    ksa._reset_cache_for_tests()


# ---- review fixes ----------------------------------------------------------------------------------------------
def test_gateway_wide_caps_bound_a_backlog(tmp_path):
    chats = {f'c{i}': {'archived': False, 'end_reason': None, 'source': 'desktop'} for i in range(12)}
    h = Harness(tmp_path, chats=chats)
    h.tick([card(f'default/t_{i}', session=f'c{i}', premise=f'p{i}') for i in range(12)])
    assert len(h.wakes) == ow.MAX_CHAT_WAKES_PER_TICK
    for n in range(1, 6):
        h.tick([card(f'default/t_{i}', session=f'c{i}', premise=f'p{i}') for i in range(12)], now=NOW + n * 30)
    assert len(h.wakes) == ow.MAX_CHAT_WAKES_HOUR


def test_waits_older_than_max_age_are_recorded_not_woken(tmp_path):
    h = Harness(tmp_path)
    old = card('default/t_old')
    old['wait']['waiting_since'] = NOW - ow.MAX_AGE_S - 1
    assert h.tick([old]) == [] and h.wakes == []
    assert ('default/t_old', 'blocked:transient:aaa', 'chat', 'too_old', 'none', None) in h.log.rows()
    assert h.tick([old], now=NOW + 60) == [] and h.wakes == []


def test_a_claim_left_by_a_dead_backend_expires(tmp_path):
    h = Harness(tmp_path)
    assert h.log.claim('default/t_a', 'blocked:transient:aaa', 'chat', NOW - ow.CLAIM_TTL_S - 1)
    assert h.tick([card('default/t_a')]) and len(h.wakes) == 1


def test_one_chat_across_its_compression_lineage_is_one_group(tmp_path):
    h = Harness(tmp_path, chats={'tip': {'archived': False, 'end_reason': None, 'source': 'desktop'}})
    out = ow.tick(cards=[card('default/t_a', session='root'), card('default/t_b', session='tip', premise='p2')],
                  wake=h.wake, log=h.log, chat_info=h.chats.get, supervise=h.supervise, last_user=lambda _s: 0.0,
                  now=NOW, canonical=lambda sid: 'tip')
    assert len(h.wakes) == 1 and h.wakes[0][0] == 'tip' and len(out[0]['cards']) == 2


# ---- second review ------------------------------------------------------------------------------------------------
def test_an_unreadable_state_store_is_unknown_not_gone(tmp_path):
    good = tmp_path / 'state.db'
    with sqlite3.connect(good) as c:
        c.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, archived INTEGER, end_reason TEXT, source TEXT)')
    bad = tmp_path / 'locked.db'
    bad.write_text('not a database')
    info = ow.chat_info_from(lambda: [good, bad], lambda sid: None)
    assert info('chat') == ow.UNKNOWN
    assert ow.chat_info_from(lambda: [good], lambda sid: None)('chat') is None  # every store read cleanly: gone
    h = Harness(tmp_path)
    out = ow.tick(cards=[card('default/t_a')], wake=h.wake, log=h.log, chat_info=info, supervise=h.supervise,
                  last_user=lambda _s: 0.0, now=NOW)
    assert out == [] and h.wakes == [] and h.routes == [] and h.log.rows() == []


def test_refused_wake_backs_off_for_the_claim_ttl_and_counts_toward_the_tick_cap(tmp_path):
    chats = {f'c{i}': {'archived': False, 'end_reason': None, 'source': 'desktop'} for i in range(6)}
    h = Harness(tmp_path, chats=chats, results=('refused',))
    h.tick([card(f'default/t_{i}', session=f'c{i}', premise=f'p{i}') for i in range(6)])
    assert len(h.wakes) == ow.MAX_CHAT_WAKES_PER_TICK  # failed attempts use up the tick
    h.tick([card('default/t_0', session='c0', premise='p0')], now=NOW + 30)
    assert len(h.wakes) == ow.MAX_CHAT_WAKES_PER_TICK  # backing off, not retried every tick
    h.results = ['started']
    h.tick([card('default/t_0', session='c0', premise='p0')], now=NOW + ow.CLAIM_TTL_S + 1)
    assert len(h.wakes) == ow.MAX_CHAT_WAKES_PER_TICK + 1


def test_bookkeeping_failure_after_a_started_wake_does_not_raise(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    monkeypatch.setattr(ow.time, 'sleep', lambda _s: None)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(h.log, 'note_wake', boom)
    out = h.tick([card('default/t_a')])
    assert out[0]['result'] == 'started' and len(h.wakes) == 1
    assert h.tick([card('default/t_a')], now=NOW + 60) == []  # finish() still recorded the premise


# ---- reverse dependencies: never "safe to close" while another card waits on it --------------------------------
def test_wake_and_supervisor_text_name_dependents_and_forbid_close(tmp_path):
    chats = {'live': {'archived': False, 'end_reason': None, 'source': 'desktop'},
             'arch': {'archived': True, 'end_reason': None, 'source': 'desktop'}}
    h = Harness(tmp_path, chats=chats)
    bodies = []
    deps = {'t_159f0063': ['default/t_b11f0591 on hub'], 't_x': []}
    ow.tick(cards=[card('default/t_159f0063', session='arch'), card('default/t_x', session='live', premise='q')],
            wake=h.wake, log=h.log, chat_info=chats.get, last_user=lambda _s: 0.0, now=NOW,
            supervise=lambda cs, sid, why: bodies.append(ow.supervisor_body(cs, sid, why)) or 't_sup',
            dependents=lambda c: deps[c['id']])
    assert len(bodies) == 1
    assert 'still needed by default/t_b11f0591 on hub: do NOT close or archive it' in bodies[0]
    assert 'reverse dependencies on every gateway' in bodies[0] and 'never makes a card safe' in bodies[0]
    # The live chat's wake carries the rule too; a card nobody waits on gets no "still needed" note.
    text = h.wakes[0][1]
    assert 'reverse dependencies on every gateway' in text and 'still needed by' not in text


def test_unreadable_dependents_say_check_before_closing(tmp_path):
    h = Harness(tmp_path)

    def boom(_c):
        raise sqlite3.OperationalError('locked')
    ow.tick(cards=[card('default/t_a')], wake=h.wake, log=h.log, chat_info=h.chats.get, supervise=h.supervise,
            last_user=lambda _s: 0.0, now=NOW, dependents=boom)
    assert 'unreadable here: check before closing' in h.wakes[0][1]


def _board(path, tasks, links=(), events=(), checks=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript('CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT);'
                    'CREATE TABLE task_links (parent_id TEXT, child_id TEXT);'
                    'CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT);'
                    'CREATE TABLE task_wake_checks (task_id TEXT PRIMARY KEY, cmd TEXT);')
    c.executemany('INSERT INTO tasks VALUES (?,?)', tasks)
    c.executemany('INSERT INTO task_links VALUES (?,?)', links)
    c.executemany('INSERT INTO task_events (task_id, kind, payload) VALUES (?,?,?)', events)
    c.executemany('INSERT INTO task_wake_checks VALUES (?,?)', checks)
    c.commit()
    c.close()
    return path


def test_dependents_from_finds_children_park_reasons_wake_checks_and_remote_cards(tmp_path):
    tid = 't_159f0063'
    default = _board(tmp_path / 'kanban.db', [
        (tid, 'triage'), ('t_child', 'todo'), ('t_donechild', 'done'), ('t_parked', 'scheduled'),
        ('t_moved_on', 'scheduled'), ('t_lookalike', 'blocked'), ('t_closed_park', 'archived')],
        links=[(tid, 't_child'), (tid, 't_donechild')],
        events=[('t_parked', 'scheduled', json.dumps({'reason': f'waiting on MacBook acceptance in {tid}.'})),
                ('t_moved_on', 'blocked', json.dumps({'reason': f'needs {tid}'})),
                ('t_moved_on', 'scheduled', json.dumps({'reason': 'now waits on CI run 42'})),
                ('t_lookalike', 'blocked', json.dumps({'reason': f'needs {tid}1'})),
                ('t_closed_park', 'blocked', json.dumps({'reason': f'needs {tid}'}))])
    other = _board(tmp_path / 'boards' / 'ops' / 'kanban.db', [('t_probe', 'scheduled')],
                   checks=[('t_probe', f'hermes kanban wait-check task {tid} --gateway macbook')])
    # The hub replica in its real shape: the incident's hub card was created by a hub chat (no origin binding to
    # this gateway) and named the card only in its last run's park summary.
    hub = _replica(tmp_path / 'hub.db', [
        {'id': 't_b11f0591', 'board': 'default', 'gateway': 'hub', 'status': 'scheduled', 'origin_bindings': [],
         'parents': ['t_8c0b6765'], 'body': 'context',
         'run': {'outcome': 'scheduled', 'summary': f'SCHEDULED_UNTIL=... waiting on MacBook acceptance in {tid}.'}},
        {'id': 't_kid', 'board': 'ops', 'gateway': 'mini', 'status': 'todo', 'parents': [tid]},
        {'id': 't_bodyonly', 'board': 'default', 'gateway': 'hub', 'status': 'ready', 'body': f'see {tid} history'},
        {'id': tid, 'board': 'default', 'gateway': 'macbook', 'status': 'triage', 'run': {'summary': tid}},
        {'id': 't_old', 'board': 'default', 'gateway': 'hub', 'status': 'done', 'parents': [tid]},
        {'id': 't_parked', 'board': 'default', 'gateway': 'macbook', 'status': 'scheduled',
         'run': {'summary': f'waiting on {tid}'}}])  # this gateway's own card, already found locally
    reads = []

    def replica_path():
        reads.append(1)
        return hub
    deps = ow.dependents_from(lambda: [default, other], replica_path)
    assert deps({'id': tid, 'key': f'default/{tid}'}) == [
        'default/t_child', 'default/t_parked', 'ops/t_probe', 'default/t_b11f0591 on hub', 'ops/t_kid on mini']
    assert deps({'key': 'default/t_nothing'}) == []
    assert reads == [1]  # the replica is read once per finder


def _replica(path, tasks):
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, data TEXT)')
    c.executemany('INSERT INTO replica VALUES (?,?,?,?)',
                  [(t['gateway'], 'tasks', f"{t['board']}/{t['id']}", json.dumps(t)) for t in tasks])
    c.commit()
    c.close()
    return path


def test_unreadable_board_or_replica_is_never_no_dependents(tmp_path):
    garbage = tmp_path / 'boards' / 'x' / 'kanban.db'
    garbage.parent.mkdir(parents=True)
    garbage.write_bytes(b'not a database' * 100)
    deps = ow.dependents_from(lambda: [garbage], lambda: None)
    assert deps({'id': 't_a'}) == [ow.UNREADABLE]

    def boom():
        raise OSError('hub state unreadable')
    assert ow.dependents_from(lambda: [], boom)({'id': 't_a'}) == [ow.UNREADABLE]
    assert ow.dependents_from(lambda: [], lambda: None)({'id': 't_a'}) == []  # no replica on this host: nothing


@pytest.mark.skipif(not core_src, reason='set HERMES_CORE_SOURCE to the fork checkout')
def test_integration_supervisor_card_names_a_card_parked_on_the_orphan(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, core_src)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_KANBAN_DB', str(tmp_path / 'kanban.db'))
    # Isolated fixture board only; inherited worker DB/identity must never reach it.
    for key in ('HERMES_KANBAN_TASK', 'HERMES_DELEGATED_CHILD_CONTEXT', 'HERMES_DELEGATED_CHILD_OWNER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    monkeypatch.setenv('CHAT_CLOSEOUT_OWNER_WAKE_DB', str(tmp_path / 'ow.db'))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli.kanban_db_connect import connect_closing
    ksa._reset_cache_for_tests()
    with connect_closing() as conn:
        orphan = kb.create_task(conn, title='scratch orphan', session_id='s_arch')
        live = kb.create_task(conn, title='scratch live', session_id='s_live')
        waiter = kb.create_task(conn, title='scratch waiter')
        for tid in (orphan, live):
            conn.execute("UPDATE tasks SET status='blocked', block_kind='capability' WHERE id=?", (tid,))
            kb._append_event(conn, tid, 'blocked', {'kind': 'capability', 'reason': 'needs the MacBook DOM read'})
        conn.execute("UPDATE tasks SET status='scheduled' WHERE id=?", (waiter,))
        kb._append_event(conn, waiter, 'scheduled', {'reason': f'SCHEDULED_UNTIL=2026-11-01T00:00:00Z waiting on {orphan}'})
        conn.commit()
    (tmp_path / 'hub-state').mkdir()
    _replica(tmp_path / 'hub-state' / 'hub.db', [
        {'id': 't_hubwaiter', 'board': 'default', 'gateway': 'hub', 'status': 'scheduled',
         'run': {'summary': f'waiting on acceptance in {live}'}}])
    woke = []
    out = plugin.owner_wake_tick(wake=lambda sid, text, **k: woke.append((sid, text)) or 'started',
                                 last_user=lambda _s: 0.0,
                                 chat_info={'s_arch': {'archived': True, 'end_reason': None, 'source': 'desktop'},
                                            's_live': {'archived': False, 'end_reason': None, 'source': 'desktop'}}.get)
    routed = [o for o in out if o['route'] == 'supervisor']
    assert len(routed) == 1
    with connect_closing() as conn:
        sup = kb.get_task(conn, routed[0]['supervisor_task'])
    assert f'still needed by default/{waiter}' in sup.body and 'do NOT close or archive' in sup.body
    # The live chat's wake (real tick, real core reads) names the hub card from the replica.
    assert [s for s, _ in woke] == ['s_live'] and 'still needed by default/t_hubwaiter on hub' in woke[0][1]
    ksa._reset_cache_for_tests()


# ---- ended worker / cron owners (t_01a18ba39f9cb81a) -------------------------------------------------------------
# On 10-28..10-30 four hub cards sat in triage 1.5-3 days: their owners were a kanban-worker session and a cron run
# that had ended, and SKIP_SOURCES skipped them for good (no wake, no supervisor).
@pytest.mark.parametrize('source,end_reason', [('kanban', 'cli_close'), ('cron', 'cron_complete')])
def test_an_ended_worker_or_cron_owner_routes_to_the_supervisor_once(tmp_path, source, end_reason):
    h = Harness(tmp_path, chats={'chat': {'archived': False, 'end_reason': end_reason, 'source': source}})
    out = h.tick([card('default/t_4c0d5006', premise='triage:x')])
    assert h.wakes == []
    assert h.routes == [(['default/t_4c0d5006'], 'chat', f'an ended {source} session ({end_reason})')]
    assert out[0]['route'] == 'supervisor' and out[0]['why'] == f'an ended {source} session ({end_reason})'
    assert h.tick([card('default/t_4c0d5006', premise='triage:x')], now=NOW + 60) == []
    assert len(h.routes) == 1


def test_an_archived_worker_session_routes_to_the_supervisor(tmp_path):
    h = Harness(tmp_path, chats={'chat': {'archived': True, 'end_reason': None, 'source': 'kanban'}})
    h.tick([card('default/t_a')])
    assert h.wakes == [] and h.routes == [(['default/t_a'], 'chat', 'an ended kanban session (archived)')]


@pytest.mark.parametrize('source', ['kanban', 'cron', 'webhook', 'subagent'])
def test_a_live_worker_or_cron_session_is_still_skipped(tmp_path, source):
    h = Harness(tmp_path, chats={'chat': {'archived': False, 'end_reason': None, 'source': source}})
    old = card('default/t_old', premise='p_old')
    old['wait']['waiting_since'] = NOW - 60 * 3600
    assert h.tick([card('default/t_a'), old]) == [] and h.wakes == [] and h.routes == []


def test_a_60h_old_wait_with_an_ended_owner_is_routed_even_after_a_too_old_record(tmp_path):
    h = Harness(tmp_path, chats={'chat': {'archived': False, 'end_reason': 'cron_complete', 'source': 'cron'},
                                 'w': {'archived': False, 'end_reason': 'cli_close', 'source': 'kanban'}})
    fresh_old = card('default/t_d5d83916', premise='triage:a')
    fresh_old['wait']['waiting_since'] = NOW - 60 * 3600
    recorded = card('default/t_01a180e08e629ea2', session='w', premise='triage:b')
    recorded['wait']['waiting_since'] = NOW - 60 * 3600
    # What the old code wrote for it: a too_old row, which used to strand the card for good.
    assert h.log.claim('default/t_01a180e08e629ea2', 'triage:b', 'w', NOW - 3600)
    h.log.finish('default/t_01a180e08e629ea2', 'triage:b', 'too_old', 'none')
    out = h.tick([fresh_old, recorded])
    assert h.wakes == []
    assert sorted(h.routes) == [(['default/t_01a180e08e629ea2'], 'w', 'an ended kanban session (cli_close)'),
                                (['default/t_d5d83916'], 'chat', 'an ended cron session (cron_complete)')]
    assert {o['route'] for o in out} == {'supervisor'}
    rows = {r[0]: r for r in h.log.rows()}
    assert rows['default/t_01a180e08e629ea2'][3:5] == ('routed', 'supervisor')
    assert rows['default/t_d5d83916'][3:5] == ('routed', 'supervisor')
    assert h.tick([fresh_old, recorded], now=NOW + 60) == [] and len(h.routes) == 2


def test_an_old_wait_of_an_archived_chat_is_routed_not_recorded(tmp_path):
    h = Harness(tmp_path, chats={'chat': {'archived': True, 'end_reason': None, 'source': 'desktop'}})
    old = card('default/t_old')
    old['wait']['waiting_since'] = NOW - 60 * 3600
    h.tick([old])
    assert h.routes == [(['default/t_old'], 'chat', 'archived')]


def test_a_60h_old_wait_with_a_live_chat_owner_is_still_recorded_too_old(tmp_path):
    h = Harness(tmp_path)
    old = card('default/t_old')
    old['wait']['waiting_since'] = NOW - 60 * 3600
    assert h.tick([old]) == [] and h.wakes == [] and h.routes == []
    assert ('default/t_old', 'blocked:transient:aaa', 'chat', 'too_old', 'none', None) in h.log.rows()
    # A fresh card of the same live chat still wakes it, and the old one stays recorded.
    out = h.tick([old, card('default/t_new', premise='p_new')], now=NOW + 60)
    assert [o['cards'] for o in out] == [['default/t_new']] and len(h.wakes) == 1


def test_old_orphan_routes_still_respect_the_hourly_supervisor_cap(tmp_path):
    chats = {f'w{i}': {'archived': False, 'end_reason': 'cli_close', 'source': 'kanban'} for i in range(10)}
    h = Harness(tmp_path, chats=chats)
    cards = []
    for i in range(10):
        c = card(f'default/t_{i}', session=f'w{i}', premise=f'p{i}')
        c['wait']['waiting_since'] = NOW - 60 * 3600
        cards.append(c)
    h.tick(cards)
    assert len(h.routes) == ow.MAX_SUPERVISOR_ROUTES_HOUR
    h.tick(cards, now=NOW + 3700)  # the rest route in the next window, not recorded too_old
    assert len(h.routes) == 10
