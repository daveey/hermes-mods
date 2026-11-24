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


def _duty_feed(api, monkeypatch, clock, cold):
    """A feed double whose full pass takes 10 s and whose wake stays set (continuous source writes)."""
    ticks, keyed = [], []
    class Stop:
        def is_set(self):
            return clock[0] >= 200.0
        def wait(self, delay):
            clock[0] += delay
            return self.is_set()
    class Flag:
        def __init__(self, on):
            self.on = on
        def set(self):
            self.on = True
        def clear(self):
            self.on = False
        def is_set(self):
            return self.on
        def wait(self, timeout=None):
            if not self.on:
                clock[0] += timeout or 1.0  # an idle wait passes time, so the loop always reaches its stop
            return self.on
    def tick():
        ticks.append(clock[0])
        clock[0] += 10.0
        if not cold:
            f._cold.clear()  # as Feed._tick does; a chat still waiting for its first bundle keeps it set
    f = SimpleNamespace(stopped=Stop(), _wake=Flag(True), _cold=Flag(cold), _retry=set(),
                        readers=SimpleNamespace(release=lambda: None), tick=tick, beat=lambda: None,
                        refresh_keyed=lambda sessions: keyed.append((clock[0], set(sessions))),
                        clock=lambda: clock[0], lock=threading.Lock(), rev=0, interest={}, _successor=True)
    monkeypatch.setattr(api, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(api, '_event_driven', lambda f: True)
    monkeypatch.setattr(api, 'write_timing', lambda f: None)
    monkeypatch.setattr(api, 'save_snapshot', lambda *args, **kwargs: True)
    return f, ticks, keyed


def _api(monkeypatch):
    api = load('_spf_mitigation', __import__('pathlib').Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py')
    monkeypatch.setenv('HERMES_PANEL_COALESCE', '1')
    monkeypatch.delenv('HERMES_PANEL_DUTY', raising=False)
    return api


def test_full_pass_rests_twice_its_duration_under_continuous_writes(monkeypatch):
    api = _api(monkeypatch)
    clock = [100.0]
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    api._run(f)
    # 10 s passes: without the rest they start back to back (100, 110, 120, ...). With it the next starts once
    # 20 s have passed since the last ended, on the 3 s floor grid: 30 to 33 s apart, never the 40 s of a 3x rest.
    assert ticks[0] == 100.0
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) >= 3 and all(30.0 <= g <= 33.0 for g in gaps), ticks
    assert f.timing['duty_deferrals'] > 0


def test_keyed_batch_during_a_rest_is_served_and_the_deferred_pass_still_runs(monkeypatch):
    api = _api(monkeypatch)
    clock = [100.0]
    f, ticks, keyed = _duty_feed(api, monkeypatch, clock, cold=False)
    real_tick = f.tick
    def tick():
        real_tick()
        f._wake.clear()  # as Feed._tick does
        if len(ticks) == 1:
            f._legacy_wake = True  # exactly one source write follows the first pass; none after it
            f._wake.set()
    f.tick = tick
    beats = []
    def beat():
        beats.append(clock[0])
        if len(beats) == 3:
            f._keyed_pending = {'chat-a'}  # a hub hint lands in the middle of the rest
    f.beat = beat
    api._run(f)
    assert len(keyed) == 1 and keyed[0][1] == {'chat-a'}, keyed
    assert len(ticks) == 2, ticks  # the pass the write made due was kept through the keyed iteration
    assert ticks[0] + 10.0 < keyed[0][0] < ticks[1], (keyed, ticks)
    assert ticks[1] - ticks[0] >= 30.0, ticks


def test_cold_reader_a_pending_retry_and_the_off_switch_skip_the_rest(monkeypatch):
    api = _api(monkeypatch)
    clock = [100.0]
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=True)  # a chat still waits for its first bundle
    api._run(f)
    assert ticks[1] - ticks[0] < 30.0, ticks
    clock[0] = 100.0
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    f._retry = {'chat-b'}  # a failed pass or a chat left to retry: recovery is not held
    f._hydrate_retry_at = 110.1  # ...while its budgeted retry clock is armed (Feed._spend)
    api._run(f)
    assert ticks[1] - ticks[0] < 30.0, ticks
    monkeypatch.setenv('HERMES_PANEL_DUTY', '0')
    clock[0] = 100.0
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    api._run(f)
    assert ticks[1] - ticks[0] < 30.0, ticks


def test_a_pass_that_raises_earns_no_rest(monkeypatch):
    api = _api(monkeypatch)
    clock = [100.0]
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    real_tick = f.tick
    def tick():
        real_tick()
        if len(ticks) == 1:
            f._cold.set()  # a reader opens a new chat: the next pass runs inside the first one's rest...
        elif len(ticks) == 2:
            raise RuntimeError('source locked')  # ...and fails without leaving a retry behind
    f.tick = tick
    api._run(f)
    # The failed pass cleared the first pass's rest: the third starts on the floor, not 20 s after the first ended.
    assert ticks[:3] == [100.0, 110.0, 120.0], ticks


