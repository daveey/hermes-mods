"""Queue receipt damage must not suppress unrelated readable records."""
import os
import signal
from pathlib import Path
from unittest.mock import patch

import pytest
from test_source_review import repository, pub


@pytest.mark.parametrize('damage', ['invalid-utf8', 'unreadable'])
def test_bad_skip_receipt_does_not_hide_healthy_record(repository, monkeypatch, damage):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    record = Path(pub.defer(root, [name], spawn=False)['record'])
    key = pub._record_key(str(directory), [record.name])
    marker = directory / ('.blocked-' + key)
    marker.write_bytes(b'\xff')
    if damage == 'unreadable':
        read = Path.read_text
        opened = pub.os.open
        def denied_read(path, *args, **kwargs):
            if path == marker:
                raise PermissionError('fixture receipt denied')
            return read(path, *args, **kwargs)
        def denied_open(path, *args, **kwargs):
            if Path(path) == marker:
                raise PermissionError('fixture receipt denied')
            return opened(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', denied_read)
        monkeypatch.setattr(pub.os, 'open', denied_open)
    with patch.object(pub, 'spawn_worker') as spawn:
        assert pub.kick(root=root), 'damaged receipt hid actual healthy pending record'
    assert spawn.call_count == 1
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        assert pub.retry_pending(ci=lambda *_: 'green', passes=1)['published'] == 1


@pytest.mark.parametrize('prefix', ['.blocked-', '.queue-held-'])
@pytest.mark.parametrize('damage', ['fifo', 'symlink', 'oversized'])
def test_receipt_reads_are_bounded_regular_and_leaf_owned(tmp_path, monkeypatch, prefix, damage):
    record = tmp_path / 'healthy.json'
    record.write_text('{}')
    generation = pub._queue_generation(record)
    receipt = tmp_path / (prefix + pub._record_key(str(tmp_path), [record.name]))
    if damage == 'fifo':
        os.mkfifo(receipt)
    elif damage == 'symlink':
        target = tmp_path / 'foreign-receipt'
        target.write_text(generation)
        receipt.symlink_to(target)
    else:
        receipt.write_bytes(b'x' * 65536)
        def forbidden_read(*_):
            raise AssertionError('oversized receipt must be rejected before reading')
        monkeypatch.setattr(pub.os, 'read', forbidden_read)
    def timed_out(*_):
        raise AssertionError('advisory receipt blocked enumeration')
    old = signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, 1)
    try:
        assert pub._queue_records(tmp_path) == [record]
        assert pub._queue_records(tmp_path, regular_only=True) == [record]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


@pytest.mark.parametrize('prefix', ['.blocked-', '.queue-held-'])
def test_matching_regular_receipt_still_suppresses_generation(tmp_path, prefix):
    record = tmp_path / 'healthy.json'
    record.write_text('{}')
    receipt = tmp_path / (prefix + pub._record_key(str(tmp_path), [record.name]))
    receipt.write_text(pub._queue_generation(record))
    assert pub._queue_records(tmp_path) == []
    assert pub._queue_records(tmp_path, regular_only=True) == []
