"""Host-level quota bound across independent mounted/projector observers."""
import importlib.util
from pathlib import Path
import threading
import json
import os
import subprocess
import sys

import pytest


def load(name):
    path = Path(__file__).parents[1] / 'dashboard' / (name + '.py')
    spec = importlib.util.spec_from_file_location('test_' + name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_independent_observers_share_success_without_renewing_ttl(tmp_path):
    shared = load('shared_observations')
    obs = load('observations')
    now = [1000.]
    calls = []

    class Reader:
        states = {'pr': 'OPEN'}
        def prefetch(self, urls):
            calls.append(tuple(urls))
        def get(self, url):
            return {'approved': True}

    def make():
        return obs.Observations(Reader, clock=lambda: now[0],
            shared=shared.SharedObservations(tmp_path, clock=lambda: now[0]))

    def report(store, context='head-1'):
        store.reader({'pr': context}).prefetch(['pr'])
        store.join(3)
        return store.reader({'pr': context}).get('pr')

    assert report(make()) == {'approved': True}
    now[0] += 590
    second = make()
    assert report(second) == {'approved': True}
    assert len(calls) == 1
    now[0] += 11
    assert report(second) == {'approved': True}
    assert len(calls) == 2  # shared hit must NOT give a new 600s lease
    assert report(make(), 'head-2') == {'approved': True}
    assert len(calls) == 3


def test_independent_concurrent_fills_are_serialized(tmp_path):
    shared = load('shared_observations')
    started, release = threading.Event(), threading.Event()
    calls, results = [], []

    class Reader:
        states = {}
        def prefetch(self, urls):
            calls.append(tuple(urls))
            started.set()
            assert release.wait(3)
        def get(self, url):
            return {'approved': True}

    def run():
        results.append(shared.SharedObservations(tmp_path).fetch({'pr': 'head'}, Reader, 600))

    a, b = threading.Thread(target=run), threading.Thread(target=run)
    try:
        a.start()
        assert started.wait(1)
        b.start()
        release.set()
        a.join(4)
        b.join(4)
        assert not a.is_alive() and not b.is_alive()
        assert len(results) == 2
        assert calls == [('pr',)]
    finally:
        release.set()
        a.join(4)
        if b.ident:
            b.join(4)


@pytest.mark.parametrize('state', ['OPEN', 'MERGED', 'CLOSED'])
def test_exact_expiry_terminal_empty_and_read_start_age(tmp_path, state):
    shared = load('shared_observations')
    now, calls = [1000.], []
    class Reader:
        states = {'pr': state}
        def prefetch(self, urls):
            calls.append(tuple(urls))
            now[0] += 10
        def get(self, url):
            return {} if state == 'CLOSED' else {'merged': state == 'MERGED'}
    cache = shared.SharedObservations(tmp_path, clock=lambda: now[0])
    first = cache.fetch({'pr': 'head'}, Reader, 600)['pr']
    assert first[0] == 10 and first[2] == (state != 'OPEN')
    ttl = 600 if state == 'OPEN' else 3600
    now[0] = 1000 + ttl - 1
    assert cache.fetch({'pr': 'head'}, Reader, 600)['pr'][0] == ttl - 1
    assert len(calls) == 1
    now[0] += 1
    assert cache.fetch({'pr': 'head'}, Reader, 600)['pr'][0] == 10
    assert len(calls) == 2


def test_unknown_failures_retry_and_partial_success_is_preserved(tmp_path):
    shared = load('shared_observations')
    calls, succeeds = [], [False]
    class Reader:
        states = {}
        def prefetch(self, urls):
            calls.append(set(urls))
        def get(self, url):
            return {'approved': True} if url == 'good' or succeeds[0] else {}
    cache = shared.SharedObservations(tmp_path)
    assert set(cache.fetch({'bad': 1, 'good': 1}, Reader, 600)) == {'good'}
    succeeds[0] = True
    assert set(cache.fetch({'bad': 1, 'good': 1}, Reader, 600)) == {'bad', 'good'}
    assert calls == [{'bad', 'good'}, {'bad'}]
    class Broken(Reader):
        def prefetch(self, urls):
            raise RuntimeError('provider failure')
    with pytest.raises(RuntimeError):
        cache.fetch({'exception': 1}, Broken, 600)
    assert 'exception' in cache.fetch({'exception': 1}, Reader, 600)


@pytest.mark.parametrize('name', ['fill.lock', 'values.json', 'values.pending'])
@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public'])
def test_unsafe_cache_files_fail_closed_without_target_write(tmp_path, name, kind):
    shared = load('shared_observations')
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    target = tmp_path / 'target'
    target.write_bytes(b'untouched')
    target.chmod(0o600)
    path = root / name
    if kind == 'symlink':
        path.symlink_to(target)
    elif kind == 'hardlink':
        os.link(target, path)
    else:
        path.write_bytes(b'untouched')
        path.chmod(0o644)
    calls = []
    class Reader:
        states = {}
        def prefetch(self, urls):
            calls.append(tuple(urls))
        def get(self, url):
            return {'approved': True}
    with pytest.raises(OSError):
        shared.SharedObservations(root).fetch({'pr': 1}, Reader, 600)
    assert calls == ([('pr',)] if name == 'values.pending' else [])
    assert target.read_bytes() == b'untouched'


@pytest.mark.parametrize('raw', ['broken', '{"version":2}', '{"version":1,"entries":[]}',
    '{"version":1,"entries":{"bad":[1,{},true]}}', '[' * 10000 + ']' * 10000])
def test_safe_corrupt_content_repairs_once_under_lock(tmp_path, raw):
    shared = load('shared_observations')
    path = tmp_path / 'values.json'
    path.write_text(raw)
    path.chmod(0o600)
    calls = []
    class Reader:
        states = {}
        def prefetch(self, urls):
            calls.append(tuple(urls))
        def get(self, url):
            return {'approved': True}
    for _ in range(2):
        assert shared.SharedObservations(tmp_path).fetch({'pr': 1}, Reader, 600)['pr'][1]['approved']
    assert len(calls) == 1
    assert json.loads(path.read_text())['version'] == 1


@pytest.mark.parametrize('timestamp', [10 ** 400, -(10 ** 400)])
@pytest.mark.parametrize('cached_url', ['pr', 'unrelated'])
def test_overflowing_timestamp_repairs_and_reuses_success(tmp_path, timestamp, cached_url):
    shared = load('shared_observations')
    path = tmp_path / 'values.json'
    key = shared._key(cached_url, 'head')
    path.write_text(json.dumps({'version': 1, 'entries': {
        key: [timestamp, {'approved': True}, False]}}))
    path.chmod(0o600)
    calls = []
    class Reader:
        states = {'pr': 'OPEN'}
        def prefetch(self, urls):
            calls.append(tuple(urls))
        def get(self, url):
            return {'approved': False}
    for _ in range(3):
        result = shared.SharedObservations(tmp_path, clock=lambda: 1001).fetch(
            {'pr': 'head'}, Reader, 600)
        assert result == {'pr': (0, {'approved': False}, False)}
    assert calls == [('pr',)]
    assert json.loads(path.read_text())['entries'] == {
        shared._key('pr', 'head'): [1001, {'approved': False}, False]}


def test_storage_failure_uses_existing_observer_backoff_not_unshared_retry(tmp_path, monkeypatch):
    shared, obs = load('shared_observations'), load('observations')
    calls = []
    class Reader:
        states = {}
        def prefetch(self, urls):
            calls.append(tuple(urls))
        def get(self, url):
            return {'approved': True}
    cache = shared.SharedObservations(tmp_path)
    def fail(entries):
        raise OSError('disk unavailable')
    monkeypatch.setattr(cache, '_write', fail)
    store = obs.Observations(Reader, shared=cache)
    store.reader({'pr': 1}).prefetch(['pr'])
    store.join(3)
    assert store.reader({'pr': 1}).get('pr') == {}
    store.reader({'pr': 1}).prefetch(['pr'])
    store.join(3)
    assert calls == [('pr',)] and store.backoff_delay == 60


def test_two_homes_do_not_share_and_backward_clock_is_not_fresh(tmp_path):
    shared = load('shared_observations')
    now, calls = [1000.], []
    class Reader:
        states = {}
        def prefetch(self, urls):
            calls.append(tuple(urls))
        def get(self, url):
            return {'approved': True}
    a = shared.SharedObservations(tmp_path / 'a', clock=lambda: now[0])
    b = shared.SharedObservations(tmp_path / 'b', clock=lambda: now[0])
    a.fetch({'pr': 1}, Reader, 600)
    b.fetch({'pr': 1}, Reader, 600)
    assert len(calls) == 2
    now[0] -= 1
    a.fetch({'pr': 1}, Reader, 600)
    assert len(calls) == 3


def test_real_processes_share_one_fill_and_lock_releases_after_crash(tmp_path):
    module = Path(__file__).parents[1] / 'dashboard' / 'shared_observations.py'
    script = tmp_path / 'child.py'
    script.write_text('''import importlib.util, pathlib, sys, time, os
spec = importlib.util.spec_from_file_location('shared', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
root = pathlib.Path(sys.argv[2])
class Reader:
    states = {}
    def prefetch(self, urls):
        with (root / 'calls').open('a') as f: f.write('fill\\n')
        if sys.argv[3] == 'crash': os._exit(17)
        time.sleep(.1)
    def get(self, url): return {'approved': True}
result = m.SharedObservations(root / 'cache').fetch({'pr': 'head'}, Reader, 600)
assert result['pr'][1] == {'approved': True}
''')
    argv = [sys.executable, '-I', '-B', str(script), str(module), str(tmp_path)]
    assert subprocess.run([*argv, 'crash'], timeout=5).returncode == 17
    children = []
    try:
        children = [subprocess.Popen([*argv, 'ok']) for _ in range(3)]
        assert [p.wait(timeout=5) for p in children] == [0, 0, 0]
    finally:
        for p in children:
            if p.poll() is None:
                p.kill()
            p.wait()
    assert (tmp_path / 'calls').read_text().splitlines() == ['fill', 'fill']


def test_lock_wait_is_bounded_and_does_not_call_provider(tmp_path, monkeypatch):
    shared = load('shared_observations')
    import fcntl
    fd = os.open(tmp_path / 'fill.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        monkeypatch.setattr(shared, 'LOCK_TIMEOUT', 0)
        with pytest.raises(TimeoutError):
            shared.SharedObservations(tmp_path).fetch({'pr': 1}, lambda: pytest.fail('provider'), 600)
    finally:
        os.close(fd)


def test_cache_rows_and_bytes_are_bounded_and_crash_stage_reused(tmp_path, monkeypatch):
    shared = load('shared_observations')
    monkeypatch.setattr(shared, 'ENTRY_CAP', 2)
    monkeypatch.setattr(shared, 'BYTE_CAP', 250)
    cache = shared.SharedObservations(tmp_path)
    (tmp_path / 'values.pending').write_bytes(b'left by crash' * 1000)
    (tmp_path / 'values.pending').chmod(0o600)
    cache._write({'a' * 64: [1, {'approved': True}, False],
                  'b' * 64: [2, {'approved': True}, False],
                  'c' * 64: [3, {'approved': True}, False]})
    raw = (tmp_path / 'values.json').read_bytes()
    assert len(raw) <= 250
    entries = json.loads(raw)['entries']
    assert len(entries) <= 2 and 'c' * 64 in entries and 'a' * 64 not in entries
    assert not (tmp_path / 'values.pending').exists()


def test_new_context_during_shared_fill_cannot_publish_old_result(tmp_path):
    shared, obs = load('shared_observations'), load('observations')
    started, release = threading.Event(), threading.Event()
    class Reader:
        states = {}
        def prefetch(self, urls):
            started.set()
            assert release.wait(3)
        def get(self, url):
            return {'approved': True}
    store = obs.Observations(Reader, shared=shared.SharedObservations(tmp_path))
    try:
        store.reader({'pr': 'old'}).prefetch(['pr'])
        assert started.wait(1)
        assert store.reader({'pr': 'new'}).get('pr') == {}
        release.set()
        store.join(3)
        assert store.reader({'pr': 'new'}).get('pr') == {}
        assert store.values == {}
    finally:
        release.set()
        store.join(3)


def test_replaced_lock_inode_is_rejected_before_fill(tmp_path, monkeypatch):
    import fcntl
    from types import SimpleNamespace
    shared = load('shared_observations')
    path = tmp_path / 'fill.lock'
    held = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    real_time = shared.time
    def replace(_delay):
        path.unlink()
        fresh = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fresh)
        fcntl.flock(held, fcntl.LOCK_UN)
    monkeypatch.setattr(shared, 'time', SimpleNamespace(monotonic=real_time.monotonic, sleep=replace))
    try:
        with pytest.raises(PermissionError, match='lock changed'):
            shared.SharedObservations(tmp_path).fetch({'pr': 1}, lambda: pytest.fail('provider'), 600)
    finally:
        os.close(held)


@pytest.mark.parametrize('value,terminal', [({'approved': 'yes'}, False),
    ({'checks': 'unknown'}, False), ({'merged': False}, True), ({'arbitrary': True}, False)])
def test_invalid_display_shapes_are_not_reused(tmp_path, value, terminal):
    shared = load('shared_observations')
    key = shared._key('pr', 'head')
    file = tmp_path / 'values.json'
    file.write_text(json.dumps({'version': 1, 'entries': {key: [1000, value, terminal]}}))
    file.chmod(0o600)
    calls = []
    class Reader:
        states = {}
        def prefetch(self, urls):
            calls.append(tuple(urls))
        def get(self, url):
            return {'approved': False}
    got = shared.SharedObservations(tmp_path, clock=lambda: 1001).fetch({'pr': 'head'}, Reader, 600)
    assert calls == [('pr',)] and got['pr'][1] == {'approved': False}


def test_trace_distinguishes_hit_from_provider_fill_and_is_failure_isolated(tmp_path):
    shared = load('shared_observations')
    events = []
    class Reader:
        states = {}
        def prefetch(self, urls):
            pass
        def get(self, url):
            return {'approved': False}
    def trace(event, *args):
        events.append(event)
        raise RuntimeError('sink unavailable')
    cache = shared.SharedObservations(tmp_path)
    assert cache.fetch({'pr': 1}, Reader, 600, trace)['pr'][1] == {'approved': False}
    assert cache.fetch({'pr': 1}, Reader, 600, trace)['pr'][1] == {'approved': False}
    assert events == ['provider_fill', 'shared_hit']
