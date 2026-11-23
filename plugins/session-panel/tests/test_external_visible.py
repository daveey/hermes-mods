"""External capture is visibility-gated, not only expensive hydration."""
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
        f.changes_bytes([], subscriber='window', visible_session='S1')
        lane.service()
        settle(lane)
        assert 'S1' not in f._board_dirty and 'S2' in f._board_dirty
    finally:
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
