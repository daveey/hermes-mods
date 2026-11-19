"""Grant-at-start pacing (no slot is held while a call waits) and chat/card/cron classification."""
import json
import random
import sys
import threading
import types

import pytest

from throughput_under_test import throttle as T

NOW = 1_800_000_030.0


class Clock:
    def __init__(self, t=NOW):
        self.t, self.sleeps, self.on_sleep = float(t), [], None

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


def _gate(root, clock, plan):
    """A gate whose level is ``plan`` (a list, so a test can change the level under a waiting call)."""
    gate = T.Gate(root, root / "no-series.sqlite", sleep=clock.sleep, clock=clock.now)
    gate.plan = lambda now: tuple(plan)
    return gate


def _state(root):
    try:
        return json.loads(T.pace_path(root).read_text())
    except (OSError, ValueError):
        return {}


def _stop_after(polls):
    seen = [0]

    def stop():
        seen[0] += 1
        return seen[0] >= polls
    return stop


# --- a. the ratchet -------------------------------------------------------------------------------------------

def test_abandoned_waits_and_a_level_change_leave_no_queue(tmp_path):
    clock, plan = Clock(), ["pace", 600.0, 1.0]
    gate = _gate(tmp_path, clock, plan)
    assert gate.wait() == 0.0  # idle: the first call starts at once
    for _ in range(10):  # ten calls start waiting and give up (a stop, a dead process, the aux bound)
        gate.wait(stop=lambda: True)
    plan[:] = ["pace", 1.0, 2.0]  # the level changes: one call a second
    assert gate.wait() <= 1.0  # not ~6000 s behind ten abandoned slots


class Boom(Exception):
    pass


def test_no_sequence_of_abandons_level_changes_or_failures_pushes_the_next_start_past_one_interval(
        tmp_path, monkeypatch):
    monkeypatch.setattr(T, "POLL_S", 1e9)  # each nap ends exactly at the start it was told about: fast and exact
    rng = random.Random(20261118)
    intervals = [1.0, 5.0, 30.0, 120.0, 600.0]
    for case in range(2000):
        root = tmp_path / str(case)
        clock = Clock()
        plan = ["pace", rng.choice(intervals), 0.0]
        gate = _gate(root, clock, plan)
        seen = [plan[1]]
        for _ in range(rng.randint(1, 8)):
            event = rng.choice(["grant", "abandon", "max_s", "exception", "level", "idle"])
            if event == "grant":
                gate.wait()
            elif event == "abandon":
                gate.wait(stop=_stop_after(rng.randint(1, 3)))
            elif event == "max_s":
                gate.wait(max_s=rng.uniform(0.0, 700.0))
            elif event == "exception":
                fail_at = rng.randint(1, 2)

                def explode(n, fail_at=fail_at):
                    if n >= fail_at:
                        raise Boom()
                clock.on_sleep = explode
                try:
                    gate.wait()
                except Boom:
                    pass
                clock.on_sleep, clock.sleeps = None, []
            elif event == "level":
                plan[:] = ["pace", rng.choice(intervals), plan[2] + 1.0]
                seen.append(plan[1])
            else:
                clock.t += rng.uniform(0.0, 900.0)
            state, now = _state(root), clock.t
            if "last_start" in state:
                assert state["last_start"] <= now, (case, event, state)
            if "next_at" in state:
                assert state["next_at"] - now <= max(seen), (case, event, state)
        t0 = clock.t
        gate.wait()
        assert clock.t - t0 <= plan[1], (case, _state(root))  # the next call waits at most one interval


# --- b. counting --------------------------------------------------------------------------------------------

