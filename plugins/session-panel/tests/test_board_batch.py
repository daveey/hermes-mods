"""The serialized board lane hydrates chats that are due together in one worker, one reader, one scope."""
import threading
import time
from contextlib import contextmanager

from test_feed import Readers, feedmod


class BatchReaders(Readers):
    made = []

    def __init__(self):
        super().__init__()
        self.scopes, self.released = [], 0
        BatchReaders.made.append(self)

    @contextmanager
    def batch(self, sessions):
        self.scopes.append(list(sessions))
        yield

    def release(self):
        self.released += 1


def setup(monkeypatch, chats=('S1', 'S2', 'S3', 'S4'), batch='1'):
    monkeypatch.setenv('HERMES_PANEL_BOARD_VISIBLE_ONLY', '0')
    monkeypatch.setenv('HERMES_PANEL_BOARD_BATCH', batch)
    BatchReaders.made = []
    f = feedmod.Feed(Readers(), emit=lambda p: None)
    f.changes(list(chats))
    lane = feedmod.BoardLane(f, BatchReaders)
    lane.capacity = 1
    return f, lane


def settle(lane):
    for _ in range(20):
        for worker in set(lane.active.values()):
            worker.join(2)
            assert not worker.is_alive()
        lane.service()
        if not lane.active:
            return


def test_due_chats_share_one_worker_one_reader_and_one_scope(monkeypatch):
    f, lane = setup(monkeypatch)
    try:
        before = {s: f.bundles[s]['bundle']['computed_at'] for s in ('S1', 'S2', 'S3', 'S4')}
        ticks = iter(range(1, 1_000_000))
        base = max(before.values())
        f.clock = lambda: base + next(ticks)  # every reading is later than the last
        first_compute_at = []
        real = f.compute
        def compute(session, **kw):
            first_compute_at.append(f.clock())
            return real(session, **kw)
        f.compute = compute
        admitted = time.time()
        for at, s in enumerate(('S1', 'S2', 'S3', 'S4')):
            lane.pending[s] = admitted + at
        lane.service()
        assert len(set(lane.active.values())) == 1 and set(lane.active) == {'S1', 'S2', 'S3', 'S4'}
        settle(lane)
        [reader] = BatchReaders.made
        assert reader.scopes == [['S1', 'S2', 'S3', 'S4']]  # oldest admission first
        assert reader.released == 1
        stamps = {f.bundles[s]['bundle']['computed_at'] for s in before}
        # One read time for the whole batch, taken before its first read (not at each chat's turn, not at the end).
        assert len(stamps) == 1 and max(before.values()) < stamps.pop() < first_compute_at[0]
        recent = f.timing['board_recent'][-4:]
        assert [e['session'] for e in recent] == ['S1', 'S2', 'S3', 'S4'] and {e['batch'] for e in recent} == {4}
        assert not lane.pending
    finally:
        lane.close()


def test_a_batch_is_capped_and_the_rest_follow(monkeypatch):
    chats = [f'C{i:02}' for i in range(20)]
    f, lane = setup(monkeypatch, chats=chats)
    lane.cooldown = 0.0
    try:
        for at, s in enumerate(chats):
            lane.pending[s] = at
        lane.service()
        assert len(lane.active) == lane.batch_max == 16
        settle(lane)
        assert [len(r.scopes[0]) for r in BatchReaders.made if r.scopes] == [16, 4]
    finally:
        lane.close()


