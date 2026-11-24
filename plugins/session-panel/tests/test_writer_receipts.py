"""Serialized writer clocks include queueing, full work and callback completion."""
import threading
import pytest

from test_feed import feedmod, make
from test_source_receipts import receipts, identity


@pytest.fixture
def log(tmp_path):
    value = receipts().ReceiptLog(tmp_path / 'writers.jsonl', identity=identity())
    value.events = []
    original = value.record
    def record(event, **data):
        value.events.append(dict(event=event, **data))
        return original(event, **data)
    value.record = record
    yield value
    assert value.close(), (value.dropped_records, value.write_errors, value.encoding_errors)


def test_writer_receipts_cover_actual_publish_and_correlate_stored_models(log):
    f, r, pushed, now = make()
    f.install_receipts(log)
    f.changes(['S1'])
    releases = [e for e in log.events if e['event'] == 'writer_release']
    assert releases
    full = next(e for e in releases if e['producer_kind'] == 'full')
    assert full['requested_ns'] <= full['acquired_ns'] <= full['released_ns']
    stored = [e for e in log.events if e['event'] == 'model_stored']
    assert any(e['publication']['writer']['writer_id'] == full['writer_id'] for e in stored)
    assert all(e['thread_cpu_ns'] >= 0 for e in releases)
    for kind, kwargs in [('decisions', {'canonical_only': True}), ('age', {'age_only': True}), ('heartbeat', {'heartbeat': True})]:
        f._publish({}, **kwargs)
        assert any(e['event'] == 'writer_release' and e['producer_kind'] == kind for e in log.events)


def test_writer_request_and_snapshot_timeout_are_observed_while_callback_holds_fence(log):
    f, r, pushed, now = make()
    entered, release, requested = threading.Event(), threading.Event(), threading.Event()

    original = log.record
    def record(event, **data):
        result = original(event, **data)
        if event == 'writer_request' and data['producer_kind'] == 'snapshot':
            requested.set()
        return result
    log.record = record
    f.install_receipts(log)
    def held(payload):
        entered.set(); assert release.wait(5)
    f.emit = held
    publisher = threading.Thread(target=lambda: f.changes(['S1']))
    publisher.start()
    try:
        assert entered.wait(2)
        assert f.snapshot(timeout=0) is None
        assert requested.is_set()
        timeout = next(e for e in log.events if e['event'] == 'writer_release' and e['producer_kind'] == 'snapshot')
        assert timeout['acquired_ns'] is None and timeout['acquired'] is False
        assert not [e for e in log.events if e['event'] == 'writer_release' and e['producer_kind'] == 'full']
        release.set(); publisher.join(3)
        assert not publisher.is_alive()
        full = next(e for e in log.events if e['event'] == 'writer_release' and e['producer_kind'] == 'full')
        assert full['released_ns'] > timeout['released_ns']
        assert full['thread_cpu_ns'] >= 0
    finally:
        release.set(); publisher.join(3); f.stopped.set()


def test_writer_receipts_do_not_change_uninstalled_timeout_or_wire_contract():
    f, r, pushed, now = make()
    f.changes(['S1'])
    before = f.changes_bytes(['S1'])
    assert f.snapshot(timeout=0)
    assert f.changes_bytes(['S1']) == before
    assert f.receipt_log is None
