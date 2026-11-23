"""Real owner IPC to the panel publisher; not production startup/latch proof.

The fixture owner publishes canonical panel bundles, not native source captures.
No lifecycle facade is substituted and no activation is claimed here.
"""
import json
import multiprocessing
import threading
import time

import pytest

contract = pytest.importorskip('hermes_cli.fact_contract', reason='facts-v1 core integration')
hostmod = pytest.importorskip('hermes_cli.fact_host', reason='facts host integration')
replica = pytest.importorskip('hermes_cli.fact_replica', reason='facts replica integration')
from test_feed import make, feedmod
from test_fact_subscription import bridge

SCOPE = contract.Scope('fixture-install', 'panel-profile', None)
OTHER = contract.Scope('fixture-install', 'other-profile', None)
TOKEN = 'panel-test-token' * 4


def owner_main(directory, pipe):
    host = hostmod.FactHost(directory, {TOKEN: SCOPE})
    host.start()
    pipe.send(True)
    try:
        while True:
            command, value = pipe.recv()
            if command == 'stop':
                break
            if command == 'publish':
                bundle, health, until = value
                fact = contract.Fact(contract.FactKey(SCOPE, 'ChatWaitingFact', 'S1'),
                                     {'panel': bundle}, health=health, valid_until=until,
                                     error_code='source_failed' if health != 'fresh' else None)
                pipe.send(host.store.publish([fact]))
    finally:
        host.close()
        pipe.close()


def start_owner(directory):
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=owner_main, args=(directory, child))
    process.start()
    child.close()
    assert parent.poll(10) and parent.recv() is True
    return process, parent


def publish(pipe, bundle, health='fresh', until=None):
    pipe.send(('publish', (bundle, health, until)))
    assert pipe.poll(10)
    return pipe.recv()


def stop_owner(process, pipe):
    if process.is_alive():
        pipe.send(('stop', None))
        process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
    pipe.close()


def advance(adapter, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        adapter.step()
        if predicate():
            return
        adapter.feed._wake.wait(.02)
    pytest.fail('panel did not converge from real replica')


def assert_request_no_io(f, readers, monkeypatch, expected):
    import builtins, io, os, socket, sqlite3, subprocess
    owner = threading.get_ident()
    attempts = []
    def guard(original):
        def call(*args, **kwargs):
            if threading.get_ident() == owner:
                attempts.append(original.__name__)
                raise AssertionError('panel request performed I/O or encoding')
            return original(*args, **kwargs)
        return call
    with monkeypatch.context() as m:
        for obj, names in ((builtins, ('open',)), (io, ('open',)), (sqlite3, ('connect',)),
                           (socket, ('socket', 'create_connection')), (subprocess, ('Popen', 'run')),
                           (os, ('stat', 'open', 'listdir', 'scandir')),
                           (f, ('compute', '_encode')),
                           (readers, ('lineage', 'linked', 'done', 'card', 'stamps', 'changed_cards', 'release'))):
            for name in names:
                m.setattr(obj, name, guard(getattr(obj, name)))
        assert json.loads(f.changes_bytes(['S1']))['bundles']['S1'] == expected
        assert f.status()['bundles'] == 1
        assert attempts == []


def test_host_large_reset_recovers_panel_without_resubscription_loop(tmp_path, monkeypatch):
    process, pipe = start_owner(tmp_path)
    handle = hostmod.acquire(SCOPE, endpoint=replica.Endpoint(str(tmp_path / 'facts.sock'), TOKEN))
    f, readers, pushed, clock = make()
    bundle = f.compute('S1')
    adapter = bridge.PanelSubscription(f, handle.v1, SCOPE, handle.v1.key)
    try:
        f.changes_bytes(['S1'])
        publish(pipe, bundle)
        advance(adapter, lambda: 'S1' in f.bundles)
        epoch = f.epoch
        large = {**bundle, 'cards': {**bundle['cards'], 'large': {'body': 'x' * (5 * 1024 * 1024)}},
                 'computed_seq': bundle['computed_seq'] + 1}
        publish(pipe, large)
        advance(adapter, lambda: f.bundles['S1']['bundle'] == large)
        assert adapter.pending_bytes <= adapter.MAX_BYTES and not adapter.reset_needed
        generation = adapter.generation
        for _ in range(3):
            adapter.step()
        assert adapter.generation == generation
        assert f.epoch == epoch
        assert_request_no_io(f, readers, monkeypatch, f._client_bundle('S1', large, f._bundle_fresh('S1', f.clock())))
        with pytest.raises(ValueError, match='unauthorized_scope'):
            handle.v1.snapshot(OTHER)
        publish(pipe, large, health='stale')
        advance(adapter, lambda: 'S1' in f.failures)
        assert f.bundles['S1']['bundle'] == large
        clock['now'] += f.heartbeat_s
        adapter.step()
        assert pushed[-1]['fresh'] == []
        publish(pipe, bundle, until=time.time() + 2)
        advance(adapter, lambda: not f.failures and f.bundles['S1']['bundle'] == bundle)
        # No later write: owner's validity scheduler must withdraw freshness.
        advance(adapter, lambda: 'S1' in f.failures)
        assert f.bundles['S1']['bundle'] == bundle
        publish(pipe, bundle)
        advance(adapter, lambda: not f.failures)
        old_epoch = adapter.cursor[0]
        process.terminate()  # fixture-owned process only, no live signals
        process.join(5)
        pipe.close()
        advance(adapter, lambda: 'S1' in f.failures)
        process, pipe = start_owner(tmp_path)
        publish(pipe, large)
        advance(adapter, lambda: not f.failures and f.bundles['S1']['bundle'] == large)
        assert adapter.cursor[0] != old_epoch and f.epoch == epoch
    finally:
        adapter.close()
        handle.close()
        stop_owner(process, pipe)
