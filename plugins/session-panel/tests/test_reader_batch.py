"""Real local + replica + queue graph: pass reuse must preserve per-lineage membership."""
import collections
import contextlib
import sqlite3

import pytest

from test_core_readers import api, board, kanban_db, readers_for


@pytest.fixture
def graph(api, board, tmp_path, monkeypatch):
    from hermes_cli import kanban_session_activity as ksa, kanban_sync_client as ksc
    from hermes_cli import kanban_db_sync as sync, kanban_chat_links as links
    from hermes_cli.kanban_db_connect import connect
    path, c = board
    # Only fixture-owned paths; retain real core membership, hydration, replica runs and queue code.
    for name in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_HOME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(ksa, '_board_db_paths', lambda: [path])
    monkeypatch.setattr(ksc, 'board_paths', lambda: [('default', path)])
    replica = tmp_path / 'replica.db'
    monkeypatch.setattr(ksc, 'shadow_path', lambda slug: replica)
    monkeypatch.setattr(ksc, 'load_identity', lambda *a, **k: ksc.Identity('https://example.com', 'fixture', 'local-test'))
    # An empty origin ledger is an independent input; replica selection itself is NOT stubbed.
    monkeypatch.setattr(ksa, '_remote_links', lambda stale_ok=False: [])
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', lambda stale_ok=False: ([], []))
    monkeypatch.setattr(sync, 'sync_config', lambda: {'enabled': True, 'links_reader': 'replica'})
    ksa._reset_cache_for_tests()
    state = sqlite3.connect(tmp_path / 'state.db')
    state.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, parent_session_id TEXT)')
    state.executemany('INSERT INTO sessions VALUES (?, NULL)', [(f'S{i}',) for i in range(23)])
    state.commit(); state.close()
    local = kanban_db.create_task(c, title='Shared local ready card', session_id='S0', initial_status='running')
    c.execute("UPDATE tasks SET status='ready' WHERE id=?", (local,))
    for i in range(1, 23):
        c.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id, created_at) VALUES (?, 'tui', ?, 1)",
                  (local, f'S{i}'))
    c.commit()
    rc = connect(replica)
    remote = kanban_db.create_task(rc, title='Shared remote ready card', session_id='elsewhere', initial_status='running')
    rc.execute('UPDATE tasks SET created_on=? WHERE id=?', ('remote-test', remote))
    for i in range(23):
        rc.execute('INSERT INTO task_chat_links (uid, task_id, chat_gateway, chat_profile, chat_session, kind, released, created_at) '
                   'VALUES (?, ?, ?, ?, ?, ?, 0, 1)',
                   (links.link_uid(remote, 'local-test', 'default', f'S{i}', 'shepherd'), remote,
                    'local-test', 'default', f'S{i}', 'shepherd'))
    rc.commit()
    r = readers_for(api, path, monkeypatch)
    try:
        yield r, local, remote, ksa
    finally:
        r.release(); rc.close(); c.close()
        ksa._reset_cache_for_tests()


