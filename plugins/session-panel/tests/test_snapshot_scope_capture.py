"""Frozen scope acquisition races at unchanged SQLite stamps (real core readers)."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from test_reader_batch import api, board, graph


@pytest.mark.parametrize('domain', ['local', 'external'])
@pytest.mark.parametrize('change', ['register', 'lineage', 'refs', 'rewatch'])
def test_scope_changes_during_real_capture_do_not_certify_successor(graph, domain, change, monkeypatch):
    r, local, remote, ksa = graph
    cap = r.snapshot_modules()
    m = cap.snapshot
    store = m.SnapshotStore()
    source = m.SourceKey('local-test', 'default', 'default', 'local' if domain == 'local' else 'hub-links',
                         str(r.boards()[0][1]))
    store.add_source(source)
    first, second = (m.ChatKey('local-test', 'default', name) for name in ('S0', 'S1'))
    store.register([m.Watch(first, '', ('S0',), 0)])
    if change != 'register':
        store.register([m.Watch(second, '', ('S1',), 0)])
    entered, release = threading.Event(), threading.Event()
    if domain == 'local':
        from hermes_cli import kanban_session_outcome_tasks as selector
        original = selector.select_listed_local_task_set
        def held(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        monkeypatch.setattr(selector, 'select_listed_local_task_set', held)
    else:
        original = r.external_capture
        def held(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        monkeypatch.setattr(r, 'external_capture', held)
    demands = store.demands(source)
    prior = store.version(source)
    hint = cap.CaptureHints(prior, store.owner_token(source), 1800000000.0)
    def capture():
        if domain == 'local':
            return r.capture_local(source, None, demands, hint)
        return r.capture_external(demands, {}, {}, hints={source: hint})[0]
    # The local deadline remains real. Position the race and release immediately;
    # the barrier is not an artificial accepted >2-second capture.
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(capture)
        try:
            assert entered.wait(3)
            if change == 'register':
                store.register([m.Watch(second, '', ('S1',), 0)])
            elif change == 'lineage':
                store.register([m.Watch(second, '', ('S1', 'S2'), 0)])
            elif change == 'refs':
                key = m.CardKey('local-test' if domain == 'local' else 'remote-test',
                                'default', 'default', local if domain == 'local' else remote)
                store.require(second, [key])
            else:
                old = store.pin(second)[1].watch_token
                store.forget(second, old)
                store.register([m.Watch(second, '', ('S1',), 0)])
        finally:
            release.set()
        part = work.result(10)
    accept = store.accept_local if domain == 'local' else store.accept_external
    snapshot, _ = accept(part, prior)
    assert not store.pin(first)[1].missing_scopes
    assert store.pin(second)[1].missing_scopes
    assert store.pending(source) == {store.demand(source, second).id}
    # No DB write since the frozen read. The successor demand still requires I/O.
    successor_hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    current = store.demands(source)
    if domain == 'local':
        successor = r.capture_local(source, part, current, successor_hint)
    else:
        successor = r.capture_external(current, {}, {source: part}, hints={source: successor_hint})[0]
    snapshot, _ = accept(successor, successor_hint.prior_version)
    assert not store.pin(second)[1].missing_scopes
    assert not store.pending(source)
