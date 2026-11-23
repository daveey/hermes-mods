"""Canonical remote union acquisition differential over real replica SQL."""
import pytest
from test_reader_batch import api, board, graph


def test_external_rows_match_canonical_per_chat_and_materialize_detail_once(graph, monkeypatch):
    r, local, remote, ksa = graph
    cap = r.snapshot_modules()
    m = cap.snapshot
    store = m.SnapshotStore()
    source = m.SourceKey('local-test', 'default', 'default', 'hub-links', '/example/external')
    store.add_source(source)
    chats = [m.ChatKey('local-test', 'default', f'S{i}') for i in range(8)]
    store.register([m.Watch(chat, '', (chat.session_id,), 0) for chat in chats])
    monkeypatch.setattr(ksa.time, 'time', lambda: 1800000000.0)
    baseline = {chat: [row for row in r.linked([chat.session_id]) if row['id'] == remote] for chat in chats}
    counts = []
    original = r._detail
    def counted(conn, tid, *args, **kwargs):
        counts.append(tid)
        return original(conn, tid, *args, **kwargs)
    monkeypatch.setattr(r, '_detail', counted)
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    part = r.capture_external(store.demands(source), {}, {}, hints={source: hint})[0]
    snapshot, _ = store.accept_external(part, hint.prior_version)
    assert counts.count(remote) == 1
    assert len(part.receipt.coverage.completed_scopes) == 8
    for chat in chats:
        actual = m.SnapshotReaders(snapshot, chat).linked([chat.session_id])
        assert actual == baseline[chat]


@pytest.mark.parametrize('unfinished', [False, True])
def test_pending_remote_release_selection_matches_canonical(graph, monkeypatch, unfinished):
    from hermes_cli import kanban_db_sync as sync
    r, local, remote, ksa = graph
    row = next(row for row in r.linked(['S0']) if row['id'] == remote)
    row.update(status='done_pending_release', unfinished=unfinished)
    monkeypatch.setattr(sync, 'sync_config', lambda: {})
    monkeypatch.setattr(ksa, '_remote_links', lambda **kwargs: [row])
    monkeypatch.setattr(ksa, 'remote_links_and_origin_rows', lambda **kwargs: ([row], []))
    monkeypatch.setattr(ksa.time, 'time', lambda: 1800000000.0)
    cap = r.snapshot_modules()
    m, store = cap.snapshot, cap.snapshot.SnapshotStore()
    source = m.SourceKey('local-test', 'default', 'default', 'hub-links', '/example/external')
    chat = m.ChatKey('local-test', 'default', 'S0')
    store.add_source(source)
    store.register([m.Watch(chat, '', ('S0',), 0)])
    old = [row for row in r.linked(['S0']) if row['id'] == remote]
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    part = r.capture_external(store.demands(source), {}, {}, hints={source: hint})[0]
    snapshot, _ = store.accept_external(part, hint.prior_version)
    assert m.SnapshotReaders(snapshot, chat).linked(['S0']) == old


def test_external_capture_cannot_certify_an_overbudget_empty_success(graph, monkeypatch):
    r, _, _, _ = graph
    cap = r.snapshot_modules()
    m, store = cap.snapshot, cap.snapshot.SnapshotStore()
    source = m.SourceKey('local-test', 'default', 'default', 'hub-links', '/example/external')
    chat = m.ChatKey('local-test', 'default', 'absent')
    store.add_source(source)
    store.register([m.Watch(chat, '', ('absent',), 0)])
    original = r.external_capture
    real_clock = cap.time.monotonic
    elapsed = [0.0]
    def held(*args, **kwargs):
        result = original(*args, **kwargs)
        elapsed[0] = 3.0
        return result
    monkeypatch.setattr(r, 'external_capture', held)
    monkeypatch.setattr(cap.time, 'monotonic', lambda: real_clock() + elapsed[0])
    hint = cap.CaptureHints(store.version(source), store.owner_token(source), 1800000000.0)
    with pytest.raises(TimeoutError):
        r.capture_external(store.demands(source), {}, {}, hints={source: hint})
    assert store.pin(chat)[1].missing_scopes
