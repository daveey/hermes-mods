"""Composed coordinator remote-parent dynamics and stale-full observed-clock barriers.

Canonical ``kanban_sync_remote_parents`` is ``(parent_id, status, refreshed_at)`` only.
Result/reason text is the replica ``tasks`` row (``result`` / ``block_task`` reason), never
invented parent-table columns. Refresh is ``refreshed_at`` without status. Delete of a
done remote-parent row that was never a task must stay unknown, not done.
"""
import threading

import pytest

from test_core_readers import api, board, kanban_db
from test_reader_batch import graph
from test_source_coordinator_core import compose, drain, readers_for, strict_empty
from test_feed import feedmod


def _sync_parents(conn):
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps
    apply_sync_schema_steps(conn)
    from hermes_cli import kanban_activity_revision as revision
    revision.ensure_revision(conn, revision.LOCAL_TABLES)
    conn.commit()


def _waiting(bundle, card_id):
    return next(row for row in bundle['model']['sections']['waiting'] if row['id'] == card_id)


def _done(bundle, card_id):
    return [row for row in bundle['model']['sections']['done'] if row['id'] == card_id]


def _open_targets(row):
    return [a.get('target') for a in row.get('actions') or () if a.get('kind') == 'open_card']


def _replica(r):
    from hermes_cli.kanban_db_connect import connect
    return connect(r.ksc.shadow_path('default'))


def _exact(leaf):
    return {'id': leaf, 'gateway': 'remote-test', 'profile': 'default', 'board': 'default'}


def _seed_remote_graph(r, remote, depth, *, hydrate_parent, status='ready'):
    """Replica child ``remote`` plus direct or two-hop parents. Parent is not S0 membership."""
    from hermes_cli.kanban_db_connect import connect
    local = connect(r.boards()[0][1])
    _sync_parents(local)
    replica = _replica(r)
    _sync_parents(replica)
    root = None
    if hydrate_parent:
        root = kanban_db.create_task(replica, title='Unrelated graph root', session_id='elsewhere')
        replica.execute("UPDATE tasks SET created_on='remote-test',status='ready' WHERE id=?", (root,))
        leaf = kanban_db.create_task(replica, title='Remote exact leaf', session_id='elsewhere')
        replica.execute("UPDATE tasks SET created_on='remote-test',status=? WHERE id=?", (status, leaf))
        parent = leaf
        if depth == 2:
            parent = kanban_db.create_task(replica, title='Intermediate wait', session_id='elsewhere')
            replica.execute("UPDATE tasks SET created_on='remote-test',status='todo' WHERE id=?", (parent,))
            replica.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (leaf, parent))
        # An unrelated root exists but is not a competing primary dependency.
        # Parent order is canonical ID order, not insertion order.
    else:
        leaf = parent = 't_abcdef123456'
    replica.execute("UPDATE tasks SET status='todo' WHERE id=?", (remote,))
    replica.execute('INSERT INTO task_links(parent_id,child_id) VALUES (?,?)', (parent, remote))
    for conn, pid in ((replica, parent), (local, parent)):
        conn.execute(
            'INSERT OR REPLACE INTO kanban_sync_remote_parents(parent_id,status,refreshed_at) VALUES (?,?,?)',
            (pid, status, 1000))
        if hydrate_parent and leaf != parent:
            conn.execute(
                'INSERT OR REPLACE INTO kanban_sync_remote_parents(parent_id,status,refreshed_at) VALUES (?,?,?)',
                (leaf, status, 1000))
    replica.commit()
    local.commit()
    return local, replica, leaf, parent, root


