"""Public lane_view cold construction and legacy expiry regressions."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import importlib.util
import threading
import types

from test_view import load


def test_cold_factory_is_singleflight(monkeypatch):
    api = load()
    start = threading.Barrier(6)
    original = importlib.util.spec_from_file_location
    calls = []
    def delayed(name, *args, **kwargs):
        spec = original(name, *args, **kwargs)
        if name == 'lanes_shared_views':
            execute = spec.loader.exec_module
            def run(module):
                # Yield to contenders while initialization is in progress.
                threading.Event().wait(.1)
                execute(module)
            spec.loader.exec_module = run
        return spec
    monkeypatch.setattr(importlib.util, 'spec_from_file_location', delayed)
    monkeypatch.setattr(api, 'core', lambda: None)
    def compute(*args):
        calls.append(1)
        return {'fixture': True}
    monkeypatch.setattr(api, '_lane_view', compute)
    def view(_):
        start.wait(3)
        return api.lane_view()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(view, range(6)))
    assert len(calls) == 1
    assert all(result == results[0] for result in results)


def test_legacy_view_does_not_extend_inner_cache_ttl(monkeypatch):
    api = load()
    now = [0.]
    @contextlib.contextmanager
    def connect():
        yield None
    def snapshot(conn, *, hours):
        return {'configured': True, 'lanes': {}, 'sample_time': now[0]}
    monkeypatch.setattr(api, 'core', lambda: (types.SimpleNamespace(connect_closing=connect), None,
                                           types.SimpleNamespace(snapshot=snapshot)))
    monkeypatch.setattr(api, 'time', types.SimpleNamespace(time=lambda: now[0]))
    api._shared_views().clock = lambda: now[0]
    assert api.lane_view(6, '1h', False)['sample_time'] == 0
    now[0] = 9
    assert api.lane_view(6, '6h', False)['sample_time'] == 0
    now[0] = 11
    assert api.lane_view(6, '6h', False)['sample_time'] == 11
