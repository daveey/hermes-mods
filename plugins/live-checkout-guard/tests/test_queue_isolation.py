"""A damaged queue entry must never starve healthy records or respawn forever."""
from pathlib import Path
from unittest.mock import patch

import pytest
from test_source_review import repository, pub


def broken_entry(monkeypatch, directory, kind):
    directory.mkdir(parents=True, exist_ok=True)
    bad = directory / '0000-bad.json'
    if kind == 'directory':
        bad.mkdir()
    else:
        bad.write_text('preserve unreadable record')
        read = Path.read_bytes
        def denied_read(path):
            if path == bad:
                raise PermissionError('fixture unreadable')
            return read(path)
        monkeypatch.setattr(Path, 'read_bytes', denied_read)
    rename = Path.rename
    def denied_rename(path, target):
        if path == bad:
            raise PermissionError('fixture quarantine denied')
        return rename(path, target)
    monkeypatch.setattr(Path, 'rename', denied_rename)
    return bad


@pytest.mark.parametrize('kind', ['directory', 'permission'])
@pytest.mark.parametrize('action', ['publish', 'expire'])
def test_unquarantinable_first_entry_does_not_starve_sibling(repository, monkeypatch, caplog, kind, action):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    bad = broken_entry(monkeypatch, directory, kind)
    pub.defer(root, [name], spawn=False, clock=lambda: 0 if action == 'expire' else pub.time.time())
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        counts = pub.retry_pending(ci=lambda *_: 'green', passes=1)
    assert counts['published' if action == 'publish' else 'expired'] == 1
    assert bad.exists()
    assert list(directory.glob('*.json')) == [bad]
    if action == 'publish':
        assert git('rev-parse', 'main', cwd=origin) != head
        assert git('status', '--porcelain') == ''
    else:
        assert name in caplog.text and 'expired' in caplog.text
        assert git('rev-parse', 'main', cwd=origin) == head


@pytest.mark.parametrize('kind', ['directory', 'permission'])
def test_only_unquarantinable_entry_exits_deduplicates_and_does_not_respawn(repository, monkeypatch, caplog, kind):
    root, origin, name, target, marker, head, git = repository
    target.write_text('base\n')
    directory = pub.pending_dir()
    bad = broken_entry(monkeypatch, directory, kind)
    reads = []
    read = Path.read_bytes
    def bounded_read(path):
        if path == bad:
            reads.append(path)
            assert len(reads) < 5, 'empty recheck spins on blocked entry'
        return read(path)
    monkeypatch.setattr(Path, 'read_bytes', bounded_read)
    flock = pub.fcntl.flock
    locks = []
    def bounded_flock(*args):
        locks.append(args)
        assert len(locks) < 20, 'empty recheck repeatedly relocks blocked-only queue'
        return flock(*args)
    monkeypatch.setattr(pub.fcntl, 'flock', bounded_flock)
    for _ in range(3):
        counts = pub.retry_pending(sleep=lambda _: pytest.fail('only blocked records must exit without sleeping'))
    monkeypatch.setattr(pub.fcntl, 'flock', flock)
    with patch.object(pub, 'spawn_worker') as spawn:
        for _ in range(20):
            assert not pub.kick(root=root)
    assert not spawn.called
    notices = [r.message for r in caplog.records if 'skill publication dirt' in r.message]
    assert len(notices) == 1
    assert 'quarantine' in notices[0] and bad.name in notices[0]
    assert bad.exists()


def test_nonregular_only_queue_does_not_spawn(repository, monkeypatch):
    root, origin, name, target, marker, head, git = repository
    target.write_text('base\n')
    directory = pub.pending_dir()
    directory.mkdir(parents=True)
    (directory / 'directory.json').mkdir()
    (directory / 'symlink.json').symlink_to(target)
    with patch.object(pub, 'spawn_worker') as spawn:
        assert not pub.kick(root=root)
    assert not spawn.called


def test_replaced_blocked_entry_becomes_eligible_and_healthy_new_record_spawns(repository, monkeypatch):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    bad = broken_entry(monkeypatch, directory, 'directory')
    pub.retry_pending(passes=1)
    # New healthy records are never hidden by another entry's persistent skip.
    pub.defer(root, [name], spawn=False)
    with patch.object(pub, 'spawn_worker') as spawn:
        assert pub.kick(root=root)
    assert spawn.call_count == 1
    # Replacing the failed entry invalidates its generation-bound marker.
    bad.rmdir()
    bad.write_text('{}')
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        counts = pub.retry_pending(ci=lambda *_: 'green', passes=1)
    assert counts['published'] == 1 and not bad.exists()


@pytest.mark.parametrize('failure', ['marker-write', 'all-receipts-write', 'notice-write', 'metadata'])
def test_skip_fallback_preserves_healthy_sibling_and_exits(repository, monkeypatch, failure):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    bad = broken_entry(monkeypatch, directory, 'permission')
    if failure in {'marker-write', 'all-receipts-write', 'notice-write'}:
        write = Path.write_text
        def denied_write(path, *args, **kwargs):
            if (path.name.startswith('.blocked-')
                    or (failure == 'all-receipts-write' and path.name.startswith('.queue-held-'))
                    or (failure == 'notice-write' and path.name.startswith('.notified-'))):
                raise PermissionError('fixture marker write denied')
            return write(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'write_text', denied_write)
    else:
        lstat = Path.lstat
        def denied_stat(path, *args, **kwargs):
            if path == bad:
                raise PermissionError('fixture metadata denied')
            return lstat(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'lstat', denied_stat)
    pub.defer(root, [name], spawn=False)
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        counts = pub.retry_pending(ci=lambda *_: 'green',
                                   sleep=lambda _: pytest.fail('only skipped entries remain'))
    assert counts['published'] == 1 and counts['dropped'] == 1
    if failure == 'marker-write':
        with patch.object(pub, 'spawn_worker') as spawn:
            for _ in range(10):
                assert not pub.kick(root=root)
        assert not spawn.called


def test_worker_never_waits_for_discovery_notice_lock(repository, monkeypatch):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    broken_entry(monkeypatch, directory, 'directory')
    pub.defer(root, [name], spawn=False)
    flock = pub.fcntl.flock
    def no_blocking_lock(fd, operation):
        assert operation != pub.fcntl.LOCK_EX, 'worker blocks on discovery notice lock'
        return flock(fd, operation)
    monkeypatch.setattr(pub.fcntl, 'flock', no_blocking_lock)
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        assert pub.retry_pending(ci=lambda *_: 'green', passes=1)['published'] == 1


def test_replacement_during_unlock_is_not_lost_to_in_memory_skip(repository, monkeypatch):
    root, origin, name, target, marker, head, git = repository
    directory = pub.pending_dir()
    bad = broken_entry(monkeypatch, directory, 'directory')
    queued = pub.defer(root, [name], spawn=False)['record']
    raw = Path(queued).read_bytes()
    Path(queued).unlink()
    flock = pub.fcntl.flock
    replaced = []
    def replace_at_unlock(fd, operation):
        result = flock(fd, operation)
        if operation == pub.fcntl.LOCK_UN and not replaced:
            replaced.append(True)
            bad.rmdir()
            bad.write_bytes(raw)
        return result
    monkeypatch.setattr(pub.fcntl, 'flock', replace_at_unlock)
    with patch.object(pub, 'publication_overlap', return_value={'status': 'clear'}):
        counts = pub.retry_pending(ci=lambda *_: 'green', sleep=lambda _: pytest.fail('unexpected sleep'))
    assert replaced and counts['published'] == 1 and not bad.exists()
