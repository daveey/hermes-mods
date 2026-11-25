"""Real route dispatch at the design population, not route-labelled API loops."""
import collections
import threading

import pytest

from test_source_coordinator_core import api, board, kanban_db, readers_for, strict_empty, compose, drain
from test_feed import feedmod


@pytest.mark.parametrize('route', ['compute', 'full', 'tick', 'catchup', 'board', 'keyed'])
def test_real_64_chat_routes_share_40_shared_and_distinct_details_without_projection_io(
        api, board, monkeypatch, route):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    sessions = [f'S{i:02}' for i in range(64)]
    shared = []
    # Bulk fixture writes, not one fsync per subscription. No production limits
    # or SQLite variable ceilings are raised to accommodate the test.
    conn.execute('BEGIN')
    for i in range(40):
        task = kanban_db.create_task(conn, title=f'Shared {i:02}', session_id=sessions[0])
        shared.append(task)
        conn.executemany("INSERT INTO kanban_notify_subs(task_id,platform,chat_id,created_at) VALUES (?,'tui',?,1)",
                         [(task, s) for s in sessions[1:]])
    distinct = {s: kanban_db.create_task(conn, title=f'Distinct {s}', session_id=s) for s in sessions}
    conn.commit()
    expected = {s: set(shared) | {distinct[s]} for s in sessions}
    # Authoritative per-chat membership controls, before legacy readers are
    # forbidden. No reference/membership promotion to make the controls pass.
    with r.batch(sessions):
        for s in sessions:
            assert {t['id'] for t in r.linked([s])} == expected[s]
    counts = collections.Counter()
    original_detail = r._detail
    def detail(*args, **kwargs):
        counts[(args[1], bool(args[3]))] += 1
        return original_detail(*args, **kwargs)
    monkeypatch.setattr(r, '_detail', detail)
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(sessions)
    lane = compose(f, r)
    costs, errors = [], []
    original_project = lane._project
    def pure(s):
        # This CPython C-boundary probe sees statements on already-open handles
        # and socket I/O attempts as well as fresh connects (not just an opener
        # monkeypatch). The emitter is intentionally inert; its I/O is separately
        # covered by the stage/writer integration tests.
        cost = type(lane).StageCost() if hasattr(type(lane), 'StageCost') else None
        if cost is None:
            import sys
            cost = sys.modules[type(lane).__module__].StageCost()
        cost.start()
        try:
            return original_project(s)
        except BaseException as exc:
            errors.append(exc)
            raise
        finally:
            costs.append(cost.finish())
    monkeypatch.setattr(lane, '_project', pure)
    try:
        # Set up the source independently of the synchronous request's existing
        # 3s budget. This is a materialization/I/O contract, not a latency gate.
        # Joining owned workers preserves all population and route assertions.
        drain(lane)
        f.changes(sessions)
        drain(lane)
        assert len(counts) == 104 and set(counts.values()) == {1}
        before = dict(counts)
        def forbidden(*args, **kwargs):
            raise AssertionError('legacy per-chat board reader invoked')
        for name in ('linked', 'done', 'card', 'board_changes'):
            monkeypatch.setattr(r, name, forbidden)
        if route == 'compute':
            for s in sessions:
                f._publish({s: f.compute(s)})
        elif route == 'full':
            f._publish(f._compute_all(sessions, refresh=True))
        elif route == 'tick':
            f.tick()
        elif route == 'catchup':
            assert set(f.changes(sessions)['bundles']) == set(sessions)
        elif route == 'keyed':
            f.queue_keyed(set(sessions))
            f.refresh_keyed()
            assert not getattr(f, '_keyed_pending', set())
        else:
            assert feedmod.BoardLane(f, lambda: r) is lane
            drain(lane)
        assert dict(counts) == before, 'unchanged routes re-materialized source details'
        assert not errors, errors
        assert costs and all(c == dict(available=True, db_opens=0, sql_calls=0, network_calls=0) for c in costs)
        for s in sessions:
            value = f.bundles[s]['bundle']
            assert {t['id'] for t in value['tasks']} == expected[s]
            assert set(value['cards']) == expected[s]
            rows = [row for section in value['model']['sections'].values() for row in section if row['kind'] == 'card']
            assert {row['id'] for row in rows} == expected[s]
            assert all('gateway' in row and 'profile' in row and 'board' in row for row in rows)
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()