@pytest.mark.parametrize('depth', [1, 2])
@pytest.mark.parametrize('mutation', ['result', 'reason', 'refresh', 'delete', 'bookkeeping'])
def test_real_composed_remote_parent_result_reason_refresh_delete_keep_exact_owner(
        graph, monkeypatch, tmp_path, depth, mutation):
    r, local_id, remote, ksa = graph
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    now = [1800000000.]
    monkeypatch.setattr(ksa.time, 'time', lambda: now[0])
    hydrate = True
    local, replica, leaf, parent, root = _seed_remote_graph(
        r, remote, depth, hydrate_parent=hydrate, status='running')
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S0'])
    lane = compose(f, r)
    try:
        f.changes(['S0']); drain(lane)
        bundle = f.bundles['S0']['bundle']
        assert remote in {row['id'] for row in bundle['tasks']}, bundle
        assert leaf not in {row['id'] for row in bundle['tasks']}, 'parent must not be held membership'
        if root is not None:
            assert root not in {row['id'] for row in bundle['tasks']}
        waiting = _waiting(bundle, remote)
        before_seq = bundle['computed_seq']
        before_computed = bundle['computed_at']
        if hydrate:
            assert _exact(leaf) in _open_targets(waiting)
            assert 'Unrelated graph root' not in (waiting.get('detail') or '')
        else:
            assert not _done(bundle, parent)
            assert parent not in {row['id'] for row in bundle.get('done') or ()}

        if mutation == 'result':
            replica.execute('UPDATE tasks SET result=? WHERE id=?', ('Exact remote result token', leaf))
        elif mutation == 'reason':
            assert kanban_db.block_task(replica, leaf, reason='Exact remote reason token')
        elif mutation == 'refresh':
            for conn in (local, replica):
                conn.execute('UPDATE kanban_sync_remote_parents SET refreshed_at=refreshed_at+1')
        elif mutation == 'bookkeeping':
            def no_materialization(*a, **kw):
                raise AssertionError('replica bookkeeping reached external materialization')
            monkeypatch.setattr(r, 'capture_external', no_materialization)
            replica.execute('UPDATE tasks SET sync_version=sync_version+1,field_hlc=?,last_heartbeat_at=? WHERE id=?',
                            ('{}', now[0], leaf))
        else:
            for conn in (local, replica):
                conn.execute('DELETE FROM kanban_sync_remote_parents WHERE parent_id=?', (parent,))
        replica.commit(); local.commit()
        drain(lane)
        after = f.bundles['S0']['bundle']
        child = _waiting(after, remote)
        if mutation == 'delete':
            assert not _done(after, parent)
            assert parent not in {row['id'] for row in after.get('done') or ()}
            assert _exact(leaf) in _open_targets(child)
            assert after['cards'][leaf]['detail']['task']['status'] == 'running'
        elif mutation == 'refresh':
            assert after['computed_seq'] != before_seq
            assert _exact(leaf) in _open_targets(child)
            card = after['cards'][leaf]['detail']['task']
            assert card.get('status') == 'running'
            assert card.get('id') == leaf
        elif mutation == 'bookkeeping':
            assert _exact(leaf) in _open_targets(child)
            assert after['computed_seq'] == before_seq
            assert after['computed_at'] == before_computed
            assert not lane.store.pending(lane.external)
        elif mutation == 'result':
            assert _exact(leaf) in _open_targets(child)
            canonical = r.card(leaf, 'default')
            diagnostic = {
                'held_result': after['cards'][leaf]['detail']['task'].get('result'),
                'canonical_result': canonical['detail']['task'].get('result'),
                'demands': {str(sid): [str(k) for k in d.refs] for sid, d in lane.store.demands(lane.external).items()},
                'raw_dependencies': {str(sid): str(raw[2]) if len(raw) == 3 else 'absent' for sid, raw in lane.external_raw.items()},
            }
            assert diagnostic['held_result'] == 'Exact remote result token', diagnostic
        else:
            assert _exact(leaf) in _open_targets(child)
            painted = child.get('detail') or ''
            extra = ' '.join(child.get('details') or ())
            parent_task = after['cards'][leaf]['detail']['task']
            blob = painted + extra + str(parent_task.get('block_reason') or '') + str(parent_task)
            assert 'Exact remote reason token' in blob
        assert 'Unrelated graph root' not in (child.get('detail') or '')
        if root is not None:
            assert _exact(root) not in _open_targets(child)
    finally:
        f.stopped.set(); lane.close()
        replica.close(); local.close()


