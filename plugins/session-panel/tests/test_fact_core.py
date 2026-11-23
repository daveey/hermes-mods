"""Execute the real shared FakeStore contract, not a panel-local store imitation."""
import json
from dataclasses import replace

import pytest

contract = pytest.importorskip('hermes_cli.fact_contract', reason='facts-v1 core integration')
fakes = pytest.importorskip('hermes_cli.fact_fake', reason='facts-v1 core integration')
from test_feed import feedmod, make
from test_fact_subscription import bridge
from test_fact_lifecycle import api


def test_dashboard_mount_close_is_exact_and_retryable(api, monkeypatch):
    from types import SimpleNamespace as NS
    scope = contract.Scope('fixture-install', 'fixture-profile', None)
    first_store, second_store = fakes.FakeStore(), fakes.FakeStore()
    def context(store):
        return NS(facts=NS(v1=store, scope=scope, handle=NS(demand=lambda *a: None)))
    first = api.bind_facts(context(first_store))
    second = api.bind_facts(context(second_store))
    original = api._quiesce
    try:
        first.close()  # must not close the newly published binding
        assert api._LOADER.facts_mount is second
        assert not second.installation.stopped.is_set()
        monkeypatch.setattr(api, '_quiesce', lambda f: False)
        with pytest.raises(RuntimeError, match='quiesce'):
            second.close()
        assert api._LOADER.facts_mount is second
        monkeypatch.setattr(api, '_quiesce', original)
        second.close()
        second.close()
        assert api._LOADER.facts_mount is None
        assert second.installation.stopped.is_set()
    finally:
        monkeypatch.setattr(api, '_quiesce', original)
        second.close()


def test_quiet_publication_readiness_is_serial_and_closes(api, monkeypatch):
    import threading
    from types import SimpleNamespace as NS
    store = fakes.FakeStore()
    scope = contract.Scope('fixture-install', 'fixture-profile', None)
    f = api._load_feed().PROCESS_FEED
    f.watch(['S1'])
    reported = threading.Event()
    reports = []
    original = store.report_ready
    def report(*args, **kwargs):
        original(*args, **kwargs)
        reports.append(kwargs['ready'])
        reported.set()
    monkeypatch.setattr(store, 'report_ready', report)
    registration = api.install_facts(store, scope, NS(demand=lambda *a: None))
    try:
        assert reported.wait(2) and reports == [False]
        reported.clear()
        fact = contract.Fact(store.key(scope, 'ChatWaitingFact', 'S1'), {'panel': {}}, health='fresh')
        view = contract.Fact(store.view_key(scope, 'panel_bundle', {'session': 'S1'}),
                             {'encoded': b'{}'}, health='fresh')
        store.publish([fact, view])
        store.drain()  # no old reader activity: publication itself wakes readiness
        assert reported.wait(2) and reports == [False, True]
        reported.clear()
        store.publish([replace(view, health='stale')])
        store.drain()
        assert reported.wait(2) and reports == [False, True, False]
    finally:
        registration.close()
    store.publish([view])
    store.drain()
    assert not registration.thread.is_alive() and reports == [False, True, False]


def test_shared_fake_parity_and_ring_recovery():
    f, readers, pushed, clock = make()
    bundle = f.compute('S1')
    scope = contract.Scope('fixture-install', 'fixture-profile', None)
    key = contract.FactKey(scope, 'ChatWaitingFact', 'S1')
    store = fakes.FakeStore(ring_revisions=2, subscriber_envelopes=2)
    fact = contract.Fact(key, {'panel': bundle}, observed_at=clock['now'], health='fresh')
    store.publish([fact])
    adapter = bridge.PanelSubscription(f, store, scope, store.key)
    try:
        f.changes_bytes(['S1'])
        adapter.step()
        store.drain()
        adapter.step()
        assert json.loads(f.changes_bytes(['S1']))['bundles']['S1'] == f._client_bundle('S1', bundle, f._bundle_fresh('S1', f.clock()))
        held_epoch = f.epoch
        pos = store.position(store.snapshot(scope).epoch, store.snapshot(scope).rev)
        for n in range(8):
            store.publish([replace(fact, value={'panel': {**bundle, 'tasks': [], 'computed_seq': n + 2}})])
        assert store.changes(scope, pos).reset
        store.drain()
        adapter.step()
        assert f.bundles['S1']['bundle']['tasks'] == []
        assert f.epoch == held_epoch
        store.publish([replace(fact, health='stale', error_code='source_failed')])
        store.drain()
        clock['now'] += f.heartbeat_s
        adapter.step()
        assert 'S1' in f.failures and pushed[-1]['fresh'] == []
        store.publish([fact])
        store.drain()
        adapter.step()
        assert f.failures == {}
        assert f.bundles['S1']['bundle'] == bundle
    finally:
        adapter.close()


