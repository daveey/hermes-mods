"""The waiter executes only a reviewed snapshot at the fixed installed path."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("attested_waiter_test", Path(__file__).resolve().parents[1] / "waiter.py")
waiter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(waiter)


@pytest.fixture
def updater(tmp_path, monkeypatch):
    path = tmp_path / "hermes-sync"
    path.write_bytes(b"# fixture updater\n")
    path.chmod(0o600)
    monkeypatch.setattr(waiter, "updater_path", lambda: path)
    monkeypatch.setattr(waiter, "UPDATER_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_attestation_accepts_reviewed_bytes(updater):
    assert waiter.attested_source() == updater.read_bytes()


def test_rejects_unreviewed_bytes(updater):
    updater.write_bytes(b"raise RuntimeError('must not execute')\n")
    with pytest.raises(ValueError, match="reviewed revision"):
        waiter.main()


@pytest.mark.parametrize("mode", [0o620, 0o602])
def test_rejects_writable_updater(updater, mode):
    updater.chmod(mode)
    with pytest.raises(ValueError, match="permissions"):
        waiter.attested_source()


def test_rejects_foreign_owner(updater, monkeypatch):
    real = os.fstat
    def foreign(fd):
        values = list(real(fd))
        values[4] = os.getuid() + 1
        return os.stat_result(values)
    monkeypatch.setattr(os, "fstat", foreign)
    with pytest.raises(ValueError, match="ownership"):
        waiter.attested_source()


def test_rejects_symlink(updater):
    target = updater.with_name("other")
    updater.rename(target)
    updater.symlink_to(target)
    with pytest.raises(OSError):
        waiter.attested_source()


def test_rejects_missing_updater(updater):
    updater.unlink()
    with pytest.raises(OSError):
        waiter.attested_source()


def test_rejects_nonregular_updater(updater):
    updater.unlink()
    os.mkfifo(updater)
    with pytest.raises(ValueError, match="permissions"):
        waiter.attested_source()


def test_executes_verified_snapshot_not_reopened_path(updater, monkeypatch):
    output = updater.with_name("receipt")
    source = ("import sys\nfrom pathlib import Path\n"
              f"Path({str(output)!r}).write_text(repr((sys.argv, __file__)))\n").encode()
    def snapshot():
        updater.write_text("raise RuntimeError('replacement must not run')\n")
        return source
    monkeypatch.setattr(waiter, "attested_source", snapshot)
    monkeypatch.setattr(sys, "argv", ["ignored", "untrusted"])
    waiter.main()
    assert output.read_text() == repr(([str(updater), "core-waiter"], str(updater)))
