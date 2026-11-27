"""Queue receipt damage must not suppress unrelated readable records."""
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
        def denied_read(path, *args, **kwargs):
            if path == marker:
                raise PermissionError('fixture receipt denied')
            return read(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', denied_read)
    with patch.object(pub, 'spawn_worker') as spawn:
        assert pub.kick(root=root), 'damaged receipt hid actual healthy pending record'
    assert spawn.call_count == 1
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        assert pub.retry_pending(ci=lambda *_: 'green', passes=1)['published'] == 1
