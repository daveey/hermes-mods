"""Urgent board mitigation: bounded reads with demand-driven trailing service."""
import threading
import time

import pytest

from test_feed import Readers, feedmod


def test_hundred_admissions_coalesce_and_publish_trailing_change(monkeypatch):
    monkeypatch.delenv('HERMES_PANEL_BOARD_COALESCE', raising=False)
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S2'])
    lane = feedmod.BoardLane(f, lambda: r)
    assert lane.capacity == 1 and lane.cooldown == 3.0
    original = f.compute
    starts, ends = [], []
    first, trailing = threading.Event(), threading.Event()

    def compute(*args, **kwargs):
        starts.append(time.monotonic())
        # Include slow work: cooldown begins at completion, not admission.
        if len(starts) == 1:
            time.sleep(.2)
        result = original(*args, **kwargs)
        ends.append(time.monotonic())
        (first if len(starts) == 1 else trailing).set()
        return result

    f.compute = compute
    lane.start_service()
    try:
        started = time.monotonic()
        for i in range(100):
            lane._admit({'S2'}, time.time())
            lane.service_wake.set()
            time.sleep(.005)
        assert first.wait(1)
        assert time.monotonic() - started < 1
        assert len(starts) == 1
        r.links['S2'] = [{**r.links['S2'][0], 'status': 'ready'}]
        lane._admit({'S2'}, time.time())
        lane.service_wake.set()
        assert trailing.wait(4), 'cooldown must wake without another source event'
        deadline = time.monotonic() + 1
        while lane.active and time.monotonic() < deadline:
            time.sleep(.01)
        assert starts[1] >= ends[0] + 3.0
        assert len(starts) == 2
        assert not lane.pending
        assert f.bundles['S2']['bundle']['tasks'][0]['status'] == 'ready'
        time.sleep(.05)
        assert len(starts) == 2
    finally:
        lane.close()


def test_serial_capacity_serves_other_session_while_first_cools(monkeypatch):
    monkeypatch.delenv('HERMES_PANEL_BOARD_COALESCE', raising=False)
    r = Readers()
    f = feedmod.Feed(r, emit=lambda p: None)
    f.changes(['S1', 'S2'])
    lane = feedmod.BoardLane(f, lambda: r)
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    original = f.compute

    def compute(session, **kwargs):
        if session == 'S1':
            entered.set()
            assert release.wait(2)
        else:
            second.set()
        return original(session, **kwargs)

    f.compute = compute
    lane.start_service()
    try:
        lane._admit({'S1'}, time.time())
        lane.service_wake.set()
        assert entered.wait(1)
        lane._admit({'S1', 'S2'}, time.time())
        lane.service_wake.set()
        assert not second.wait(.05)
        assert len(lane.active) == 1
        release.set()
        assert second.wait(1)
        assert 'S1' in lane.pending
    finally:
        release.set()
        lane.close()


@pytest.mark.parametrize('value', ['0', 'false', 'off', 'no', 'OFF'])
def test_flag_rollback_restores_legacy_capacity(monkeypatch, value):
    monkeypatch.setenv('HERMES_PANEL_BOARD_COALESCE', value)
    lane = feedmod.BoardLane(feedmod.Feed(Readers(), emit=lambda p: None), Readers)
    assert lane.capacity == 4 and lane.cooldown == 0
    lane.close()
