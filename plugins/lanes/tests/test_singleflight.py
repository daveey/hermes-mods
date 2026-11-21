"""Concurrent viewers share one projection; dirty churn cannot starve a reader."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from test_view import load


def test_concurrent_identical_viewers_compute_once(monkeypatch):
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def read(hours):
        calls.append(hours)
        entered.set()
        assert release.wait(3)
        return {'configured': False, 'reason': 'fixture'}
    monkeypatch.setattr(api, 'read', read)
    with ThreadPoolExecutor(max_workers=6) as executor:
        first = executor.submit(api.lane_view, 6, '6h', False)
        assert entered.wait(1)
        others = [executor.submit(api.lane_view, 6, '6h', False) for _ in range(5)]
        try:
            time.sleep(.05)  # allow all real threads to contend at the blocked projection
        finally:
            release.set()
        results = [first.result(3), *(f.result(3) for f in others)]
    assert len(calls) == 1
    assert all(r == results[0] for r in results)
    api._written()
    api.lane_view(6, '6h', False)
    assert len(calls) == 2


def test_churn_during_projection_does_not_restart_or_publish_as_new_generation(monkeypatch):
    api = load()
    monkeypatch.setattr(api, 'core', lambda: None)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def read(hours):
        value = len(calls) + 1
        calls.append(value)
        if value == 1:
            entered.set()
            assert release.wait(3)
        return {'configured': False, 'reason': str(value)}
    monkeypatch.setattr(api, 'read', read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(api.lane_view, 6, '6h', False)
        assert entered.wait(1)
        for _ in range(50):
            api._written()
        new = executor.submit(api.lane_view, 6, '6h', False)
        release.set()
        assert old.result(3)['reason'] == '1'
        assert new.result(3)['reason'] == '2'
    assert calls == [1, 2]
    assert api.lane_view(6, '6h', False)['reason'] == '2'
    assert calls == [1, 2]
