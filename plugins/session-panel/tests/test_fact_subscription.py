"""Panel bridge unit tests; shared FakeStore integration is a separate suite."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
from dataclasses import dataclass

import pytest
from test_feed import feedmod, make

_spec = importlib.util.spec_from_file_location('panel_fact_subscription_test',
    Path(__file__).resolve().parents[1] / 'dashboard' / 'fact_subscription.py')
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


@dataclass(frozen=True)
class Key:
    scope: str
    kind: str
    id: str


class Transport:
    """Controllable callback transport, deliberately NOT a competing FakeStore."""
    def __init__(self):
        self.calls = []
        self.closes = []
        self.latest = None
        self.facts = {}

    def snapshot(self, scope):
        delta, facts = self.latest, dict(self.facts)
        return NS(epoch=delta.epoch, rev=delta.rev,
                  get_many=lambda keys: tuple(facts.get(k) for k in keys))

    def subscribe(self, scope, kinds, keys, after, callback):
        self.calls.append((scope, kinds, keys, after, callback))
        return NS(close=lambda: self.closes.append(callback))

    def send(self, bundle=None, *, epoch='owner-a', prev=0, rev=1, reset=True,
             health='fresh', deletes=(), scope='profile-a'):
        facts = () if bundle is None else (NS(key=Key(scope, 'ChatWaitingFact', 'S1'),
            value={'panel': bundle}, health=health, error_code=None, valid_until=None),)
        delta = NS(epoch=epoch, prev=prev, rev=rev, reset=reset, upserts=facts, deletes=deletes)
        self.latest = delta
        if reset:
            self.facts.clear()
        self.facts.update((f.key, f) for f in facts)
        for key in deletes:
            self.facts.pop(key, None)
        self.calls[-1][-1](delta)
        return delta


@pytest.fixture
def setup():
    f, readers, pushed, clock = make()
    bundle = f.compute('S1')
    transport = Transport()
    adapter = bridge.PanelSubscription(f, transport, 'profile-a', Key)
    f.changes_bytes(['S1'])
    adapter.step()
    yield f, readers, pushed, clock, bundle, transport, adapter
    adapter.close()


def test_exact_legacy_bundle_wire_and_push(setup):
    f, _, pushed, _, bundle, transport, adapter = setup
    transport.send(bundle)
    adapter.step()
    actual = json.loads(f.changes_bytes(['S1']))
    assert actual['bundles']['S1'] == feedmod.wire_bundle(bundle)
    assert pushed[-1]['bundles'] == actual['bundles']
    assert pushed[-1]['prev'] == 0
    assert pushed[-1]['rev'] == actual['rev']
    assert f.bundles['S1']['bundle'] == bundle


def test_readiness_exception_recovery_and_deadline_without_publication():
    import threading
    transport = Transport()
    reported = threading.Event()
    reports = []
    state = {'fail': False, 'ready': True, 'delay': None}
    def refresh():
        if state['fail']:
            raise OSError('replica unavailable')
        return state['ready'], state['delay']
    def report(*a, **kw):
        reports.append(kw['ready'])
        reported.set()
    transport.report_ready = report
    worker = bridge.PanelReadiness(transport, 'profile-a', refresh)
    try:
        assert reported.wait(2) and reports == [True]
        reported.clear()
        state['fail'] = True
        worker.wake.set()
        assert reported.wait(2) and reports == [True, False]
        reported.clear()
        state.update(fail=False, delay=0.05)
        worker.wake.set()
        assert reported.wait(2) and reports == [True, False, True]
        reported.clear()
        state.update(ready=False, delay=None)
        # No publication/wake: the outstanding expiry timer withdraws readiness.
        assert reported.wait(2) and reports == [True, False, True, False]
    finally:
        worker.close()


def test_readiness_close_during_refresh_never_reports():
    import threading
    transport = Transport()
    entered, release = threading.Event(), threading.Event()
    reports = []
    transport.report_ready = lambda *a, **kw: reports.append(kw)
    def refresh():
        entered.set()
        assert release.wait(2)
        return True, None
    worker = bridge.PanelReadiness(transport, 'profile-a', refresh)
    assert entered.wait(2)
    closer = threading.Thread(target=worker.close)
    closer.start()
    assert worker.stopped.wait(2)
    release.set()
    closer.join(2)
    assert not closer.is_alive()
    assert reports == []


def test_readiness_report_failure_retries_without_publication():
    import threading
    transport = Transport()
    recovered = threading.Event()
    reports = []
    def report(*a, **kw):
        reports.append(kw['ready'])
        if len(reports) == 1:
            raise OSError('temporary report failure')
        recovered.set()
    transport.report_ready = report
    worker = bridge.PanelReadiness(transport, 'profile-a', lambda: (True, None))
    try:
        assert recovered.wait(3)
        assert reports == [True, True]
    finally:
        worker.close()


def test_readiness_deferred_start_can_close_without_reporting():
    transport = Transport()
    transport.report_ready = lambda *a, **kw: pytest.fail('unaccepted binding reported')
    worker = bridge.PanelReadiness(transport, 'profile-a', lambda: (True, None), start=False)
    assert worker.thread.ident is None
    worker.close()
    assert transport.closes


def test_warm_and_cold_reads_never_call_source_helpers(setup, monkeypatch):
    f, readers, _, _, bundle, transport, adapter = setup
    transport.send(bundle)
    adapter.step()
    def forbidden(*args, **kwargs):
        raise AssertionError('request performed I/O')
    for name in ('lineage', 'linked', 'done', 'card', 'stamps', 'changed_cards', 'release'):
        monkeypatch.setattr(readers, name, forbidden)
    monkeypatch.setattr(f, 'compute', forbidden)
    import builtins, io, sqlite3, socket, subprocess, os
    for obj, names in ((builtins, ('open',)), (io, ('open',)), (sqlite3, ('connect',)),
                       (socket, ('socket', 'create_connection')), (subprocess, ('Popen', 'run')),
                       (os, ('stat', 'open', 'listdir', 'scandir'))):
        for name in names:
            monkeypatch.setattr(obj, name, forbidden)
    assert json.loads(f.changes_bytes(['S1']))['bundles']['S1'] == feedmod.wire_bundle(bundle)
    assert f.status()['bundles'] == 1
    assert f.changes(['cold'])['bundles'] == {}


def test_source_failure_keeps_last_good_and_quiet_recovery(setup):
    f, _, pushed, clock, bundle, transport, adapter = setup
    transport.send(bundle)
    adapter.step()
    transport.send(bundle, reset=False, prev=1, rev=2, health='stale')
    clock['now'] += 20
    adapter.step()
    assert f.bundles['S1']['bundle'] == bundle
    assert pushed[-1]['fresh'] == []
    assert 'S1' in f.failures
    transport.send(bundle, reset=False, prev=2, rev=3)
    adapter.step()
    assert f.failures == {}
    assert 'S1' not in f._retry


def test_owner_epoch_reset_accepts_lower_sequence_without_wire_mutation(setup):
    f, _, _, _, bundle, transport, adapter = setup
    bundle = {**bundle, 'computed_seq': 900, 'canonical_seq': 900}
    transport.send(bundle)
    adapter.step()
    restarted = {**bundle, 'computed_seq': 1, 'canonical_seq': 1, 'tasks': []}
    transport.send(restarted, epoch='owner-b')
    adapter.step()
    assert f.bundles['S1']['bundle'] == restarted


def test_gap_resubscribes_and_stale_callback_is_fenced(setup):
    f, _, _, _, bundle, transport, adapter = setup
    first_callback = transport.calls[-1][-1]
    transport.send(bundle)
    adapter.step()
    transport.send(bundle, reset=False, prev=8, rev=9)
    adapter.step()
    assert len(transport.calls) == 2
    assert len(transport.closes) == 1
    assert 'S1' in f.failures
    old = transport.send(bundle, epoch='owner-b')
    first_callback(old)
    adapter.step()
    assert adapter.cursor == ('owner-b', 1)
    assert f.failures == {}


def test_duplicate_or_reordered_delta_does_not_regress(setup):
    f, _, _, _, bundle, transport, adapter = setup
    transport.send(bundle)
    adapter.step()
    updated = {**bundle, 'tasks': []}
    transport.send(updated, reset=False, prev=1, rev=2)
    adapter.step()
    transport.send(bundle, reset=False, prev=0, rev=1)
    adapter.step()
    assert f.bundles['S1']['bundle'] == updated
    assert adapter.cursor == ('owner-a', 2)


def test_cross_scope_data_is_not_published(setup):
    f, _, _, _, bundle, transport, adapter = setup
    transport.send(bundle, scope='other-profile')
    adapter.step()
    assert f.bundles == {}
    assert 'S1' in f.failures


def test_deleted_fact_is_not_successful_empty_panel(setup):
    f, _, _, _, bundle, transport, adapter = setup
    transport.send(bundle)
    adapter.step()
    transport.send(reset=False, prev=1, rev=2, deletes=(Key('profile-a', 'ChatWaitingFact', 'S1'),))
    adapter.step()
    assert f.bundles['S1']['bundle'] == bundle
    assert f.failures['S1']['error'] == 'fact_panel_removed'


def test_bounded_backpressure_resets_instead_of_dropping_silently(setup):
    f, _, _, _, bundle, transport, adapter = setup
    adapter.MAX_ENVELOPES = 2
    for rev in range(1, 4):
        transport.send(bundle, rev=rev)
    assert not adapter.pending and adapter.reset_needed
    adapter.step()
    assert len(transport.calls) == 1  # same armed subscription, memory-only catch-up
    assert adapter.cursor == ('owner-a', 3) and not f.failures
    assert f.bundles['S1']['bundle'] == bundle
    transport.send(bundle, rev=4)
    adapter.step()
    assert f.failures == {}


def test_overflow_snapshot_does_not_accept_inflight_previous_epoch(setup, monkeypatch):
    f, _, pushed, _, bundle, transport, adapter = setup
    old = transport.send(bundle)
    adapter.step()
    replacement = {**bundle, 'tasks': [], 'computed_seq': 2}
    transport.send(replacement, epoch='owner-b')
    adapter.reset_needed = True
    original = transport.snapshot
    def snapshot(scope):
        snap = original(scope)
        monkeypatch.setattr(transport, 'snapshot', original)
        # Prior owner callback was formed before B's replica publication but
        # remained in flight until snapshot catch-up. It must never republish A.
        transport.calls[-1][-1](old)
        return snap
    monkeypatch.setattr(transport, 'snapshot', snapshot)
    before = len(pushed)
    adapter.step()
    assert adapter.cursor == ('owner-b', 1)
    assert f.bundles['S1']['bundle'] == replacement
    assert all(event['bundles'].get('S1', {}).get('tasks', []) == [] for event in pushed[before:])


def test_watch_expiry_and_cap_use_existing_feed_rules(setup):
    f, _, _, clock, bundle, transport, adapter = setup
    transport.send(bundle)
    adapter.step()
    clock['now'] += f.interest_ttl_s + 1
    adapter.step()
    assert f.interest == {} and f.bundles == {}
    assert transport.closes
    for start in range(0, 600, 200):
        f.changes_bytes([f's-{n}' for n in range(start, start + 200)])
    adapter.step()
    assert len(f.interest) == feedmod.MAX_WATCHED
    assert len(transport.calls[-1][2]) == feedmod.MAX_WATCHED


def test_snapshot_resume_keeps_panel_epoch_and_validates_carried_data(setup):
    f, _, pushed, clock, bundle, transport, adapter = setup
    transport.send(bundle)
    adapter.step()
    snap = f.snapshot()
    restored = feedmod.Feed(None, emit=pushed.append, clock=lambda: clock['now']).restore(snap)
    other = bridge.PanelSubscription(restored, transport, 'profile-a', Key)
    try:
        assert restored.epoch == f.epoch
        assert 'S1' in restored._carried
        other.step()
        transport.send(bundle)
        other.step()
        assert 'S1' not in restored._carried
        assert restored.rev > snap['rev']
        assert restored.bundles['S1']['bundle'] == bundle
    finally:
        other.close()
