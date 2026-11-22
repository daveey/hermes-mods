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
    assert (module.TTL_S, module.BACKOFF_MIN_S, module.BACKOFF_MAX_S) == (300, 60, 900)
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
