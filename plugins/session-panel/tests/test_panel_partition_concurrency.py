"""Request authority stays request-local while the background slot changes."""
from types import SimpleNamespace
import threading
import test_explicit_home_acquisition as helpers

api = helpers.api


def test_concurrent_requests_keep_their_own_captured_authority(api, monkeypatch):
    first, second = object(), object()
    captured, release = threading.Event(), threading.Event()
    local = threading.local()
    errors = []

    class Loader(SimpleNamespace):
        def __setattr__(self, name, value):
            super().__setattr__(name, value)
            if name == 'app_state' and value is first:
                captured.set()
                assert release.wait(10)

    class Relay:
        partitioned = True
        def bind_partition(self, value):
            self.bound = value

    monkeypatch.setattr(api, '_LOADER', Loader())
    monkeypatch.setattr(api, '_external_projection', lambda: local.relay)
    monkeypatch.setattr(api, 'current_binding', lambda state: state)
    relay_a, relay_b = Relay(), Relay()

    def request_a():
        try:
            local.relay = relay_a
            api._bound_external(SimpleNamespace(app=SimpleNamespace(state=first)))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=request_a)
    worker.start()
    try:
        assert captured.wait(10)
        local.relay = relay_b
        api._bound_external(SimpleNamespace(app=SimpleNamespace(state=second)))
        assert relay_b.bound is second
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert not errors
    assert relay_a.bound is first
    assert api._LOADER.app_state is second
