"""Slow network observation must never delay the board projection."""
import importlib.util
import threading
import time
from pathlib import Path


def load():
    spec = importlib.util.spec_from_file_location('lanes_observations_test', Path(__file__).parents[1] / 'dashboard' / 'observations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_slow_observation_is_nonblocking_coalesced_and_immutable():
    module = load()
    started, release = threading.Event(), threading.Event()
    calls = []

    class Slow:
        def prefetch(self, urls):
            calls.append(set(urls))
            started.set()
            assert release.wait(3)

        def get(self, url):
            return {'approved': True, 'checks': 'green'}

    store = module.Observations(Slow, ttl=60)
    reader = store.reader({'pr1': 'board-1'})
    try:
        before = time.monotonic()
        reader.prefetch(['pr1'])
        assert time.monotonic() - before < .1
        assert started.wait(1)
        assert reader.get('pr1') == {}
        store.reader({'pr1': 'board-1'}).prefetch(['pr1'])
        release.set()
        store.join(2)
        assert calls == [{'pr1'}]
        assert reader.get('pr1') == {}  # immutable projection, no mid-report change
        published = store.reader({'pr1': 'board-1'})
        value = published.get('pr1')
        assert value['approved'] is True
        value['approved'] = False
        assert published.get('pr1')['approved'] is True
    finally:
        release.set()
        store.join(3)


def test_new_board_evidence_and_expiry_fail_closed():
    module = load()
    clock = [100.]

    class Reader:
        def prefetch(self, urls):
            pass

        def get(self, url):
            return {'approved': True, 'checks': 'green'}

    store = module.Observations(Reader, ttl=60, clock=lambda: clock[0])
    store.reader({'pr': 'head-1'}).prefetch(['pr'])
    store.join(2)
    assert store.reader({'pr': 'head-1'}).get('pr')['approved']
    revision = store.cursor()
    clock[0] += 61
    assert store.cursor() != revision
    assert store.reader({'pr': 'head-1'}).get('pr') == {}
    assert store.reader({'pr': 'head-2'}).get('pr') == {}


def test_refresh_due_cursor_without_content_change():
    module = load()
    clock = [100.]
    class Reader:
        def prefetch(self, urls):
            pass
        def get(self, url):
            return {'approved': True}
    store = module.Observations(Reader, ttl=60, clock=lambda: clock[0])
    contexts = {'pr': 'head'}
    store.reader(contexts).prefetch(['pr'])
    store.join(2)
    before = store.cursor()
    clock[0] += 31
    assert store.cursor() == before  # no refresh is due before ttl: stream must not spin
    clock[0] += 30
    assert store.cursor() != before  # expiry wakes a quiet stream
    store.reader(contexts).prefetch(['pr'])
    store.join(2)
    assert store.reader(contexts).get('pr')['approved']
    assert store.cursor()[1:] == before[1:]  # same fresh set; re-fill after expiry bumps revision once


def test_late_old_epoch_cannot_publish_after_head_change():
    module = load()
    started, release = threading.Event(), threading.Event()

    class Slow:
        def prefetch(self, urls):
            started.set()
            assert release.wait(3)

        def get(self, url):
            return {'approved': True}

    store = module.Observations(Slow)
    try:
        store.reader({'pr': 'old'}).prefetch(['pr'])
        assert started.wait(1)
        assert store.reader({'pr': 'new'}).get('pr') == {}
        release.set()
        store.join(2)
        assert store.reader({'pr': 'new'}).get('pr') == {}
    finally:
        release.set()
        store.join(3)


class Counting:
    """Factory whose readers count reads; `fail` makes every observation empty."""
    def __init__(self, fail=False, raises=False):
        self.calls, self.fail, self.raises = [], fail, raises

    def __call__(self):
        outer = self

        class Reader:
            def prefetch(self, urls):
                outer.calls.append(list(urls))
                if outer.raises:
                    raise RuntimeError('rate limited')

            def get(self, url):
                return {} if outer.fail else {'approved': True}
        return Reader()


def poke(store, contexts, urls=None):
    store.reader(contexts).prefetch(list(urls if urls is not None else contexts))
    store.join(2)


def test_constants_and_default_ttl():
    module = load()
    assert (module.TTL_S, module.BACKOFF_MIN_S, module.BACKOFF_MAX_S) == (600, 60, 900)
    assert module.Observations(Counting()).ttl == module.TTL_S


def test_no_reread_before_ttl_and_reread_after():
    module = load()
    clock, f = [0.], Counting()
    store = module.Observations(f, ttl=300, clock=lambda: clock[0])
    ctx = {'pr': 'h'}
    poke(store, ctx)
    clock[0] = 299
    poke(store, ctx)
    assert len(f.calls) == 1
    assert store.reader(ctx).get('pr')['approved']
    clock[0] = 301
    poke(store, ctx)
    assert len(f.calls) == 2


def test_context_change_invalidates_immediately():
    module = load()
    clock, f = [0.], Counting()
    store = module.Observations(f, ttl=300, clock=lambda: clock[0])
    poke(store, {'pr': 'h1'})
    clock[0] = 5
    poke(store, {'pr': 'h2'})
    assert len(f.calls) == 2


def test_all_empty_batch_backs_off_doubling_to_cap_and_success_resets():
    module = load()
    clock, f = [0.], Counting(fail=True)
    store = module.Observations(f, ttl=300, clock=lambda: clock[0])
    ctx = {'a': '1', 'b': '1'}
    poke(store, ctx)
    assert len(f.calls) == 1 and store.backoff_until == 60
    clock[0] = 59
    poke(store, ctx)
    assert len(f.calls) == 1  # suppressed while backing off
    for want in [120, 240, 480, 900, 900]:
        clock[0] = store.backoff_until
        n = len(f.calls)
        poke(store, ctx)
        assert len(f.calls) == n + 1
        assert store.backoff_until == clock[0] + want
    f.fail = False
    clock[0] = store.backoff_until
    poke(store, ctx)
    assert store.backoff_until == 0
    assert store.reader(ctx).get('a')['approved']


def test_exception_batch_backs_off_and_partial_success_resets():
    module = load()
    clock, f = [0.], Counting(raises=True)
    store = module.Observations(f, ttl=300, clock=lambda: clock[0])
    poke(store, {'a': '1'})
    assert store.backoff_until == 60
    clock[0] = 60
    f.raises = False
    poke(store, {'a': '1'})
    assert store.backoff_until == 0


def test_refresh_storm_is_bounded():
    module = load()
    clock, f = [0.], Counting()
    store = module.Observations(f, clock=lambda: clock[0])
    ctx = {f'pr{i}': 'h' for i in range(40)}
    while clock[0] <= 3600:
        poke(store, ctx)
        clock[0] += 5
    assert sum(len(c) for c in f.calls) <= 40 * (3600 // 300 + 1)


def test_queued_batch_waits_for_cooldown_and_preserves_intent():
    module = load()
    clock, calls = [0.], []
    started, release = threading.Event(), threading.Event()
    class Reader:
        def prefetch(self, urls):
            calls.append(set(urls))
            started.set()
            assert release.wait(3)
        def get(self, url):
            return {}
    store = module.Observations(Reader, clock=lambda: clock[0])
    ctx = {'a': '1', 'b': '1'}
    try:
        store.reader(ctx).prefetch(['a'])
        assert started.wait(1)
        store.reader(ctx).prefetch(['b'])
        release.set()
        store.join(2)
        assert calls == [{'a'}]
        assert store.pending == {'b': '1'}
        clock[0] = 60
        poke(store, ctx, ['a'])
        assert calls == [{'a'}, {'a', 'b'}]
    finally:
        release.set()
        store.join(3)


def test_quiet_cursor_wakes_once_when_backoff_exceeds_ttl():
    module = load()
    clock, factory = [0.], Counting(fail=True)
    store = module.Observations(factory, clock=lambda: clock[0])
    ctx = {'a': '1'}
    for now in (0, 300, 600, 900):
        clock[0] = now
        poke(store, ctx)
    assert store.backoff_until == 1380
    clock[0] = 1200
    poke(store, ctx)
    before = store.cursor()
    clock[0] = 1379
    assert store.cursor() == before
    clock[0] = 1380
    assert store.cursor() != before
    ready = store.cursor()
    clock[0] = 1381
    assert store.cursor() == ready
    factory.fail = False
    poke(store, ctx)
    assert len(factory.calls) == 5
    assert store.reader(ctx).get('a')['approved']


def test_terminal_observations_live_for_one_hour_and_failed_reads_are_not_values():
    module = load()
    clock, factory = [0.], Counting()
    store = module.Observations(factory, clock=lambda: clock[0])
    assert store.ttl == 600
    poke(store, {'pr': 'head'})
    clock[0] = 599
    poke(store, {'pr': 'head'})
    assert len(factory.calls) == 1
    clock[0] = 600
    poke(store, {'pr': 'head'})
    assert len(factory.calls) == 2

    class Terminal:
        states = {'closed': 'CLOSED', 'merged': 'MERGED'}
        def prefetch(self, urls):
            pass
        def get(self, url):
            return {'merged': True} if url == 'merged' else {}
    terminal = module.Observations(Terminal, clock=lambda: clock[0])
    ctx = {'closed': 'h', 'merged': 'h', 'failed': 'h'}
    poke(terminal, ctx)
    assert set(terminal.values) == {'closed', 'merged'}
    clock[0] = 4199
    assert set(terminal.reader(ctx).values) == {'closed', 'merged'}
    clock[0] = 4200
    assert terminal.reader(ctx).values == {}


def test_trace_distinguishes_expiry_retry_invalidation_and_process_local_cold():
    module = load()
    clock, factory, events = [0.], Counting(), []
    store = module.Observations(factory, clock=lambda: clock[0],
                                trace=lambda *a, **kw: events.append((a, kw)))
    poke(store, {'pr': 'h1'})
    poke(store, {'pr': 'h1'})
    assert [kw['reason'] for a, kw in events if a[1] == 'fill'] == ['cold']
    clock[0] = 600
    poke(store, {'pr': 'h1'})
    assert events[-2][1] == {'reason': 'expiry', 'age': 600}
    poke(store, {'pr': 'h2'})
    assert any(a[1] == 'invalidate' and a[3] == 'h1' for a, kw in events)
    assert events[-2][1]['reason'] == 'retry'
    factory.fail = True
    poke(store, {'pr': 'h3'})
    assert events[-1][1]['outcome'] == 'failed'
    assert store.reader({'pr': 'h3'}).get('pr') == {}
    assert module.Observations(factory).cache_id != store.cache_id


def test_trace_sink_runs_outside_observer_lock():
    module = load()
    factory, events = Counting(), []
    def sink(*args, **fields):
        assert store.lock.acquire(blocking=False)
        store.lock.release()
        events.append(args[1])
    store = module.Observations(factory, trace=sink)
    poke(store, {'pr': 'h1'})
    poke(store, {'pr': 'h2'})
    assert events == ['fill', 'result', 'invalidate', 'fill', 'result']


def test_trace_errors_do_not_strand_refresh_and_discarded_results_are_distinct():
    module = load()
    def broken(*a, **kw):
        raise OSError('unavailable')
    store = module.Observations(Counting(), trace=broken)
    poke(store, {'pr': 'h1'})
    assert store.reader({'pr': 'h1'}).get('pr')['approved']
    assert store.thread is None
    records = []
    class Changing:
        def prefetch(self, urls):
            changing.reader({'pr': 'h2'})
        def get(self, url):
            return {'merged': False}
    changing = module.Observations(Changing, trace=lambda *a, **kw: records.append((a, kw)))
    poke(changing, {'pr': 'h1'})
    assert records[-1][1]['outcome'] == 'discarded'
    assert changing.reader({'pr': 'h2'}).get('pr') == {}


def trace_module():
    spec = importlib.util.spec_from_file_location('lanes_trace_test', Path(__file__).parents[1] / 'dashboard' / 'observation_trace.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trace_ring_bounded_private_and_nonblocking(tmp_path, monkeypatch):
    import fcntl
    import json
    import os
    module = trace_module()
    path = tmp_path / 'trace.json'
    for i in range(module.CAP + 4):
        module.emit({'event': 'fill', 'index': i}, path=path)
    rows = json.loads(path.read_text())
    assert len(rows) == module.CAP and rows[0]['index'] == 4
    assert rows[-1]['pid'] == os.getpid() and rows[-1]['at'] > 0
    assert path.stat().st_mode & 0o777 == 0o600
    with path.open('rb') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = path.read_bytes()
        module.emit({'event': 'skipped'}, path=path)
        assert path.read_bytes() == before
    captured = []
    monkeypatch.setattr(module, 'emit', captured.append)
    module.observer_trace('cache', 'fill', 'https://example.com/private', {'secret': 'payload'}, reason='cold')
    assert 'private' not in json.dumps(captured) and 'payload' not in json.dumps(captured)
    assert len(captured[0]['url']) == len(captured[0]['context']) == 16


def test_trace_rejects_symlink_and_recovers_corruption(tmp_path):
    import json
    module = trace_module()
    target, link = tmp_path / 'target', tmp_path / 'link'
    target.write_text('sentinel')
    link.symlink_to(target)
    module.emit({'event': 'fill'}, path=link)
    assert target.read_text() == 'sentinel'
    module.emit({'event': 'fill'}, path=target)
    assert json.loads(target.read_text())[0]['event'] == 'fill'
    module.emit({'event': 'fill'}, path=tmp_path / 'absent' / 'trace')
