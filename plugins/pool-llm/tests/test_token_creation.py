"""Directory custody and concurrent first-use controls for the persistent token."""
import concurrent.futures
import os
import stat
import threading

import pytest
from _load import load

pl = load('pool_llm')


def test_existing_directory_is_private(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    directory = tmp_path / 'pool-llm'
    directory.mkdir()
    directory.chmod(0o755)
    pl.serve_token()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_foreign_directory_refused_before_token_creation(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    directory = tmp_path / 'pool-llm'
    directory.mkdir(mode=0o700)
    original = os.fstat
    def foreign(fd):
        info = original(fd)
        if stat.S_ISDIR(info.st_mode):
            values = list(info)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        return info
    monkeypatch.setattr(os, 'fstat', foreign)
    with pytest.raises(ValueError, match='directory must be owned'):
        pl.serve_token()
    assert not (directory / 'serve.token').exists()


def test_concurrent_first_read_waits_for_complete_token(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    writing, release, reading = threading.Event(), threading.Event(), threading.Event()
    original = pl.secrets.token_urlsafe
    def paused(size):
        writing.set()
        assert release.wait(5)
        return original(size)
    monkeypatch.setattr(pl.secrets, 'token_urlsafe', paused)
    def read():
        reading.set()
        return pl.serve_token()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        creator = pool.submit(pl.serve_token)
        assert writing.wait(5)
        reader = pool.submit(read)
        try:
            assert reading.wait(5)
            # A reader must not observe the empty O_EXCL-created inode.
            with pytest.raises(concurrent.futures.TimeoutError):
                reader.result(timeout=0.1)
        finally:
            release.set()
        assert creator.result(timeout=5) == reader.result(timeout=5)