def stable(value):
    # Evidence clocks remain real in production; compare their content/identity, not sequential probe times.
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items()
                if k not in ('observed_at', 'observed_at_ms', 'age_ms', 'age_s', 'read_at', 'computed_at', 'computed_seq')}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def test_batch_reuses_real_local_replica_and_queue_reads_without_losing_associations(graph, api, monkeypatch):
    from hermes_cli import kanban_queue_position as kqp, kanban_chat_links as links
    r, local, remote, ksa = graph
    sessions = [f'S{i}' for i in range(23)]
    now = api.time.time()
    monkeypatch.setattr(api.time, 'time', lambda: now)
    counts = collections.Counter()
    raw, remote_read, detail = ksa._read_session_tasks, links._read_replica, kqp.detail_fields

    def read(*a, **k):
        counts['local'] += 1
        return raw(*a, **k)

    def foreign(*a, **k):
        counts['replica'] += 1
        return remote_read(*a, **k)

    def queue(*a, **k):
        counts['queue'] += 1
        return detail(*a, **k)

    monkeypatch.setattr(ksa, '_read_session_tasks', read)
    monkeypatch.setattr(links, '_read_replica', foreign)
    monkeypatch.setattr(kqp, 'detail_fields', queue)
    core = api._load_feed()
    baseline = {s: core.Feed(r, emit=lambda _: None).compute(s) for s in sessions}
    assert all({local, remote} <= {t['id'] for t in b['tasks']} for b in baseline.values())
    counts.clear()
    f = core.Feed(r, emit=lambda _: None)
    # On old code this exercises the normal per-session path, not an AttributeError on a missing API.
    batch = getattr(r, 'batch', lambda _: contextlib.nullcontext())
    with batch(sessions):
        actual = {s: f.compute(s) for s in sessions}
    def differences(a, b, key=''):
        if isinstance(a, dict) and isinstance(b, dict):
            return [d for k in a.keys() | b.keys() for d in differences(a.get(k), b.get(k), key + '/' + str(k))]
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, key + '/' + str(i))]
        return [] if a == b else [(key, a, b)]
    assert not differences(stable(actual), stable(baseline)), differences(stable(actual), stable(baseline))[:8]
    assert counts['local'] == 2  # one enriched local read per status set, not 46
    assert counts['replica'] == 2
    assert counts['queue'] == 1  # shared local card; the replica deliberately has no local queue fields
    assert actual['S0']['tasks'][0]['via'] == 'created'
    assert actual['S1']['tasks'][0]['via'] == 'subscribed'
    # A subsequent pass is fresh, not a globally retained trusted snapshot.
    with batch(['S0', 'S1']):
        f.compute('S0')
    assert counts['local'] == 4 and counts['queue'] == 2


def test_transient_missing_card_is_not_memoized_for_the_pass(graph, monkeypatch):
    r, local, _, _ = graph
    original = r._card
    calls = []
    def flaky(*args):
        calls.append(1)
        return None if len(calls) == 1 else original(*args)
    monkeypatch.setattr(r, '_card', flaky)
    with r.batch(['S0', 'S1']):
        assert r.card(local, 'default') is None
        assert r.card(local, 'default') is not None
        assert r.card(local, 'default') is not None
    assert len(calls) == 2


def test_union_execution_error_does_not_spread_to_other_lineages(graph, monkeypatch):
    r, local, _, ksa = graph
    original = ksa._read_session_tasks
    calls = []
    def damaged(path, ids, *args, **kwargs):
        calls.append(list(ids))
        rows = original(path, ids, *args, **kwargs)
        if len(ids) > 1:
            for row in rows:
                row['_execution_error'] = True
        return rows
    monkeypatch.setattr(ksa, '_read_session_tasks', damaged)
    with r.batch(['S0', 'S1']):
        for sid in ('S0', 'S1'):
            assert local in {t['id'] for t in r.linked([sid])}
    assert ['S0'] in calls and ['S1'] in calls
    assert sum(len(ids) > 1 for ids in calls) == 1


def test_batch_failure_falls_back_to_stable_lineage_reads(graph, monkeypatch):
    r, local, _, ksa = graph
    original = ksa._read_session_tasks
    calls = []

    def busy(path, ids, *a, **k):
        calls.append(list(ids))
        if len(ids) > 1:
            raise sqlite3.OperationalError('fixture union busy')
        return original(path, ids, *a, **k)

    monkeypatch.setattr(ksa, '_read_session_tasks', busy)
    with r.batch(['S0', 'S1']):
        assert local in {t['id'] for t in r.linked(['S0'])}
        assert local in {t['id'] for t in r.linked(['S1'])}
    assert ['S0'] in calls and ['S1'] in calls
    assert sum(len(ids) > 1 for ids in calls) == 1, 'no repeated union retry budgets'
    assert getattr(r._local, 'batch', None) is None


def test_shared_reads_keep_per_lineage_busy_memos_current(graph, monkeypatch):
    r, local, _, ksa = graph
    with r.batch(['S0', 'S1']):
        for sid in ('S0', 'S1'):
            assert local in {t['id'] for t in r.linked([sid])}
    def busy(*a, **k):
        from hermes_cli.kanban_generation import BoardLifecycleBusy
        raise BoardLifecycleBusy()
    monkeypatch.setattr(ksa, '_read_session_tasks', busy)
    with r.batch(['S0', 'S1']):
        for sid in ('S0', 'S1'):
            rows = r.linked([sid])
            assert local in {t['id'] for t in rows}
            assert next(t for t in rows if t['id'] == local)['stale'] is True


