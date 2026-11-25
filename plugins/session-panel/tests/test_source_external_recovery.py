"""Real replica capture cannot certify a rewatch after disk/owner recovery."""
import threading
import sqlite3
from contextlib import closing

import pytest
from test_core_readers import api, board
from test_reader_batch import graph
from test_source_coordinator_core import compose, drain, feedmod
from test_coordinator_scope_lifetime import _freeze_capture_clock


@pytest.mark.parametrize('recovery', ['disk', 'stopped_owner'])
@pytest.mark.parametrize('old_result', ['success', 'failure'])
def test_held_external_recovery_requires_current_watch_coverage(
        graph, api, monkeypatch, tmp_path, recovery, old_result):
    r, local, remote, _ = graph
    now = [1800000000.]
    old = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0])
    old.watch(['S0'])
    previous = compose(old, r)
    current = lane = None
    entered, release = threading.Event(), threading.Event()
    try:
        old.changes(['S0']); drain(previous)
        assert remote in old.bundles['S0']['bundle']['cards']
        snapshot_path = tmp_path / 'feed-recovery.json'
        if recovery == 'disk':
            assert api.save_snapshot(old, snapshot_path, claim=True)
        old.stopped.set(); previous.close()
        if recovery == 'disk':
            loaded = api.load_snapshot(snapshot_path, now=now[0])
            assert loaded
            current = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0]).restore(loaded)
            lane = compose(current, r)
            assert not lane.chats, 'disk bytes cannot restore source coverage'
        else:
            monkeypatch.setattr(feedmod, 'SNAPSHOT_READER_FACTORY', lambda: r)
            # This test manually drives the real established owner; genuine saved
            # predecessor automatic-service establishment has separate controls.
            monkeypatch.setattr(type(previous), 'start_service', lambda self: None)
            current = feedmod.Feed(r, emit=lambda _: None, clock=lambda: now[0]).resume(old)
            pending = current._source_coordinator
            current.ensure_snapshot_service(pending.api_generation, pending.reader_factory)
            lane = current._source_coordinator
            assert not getattr(lane, 'pending_install', False)
            assert lane.store.pin(lane.chats['S0'])[0].parts
        # Demand a genuine new source version before the held turn. The rewatch
        # itself below performs no database write and cannot piggyback on this
        # old token's acquisition, including after compatible store transfer.
        from hermes_cli import kanban_sync_client as ksc
        with closing(sqlite3.connect(ksc.shadow_path('default'))) as conn:
            conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Recovered external successor', remote))
            conn.commit()
        original = r.capture_external
        def held(*args, **kwargs):
            value = original(*args, **kwargs)
            entered.set()
            assert release.wait(5), 'external barrier not released'
            if old_result == 'failure':
                raise RuntimeError('old recovered external attempt failed')
            return value
        monkeypatch.setattr(r, 'capture_external', held)
        _freeze_capture_clock(lane, monkeypatch)
        current.watch(['S0'])
        lane._external_requested = True
        # Fresh disk coverage first needs current local registration/lineage;
        # drive that real completion before expecting an external demand turn.
        for _ in range(3):
            lane.step()
            worker = lane.acquiring.get('local')
            if worker is not None:
                worker.join(5)
                assert not worker.is_alive()
            lane.service()
            if entered.is_set():
                break
        assert entered.wait(3)
        chat = lane.chats['S0']
        source = lane.external
        frozen = dict(lane.store.demands(source))
        old_pin = lane.store.pin(chat)[1]
        old_feed_token = current._watch_tokens['S0']
        with current.lock:
            current._forget('S0')
        current.watch(['S0'])
        assert current._watch_tokens['S0'] is not old_feed_token
        lane.store.forget(chat, old_pin.watch_token)
        lane.store.register([lane.module.Watch(chat, '', ('S0',), 0)])
        lane.tokens['S0'] = current._watch_tokens['S0']
        pin = lane.store.pin(chat)[1]
        token = pin.watch_token
        assert token != old_pin.watch_token and source in pin.missing_scopes
        assert pin.required_scopes[source].id not in frozen
        release.set()
        for worker in list(lane.acquiring.values()):
            worker.join(5)
            assert not worker.is_alive()
        lane.service()
        pin = lane.store.pin(chat)[1]
        assert pin.watch_token == token and source in pin.missing_scopes
        assert pin.required_scopes[source].id not in frozen
        assert not current._bundle_fresh('S0', now[0])
        monkeypatch.setattr(r, 'capture_external', original)
        drain(lane)
        pin = lane.store.pin(chat)[1]
        assert not pin.missing_scopes
        assert remote in current.bundles['S0']['bundle']['cards']
    finally:
        release.set()
        if current is not None:
            current.stopped.set()
        if lane is not None:
            lane.close()
        old.stopped.set(); previous.close()


def test_stopped_drained_owner_rolls_back_only_to_clean_uncomposed_feed(graph):
    r, local, remote, _ = graph
    old = feedmod.Feed(r, emit=lambda _: None)
    old.watch(['S0'])
    baseline = old.compute('S0')
    lane = compose(old, r)
    legacy = None
    try:
        old.changes(['S0']); drain(lane)
        chat = lane.chats['S0']
        pin = lane.store.pin(chat)
        old.stopped.set(); lane.close()
        assert lane.closed and lane.drained.is_set()
        assert not any(w.is_alive() for w in (*lane.acquiring.values(), *lane.active.values()))
        assert lane.service_worker is None or not lane.service_worker.is_alive()
        assert not lane.store.current(pin[1]), 'retired source authority survived drain'
        legacy = feedmod.Feed(r, emit=lambda _: None)  # deliberately NOT resume(old)
        assert getattr(legacy, '_source_coordinator', None) is None
        assert legacy.epoch != old.epoch
        bundle = legacy.compute('S0')
        assert {local, remote} <= {t['id'] for t in bundle['tasks']}
        assert set(bundle.get('input_errors', ())) == set(baseline.get('input_errors', ()))
        assert bundle['model']['header']['source'] == baseline['model']['header']['source']
        assert bundle['model']['header']['status'] == baseline['model']['header']['status']
        assert getattr(legacy, '_source_coordinator', None) is None
        assert not lane.store.current(pin[1])
    finally:
        if legacy is not None:
            legacy.stopped.set()
        old.stopped.set(); lane.close()
