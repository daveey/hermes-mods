"""External capture is visibility-gated, not only expensive hydration."""
import threading

from test_board_visible import setup, settle
from test_feed import Readers, feedmod


def test_hidden_external_capture_keeps_attach_demand(monkeypatch):
    f, initial, _ = setup(monkeypatch)
    initial.close()
    captures = []
    class External(Readers):
        def external_capture(self, held):
            captures.append(set(held))
            return {s: ('current',) for s in held}
    lane = feedmod.BoardLane(f, External)
    try:
        lane.step()
        assert captures == [set()]
        assert f._board_dirty == {'S1', 'S2'}
        pushed = []
        f.emit = pushed.append
        f.last_emit = 0
        f._publish({}, heartbeat=True)
        assert pushed[-1]['fresh'] == []  # deferred hidden inputs are not vouched for
        f.changes_bytes([], subscriber='window', visible_session='S1')
        lane.service()
        settle(lane)
        assert 'S1' not in f._board_dirty and 'S2' in f._board_dirty
        f.last_emit = 0
        f._publish({}, heartbeat=True)
        assert pushed[-1]['fresh'] == ['S1']  # only completed attach hydration vouches
    finally:
        lane.close()


def test_external_deferral_racing_read_cannot_restore_freshness(monkeypatch):
    f, initial, _ = setup(monkeypatch)
    initial.close()
    entered, release = threading.Event(), threading.Event()
    class External(Readers):
        def external_capture(self, held):
            return {s: ('external',) for s in held}
    lane = feedmod.BoardLane(f, External)
    compute = f.compute
    pushed = []
    f.emit = pushed.append
    def slow(session, **kw):
        bundle = compute(session, **kw)
        entered.set()
        assert release.wait(3)
        return bundle
    f.compute = slow
    try:
        f.changes_bytes([], subscriber='window', visible_session='S1')
        lane._admit({'S1'}, 1)
        lane.service()
        assert entered.wait(2)
        f.changes_bytes([], subscriber='window', visible_session=None)
        lane.service()
        lane.step()  # no local changed_cards: only skipped hidden external demand
        assert 'S1' in f._board_dirty
        release.set()
        settle(lane)
        f.last_emit = 0
        f._publish({}, heartbeat=True)
        assert pushed[-1]['fresh'] == []
        assert 'S1' in f._board_failures
    finally:
        release.set()
        lane.close()


def test_flag_off_preserves_full_external_capture(monkeypatch):
    monkeypatch.setenv('HERMES_PANEL_EXTERNAL_VISIBLE_ONLY', '0')
    f, initial, _ = setup(monkeypatch)
    initial.close()
    captures = []
    class External(Readers):
        def external_capture(self, held):
            captures.append(set(held))
            return {s: () for s in held}
    lane = feedmod.BoardLane(f, External)
    try:
        lane.step()
        assert captures == [{'S1', 'S2'}]
    finally:
        lane.close()
