"""Registry capacity and nonblocking, fail-unknown sidebar enrichment."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest


def load():
    spec = importlib.util.spec_from_file_location('lane_metrics_fleet_test', Path(__file__).parents[1] / 'dashboard/metrics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_configured_capacity_not_utilization_or_pickup_policy():
    registry = {'me': 'local', 'machines': {
        'local': {'state': 'active', 'max_card_runs': 3, 'pickup': False, 'pinned_only': True,
                  'metrics': {'active_card_runs': 99}, 'last_seen': 0},
        'worker': {'max_card_runs': 5, 'hold': False},
        'zero': {'state': 'active', 'max_card_runs': 0},
        'pinned-unbounded': {'state': 'active', 'pinned_only': True, 'max_card_runs': None},
        'unknown-capacity': {'state': 'active', 'max_card_runs': None},
        'held': {'state': 'active', 'hold': True, 'max_card_runs': None},
        'retired': {'state': 'retired', 'max_card_runs': 'invalid'},
    }}
    before = deepcopy(registry)
    assert load().registry_slots(registry) == 5  # pinned-only capacity is not shared pool
    assert registry == before


@pytest.mark.parametrize('registry,expected', [
    (None, None), ({}, None), ({'machines': None}, None), ({'machines': []}, None),
    ({'machines': {'broken': None}}, None), ({'machines': {'broken': []}}, None),
    ({'machines': {}}, 0),
    ({'machines': {'held': {'hold': True}, 'off': {'state': 'retired'}}}, 0),
])
def test_registry_unavailable_malformed_and_empty_are_distinct(registry, expected):
    assert load().registry_slots(registry) == expected


@pytest.mark.parametrize('cap', [True, False, -1, 1.5, '4'])
def test_malformed_capacity_invalidates_pool(cap):
    assert load().registry_slots({'machines': {'known': {'max_card_runs': 4},
                                               'unknown': {'max_card_runs': cap}}}) is None


def test_fetch_explicitly_rejects_core_last_good_outage_cache(monkeypatch):
    calls = []
    def registry(*, require_fresh=False):
        calls.append(require_fresh)
        return None if require_fresh else {'machines': {'stale': {'max_card_runs': 50}}}
    # No real core import or Board I/O, and restore the module binding after this test.
    monkeypatch.setitem(sys.modules, 'hermes_cli', SimpleNamespace(
        kanban_worker_node=SimpleNamespace(registry=registry)))
    assert load()._fetch_registry_slots() is None
    assert calls == [True]


def deferred_cache(monkeypatch):
    metrics = load()
    clock, jobs = [0.], []
    monkeypatch.setattr(metrics, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    class DeferredThread:
        def __init__(self, *, target, daemon, name):
            assert daemon and name == 'lane-fleet-slots'
            self.target = target
        def start(self):
            jobs.append(self.target)
    monkeypatch.setattr(metrics, 'threading', SimpleNamespace(Thread=DeferredThread))
    return metrics, clock, jobs


def test_cold_ttl_expiry_singleflight_and_completion_based_age(monkeypatch):
    metrics, clock, jobs = deferred_cache(monkeypatch)
    calls = []
    def fetch():
        calls.append(clock[0])
        return 8
    monkeypatch.setattr(metrics, '_fetch_registry_slots', fetch)
    assert metrics.fleet_slots() is None  # cold works even at monotonic zero
    for _ in range(20):
        assert metrics.fleet_slots() is None
    assert len(jobs) == 1 and calls == []  # never fetch on caller's stack
    clock[0] = 10.
    jobs.pop(0)()
    assert calls == [10.]
    assert metrics.fleet_slots() == 8
    clock[0] = 69.999
    assert metrics.fleet_slots() == 8
    assert not jobs
    clock[0] = 70.
    assert metrics.fleet_slots() is None  # no stale authoritative capacity
    clock[0] = 500.
    assert metrics.fleet_slots() is None
    assert len(jobs) == 1
    jobs.pop(0)()
    assert metrics.fleet_slots() == 8
    assert calls == [10., 500.]


@pytest.mark.parametrize('failure', [ImportError, OSError, RuntimeError, ValueError, TypeError])
def test_failed_refresh_invalidates_and_retries_after_ttl(monkeypatch, failure):
    metrics, clock, jobs = deferred_cache(monkeypatch)
    monkeypatch.setattr(metrics, '_fetch_registry_slots', lambda: 3)
    assert metrics.fleet_slots() is None
    jobs.pop(0)()
    assert metrics.fleet_slots() == 3
    def fail():
        raise failure('unavailable')
    monkeypatch.setattr(metrics, '_fetch_registry_slots', fail)
    clock[0] = 60.
    assert metrics.fleet_slots() is None
    jobs.pop(0)()
    assert metrics._registry_value is None and not metrics._registry_busy
    clock[0] = 119.999
    assert metrics.fleet_slots() is None and not jobs
    clock[0] = 120.
    monkeypatch.setattr(metrics, '_fetch_registry_slots', lambda: 0)
    assert metrics.fleet_slots() is None and len(jobs) == 1
    jobs.pop(0)()
    assert metrics.fleet_slots() == 0


def test_failed_thread_launch_is_unknown_and_retryable(monkeypatch):
    metrics, clock, jobs = deferred_cache(monkeypatch)
    def unavailable(**kwargs):
        raise RuntimeError('cannot start new thread')
    good_thread = metrics.threading
    monkeypatch.setattr(metrics, 'threading', SimpleNamespace(Thread=unavailable))
    assert metrics.fleet_slots() is None
    assert not metrics._registry_busy
    monkeypatch.setattr(metrics, 'threading', good_thread)
    assert metrics.fleet_slots() is None and not jobs
    clock[0] = 60.
    assert metrics.fleet_slots() is None and len(jobs) == 1


def test_real_blocked_registry_does_not_block_concurrent_sidebar_reads(monkeypatch):
    metrics = load()
    entered, release = threading.Event(), threading.Event()
    workers, calls = [], []
    real_thread = threading.Thread
    def tracked_thread(**kwargs):
        worker = real_thread(**kwargs)
        workers.append(worker)
        return worker
    def fetch():
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3), 'test did not release registry fetch'
        return 7
    monkeypatch.setattr(metrics, 'threading', SimpleNamespace(Thread=tracked_thread))
    monkeypatch.setattr(metrics, '_fetch_registry_slots', fetch)
    try:
        assert metrics.fleet_slots() is None
        assert entered.wait(2)
        # A registry HTTP call is still blocked; every sidebar caller returns.
        readers = [real_thread(target=lambda: results.append(metrics.fleet_slots())) for _ in range(8)]
        results = []
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join(1)
            assert not reader.is_alive()
        assert results == [None] * 8
        assert len(workers) == 1 and len(calls) == 1
        assert calls[0] != threading.get_ident()
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
    assert metrics.fleet_slots() == 7
    assert not any(worker.is_alive() for worker in workers)
