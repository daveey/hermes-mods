"""Linux watch registration races and aliases."""
import sys
import errno
import ctypes
import struct
from pathlib import Path

import pytest
from test_routes import load

API = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux'), reason='inotify')


def test_overflow_requires_topology_relist(tmp_path, monkeypatch):
    import select
    api = load('_overflow_watch', API)
    w = api.FileWatch()
    try:
        w.set_paths([tmp_path / 'hub.db'])
        monkeypatch.setattr(select, 'select', lambda *a: ([w._fd], [], []))
        monkeypatch.setattr(api.os, 'read', lambda *a: struct.pack('iIII', -1, w._Q_OVERFLOW, 0, 0))
        assert w.wait(.1)
        assert w.stale, 'overflow may hide directory replacement, not just DB writes'
    finally:
        w.close()


def test_unreadable_ancestor_refuses_incomplete_coverage(tmp_path):
    api = load('_permission_watch', API)
    w = api.FileWatch()
    real = w._libc
    class Lib:
        def inotify_add_watch(self, fd, path, mask):
            if path == bytes(tmp_path):
                ctypes.set_errno(errno.EACCES)
                return -1
            return real.inotify_add_watch(fd, path, mask)
        def inotify_rm_watch(self, fd, wd):
            return real.inotify_rm_watch(fd, wd)
    w._libc = Lib()
    try:
        with pytest.raises(OSError) as error:
            w.set_paths([tmp_path / 'hub.db'])
        assert error.value.errno == errno.EACCES
    finally:
        w.close()


def test_ancestor_alias_preserves_leaf_names_and_removal(tmp_path):
    api = load('_alias_watch', API)
    alias = tmp_path / 'link'
    alias.symlink_to(tmp_path, target_is_directory=True)
    db = tmp_path / 'hub.db'
    db.write_bytes(b'old')
    w = api.FileWatch()
    try:
        w.set_paths([alias / 'hub.db'])
        db.write_bytes(b'first')
        assert w.wait(.2), 'alias ancestor must not overwrite DB filename filter'
        w.set_paths([alias / 'hub.db', db])
        w.set_paths([db])
        while w.wait(.01):
            if w.stale:
                w.set_paths([db])
        db.write_bytes(b'second')
        assert w.wait(.2), 'removing an alias must not remove shared inode coverage'
    finally:
        w.close()


def test_registration_arms_ancestors_before_missing_leaf(tmp_path):
    api = load('_registration_watch', API)
    db = tmp_path / 'late' / 'nested' / 'hub.db'
    w = api.FileWatch()
    real = w._libc
    class Lib:
        def inotify_add_watch(self, fd, path, mask):
            if path == bytes(tmp_path) and not db.exists():
                db.parent.mkdir(parents=True)
                db.write_bytes(b'appeared during registration')
            return real.inotify_add_watch(fd, path, mask)
        def inotify_rm_watch(self, fd, wd):
            return real.inotify_rm_watch(fd, wd)
    w._libc = Lib()
    try:
        w.set_paths([db])
        db.write_bytes(b'newer')
        assert w.wait(.2), 'registration race must not leave the leaf uncovered'
    finally:
        w.close()
