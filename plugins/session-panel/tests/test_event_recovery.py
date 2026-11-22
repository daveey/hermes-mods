"""Without a timer sweep, a source event must not be lost: lineage changes and caught failures recover."""
import importlib.util
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

from test_feed import Readers, make
import test_feed


def api_module():
    name = 'session_panel_event_recovery_api'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def idle(f, t, seconds=120):
    """The serve's idle loop: beats only, a pass when one says it is due. Returns the passes run."""
    passes = 0
    for _ in range(seconds):
        t['now'] += 1
        f.beat()
        if f._wake.is_set():
            passes += 1
            f.tick()
    return passes


def lineage_feed(tmp_path, rows):
    """Real SQLite lineage and the production stamp/lineage readers; everything else controlled."""
    api = api_module()
    path = tmp_path / 'state.db'
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute('create table sessions(id text, parent_session_id text, outcome_json text)')
    c.executemany('insert into sessions values (?,?,NULL)', rows)
    c.commit()
    cr = object.__new__(api.CoreReaders)
    cr._local = threading.local()
    cr.state_db = lambda: path
    cr.conn = lambda p: c if p == path else None
    r = Readers()
    r.lineage = lambda s: api.CoreReaders.lineage(cr, s)
    r.panel_stamps = cr.panel_stamps
    r.panel_files_stamp = lambda: api.CoreReaders._stat(path)
    r.board_stamps = lambda: 0
    t = {'now': 1000.0}
    f = test_feed.feedmod.Feed(r, emit=lambda x: None, clock=lambda: t['now'])
    f.watch(['S1'])
    f.tick()
    # Prime the held lineage's stamps with a write that changes none of them.
    c.execute("insert into sessions values ('unrelated', NULL, NULL)")
    c.commit()
    r.stamp += 1
    f.tick()
    return f, r, c, t


def write(c, r, *statements):
    for sql in statements:
        c.execute(sql)
    c.commit()
    r.stamp += 1


def test_parent_detached_and_deleted_reaches_the_bundle_on_the_event(tmp_path):
    f, r, c, t = lineage_feed(tmp_path, [('S1', 'P'), ('P', None)])
    assert f.bundles['S1']['bundle']['lineage'] == ['S1', 'P']
    write(c, r, "update sessions set parent_session_id=NULL where parent_session_id='P'", "delete from sessions where id='P'")
    f.tick()
    assert f.bundles['S1']['bundle']['lineage'] == r.lineage('S1') == ['S1']
    assert idle(f, t) == 0, 'the event pass repaired it: nothing is left for a timer'


def test_parent_attached_reaches_the_bundle_on_the_event(tmp_path):
    f, r, c, t = lineage_feed(tmp_path, [('S1', None), ('P', None)])
    r.links['P'] = [{'id': 't_parent1', 'status': 'running', 'board': 'default'}]
    assert f.bundles['S1']['bundle']['lineage'] == ['S1']
    write(c, r, "update sessions set parent_session_id='P' where id='S1'")
    f.tick()
    b = f.bundles['S1']['bundle']
    assert b['lineage'] == ['S1', 'P'] and 't_parent1' in [x['id'] for x in b['tasks']]


def test_deleted_ancestor_alone_invalidates_the_held_lineage(tmp_path):
    f, r, c, t = lineage_feed(tmp_path, [('S1', 'P'), ('P', 'G'), ('G', None)])
    assert f.bundles['S1']['bundle']['lineage'] == ['S1', 'P', 'G']
    held = f.bundles['S1']['bundle']
    write(c, r, "update sessions set parent_session_id=NULL where id='P'", "delete from sessions where id='G'")
    f.tick()
    assert f.bundles['S1']['bundle'] is not held
    assert f.bundles['S1']['bundle']['lineage'] == ['S1', 'P']


def test_unrelated_state_writes_never_rehydrate_an_unchanged_lineage(tmp_path):
    f, r, c, t = lineage_feed(tmp_path, [('S1', 'P'), ('P', None)])
    held = f.bundles['S1']['bundle']
    for i in range(10):
        write(c, r, f"insert into sessions values ('other{i}', NULL, NULL)")
        f.tick()
    assert f.bundles['S1']['bundle'] is held


def test_one_caught_hydration_failure_recovers_without_another_write():
    f, r, pushed, t = make()
    f.watch(['S1'])
    f.tick()
    r.links['S1'] = [dict(r.links['S1'][0], title='new')]
    r.events = [('t_aaaaaa1', 'S1')]
    r.stamp += 1
    linked = r.linked
    r.linked = lambda ids: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked'))
    f.tick()  # caught inside the pass: the event's cursors are consumed
    assert 'S1' in f.failures and 'S1' in f._retry
    r.linked = linked
    assert idle(f, t, 5) == 1
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'new'
    assert not f.failures and 'S1' not in f._retry and f._due(t['now']) == (None, None)
    assert idle(f, t) == 0, 'recovered: no further pass without a source event'


def test_cold_hydration_failure_recovers_without_another_request():
    f, r, pushed, t = make()
    linked = r.linked
    r.linked = lambda ids: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked'))
    f.watch(['S1'])
    f.tick()
    assert 'S1' not in f.bundles and 'S1' in f.failures
    r.linked = linked
    assert idle(f, t, 5) == 1
    assert 'S1' in f.bundles and not f.failures


