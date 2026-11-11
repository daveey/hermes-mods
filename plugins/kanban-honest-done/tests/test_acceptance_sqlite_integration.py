"""Integration of the URI-safe reader with Gate F's first-read WAL probe."""
import sqlite3
from unittest import mock

import pytest

from test_acceptance_provenance_real_shapes import acc, make_db, T_BODY


@pytest.mark.parametrize("name", ["state ?#%.db", "state ü.db"])
@pytest.mark.parametrize("fallback", [False, True])
def test_escaped_path_reads_citation_and_never_writes(tmp_path, name, fallback):
    path = make_db(tmp_path / name)
    connect = sqlite3.connect
    calls = []

    def open_db(database, **kwargs):
        calls.append((database, kwargs))
        if fallback and database.endswith("?mode=ro"):
            raise sqlite3.OperationalError("sidecars unavailable")
        return connect(database, **kwargs)

    with mock.patch.object(acc.sqlite3, "connect", side_effect=open_db):
        assert acc.verify_citation(1001, "S_CHAT", path, T_BODY) == (True, "")
        conn = acc._ro_connect(path, timeout=1)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("DELETE FROM messages")
            assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] > 0
        finally:
            conn.close()
    modes = ["ro", "rw"] if fallback else ["ro"]
    assert [uri for uri, _ in calls] == [f"{path.as_uri()}?mode={mode}" for mode in modes] * 2
    assert all(kwargs["uri"] is True for _, kwargs in calls)
    assert calls[-1][1]["timeout"] == 1


def test_probe_failure_closes_ro_before_query_only_fallback(tmp_path):
    path = make_db(tmp_path / "probe ?#.db")
    failed = mock.Mock()
    failed.execute.side_effect = sqlite3.OperationalError("first read needs WAL sidecars")
    connect = sqlite3.connect
    returned = []

    def open_db(database, **kwargs):
        if database.endswith("?mode=ro"):
            return failed
        failed.close.assert_called_once_with()
        conn = connect(database, **kwargs)
        returned.append(conn)
        return conn

    with mock.patch.object(acc.sqlite3, "connect", side_effect=open_db):
        conn = acc._ro_connect(path)
        try:
            assert conn is returned[0]
            assert conn.execute("PRAGMA query_only").fetchone() == (1,)
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE forbidden (x)")
        finally:
            conn.close()
    failed.execute.assert_called_once_with("select 1 from sqlite_master limit 1")


def test_query_only_failure_closes_fallback_and_propagates(tmp_path):
    fallback = mock.Mock()
    fallback.execute.side_effect = sqlite3.OperationalError("cannot enable query_only")
    with mock.patch.object(acc.sqlite3, "connect", side_effect=[
        sqlite3.OperationalError("ro unavailable"), fallback
    ]):
        with pytest.raises(sqlite3.OperationalError, match="cannot enable query_only"):
            acc._ro_connect(tmp_path / "missing ?#.db")
    fallback.close.assert_called_once_with()


@pytest.mark.parametrize("reader", ["_ro", "_ro_connect"])
def test_escaped_missing_path_never_creates_database(tmp_path, reader):
    path = tmp_path / "missing ?#%.db"
    with pytest.raises(sqlite3.OperationalError):
        getattr(acc, reader)(path)
    assert list(tmp_path.iterdir()) == []


def test_compatibility_name_is_the_same_shared_reader():
    assert acc._ro is acc._ro_connect