def test_only_started_calls_count_in_the_minute_they_start(tmp_path):
    clock, plan = Clock(), ["pace", 600.0, 1.0]
    gate = _gate(tmp_path, clock, plan)
    gate.wait()  # starts at NOW
    for _ in range(10):
        gate.wait(stop=lambda: True)  # abandoned: never counted
    pacer = T.Pacer(tmp_path)
    for t in range(int(NOW), int(NOW) + 3 * 3600, 60):
        assert pacer.passes(t) <= 1
    assert pacer.passes(NOW) == 1
    assert T.call_tokens(30_000.0, pacer.passes(NOW + 60)) == pytest.approx(30_000.0)  # not 30k / 11
    clock.t = NOW + 600
    gate.wait()  # the next one starts one interval later and is counted in that minute
    assert set(_state(tmp_path)["calls"]) == {str(int(NOW) // 60), str(int(NOW + 600) // 60)}


# --- c. migrating a reserve-before-wait file ----------------------------------------------------------------

@pytest.mark.parametrize("stored", [{"next_at": NOW + 6000, "calls": {}},
                                    {"next_at": NOW + 6000, "last_start": NOW + 5000}])
def test_a_stamp_far_ahead_is_clamped_on_read(tmp_path, stored):
    T.pace_path(tmp_path).parent.mkdir(parents=True)
    T.pace_path(tmp_path).write_text(json.dumps(stored))
    clock = Clock()
    assert _gate(tmp_path, clock, ["pace", 10.0, 1.0]).wait() <= 10.0


def test_an_older_process_sharing_the_file_stays_spaced(tmp_path):
    T.pace_path(tmp_path).parent.mkdir(parents=True)
    T.pace_path(tmp_path).write_text(json.dumps({"next_at": NOW + 5}))  # an old process booked a slot 5 s out
    clock = Clock()
    assert _gate(tmp_path, clock, ["pace", 10.0, 1.0]).wait() == pytest.approx(5.0)  # within one interval: honoured
    state = _state(tmp_path)
    # what an old process does with the new file: slot = max(next_at, now), i.e. one interval after this start
    assert state["last_start"] == NOW + 5 and state["next_at"] == NOW + 15
    assert T.Pacer(tmp_path).try_take(NOW + 6, 1.0) == pytest.approx(0.0)  # our own next_at never lengthens a
    # shorter interval: 1 s after the last start, not the 10 s it was written with


# --- d. two processes on one file ----------------------------------------------------------------------------

def test_concurrent_waiters_on_the_real_lock_are_spaced_and_bounded(tmp_path, monkeypatch):
    interval, per_process, processes = 0.3, 3, 2
    n = per_process * processes
    grants, waits, guard = [], [], threading.Lock()
    real = T.Pacer.try_take

    def recording(self, now, iv):
        left = real(self, now, iv)
        if left <= 0:
            with guard:
                grants.append(now)
        return left
    monkeypatch.setattr(T.Pacer, "try_take", recording)
    gates = []
    for _ in range(processes):  # one gate (own Pacer, own lock fd per call) per "process"
        gate = T.Gate(tmp_path, tmp_path / "no-series.sqlite")
        gate.plan = lambda now: ("pace", interval, 1.0)
        gates.append(gate)
    barrier = threading.Barrier(n)

    def run(gate):
        barrier.wait()
        waited = gate.wait()
        with guard:
            waits.append(waited)
    threads = [threading.Thread(target=run, args=(gates[i % processes],)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    assert len(grants) == n and len(waits) == n
    starts = sorted(grants)
    assert all(b - a >= interval for a, b in zip(starts, starts[1:]))
    assert max(waits) <= n * interval


# --- e. classification ---------------------------------------------------------------------------------------

def test_throttled_only_cards_and_cron():
    assert T.throttled("cli", env={}) is False and T.throttled("", env={}) is False
    assert T.throttled(None, env={}) is False and T.throttled("telegram", env={}) is False
    assert T.throttled("cron", env={}) is True
    assert T.throttled("cli", env={"HERMES_KANBAN_TASK": "t_1"}) is True
    assert T.throttled("", env={"HERMES_KANBAN_WORKER_TASK": "t_1"}) is True
    assert T.throttled("cli", env={"HERMES_KANBAN_TASK": ""}) is False


def _stub_heartbeat(monkeypatch):
    tools = types.ModuleType("tools")
    kanban_tools = types.ModuleType("tools.kanban_tools")
    kanban_tools.heartbeat_current_worker_from_env = lambda: None
    tools.kanban_tools = kanban_tools
    monkeypatch.setitem(sys.modules, "tools", tools)
    monkeypatch.setitem(sys.modules, "tools.kanban_tools", kanban_tools)


def test_a_chat_call_never_waits_or_touches_the_pace(tmp_path):
    T.write_level(tmp_path, 100)  # the cap at its minimum: every throttled call waits
    clock = Clock()
    gate = T.Gate(tmp_path, tmp_path / "no-series.sqlite", sleep=clock.sleep, clock=clock.now)
    assert gate.middleware(next_call=lambda request: "r", request={}, platform="cli") == "r"
    assert clock.sleeps == [] and "last_start" not in _state(tmp_path) and "next_at" not in _state(tmp_path)
    assert T.Pacer(tmp_path).passes(NOW) == 1  # still counted for tokens-per-call
    paced = _gate(tmp_path / "p", clock, ["pace", 600.0, 1.0])
    assert paced.wait() == 0.0  # a card call just started: the next throttled start is 600 s away
    before = {k: v for k, v in _state(tmp_path / "p").items() if k != "calls"}
    assert paced.middleware(next_call=lambda request: "r", request={}, platform="cli") == "r"
    assert clock.sleeps == [] and {k: v for k, v in _state(tmp_path / "p").items() if k != "calls"} == before


@pytest.mark.parametrize("env,platform", [("HERMES_KANBAN_TASK", "cli"), ("HERMES_KANBAN_WORKER_TASK", ""),
                                          (None, "cron")])
def test_card_and_cron_calls_wait(tmp_path, monkeypatch, env, platform):
    _stub_heartbeat(monkeypatch)
    if env:
        monkeypatch.setenv(env, "t_1")
    T.write_level(tmp_path, 100)
    clock = Clock()
    gate = T.Gate(tmp_path, tmp_path / "no-series.sqlite", sleep=clock.sleep, clock=clock.now)
    clock.on_sleep = lambda n: T.write_level(tmp_path, 0) if n == 2 else None
    assert gate.middleware(next_call=lambda request: "r", request={}, platform=platform) == "r"
    assert len(clock.sleeps) == 2
    T.write_level(tmp_path, 100)
    clock = Clock()
    gate = T.Gate(tmp_path, tmp_path / "no-series.sqlite", sleep=clock.sleep, clock=clock.now)
    gate.aux_hold(aux_task="compression", platform=platform)
    assert sum(clock.sleeps) == pytest.approx(T.AUX_HOLD_MAX_S)  # the auxiliary path follows the same rule


@pytest.mark.parametrize("platform", ["cli", "", None])
def test_a_chat_auxiliary_call_never_waits(tmp_path, platform):
    T.write_level(tmp_path, 100)
    clock = Clock()
    gate = T.Gate(tmp_path, tmp_path / "no-series.sqlite", sleep=clock.sleep, clock=clock.now)
    kwargs = {} if platform is None else {"platform": platform}
    gate.aux_hold(aux_task="title_generation", **kwargs)
    assert clock.sleeps == [] and "last_start" not in _state(tmp_path)