def test_shared_fake_future_cursor_reset_and_atomic_catchup():
    store = fakes.FakeStore()
    scope = contract.Scope('fixture-install', 'fixture-profile', None)
    f, _, _, _ = make()
    bundle = f.compute('S1')
    fact = contract.Fact(store.key(scope, 'ChatWaitingFact', 'S1'), {'panel': bundle}, health='fresh')
    snapshot = store.snapshot(scope)
    received = []
    sub = store.subscribe(scope, ('ChatWaitingFact',), (fact.key,),
                          store.position(snapshot.epoch, snapshot.rev + 100), received.append)
    try:
        store.publish([fact])  # publication between subscribe and callback drain
        store.drain()
        assert received and received[0].reset
        assert any(any(x.key == fact.key for x in d.upserts) for d in received)
    finally:
        sub.close()


def test_latch_stops_legacy_lanes_and_rollback_starts_once(monkeypatch):
    import threading
    import sys
    from pathlib import Path
    from types import SimpleNamespace as NS
    from test_routes import load
    from test_feed import Readers
    # Isolate the process-wide singleton from other API-generation fixtures.
    monkeypatch.delitem(sys.modules, '_session_panel_feed_core', raising=False)
    monkeypatch.delitem(sys.modules, '_session_panel_feed_loader', raising=False)
    api = load('_panel_facts_lifecycle_test', Path(__file__).resolve().parents[1] / 'dashboard/plugin_api.py')
    core = api._load_feed()
    # This fixture owns readiness explicitly to test lifecycle idempotency. Do
    # not race its manual report_ready(True) with the real readiness publisher:
    # a refresh during facts_transition_pending legitimately reports False.
    # Real asynchronous readiness/publication is exercised by the test above.
    monkeypatch.setattr(core.PanelReadiness, 'start', lambda self: None)
    monkeypatch.setattr(api, 'CoreReaders', Readers)
    monkeypatch.setattr(api, 'save_snapshot', lambda *a, **k: True)
    monkeypatch.setattr(api, 'load_snapshot', lambda *a, **k: None)
    monkeypatch.setattr(api, '_emit', lambda *a: None)
    starts, exits = [], []
    events = {name: threading.Event() for name in ('feed', 'decisions', 'board')}
    def lane(f, name):
        starts.append(name)
        events[name].set()
        f.stopped.wait(5)
        exits.append(name)
    monkeypatch.setattr(api, '_run', lambda f: lane(f, 'feed'))
    monkeypatch.setattr(api, '_run_decisions', lambda f: lane(f, 'decisions'))
    monkeypatch.setattr(api, '_run_board', lambda f, cls: lane(f, 'board'))
    store = fakes.FakeStore()
    scope = contract.Scope('fixture-install', 'fixture-profile', None)
    registrations = []
    try:
        original = api.feed()
        for event in events.values():
            assert event.wait(2)
        epoch = original.epoch
        registrations.append(api.install_facts(store, scope, NS(demand=lambda *a: None)))
        with pytest.raises(RuntimeError, match='consumer set not ready'):
            store.activate(scope)
        for name in ('sessions', 'annotations', 'board', 'notifications'):
            registrations.append(store.bind_consumer(name, scope, lambda *a: None))
            store.report_ready(name, scope)
        store.report_ready('panel', scope)
        store.activate(scope)
        active = core.PROCESS_FEED
        assert sorted(exits) == ['board', 'decisions', 'feed']
        assert active is not original and active.epoch == epoch
        assert active._facts_mode and active.readers is None
        store.activate(scope)
        assert core.PROCESS_FEED is active
        for event in events.values():
            event.clear()
        store.rollback(scope)
        for event in events.values():
            assert event.wait(2)
        restored = core.PROCESS_FEED
        assert restored is not active and restored.epoch == epoch
        assert active._fact_subscription.closed
        store.rollback(scope)
        assert core.PROCESS_FEED is restored
        assert sorted(starts) == ['board', 'board', 'decisions', 'decisions', 'feed', 'feed']
    finally:
        current = core.PROCESS_FEED
        if current is not None:
            api._quiesce(current, timeout=3)
        core.PROCESS_FEED = None
        for registration in registrations:
            registration.close()
        monkeypatch.delitem(sys.modules, '_session_panel_feed_core', raising=False)
        monkeypatch.delitem(sys.modules, '_session_panel_feed_loader', raising=False)