def test_origin_alias_obligation_survives_union_replica_selection(graph, monkeypatch):
    r, _, remote, ksa = graph
    # Only S1's replica link remains, but the ledger also names S0 as origin_session.
    replica = sqlite3.connect(r.ksc.shadow_path('default'))
    replica.execute("DELETE FROM task_chat_links WHERE chat_session != 'S1'")
    replica.commit(); replica.close()
    origin = {'id': remote, 'session': 'S1', 'origin_session': 'S0', 'gateway': 'remote-test',
              'profile': 'default', 'board': 'default', 'key': f'remote-test/default/default/{remote}',
              'origin_gateway': 'local-test', 'origin_profile': 'default', 'status': 'ready',
              'unfinished': True, 'released': False, 'title': 'Origin alias work'}
    monkeypatch.setattr(ksa, '_remote_links', lambda stale_ok=False: [origin])
    baseline = {s: r.linked([s]) for s in ('S0', 'S1')}
    with r.batch(['S0', 'S1']):
        actual = {s: r.linked([s]) for s in ('S0', 'S1')}
    for sid in baseline:
        assert [(t['id'], t.get('source')) for t in actual[sid]] == [(t['id'], t.get('source')) for t in baseline[sid]]
        assert remote in {t['id'] for t in actual[sid]}


def test_unreadable_ledger_does_not_make_absent_lineage_successful(graph, monkeypatch):
    r, _, _, ksa = graph
    replica = sqlite3.connect(r.ksc.shadow_path('default'))
    replica.execute("DELETE FROM task_chat_links WHERE chat_session != 'S1'")
    replica.commit(); replica.close()
    def unavailable(*a, **k):
        raise RuntimeError('ledger unavailable')
    monkeypatch.setattr(ksa, '_remote_links', unavailable)
    with r.batch(['S1', 'S0']):
        assert any(t.get('partial_source_unknown') for t in r.linked(['S1']))
        with pytest.raises(RuntimeError, match='ledger unavailable'):
            r.linked(['S0'])


def test_batch_card_memo_invalidates_on_file_replacement(api, board, monkeypatch, tmp_path):
    import os
    from hermes_cli.kanban_db_connect import connect
    path, c = board
    old = kanban_db.create_task(c, title='Old file', session_id='S0', initial_status='running')
    r = readers_for(api, path, monkeypatch)
    fresh = tmp_path / 'replacement.db'
    replacement = connect(fresh)
    new = kanban_db.create_task(replacement, title='New file', session_id='S0', initial_status='running')
    assert c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    assert replacement.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    replacement.close()
    try:
        with r.batch(['S0', 'S1']):
            assert r.card(old, 'default') is not None
            held = r.conn(path)
            os.replace(fresh, path)
            assert r.card(new, 'default') is not None
            assert r.card(old, 'default') is None
            with pytest.raises(sqlite3.ProgrammingError):
                held.execute('SELECT 1')
    finally:
        c.close(); r.release()


def test_feed_thread_closes_its_own_connections_on_stop(api, board, monkeypatch):
    import threading
    path, c = board
    r = readers_for(api, path, monkeypatch)
    f = api._load_feed().Feed(r, emit=lambda _: None)
    closed, verified = [], []
    original_release = r.release
    def release():
        handles = [entry[0] for entry in getattr(r._local, 'conns', {}).values()]
        original_release()
        for handle in handles:
            try:
                handle.execute('SELECT 1')
            except sqlite3.ProgrammingError as e:
                verified.append('closed database' in str(e))
            else:
                verified.append(False)
    monkeypatch.setattr(r, 'release', release)
    def tick():
        handle = r.conn(path)
        f.stopped.set()
        closed.append(handle)
    f.tick = tick
    th = threading.Thread(target=api._run, args=(f,))
    th.start(); th.join(5)
    assert not th.is_alive() and len(closed) == 1
    assert verified == [True], 'release closed the handle on its creator thread'
    # Verify on creator thread inside release, not by using a foreign-thread handle.
    assert getattr(r._local, 'conns', {}) == {}, 'worker connections never entered the main thread'
    c.close()
