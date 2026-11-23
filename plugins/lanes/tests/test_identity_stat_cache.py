"""Identity checks must not reread every core source on every projection."""
import hashlib
import importlib.util
import os
from pathlib import Path


def module():
    path = Path(__file__).parents[1] / 'dashboard/projector_identity.py'
    spec = importlib.util.spec_from_file_location('identity_stat_test', path)
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_stable_identity_reads_bytes_once_and_rejects_same_size_edit(tmp_path, monkeypatch):
    obj = module()
    p = tmp_path / 'core.py'
    p.write_bytes(b'old')
    manifest = {str(p): hashlib.sha256(b'old').hexdigest()}
    reads = []
    original = Path.read_bytes
    def read(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    assert obj.matches(manifest)
    assert obj.matches(manifest)
    assert reads == [p]
    stamp = p.stat()
    p.write_bytes(b'new')
    os.utime(p, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert not obj.matches(manifest)  # ctime fences restored mtime
    assert len(reads) == 2
    p.unlink()
    assert not obj.matches(manifest)


def test_cache_disabled_and_manifest_change_are_not_blessed(tmp_path, monkeypatch):
    obj = module()
    p = tmp_path / 'core.py'
    p.write_bytes(b'old')
    manifest = {str(p): hashlib.sha256(b'old').hexdigest()}
    assert obj.matches(manifest)
    assert not obj.matches({str(p): 'wrong'})
    monkeypatch.setenv('HERMES_LANES_IDENTITY_STAT_CACHE', '0')
    monkeypatch.setattr(Path, 'read_bytes', lambda _: b'new')
    assert not obj.matches(manifest)
