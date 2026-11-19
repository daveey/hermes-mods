"""Repeated expiry strings are parsed once per build, never across builds."""
from copy import deepcopy
from datetime import datetime

from plugins.runners import service as S
from plugins.runners.collector import Collector
from plugins.runners.store import Store
from test_collector import Adapter, config


def test_repeated_provenance_expiry_is_parsed_once(tmp_path, monkeypatch):
    cfg = config()
    with Store(tmp_path / 'state') as store:
        snap = Collector(store, cfg, Adapter(), clock=lambda: 1000).tick()
    entry = snap['provenance'][0]
    entry.update(state='fresh', expires_at='1970-01-01T00:20:00Z')
    snap['provenance'] = [deepcopy(entry) for _ in range(100)]
    calls = []
    class CountingDateTime:
        @staticmethod
        def fromisoformat(value):
            calls.append(value)
            return datetime.fromisoformat(value)
    monkeypatch.setattr(S, 'datetime', CountingDateTime)
    svc = S.Service(tmp_path / 'state', cfg, clock=lambda: 1001)
    first = svc._inventory(snap, None)
    assert all(p['state'] == 'fresh' for p in first['meta']['provenance'][:100])
    assert calls.count('1970-01-01T00:20:00+00:00') == 1
    calls.clear()
    svc.clock = lambda: 1201
    # Keep inventory fresh: expiry itself, rather than snapshot staleness, must decide.
    snap['at'] = 1201
    second = svc._inventory(snap, None)
    assert all(p['state'] == 'stale' for p in second['meta']['provenance'][:100])
    assert calls.count('1970-01-01T00:20:00+00:00') == 1
    assert all(p['state'] == 'fresh' for p in snap['provenance'])


def test_shared_runner_timestamps_format_once_per_build(tmp_path, monkeypatch):
    cfg = config()
    with Store(tmp_path / 'state') as store:
        snap = Collector(store, cfg, Adapter(), clock=lambda: 1000).tick()
    row = snap['runners'][0]
    state = snap['states'][row['id']]
    snap['runners'] = [dict(row, id=f'runner_{i:016x}') for i in range(100)]
    snap['states'] = {r['id']: deepcopy(state) for r in snap['runners']}
    calls = []
    real = S.iso
    monkeypatch.setattr(S, 'iso', lambda value: calls.append(value) or real(value))
    svc = S.Service(tmp_path / 'state', cfg, clock=lambda: 1001)
    inv = svc._inventory(snap, None)
    assert len(inv['data']['runners']) == 100
    # One metadata timestamp plus one formatted row timestamp; not one per row.
    assert calls.count(1000) == 2
    assert all(p['observed_at'] == real(1000) for p in inv['meta']['provenance'] if p['source_id'].startswith('runner_'))