def test_a_deferred_reconcile_is_not_held(monkeypatch):
    api = _api(monkeypatch)
    clock = [100.0]
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    f._reconcile_deferred = {'chat-c'}  # the producer could not finish this chat; its backoff retry is due
    f._reconcile_retry_at = 110.1
    api._run(f)
    assert ticks[1] - ticks[0] < 30.0, ticks


@pytest.mark.parametrize('owed', ['_reconcile_deferred', '_retry'])
def test_a_chat_out_of_retries_does_not_keep_the_rest_off(monkeypatch, owed):
    """The hub wake storm (2026-11-24): four chats whose remote reconciliation defers on every pass stayed in
    `_reconcile_deferred` with no retry clock armed (out of retries, still owed: test_selective_producer pins
    that Feed state), and the non-empty set alone switched the rest off, so 6-21 s passes ran back to back."""
    api = _api(monkeypatch)
    clock = [100.0]
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    setattr(f, owed, {'chat-c'})
    f._admission_retry_at = f._hydrate_retry_at = f._reconcile_retry_at = None  # every budget spent
    api._run(f)
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) >= 3 and all(30.0 <= g <= 33.0 for g in gaps), ticks
    assert f.timing['duty_deferrals'] > 0


def test_a_write_during_the_rest_of_an_exhausted_chat_is_not_lost_and_a_new_failure_is_retried_at_once(monkeypatch):
    """Notification loss: the write that lands while the feed rests still gets its pass (once, after the rest),
    and a chat whose retry clock is armed again by that pass is retried without waiting out another rest."""
    api = _api(monkeypatch)
    clock = [100.0]
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    f._reconcile_deferred = {'chat-c'}
    f._reconcile_retry_at = None
    real_tick = f.tick
    def tick():
        real_tick()
        f._wake.clear()  # as Feed._tick does
        if len(ticks) == 2:
            f._retry = {'chat-d'}  # the second pass leaves a fresh failure with retries left
            f._hydrate_retry_at = clock[0] + 0.1
            f._wake.set()
        elif len(ticks) == 3:
            f._retry, f._hydrate_retry_at = set(), None  # the retry succeeded
    f.tick = tick
    beats = []
    def beat():
        beats.append(clock[0])
        if len(beats) == 3 and len(ticks) == 1:
            f._wake.set()  # exactly one source write, in the middle of the first pass's rest
    f.beat = beat
    api._run(f)
    assert len(ticks) == 3, ticks
    assert ticks[1] - ticks[0] >= 30.0, ticks  # the write waited out the rest, and was served
    assert ticks[2] - ticks[1] < 15.0, ticks   # the armed retry ran on the floor, not after a 20 s rest


def test_recovery_due_needs_owed_work_and_an_armed_clock(monkeypatch):
    api = _api(monkeypatch)
    due = lambda **kw: api._recovery_due(SimpleNamespace(**kw))
    assert not due()
    assert not due(_retry={'a'}) and not due(_reconcile_deferred={'a'})
    assert not due(_retry=set(), _reconcile_deferred=set(), _hydrate_retry_at=5.0)  # a stale clock alone
    assert due(_retry={'a'}, _hydrate_retry_at=5.0)
    assert due(_reconcile_deferred={'a'}, _reconcile_retry_at=5.0)
    assert due(_retry={'a'}, _admission_retry_at=5.0)


def test_rest_without_the_coalesce_floor(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setenv('HERMES_PANEL_COALESCE', '0')
    clock = [100.0]
    f, ticks, _ = _duty_feed(api, monkeypatch, clock, cold=False)
    api._run(f)
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert gaps and all(30.0 <= g <= 31.0 for g in gaps), ticks


def test_rest_keeps_a_held_bundle_inside_its_trust_window(monkeypatch):
    api = _api(monkeypatch)
    monkeypatch.setattr(api, 'time', SimpleNamespace(monotonic=lambda: 1000.0))
    owed = lambda took, **kw: api._duty_owed(SimpleNamespace(_full_pass=(1000.0, took), **kw))
    assert owed(5.0) == 10.0   # twice the pass
    assert owed(10.0) == 20.0
    assert owed(20.0) == 10.0  # bounded: 20 s read + 10 s rest + 20 s next pass + the 10 s margin = 60 s
    assert owed(25.0) == 0.0   # a pass this long already fills the window with the next one
    assert owed(56.0) == 0.0
    assert owed(5.0, full_every_s=25.0) == 5.0
    assert api._duty_owed(SimpleNamespace()) == 0.0
    # The age of a held bundle when the next pass replaces it never exceeds the window, floor grid included.
    for took in (1.0, 5.0, 10.0, 12.5, 15.0, 20.0, 24.0, 30.0):
        assert took + owed(took) + 3.0 + took <= max(60.0, 2 * took + 3.0), took
    monkeypatch.setattr(api, 'time', SimpleNamespace(monotonic=lambda: 1012.0))
    assert owed(10.0) == 8.0   # counted from the pass's end
