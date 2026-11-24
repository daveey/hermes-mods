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

    class Result(list):
        unchanged = True

    class Steps:
        @staticmethod
        def panel_reconcile(home, session, lineage):
            seen.append(session)
            if session == 'bad':
                raise OSError('Remote ask owner projection unavailable')
            return ['S: RuntimeError: deferred'] if session == 'late' else Result()

    cr._user_step_api = lambda: Steps
    assert cr.reconcile_sources(['ok', 'bad', 'late', 'ok2']) == {'bad', 'late'}
    assert seen == ['ok', 'bad', 'late', 'ok2']
    assert [row['unchanged'] for row in cr.reconcile_timings] == [True, False, False, True]
    assert all(row['at'] > 0 and row['reconcile_s'] >= 0 for row in cr.reconcile_timings)


# ── one retry budget per chat: a chat that fails forever must not spend another chat's retries ──────────────

def test_a_transient_hydration_failure_recovers_beside_a_permanently_failing_chat():
    f, r, pushed, t = make()
    r.links['T'] = [dict(r.links['S1'][0], id='t_bbbbbb1', title='old')]
    f.watch(['S1', 'T'])
    f.tick()
    original, state = r.linked, {'t_fail': 0}

    def linked(ids):
        if 'S1' in ids:
            raise sqlite3.OperationalError('S1 permanently broken')
        if 'T' in ids and state['t_fail'] > 0:
            state['t_fail'] -= 1
            raise sqlite3.OperationalError('database is locked')
        return original(ids)

    r.linked = linked
    r.events = [('t_aaaaaa1', 'S1')]
    r.stamp += 1
    f.tick()
    assert idle(f, t, 60) == 3 and idle(f, t, 60) == 0, 'S1 spent its own retries'
    state['t_fail'] = 1
    r.links['T'] = [dict(r.links['T'][0], title='new')]
    r.events = [('t_bbbbbb1', 'T')]
    r.stamp += 1
    f.tick()
    assert 'T' in f.failures
    assert idle(f, t, 5) == 1, "T's first failure is retried although S1 is out of retries"
    assert f.bundles['T']['bundle']['tasks'][0]['title'] == 'new' and 'T' not in f.failures
    assert 'S1' in f.failures and f._due(t['now']) == (None, None)
    assert idle(f, t) == 0, 'S1 alone admits no further pass'


def test_a_hydration_that_succeeds_with_stale_rows_is_retried_without_another_write():
    f, r, pushed, t = make()
    f.watch(['S1'])
    f.tick()
    fresh = dict(r.links['S1'][0], title='new')
    r.links['S1'] = [dict(r.links['S1'][0], stale=True)]  # core's busy fallback: last-observed rows, no exception
    r.events = [('t_aaaaaa1', 'S1')]
    r.stamp += 1
    f.tick()
    assert f.bundles['S1']['source_stale'] and 'S1' in f._retry and not f.failures
    r.links['S1'] = [fresh]  # readable again, no further write
    assert idle(f, t, 5) == 1
    assert f.bundles['S1']['bundle']['tasks'][0]['title'] == 'new' and not f.bundles['S1']['source_stale']
    assert 'S1' not in f._retry and f._due(t['now']) == (None, None)
    assert idle(f, t) == 0


def test_persistently_stale_rows_are_retried_three_times_then_wait_for_a_source_event():
    f, r, pushed, t = make()
    f.watch(['S1'])
    f.tick()
    r.links['S1'] = [dict(r.links['S1'][0], stale=True)]
    r.events = [('t_aaaaaa1', 'S1')]
    r.stamp += 1
    f.tick()
    assert idle(f, t, 300) == 3
    t['now'] += f.heartbeat_s
    f.beat()
    assert 'S1' not in pushed[-1]['fresh'], 'stale rows are never vouched for'
    assert f._due(t['now']) == (None, None) and f.idle_wait() == f.heartbeat_s


def test_a_transient_decision_failure_recovers_beside_a_permanently_failing_chat():
    f, r, pushed, t = make()
    r.links['T'] = [dict(r.links['S1'][0], id='t_bbbbbb1')]
    state = {'t_fail': 0, 'broken': False, 'v': 1}

    def decision_inputs(s, lineage):
        if s == 'S1' and state['broken']:
            raise sqlite3.OperationalError('S1 permanently broken')
        if s == 'T' and state['t_fail'] > 0:
            state['t_fail'] -= 1
            raise sqlite3.OperationalError('database is locked')
        return {'outcome_json': {'v': state['v']}, 'requests': []}

    r.decision_inputs = decision_inputs
    r.panel_files_stamp = lambda: r.stamp
    r.decision_store_stamp = lambda: r.stamp
    r.panel_stamps = lambda ids: {s: r.stamp for s in ids}
    f.watch(['S1', 'T'])
    f.tick()
    f.refresh_decisions()
    state['broken'] = True
    r.stamp += 1
    f.refresh_decisions()
    assert decide_idle(f, t, 60) == 3 and decide_idle(f, t, 60) == 0
    state.update(t_fail=1, v=2)
    r.stamp += 1
    f.refresh_decisions()
    assert {'S1', 'T'} <= f._decision_failures
    assert decide_idle(f, t, 5) == 1
    assert f.bundles['T']['bundle']['outcome_json'] == {'v': 2} and 'T' not in f._decision_failures
    assert decide_idle(f, t) == 0 and f._due(t['now']) == (None, None)


def test_a_transient_producer_deferral_is_retried_beside_a_permanently_deferred_chat():
    deferred = {'S1'}
    f, r, source, calls, t = producer(deferred)
    f.watch(['S1', 'S2'])
    f.tick()
    assert idle(f, t, 60) == 3 and idle(f, t, 60) == 0, 'S1 spent its own retries'
    deferred.add('S2')
    source['version'] += 1
    f.tick()
    assert f._reconcile_deferred == {'S1', 'S2'}
    deferred.discard('S2')
    assert idle(f, t, 5) == 1, "S2's first deferral is retried although S1 is out of retries"
    assert f._reconcile_deferred == {'S1'}
    assert idle(f, t) == 0 and f._due(t['now']) == (None, None)
