"""Reversible panel mitigation and event-driven producer cadence."""
import threading
from types import SimpleNamespace

import pytest
from test_routes import load


@pytest.mark.parametrize('value,enabled', [('1', True), ('0', False), ('false', False)])
def test_flags(monkeypatch, value, enabled):
    api = load('_spf_mitigation', __import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    monkeypatch.setenv('HERMES_PANEL_COALESCE', value)
    assert api._mitigation('HERMES_PANEL_COALESCE') is enabled


@pytest.mark.parametrize('lane', ['feed', 'board', 'decisions'])
@pytest.mark.parametrize('burst', [True, False])
def test_producer_burst_and_idle(monkeypatch, lane, burst):
    api = load('_spf_mitigation', __import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    monkeypatch.setenv('HERMES_PANEL_COALESCE', '1')
    clock = [100.0]
    schedule = [101 + i / 100 for i in range(100)] if burst else []
    calls = []
    class Stop:
        done = False
        def is_set(self):
            return self.done
        def wait(self, delay):
            clock[0] += delay
            return self.done
    stop = Stop()
    class Event:
        def set(self):
            pass
        def clear(self):
            while schedule and schedule[0] <= clock[0]:
                schedule.pop(0)
        def wait(self, timeout=None):
            if schedule:
                clock[0] = max(clock[0], schedule[0])
                return True
            stop.done = True
            return False
    event = Event()
    readers = SimpleNamespace(release=lambda: None)
    def step(*args, **kwargs):
        event.clear()
        calls.append(clock[0])
    f = SimpleNamespace(stopped=stop, _wake=event, _cold=event, _decide=event, _board=event,
                        readers=readers, refresh_decisions=step, tick=step, beat=lambda: None,
                        clock=lambda: clock[0], lock=threading.Lock(), rev=0, interest={}, _successor=True)
    monkeypatch.setattr(api, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(api, '_event_driven', lambda f: True)
    monkeypatch.setattr(api, 'CoreReaders', lambda: readers)
    monkeypatch.setattr(api, 'write_timing', lambda f: None)
    monkeypatch.setattr(api, 'save_snapshot', lambda *args, **kwargs: True)
    class Lane:
        def __init__(self, *args):
            pass
        def start_service(self):
            pass
        def retry_at(self):
            return None
        def step(self):
            step()
        def close(self):
            pass
    if lane == 'feed':
        # The feed's startup _wake.set() is explicit demand.
        schedule.insert(0, 100)
        api._run(f)
    elif lane == 'board':
        api._run_board(f, Lane)
    else:
        api._run_decisions(f)
    assert calls == ([100.0, 103.0] if burst else [100.0])
    assert len([t for t in calls if t >= 101]) <= 2
    # Only the explicit startup seed: zero event-free refreshes.


def test_legacy_settle_can_be_cut_short_by_cold_reader(monkeypatch):
    api = load('_spf_mitigation', __import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    monkeypatch.setenv('HERMES_PANEL_COALESCE', '0')
    waits = []
    f = SimpleNamespace(_cold=SimpleNamespace(wait=waits.append),
                        stopped=SimpleNamespace(wait=lambda _: pytest.fail('new floor used')))
    monkeypatch.setattr(api, 'time', SimpleNamespace(monotonic=lambda: 100.0))
    api._settle(f, 100.0)
    assert waits == [api.MIN_GAP_S]
