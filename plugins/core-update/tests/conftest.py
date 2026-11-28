"""Filesystem-only adapter for temp-root ancestors, never product permissions.

pytest temp roots may be below sticky /tmp or a dispatcher 0775 workspace.
Only ancestors of the private fixture root get safe fstat mode observations;
all fixture files/directories (including malicious cases) retain real checks.
"""
import os
import pytest


@pytest.fixture(autouse=True)
def private_fixture_ancestors(tmp_path, monkeypatch):
    ancestors = {(p.stat().st_dev, p.stat().st_ino) for p in tmp_path.parents}
    real = os.fstat
    def observed(fd):
        info = real(fd)
        if (info.st_dev, info.st_ino) in ancestors:
            values = list(info)
            values[0] = int(values[0]) & ~0o022
            return os.stat_result(values)
        return info
    monkeypatch.setattr(os, 'fstat', observed)
