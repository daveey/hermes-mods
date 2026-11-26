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


@pytest.mark.parametrize('depth', [100, 100000])
def test_deeply_nested_ring_recovers_and_publishes(tmp_path, depth):
    trace = trace_module()
    path = tmp_path / 'ring.json'
    # Shallow case always decodes: non-record rows must be discarded even
    # on interpreters/stack limits which successfully decode the deep case.
    path.write_text('[' * depth + ']' * depth)
    assert path.stat().st_size < trace.BYTE_CAP
    store = load().Observations(Counting(), trace=lambda *a, **kw: trace.emit(
        {'event': a[1]}, path=path))
    poke(store, {'pr': 'old'})
    assert store.reader({'pr': 'old'}).get('pr')['approved']
    assert [row['event'] for row in json.loads(path.read_text())] == ['fill', 'result']
    poke(store, {'pr': 'new'})
    assert store.reader({'pr': 'new'}).get('pr')['approved']
    assert store.thread is None and store.active == {}


def test_decode_exception_recovers_without_stack_limit_dependency(tmp_path, monkeypatch):
    trace = trace_module()
    path = tmp_path / 'ring.json'
    path.write_text('[{}]')
    def broken(raw):
        raise RecursionError('synthetic decoder boundary')
    with monkeypatch.context() as patch:
        patch.setattr(trace.json, 'loads', broken)
        trace.emit({'event': 'recovered'}, path=path)
    assert [row['event'] for row in json.loads(path.read_text())] == ['recovered']


def test_ring_recovers_oversized_history_and_truncates(tmp_path):
    trace = trace_module()
    path = tmp_path / 'ring.json'
    path.write_text(json.dumps([{'old': 'x' * (trace.BYTE_CAP - 40)}]))
    assert path.stat().st_size < trace.BYTE_CAP
    trace.emit({'event': 'new', 'data': 'y' * 100}, path=path)
    rows = json.loads(path.read_text())
    assert len(rows) == 1 and rows[0]['event'] == 'new'
    assert path.stat().st_size < 1000
    before = path.read_bytes()
    trace.emit({'oversized': 'x' * trace.BYTE_CAP}, path=path)
    assert path.read_bytes() == before
    path.write_bytes(b'x' * (trace.BYTE_CAP + 1))
    trace.emit({'event': 'reset'}, path=path)
    assert [row['event'] for row in json.loads(path.read_text())] == ['reset']


def test_ring_rejects_foreign_owner_and_nonregular_file(tmp_path, monkeypatch):
    import os
    import stat
    from types import SimpleNamespace
    trace = trace_module()
    path = tmp_path / 'ring.json'
    path.write_text('sentinel')
    for mode, uid in [(stat.S_IFREG, os.getuid() + 1), (stat.S_IFIFO, os.getuid())]:
        with monkeypatch.context() as patch:
            patch.setattr(trace.os, 'fstat', lambda fd: SimpleNamespace(st_mode=mode, st_uid=uid))
            trace.emit({'event': 'refused'}, path=path)
        assert path.read_text() == 'sentinel'


def test_ring_read_is_byte_bounded(tmp_path, monkeypatch):
    trace = trace_module()
    real_read, sizes = trace.os.read, []
    def read(fd, size):
        sizes.append(size)
        return real_read(fd, size)
    monkeypatch.setattr(trace.os, 'read', read)
    trace.emit({'event': 'new'}, path=tmp_path / 'ring.json')
    assert sizes == [trace.BYTE_CAP + 1]


def test_attempted_contexts_are_pruned_with_board():
    store = load().Observations(Counting())
    poke(store, {'first': 'h1', 'second': 'h2'})
    assert store.attempted == {'first': 'h1', 'second': 'h2'}
    store.reader({'second': 'h2'})
    assert store.attempted == {'second': 'h2'}
    store.reader({})
    assert store.attempted == {}


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
    assert [row['event'] for row in rows] == ['fill', 'provider_fill', 'result']
    assert rows[-1]['outcome'] == 'success'
    assert url not in raw and context not in raw
    assert all(len(row['url']) == len(row['context']) == 16 for row in rows)
    assert store.thread is None
    assert store.shared.directory == root / 'cache' / 'lanes-pr-observations-v1'
    assert (store.shared.directory / 'values.json').is_file()
    # A distinct mounted API generation must consume the persisted observation.
    api._observations.cache_clear()
    second = api._observations()
    def unexpected_provider():
        pytest.fail('second API generation refilled a fresh shared observation')
    second.factory = unexpected_provider
    poke(second, {url: context})
    assert second.reader({url: context}).get(url)['approved']
    assert second.thread is None
    events = [row['event'] for row in json.loads((root / 'logs' / 'lanes-pr-observations.json').read_text())]
    assert events[-3:] == ['fill', 'shared_hit', 'result']