def test_persistent_hydration_failure_is_bounded_and_resumes_on_the_next_event():
    f, r, pushed, t = make()
    f.watch(['S1'])
    f.tick()
    r.stamp += 1
    r.events = [('t_aaaaaa1', 'S1')]
    linked = r.linked
    r.linked = lambda ids: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked'))
    f.tick()
    assert idle(f, t, 300) == 3, 'three retries, then no pass until a source event'
    t['now'] += f.heartbeat_s
    f.beat()
    assert 'S1' not in pushed[-1]['fresh'], 'never vouched for while its read fails'
    r.linked = linked
    r.links['S1'] = [dict(r.links['S1'][0], title='later')]
    r.stamp += 1  # any source event admits the retained work
    f.tick()
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'later' and not f.failures


def decision_feed():
    f, r, pushed, t = make()
    state = {'fail': False, 'reads': 0, 'outcome': {'v': 1}}

    def decision_inputs(s, lineage):
        state['reads'] += 1
        if state['fail']:
            raise sqlite3.OperationalError('database is locked')
        return {'outcome_json': dict(state['outcome']), 'requests': []}

    r.decision_inputs = decision_inputs
    r.panel_files_stamp = lambda: r.stamp
    r.decision_store_stamp = lambda: r.stamp
    r.panel_stamps = lambda ids: {s: r.stamp for s in ids}
    f.watch(['S1'])
    f.tick()
    f.refresh_decisions()
    return f, r, state, t


def decide_idle(f, t, seconds=120):
    passes = 0
    for _ in range(seconds):
        t['now'] += 1
        f.beat()
        if f._decide.is_set():
            f._decide.clear()
            passes += 1
            f.refresh_decisions()
    return passes


def test_decision_lane_read_failure_recovers_without_another_write():
    f, r, state, t = decision_feed()
    assert decide_idle(f, t) == 0
    state.update(fail=True, outcome={'v': 2})
    r.stamp += 1
    f.refresh_decisions()
    assert 'S1' in f._decision_failures
    state['fail'] = False
    assert decide_idle(f, t, 5) == 1
    assert f.bundles['S1']['bundle']['outcome_json'] == {'v': 2}
    assert not f._decision_failures and f._due(t['now']) == (None, None)
    assert decide_idle(f, t) == 0


def test_decision_lane_retry_is_bounded_while_the_store_stays_unreadable():
    f, r, state, t = decision_feed()
    state['fail'] = True
    r.stamp += 1
    f.refresh_decisions()
    assert decide_idle(f, t, 300) == 3
    assert 'S1' in f._decision_failures


def producer(deferred):
    f, r, pushed, t = make()
    source = {'version': 1}
    calls = []
    r.reconciliation_stamp = lambda: source['version']

    def reconcile(sessions):
        calls.append(sorted(sessions))
        return {s for s in sessions if s in deferred}

    r.reconcile_sources = reconcile
    return f, r, source, calls, t


def test_deferred_producer_work_does_not_fail_the_pass_and_is_retried():
    deferred = {'S1'}
    f, r, source, calls, t = producer(deferred)
    f.watch(['S1', 'S2'])
    f.tick()
    assert calls == [['S1', 'S2']]
    assert {'S1', 'S2'} <= set(f.bundles), 'an unavailable producer source never withholds the panel'
    deferred.clear()
    assert idle(f, t, 5) == 1
    assert calls == [['S1', 'S2'], ['S1']], 'only the deferred chat, once'
    assert idle(f, t) == 0 and f._due(t['now']) == (None, None)


def test_deferred_producer_retry_is_bounded_then_waits_for_a_source_event():
    deferred = {'S1'}
    f, r, source, calls, t = producer(deferred)
    f.watch(['S1'])
    f.tick()
    assert idle(f, t, 300) == 3
    assert calls == [['S1']] * 4
    for _ in range(5):  # the producer's own store writes and other wakes admit nothing
        r.stamp += 1
        f.tick()
    assert calls == [['S1']] * 4
    deferred.clear()
    source['version'] += 1
    f.tick()
    assert calls == [['S1']] * 5 and not f._reconcile_deferred
    assert idle(f, t) == 0


def test_a_due_producer_retry_cannot_outlive_its_chat_and_spin_the_idle_loop():
    f, r, source, calls, t = producer({'S1'})
    f.watch(['S1'])
    f.tick()
    assert f._reconcile_retry_at is not None
    with f.lock:
        f._forget('S1')
    assert idle(f, t, 60) <= 1
    assert f._due(t['now']) == (None, None) and f.idle_wait() == f.heartbeat_s


def test_core_reader_isolates_one_chats_producer_failure():
    api = api_module()
    cr = object.__new__(api.CoreReaders)
    cr._local = threading.local()
    cr.state_db = lambda: Path('/nonexistent/state.db')
    cr.lineage = lambda s: [s]
    seen = []

    class Steps:
        @staticmethod
        def panel_reconcile(home, session, lineage):
            seen.append(session)
            if session == 'bad':
                raise OSError('Remote ask owner projection unavailable')
            return ['S: RuntimeError: deferred'] if session == 'late' else []

    cr._user_step_api = lambda: Steps
    assert cr.reconcile_sources(['ok', 'bad', 'late', 'ok2']) == {'bad', 'late'}
    assert seen == ['ok', 'bad', 'late', 'ok2']
