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
    assert store.cursor() != before  # wake a quiet stream before expiry
    assert store.reader(contexts).get('pr')['approved']
    store.reader(contexts).prefetch(['pr'])
    store.join(2)
    assert store.cursor() == before  # unchanged content doesn't bump revision


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
