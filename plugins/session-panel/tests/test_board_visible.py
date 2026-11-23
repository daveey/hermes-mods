"""Only mounted panels create BoardLane hydration demand (sidebar interest does not)."""
import time
import threading

from test_feed import Readers, feedmod


def setup(monkeypatch, enabled=True):
    monkeypatch.setenv('HERMES_PANEL_BOARD_VISIBLE_ONLY', '1' if enabled else '0')
    f = feedmod.Feed(Readers(), emit=lambda p: None)
    f.changes(['S1', 'S2'])
    lane = feedmod.BoardLane(f, Readers)
    reads = []
    real = f.compute
    def compute(session, **kw):
        reads.append(session)
        return real(session, **kw)
    f.compute = compute
    return f, lane, reads


def settle(lane):
    for worker in list(lane.active.values()):
        worker.join(2)
        assert not worker.is_alive()
    lane.service()


def test_requested_hidden_bundle_is_recomputed(monkeypatch):
    f, lane, reads = setup(monkeypatch)
    f._board_lane = lane
    try:
        lane._admit({'S1', 'S2'}, time.time())
        f.changes_bytes(['S2'])
        lane.service()
        settle(lane)
        assert reads == ['S2']
        assert f._board_dirty == {'S1'}
    finally:
        lane.close()


def test_visible_chat_precedes_forty_older_pending_chats(monkeypatch):
    f, lane, reads = setup(monkeypatch, enabled=False)
    lane.capacity = 1
    gate = threading.Event()
    real = f.compute
    def compute(session, **kw):
        assert gate.wait(2)
        return real(session, **kw)
    try:
        for i in range(40):
            sid = f'A{i:02}'
            f.changes([sid])
            lane.pending[sid] = i
        f.compute = compute
        lane.pending['S2'] = 100
        f.changes_bytes([], subscriber='window', visible_session='S2')
        lane.service()
        assert list(lane.active) == ['S2']
    finally:
        lane.pending.clear()
        gate.set()
        settle(lane)
        lane.close()


def test_hidden_board_changes_do_zero_reads(monkeypatch):
    f, lane, reads = setup(monkeypatch)
    try:
        lane.step()  # a real initial board capture affects held chats
        for _ in range(5):
            lane._admit({'S1', 'S2'}, time.time())
            lane.service()
        assert reads == [] and not lane.pending
        assert f._board_dirty == {'S1', 'S2'}
    finally:
        lane.close()


def test_one_subscriber_coalesces_only_its_session(monkeypatch):
    f, lane, reads = setup(monkeypatch)
    try:
        f.changes_bytes([], subscriber='window', visible_session='S1')
        lane.service()
        for _ in range(5):
            lane._admit({'S1', 'S2'}, time.time())
        lane.service()
        settle(lane)
        assert reads == ['S1']
        assert f._board_dirty == {'S2'}
    finally:
        lane.close()


def test_dirty_attach_and_renewal_read_exactly_once(monkeypatch):
    f, lane, reads = setup(monkeypatch)
    try:
        lane._admit({'S1', 'S2'}, time.time())
        for _ in range(3):
            f.changes_bytes([], subscriber='window', visible_session='S1')
        lane.service()
        settle(lane)
        f.changes_bytes([], subscriber='window', visible_session='S1')
        lane.service()
        assert reads == ['S1']
        f.changes_bytes([], subscriber='window', visible_session=None)
        lane.service()
        lane._admit({'S1'}, time.time())
        lane.service()
        assert reads == ['S1']
        f.changes_bytes([], subscriber='window', visible_session='S1')
        lane.service()
        settle(lane)
        assert reads == ['S1', 'S1']  # reattach bypasses completion cooldown
    finally:
        lane.close()


def test_expired_subscriber_cannot_start_pending_read(monkeypatch):
    f, lane, reads = setup(monkeypatch)
    try:
        f._panel_subscribers['gone'] = ('S1', f.clock() - 1)
        lane.pending['S1'] = time.time()
        lane.service()
        assert reads == [] and f._board_dirty == {'S1'}
    finally:
        lane.close()


def test_flag_off_restores_hidden_admission(monkeypatch):
    f, lane, reads = setup(monkeypatch, enabled=False)
    try:
        lane._admit({'S1', 'S2'}, time.time())
        lane.service()
        settle(lane)
        settle(lane)
        assert sorted(reads) == ['S1', 'S2']
    finally:
        lane.close()


def test_old_visible_read_cannot_vouch_for_new_hidden_dirty_work(monkeypatch):
    f, lane, reads = setup(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    compute = f.compute
    pushed = []
    f.emit = pushed.append
    def slow(session, **kw):
        bundle = compute(session, **kw)  # snapshot predates the later board event
        entered.set()
        assert release.wait(2)
        return bundle
    f.compute = slow
    try:
        f.changes_bytes([], subscriber='window', visible_session='S1')
        lane.service()
        lane._admit({'S1'}, time.time())
        lane.service()
        assert entered.wait(1)
        f.changes_bytes([], subscriber='window', visible_session=None)
        lane.service()
        lane._admit({'S1'}, time.time())  # newer delta after panel detached
        release.set()
        settle(lane)
        assert f._board_dirty == {'S1'}
        assert 'S1' in f._board_failures
        pushed.clear()
        f.last_emit = 0
        f._publish({}, heartbeat=True)
        assert len(pushed) == 1
        assert 'S1' not in pushed[-1]['fresh']
    finally:
        release.set()
        lane.close()
