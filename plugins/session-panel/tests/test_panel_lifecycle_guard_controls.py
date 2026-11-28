"""Unmasked lifecycle predicates: no earlier authority check may hide these guards."""
import contextlib
import sys
import types

import pytest

pytest.importorskip('hermes_cli.serve_producer_admission')
import test_explicit_home_context as home_helpers

api = home_helpers.api


@pytest.mark.parametrize('state', ['absent', 'held', 'needs-custody'])
def test_authorize_legacy_observes_exact_participant_ownership(api, monkeypatch, state):
    from hermes_cli import serve_producer_admission as admission
    calls = []
    gate = object()
    @contextlib.contextmanager
    def wait(runtime, deadline):
        calls.append(('gate', runtime))
        yield gate
    monkeypatch.setattr(admission, '_wait_gate', wait)
    participant = None if state == 'absent' else types.SimpleNamespace(
        _owner=None if state == 'needs-custody' else object(), runtime='fixture-runtime',
        begin_legacy=lambda actual: calls.append(('begin', actual)))
    monkeypatch.setattr(api._LOADER, 'panel_participant', participant, raising=False)
    api._authorize_legacy()
    assert calls == ([('gate', 'fixture-runtime'), ('begin', gate)] if state == 'needs-custody' else [])


def test_legacy_http_fence_is_independent_of_off_ready_state(api, monkeypatch):
    monkeypatch.setattr(api._LOADER, 'panel_lifecycle', types.SimpleNamespace(state='off', _legacy_running=True), raising=False)
    monkeypatch.setattr(api, '_legacy_fenced', lambda: True)
    assert api._legacy_http_unavailable() is True
    monkeypatch.setattr(api, '_legacy_fenced', lambda: False)
    assert api._legacy_http_unavailable() is False


def test_detach_only_withdraws_the_exact_retained_relay(api, monkeypatch):
    first, second = [], []
    current = types.SimpleNamespace(set_event_sink=first.append)
    stale = types.SimpleNamespace(set_event_sink=second.append)
    monkeypatch.setattr(api._LOADER, 'external_projection', current, raising=False)
    api.detach_external(stale)
    assert api._LOADER.external_projection is current and second == [None] and first == []
    api.detach_external(current)
    assert api._LOADER.external_projection is None and first == [None]


@pytest.mark.parametrize('entry', ['_retire_legacy_panel', '_retire_legacy_locked'])
@pytest.mark.parametrize('survives', [False, True])
def test_retirement_joins_the_scheduled_starter_before_clearing_custody(api, monkeypatch, entry, survives):
    calls = []
    alive = [True]
    def join(timeout):
        calls.append(timeout)
        alive[0] = survives
    monkeypatch.setattr(api._LOADER, 'starting', types.SimpleNamespace(is_alive=lambda: alive[0], join=join), raising=False)
    monkeypatch.delitem(sys.modules, api._FEED_KEY, raising=False)
    if entry == '_retire_legacy_panel':
        monkeypatch.setattr(api, '_retire_legacy_locked', lambda timeout: True)
    assert getattr(api, entry)(timeout=0.2) is (not survives)
    assert calls == [0.2]
    assert api._legacy_fenced()


def test_empty_retirement_needs_no_feed_but_fences_future_start(api, monkeypatch):
    monkeypatch.delitem(sys.modules, api._FEED_KEY, raising=False)
    monkeypatch.setattr(api._LOADER, 'starting', None, raising=False)
    assert api._retire_legacy_locked() is True
    assert api._legacy_fenced()


def test_locked_start_checks_fence_without_public_entry_mask(api, monkeypatch):
    monkeypatch.setattr(api, '_external_projection', lambda: None)
    monkeypatch.setattr(api, '_legacy_fenced', lambda: True)
    monkeypatch.setattr(api, '_load_feed', lambda: pytest.fail('fenced start loaded a source'))
    with pytest.raises(RuntimeError, match='fenced'):
        api._feed_locked()


def test_starter_already_stopped_is_not_joined_or_claimed_live(api, monkeypatch):
    monkeypatch.delitem(sys.modules, api._FEED_KEY, raising=False)
    monkeypatch.setattr(api._LOADER, 'starting', types.SimpleNamespace(
        is_alive=lambda: False, join=lambda timeout: pytest.fail('stopped starter joined')), raising=False)
    assert api._retire_legacy_locked() is True
    assert api._retire_legacy_panel() is True


