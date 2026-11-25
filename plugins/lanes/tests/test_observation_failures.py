"""Diagnostics cannot affect evidence publication or the next refresh."""
import importlib.util
import json
from pathlib import Path

import pytest

from test_observations import Counting, load, poke, trace_module


@pytest.mark.parametrize('error', [RecursionError, RuntimeError, AssertionError])
@pytest.mark.parametrize('event', ['fill', 'invalidate', 'result'])
def test_unexpected_trace_failure_cannot_strand_fill_or_invalidation(error, event):
    def broken(*args, **fields):
        if args[1] == event:
            raise error('diagnostic failure')
    store = load().Observations(Counting(), trace=broken)
    poke(store, {'pr': 'old'})
    assert store.reader({'pr': 'old'}).get('pr')['approved']
    # reader() emits invalidation on the request thread as well as background fills.
    poke(store, {'pr': 'new'})
    assert store.reader({'pr': 'new'}).get('pr')['approved']
    assert store.thread is None and store.active == {}


def test_deeply_nested_ring_recovers_and_publishes(tmp_path):
    trace = trace_module()
    path = tmp_path / 'ring.json'
    path.write_text('[' * 100000 + ']' * 100000)
    assert path.stat().st_size < trace.BYTE_CAP
    store = load().Observations(Counting(), trace=lambda *a, **kw: trace.emit(
        {'event': a[1]}, path=path))
    poke(store, {'pr': 'old'})
    assert store.reader({'pr': 'old'}).get('pr')['approved']
    assert [row['event'] for row in json.loads(path.read_text())] == ['fill', 'result']
    poke(store, {'pr': 'new'})
    assert store.reader({'pr': 'new'}).get('pr')['approved']
    assert store.thread is None and store.active == {}


def test_real_factory_wires_private_trace_without_network(tmp_path, monkeypatch):
    constants = pytest.importorskip('hermes_constants')
    pytest.importorskip('hermes_cli.kanban_lanes_flow_pr')
    root = tmp_path / 'home'
    (root / 'logs').mkdir(parents=True)
    monkeypatch.setattr(constants, 'get_hermes_home', lambda: root)
    path = Path(__file__).parents[1] / 'dashboard' / 'plugin_api.py'
    spec = importlib.util.spec_from_file_location('lanes_trace_factory', path)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    store = api._observations()
    store.factory = Counting()  # replace only provider I/O, not diagnostic wiring
    url, context = 'https://github.com/example/repo/pull/1', 'private-context'
    poke(store, {url: context})
    raw = (root / 'logs' / 'lanes-pr-observations.json').read_text()
    rows = json.loads(raw)
    assert [row['event'] for row in rows] == ['fill', 'result']
    assert rows[-1]['outcome'] == 'success'
    assert url not in raw and context not in raw
    assert all(len(row['url']) == len(row['context']) == 16 for row in rows)
    assert store.thread is None