def test_an_event_before_a_chats_turn_defers_it_to_one_fresh_read(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2'))
    gate, entered = threading.Event(), threading.Event()
    real, order = f.compute, []
    def compute(session, **kw):
        order.append(session)
        entered.set()
        assert gate.wait(2)
        return real(session, **kw)
    f.compute = compute
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        assert entered.wait(2)
        lane._admit({'S2'}, time.time())  # S2's board row moves while the batch is still on S1
        assert 'S2' in lane.pending and 'S2' in lane.active
        gate.set()
        settle(lane)
        # Not read at its turn (the scope may hold rows cached before the event), then read once, with no cooldown
        # to wait out because the batch never read it.
        assert order == ['S1', 'S2'] and not lane.pending
        assert [e['batch'] for e in f.timing['board_recent'][-2:]] == [2, 1]
        assert 'S2' not in f._board_failures
    finally:
        gate.set()
        lane.close()


def test_an_event_during_a_chats_own_read_gets_a_trailing_read_after_its_cooldown(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2'))
    lane.cooldown = 0.05
    gate, entered = threading.Event(), threading.Event()
    real, order, unvouched = f.compute, [], []
    def compute(session, **kw):
        order.append(session)
        unvouched.append(session in f._board_failures)
        if session == 'S2' and order.count('S2') == 1:
            entered.set()
            assert gate.wait(2)
        return real(session, **kw)
    f.compute = compute
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        assert entered.wait(2)
        lane._admit({'S2'}, time.time())  # the event lands while S2 itself is being read
        gate.set()
        deadline = time.monotonic() + 5
        while (lane.pending or lane.active) and time.monotonic() < deadline:
            settle(lane)
            time.sleep(0.01)
        assert order == ['S1', 'S2', 'S2']
        assert unvouched[2] is True and 'S2' not in f._board_failures  # unvouched until the trailing read
    finally:
        gate.set()
        lane.close()


def test_one_failing_chat_does_not_fail_the_others(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2', 'S3'))
    real = f.compute
    def compute(session, **kw):
        if session == 'S2':
            raise RuntimeError('board unreadable')
        return real(session, **kw)
    f.compute = compute
    try:
        lane.pending.update(S1=1.0, S2=2.0, S3=3.0)
        lane.service()
        settle(lane)
        recent = [e['session'] for e in f.timing['board_recent']]
        assert recent[-2:] == ['S1', 'S3']
        assert 'S2' in f._board_failures and 'S2' in lane.retries
        assert BatchReaders.made[0].released == 1
    finally:
        lane.close()


def test_flag_off_reads_one_chat_per_worker_with_its_own_reader(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2'), batch='0')
    lane.cooldown = 0.0
    try:
        assert lane.batch_max == 1
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        assert list(lane.active) == ['S1']
        settle(lane)
        assert len(BatchReaders.made) == 2 and all(not r.scopes and r.released == 1 for r in BatchReaders.made)
        assert f.timing['board_recent'][-1]['batch'] == 1
    finally:
        lane.close()


def test_a_reader_without_batch_scope_still_shares_the_worker(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2'))
    lane.reader_factory = Readers  # an injected or older reader: no `batch`
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        assert len(set(lane.active.values())) == 1
        settle(lane)
        assert [e['session'] for e in f.timing['board_recent'][-2:]] == ['S1', 'S2']
    finally:
        lane.close()


def test_the_legacy_four_worker_lane_does_not_batch(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2'))
    lane.capacity = 4
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        assert len(set(lane.active.values())) == 2
        settle(lane)
        assert all(not r.scopes for r in BatchReaders.made)
    finally:
        lane.close()


def _gated(f):
    gate, entered, order = threading.Event(), threading.Event(), []
    real = f.compute
    def compute(session, **kw):
        order.append(session)
        entered.set()
        assert gate.wait(2)
        return real(session, **kw)
    f.compute = compute
    return gate, entered, order


def test_a_stop_ends_the_batch_at_the_current_chat(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2', 'S3'))
    gate, entered, order = _gated(f)
    try:
        lane.pending.update(S1=1.0, S2=2.0, S3=3.0)
        lane.service()
        [worker] = set(lane.active.values())
        assert entered.wait(2)
        lane.service_stop.set()  # unload, reload or close while the batch is on its first chat
        gate.set()
        worker.join(2)
        assert not worker.is_alive()
        assert order == ['S1']  # the remaining chats are not read
        assert not {'S2', 'S3'} & set(lane.active) and not lane.pending
        assert BatchReaders.made[0].released == 1
    finally:
        gate.set()
        lane.close()


def test_a_chat_that_becomes_visible_cuts_the_batch_short(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2', 'S3', 'V1'))
    lane.cooldown = 0.0
    gate, entered, order = _gated(f)
    try:
        lane.pending.update(S1=1.0, S2=2.0, S3=3.0)
        lane.service()
        assert entered.wait(2)
        f.changes_bytes([], subscriber='window', visible_session='V1')
        with f.lock:
            lane.pending['V1'] = 9.0  # its panel was just mounted: due now, admitted last
        lane.service()  # the service thread's turn: it records the new subscriber, and cannot start a worker yet
        gate.set()
        settle(lane)
        assert order[:2] == ['S1', 'V1'], order  # not after S2 and S3
        assert sorted(order[2:]) == ['S2', 'S3'] and not lane.pending and not lane.active
    finally:
        gate.set()
        lane.close()


def test_a_chat_unwatched_during_the_batch_is_dropped_unread(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2', 'S3'))
    gate, entered, order = _gated(f)
    try:
        lane.pending.update(S1=1.0, S2=2.0, S3=3.0)
        lane.service()
        assert entered.wait(2)
        with f.lock:
            f._watch_tokens.pop('S2', None)  # its watch ended while the batch was on S1
        gate.set()
        settle(lane)
        assert order == ['S1', 'S3'] and not lane.active
    finally:
        gate.set()
        lane.close()


def test_a_scope_that_fails_to_open_still_reads_every_chat(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2'))
    class Broken(BatchReaders):
        def batch(self, sessions):
            raise RuntimeError('lineage store unavailable')
    lane.reader_factory = Broken
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        settle(lane)
        assert [e['session'] for e in f.timing['board_recent'][-2:]] == ['S1', 'S2']
        assert not lane.active and BatchReaders.made[0].released == 1
    finally:
        lane.close()


def test_a_reader_that_cannot_be_made_for_the_batch_falls_back_per_chat(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2'))
    calls = []
    def factory():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('no connection')
        return BatchReaders()
    lane.reader_factory = factory
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        settle(lane)
        assert [e['session'] for e in f.timing['board_recent'][-2:]] == ['S1', 'S2']
        assert not lane.active and len(BatchReaders.made) == 2 and all(r.released == 1 for r in BatchReaders.made)
    finally:
        lane.close()


def test_more_visible_due_chats_than_one_batch_holds_are_all_read(monkeypatch):
    chats = [f'V{i:02}' for i in range(17)]
    f, lane = setup(monkeypatch, chats=chats)
    lane.cooldown = 0.0
    reads = []
    real = f.compute
    def compute(session, **kw):
        reads.append(session)
        return real(session, **kw)
    f.compute = compute
    try:
        with f.lock:
            f._panel_subscribers.update({s: (s, float('inf')) for s in chats})  # seventeen mounted panels
            lane.pending.update({s: float(at) for at, s in enumerate(chats)})
        lane.service()
        settle(lane)
        assert sorted(reads) == chats and not lane.pending and not lane.active
        assert len(BatchReaders.made) == 2  # sixteen, then the seventeenth: no batch was cut before a read
    finally:
        lane.close()


def test_a_deferred_chat_keeps_its_place_in_the_queue(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2', 'S3'))
    gate, entered, order = _gated(f)
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        assert entered.wait(2)
        with f.lock:
            lane.pending['S3'] = 3.0          # a later admission, waiting for the next worker
        lane._admit({'S2'}, time.time())      # S2 is re-admitted before its turn
        gate.set()
        settle(lane)
        assert order == ['S1', 'S2', 'S3']    # S2 still ahead of S3, not behind it on the newer event time
    finally:
        gate.set()
        lane.close()


def test_feed_stop_ends_the_batch_at_the_current_chat(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2', 'S3'))
    gate, entered, order = _gated(f)
    try:
        lane.pending.update(S1=1.0, S2=2.0, S3=3.0)
        lane.service()
        [worker] = set(lane.active.values())
        assert entered.wait(2)
        f.stopped.set()
        gate.set()
        worker.join(2)
        assert not worker.is_alive() and order == ['S1']
        assert not {'S2', 'S3'} & set(lane.active)
    finally:
        gate.set()
        lane.close()


def test_a_chat_later_in_the_batch_that_becomes_visible_is_not_left_behind_hidden_ones(monkeypatch):
    f, lane = setup(monkeypatch, chats=('H1', 'H2', 'H3', 'H4'))
    lane.cooldown = 0.0
    gate, entered, order = _gated(f)
    try:
        lane.pending.update(H1=1.0, H2=2.0, H3=3.0, H4=4.0)
        lane.service()
        assert entered.wait(2)
        f.changes_bytes([], subscriber='window', visible_session='H4')  # its panel opens while H1 is being read
        lane.service()
        gate.set()
        settle(lane)
        assert order[:2] == ['H1', 'H4'], order
        assert sorted(order) == ['H1', 'H2', 'H3', 'H4'] and not lane.pending and not lane.active
    finally:
        gate.set()
        lane.close()


def test_the_first_chat_of_a_worker_is_read_even_when_re_admitted_or_outranked(monkeypatch):
    f, lane = setup(monkeypatch, chats=('S1', 'S2', 'V1'))
    lane.cooldown = 0.0
    made = threading.Event()
    hold = threading.Event()
    class Slow(BatchReaders):
        def __init__(self):
            made.set()
            assert hold.wait(2)  # the worker is between `service()` and its first turn
            super().__init__()
    lane.reader_factory = Slow
    order = []
    real = f.compute
    def compute(session, **kw):
        order.append(session)
        return real(session, **kw)
    f.compute = compute
    try:
        lane.pending.update(S1=1.0, S2=2.0)
        lane.service()
        assert made.wait(2)
        lane._admit({'S1'}, time.time())                                 # the head is re-admitted
        f.changes_bytes([], subscriber='window', visible_session='V1')   # and a visible chat comes due
        with f.lock:
            lane.pending['V1'] = 9.0
        lane.service()
        hold.set()
        settle(lane)
        assert order[0] == 'S1', order           # read once: that read serves the newer event too
        assert order.count('S1') == 1 and order[1] == 'V1' and sorted(order) == ['S1', 'S2', 'V1']
    finally:
        hold.set()
        lane.close()


def test_a_visible_chat_re_admitted_during_its_own_read_does_not_cut_the_hidden_turns(monkeypatch):
    f, lane = setup(monkeypatch, chats=('V1', 'H1', 'H2'))
    lane.cooldown = 30.0
    gate, entered = threading.Event(), threading.Event()
    real, order = f.compute, []
    def compute(session, **kw):
        order.append(session)
        if session == 'V1':
            entered.set()
            assert gate.wait(2)
        return real(session, **kw)
    f.compute = compute
    try:
        f.changes_bytes([], subscriber='window', visible_session='V1')
        lane.pending.update(V1=1.0, H1=2.0, H2=3.0)
        lane.service()
        [worker] = set(lane.active.values())
        assert entered.wait(2)
        lane._admit({'V1'}, time.time())  # V1 owes a trailing read after its cooldown; H1 and H2 must not wait
        gate.set()
        worker.join(2)
        assert order == ['V1', 'H1', 'H2']
        assert len(BatchReaders.made) == 1  # one worker read all three: no cut, no second set-up
    finally:
        gate.set()
        lane.pending.clear()
        lane.close()