def test_unfenced_publication_starts_one_background_owner_without_loading_feed(api, monkeypatch):
    started = []
    def create(**kwargs):
        assert kwargs == {'target': api.feed, 'name': 'session-panel-start', 'daemon': True}
        return types.SimpleNamespace(start=lambda: started.append('start'), is_alive=lambda: True)
    monkeypatch.setattr(api, 'threading', types.SimpleNamespace(Thread=create))
    monkeypatch.setattr(api, '_external_projection', lambda: None)
    monkeypatch.setattr(api, '_legacy_fenced', lambda: False)
    monkeypatch.setattr(api._LOADER, 'starting', None, raising=False)
    assert api.start_publication() is True
    assert started == ['start']
    assert api.start_publication() is False
    assert started == ['start']


def test_public_feed_fence_cannot_be_masked_by_the_locked_entry(api, monkeypatch):
    monkeypatch.setattr(api, '_legacy_fenced', lambda: True)
    monkeypatch.setattr(api, '_authorize_legacy', lambda: pytest.fail('fenced public feed requested custody'))
    monkeypatch.setattr(api, '_feed_locked', lambda: pytest.fail('public fence bypassed'))
    with pytest.raises(RuntimeError, match='fenced'):
        api.feed()


def test_facts_registration_prevents_positive_retirement_after_lanes_drain(api, monkeypatch):
    wakes = []
    fake = types.SimpleNamespace(**{name: types.SimpleNamespace(set=lambda name=name: wakes.append(name))
                                    for name in ('_wake', '_decide', '_board')})
    core = types.SimpleNamespace(PROCESS_FEED=fake)
    monkeypatch.setitem(sys.modules, api._FEED_KEY, core)
    monkeypatch.setattr(api._LOADER, 'starting', None, raising=False)
    monkeypatch.setattr(api._LOADER, 'facts_registration', object(), raising=False)
    seen = []
    monkeypatch.setattr(api, '_quiesce', lambda feed, timeout: seen.append(feed) or True)
    assert api._retire_legacy_locked() is False
    assert seen == [fake] and core.PROCESS_FEED is fake
    assert wakes == ['_wake', '_decide', '_board']
    monkeypatch.setattr(api._LOADER, 'facts_registration', None)
    assert api._retire_legacy_locked() is True and core.PROCESS_FEED is None


def test_initial_publication_wait_is_bounded_and_only_completed_output_is_ready(api, monkeypatch):
    from hermes_cli import serve_producer_lifecycle as lifecycle
    from hermes_cli import serve_producer_mode as mode
    from hermes_cli import serve_producer_retirement as retirement
    captured = {}
    def construct(installation, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(start_watching=lambda: None)
    monkeypatch.setattr(lifecycle, 'PanelLifecycle', construct)
    monkeypatch.setattr(mode, 'effective_external', lambda *args: False)
    monkeypatch.setattr(retirement, 'bind_panel', lambda *args, **kwargs: types.SimpleNamespace(participant=object()))
    monkeypatch.setattr(api, '_start_legacy_authorized', lambda: True)
    api.install_panel_lifecycle({})
    assert captured['legacy_running'] is True, 'OFF construction preserves the legacy owner state'
    publish = captured['publish_initial']
    reads, sleeps = [], []
    def read(key):
        reads.append(key)
        assert len(reads) <= 2, 'initial publication ignored its deadline'
        return None
    relay = types.SimpleNamespace(submit=lambda payload: 'initial-key', read=read)
    ticks = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(api, 'time', types.SimpleNamespace(monotonic=lambda: next(ticks), sleep=sleeps.append))
    monkeypatch.setattr(api, 'panel_initial_payload', lambda: {'initial': True})
    assert publish(relay, timeout=0.2) is False
    assert reads == ['initial-key'] and sleeps == [0.05]
    ticks = iter([0.0, 0.1])
    relay.read = lambda key: b'{"running":true}'
    assert publish(relay, timeout=0.2) is True


@pytest.mark.parametrize('spec', [None, types.SimpleNamespace(loader=None)])
def test_callback_loader_refuses_missing_import_authority_before_module_construction(api, monkeypatch, spec):
    monkeypatch.delitem(sys.modules, '_session_panel_callback_bootstrap', raising=False)
    monkeypatch.setattr(api.importlib.util, 'spec_from_file_location', lambda *args: spec)
    monkeypatch.setattr(api.importlib.util, 'module_from_spec', lambda *args: pytest.fail('missing import authority used'))
    with pytest.raises(AssertionError):
        api._callback_bootstrap_mod()


def test_callback_loader_returns_the_initialized_cached_module_without_reexecution(api, monkeypatch):
    module = api._callback_bootstrap_mod()
    monkeypatch.setattr(api.importlib.util, 'spec_from_file_location', lambda *a, **k: pytest.fail('cached bootstrap reexecuted'))
    assert api._callback_bootstrap_mod() is module