@pytest.mark.parametrize('kind', ['clock', 'ci', 'deploy'])
def test_real_composed_clock_ci_deploy_wait_does_not_adopt_unrelated_graph_root(
        graph, monkeypatch, tmp_path, kind):
    r, local_id, remote, ksa = graph
    monkeypatch.setenv('HERMES_HUB_STATE', str(tmp_path / 'hub-state'))
    now = [1800000000.]
    monkeypatch.setattr(ksa.time, 'time', lambda: now[0])
    local, replica, leaf, parent, root = _seed_remote_graph(
        r, remote, 1, hydrate_parent=True, status='ready')
    awaited = {'kind': kind, 'why': 'Cooldown before release'}
    if kind != 'clock':
        awaited['ref'] = 'https://example.com/runs/123'
    assert kanban_db.schedule_task(
        replica, remote, reason='SCHEDULED_UNTIL=2026-11-23T12:00:00+00:00 Cooldown',
        clock_only=kind == 'clock', awaiting=awaited,
        wake_check='false' if kind in ('ci', 'deploy') else None)
    replica.commit()
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S0'])
    lane = compose(f, r)
    try:
        f.changes(['S0']); drain(lane)
        child = _waiting(f.bundles['S0']['bundle'], remote)
        assert 'Unrelated graph root' not in (child.get('detail') or '')
        assert _exact(root) not in _open_targets(child)
    finally:
        f.stopped.set(); lane.close()
        replica.close(); local.close()


@pytest.mark.parametrize('terminal', ['done', 'archived'])
def test_real_stale_full_barrier_held_sweep_later_seq_cannot_renew_observed_clocks(
        api, board, monkeypatch, terminal):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    now = [api.time.time()]
    task = kanban_db.create_task(conn, title='Waiting work', session_id='S')

    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S'])
    lane = compose(f, r)
    entered, release = threading.Event(), threading.Event()
    returned, errors = [], []
    caller = None
    try:
        f.changes(['S']); drain(lane)
        assert f.bundles['S']['bundle']['model']['sections']['waiting'], f.bundles['S']['bundle']
        pin = lane.store.pin(lane.chats['S'])
        base = f.bundles['S']['bundle']
        envelope = type(lane.full(['S'])['S'])
        conn.execute('UPDATE tasks SET status=? WHERE id=?', (terminal, task)); conn.commit()
        drain(lane)
        current = f.bundles['S']['bundle']
        assert not any(row['id'] == task for row in current['model']['sections']['waiting'])
        done_rows = _done(current, task)
        assert done_rows and done_rows[0].get('id') == task
        listed = [row for row in current.get('done') or () if row.get('id') == task]
        assert listed and listed[0].get('status') == terminal
        observed = current.get('board_observed_at', current['computed_at'])
        seq = current['computed_seq']
        def stale_full():
            try:
                stale = f.project_board(lane.store, lane.chats['S'], canonical_base=base,
                                        pin=pin, seq=seq + 100000)
                returned.append(stale)
                entered.set()
                assert release.wait(5)
                # Use the real full-route publication adapter, never raw bundle overwrite.
                f._publish({'S': envelope(stale, lane.module)})
            except BaseException as exc:
                errors.append(exc); entered.set()
        caller = threading.Thread(target=stale_full, name='fixture-stale-full')
        caller.start()
        assert entered.wait(2) and returned, errors
        assert returned[0].bundle['computed_seq'] > seq
        now[0] += 900
        release.set()
        caller.join(5)
        assert not caller.is_alive()
        assert not errors, errors
        after = f.bundles['S']['bundle']
        assert not any(row['id'] == task for row in after['model']['sections']['waiting'])
        later = _done(after, task)
        assert later and later[0].get('id') == task
        listed = [row for row in after.get('done') or () if row.get('id') == task]
        assert listed and listed[0].get('status') == terminal
        assert after['computed_seq'] == seq
        assert after.get('board_observed_at', after['computed_at']) == observed
        assert lane.store.current(pin[1]) is False
    finally:
        release.set()
        if caller is not None:
            caller.join(3)
        f.stopped.set(); lane.close(); r.release(); conn.close()
