"""Acceptance authority reads remain read-only when WAL needs sidecars."""
import sqlite3
from unittest import mock

import pytest

from test_acceptance_authority import core


def test_ro_fallback_reads_but_cannot_write(tmp_path):
    module = core._acceptance_mod()
    path = tmp_path / "authority.db"
    connect = sqlite3.connect
    with connect(path) as conn:
        conn.execute("CREATE TABLE evidence (value)")
        conn.execute("INSERT INTO evidence VALUES (42)")
    with mock.patch.object(module.sqlite3, "connect", side_effect=[
        sqlite3.OperationalError("unable to open database file"), connect(f"{path.as_uri()}?mode=rw", uri=True)
    ]) as opened:
        conn = module._ro(path)
        try:
            assert conn.execute("SELECT value FROM evidence").fetchone() == (42,)
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("DELETE FROM evidence")
            assert opened.call_args.args[0] == f"{path.as_uri()}?mode=rw"
        finally:
            conn.close()


def test_ro_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        core._acceptance_mod()._ro(path)
    assert not path.exists()
