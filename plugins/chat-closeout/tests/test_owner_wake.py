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


def test_waits_older_than_a_week_are_recorded_not_woken(tmp_path):
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
