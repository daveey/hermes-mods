"""A write must not depend on CPython freeing an unread cursor at once.

hermes-hub once logged ``cannot commit transaction - SQL statements in progress`` from ``observe_counter``: each
``_conn()`` ran ``PRAGMA journal_mode=WAL`` and dropped its unread result, so the PRAGMA statement stayed active
until the cursor was garbage collected. If anything held that cursor a moment longer, the COMMIT failed.
This test keeps a strong reference to every cursor ``execute`` returns, which is that late free, made certain.
"""
import sqlite3

from throughput_under_test.store import Store

_real_connect = sqlite3.connect


class _HoldingConn(sqlite3.Connection):
    held: list = []

    def execute(self, *a, **kw):
        cur = super().execute(*a, **kw)
        if a and str(a[0]).lstrip().upper().startswith("PRAGMA"):
            # The cursor nobody reads stays alive while its connection is in use, as a late free would.
            _HoldingConn.held = [cur]
        return cur

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)  # the COMMIT runs while the PRAGMA cursor is still alive
        finally:
            _HoldingConn.held = []  # freed by the next call (the hub's earlier connections had been freed)


def _holding_connect(*a, **kw):
    kw.setdefault("factory", _HoldingConn)
    return _real_connect(*a, **kw)


def test_writes_survive_late_freed_cursors(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", _holding_connect)
    _HoldingConn.held = []
    st = Store(tmp_path / "s.sqlite")
    st.observe_counter("gw", "m", 100, 1.0, at=1.0)
    st.observe_counter("gw", "m", 101, 3.0, at=2.0)
    st.put("gw", "x", [(100, 1.0)])
    _HoldingConn.held = []
    with _real_connect(str(tmp_path / "s.sqlite")) as c:
        assert c.execute("SELECT minute, value FROM counters").fetchall() == [(101, 3.0)]
        assert c.execute("SELECT value FROM minutes WHERE metric='x'").fetchall() == [(1.0,)]
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connections_are_closed_after_use(tmp_path, monkeypatch):
    opened = []

    def connect(*a, **kw):
        c = _real_connect(*a, **kw)
        opened.append(c)
        return c

    monkeypatch.setattr(sqlite3, "connect", connect)
    st = Store(tmp_path / "s.sqlite")
    st.put("gw", "x", [(100, 1.0)])
    st.observe_counter("gw", "m", 100, 1.0, at=1.0)
    for c in opened:
        try:
            c.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            continue
        raise AssertionError("a Store connection was left open")
