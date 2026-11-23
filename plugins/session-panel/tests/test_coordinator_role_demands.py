"""Independent exact-demand renewal probe; fixture-only readers and state."""
import threading
import pytest
from test_source_coordinator_core import api, board, kanban_db, readers_for, feedmod, compose, drain, strict_empty


@pytest.mark.parametrize('role', ['local', 'external'])
@pytest.mark.parametrize('renewal', ['demand', 'source'])
def test_exact_demand_renews_exhausted_reader_role(api, board, monkeypatch, renewal, role):
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    task = kanban_db.create_task(conn, title='Initial local row', session_id='S')
    now = [1800000000.]
    f = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    f.watch(['S'])
    lane = compose(f, r)
    calls = []
    broken = [True]
    try:
        f.changes(['S']); drain(lane)
        # Keep source and membership identity unchanged; only exact demand will move.
        def factory():
            if threading.current_thread().name.endswith(role):
                calls.append(broken[0])
                if broken[0]:
                    raise RuntimeError('fixture reader unavailable: ' + role)
            return r
        lane.reader_factory = factory
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Trigger role failure', task))
        conn.commit()
        drain(lane)
        for _ in range(3):
            now[0] = lane.role_attempts[role].retry_at
            assert now[0] is not None
            drain(lane)
        assert calls == [True] * 4
        assert lane.retry_at() is None
        broken[0] = False
        key = lane.module.CardKey('fixture', 'default', 'default', task)
        assert lane.require('S', [key])
        if renewal == 'source':
            conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Recovery control', task))
            conn.commit()
        drain(lane)
        print('ROLE_DEMAND_RENEWAL', calls, lane.role_attempts[role],
              'MISSING', lane.store.pin(lane.chats['S'])[1].missing_scopes)
        assert calls == [True] * 4 + [False], 'changed exact demand cannot re-admit exhausted reader role: ' + role
        assert not lane.store.pin(lane.chats['S'])[1].missing_scopes
    finally:
        f.stopped.set(); lane.close(); r.release(); conn.close()
