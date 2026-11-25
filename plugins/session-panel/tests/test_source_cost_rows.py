"""Physical fetch accounting includes detail queries, not only selected objects."""
import sqlite3
import threading
from test_core_readers import api, board, kanban_db
from test_snapshot_acquisition import acquisition, acquire


def test_capture_rows_include_enrichment_and_detail_fetches(acquisition, monkeypatch):
    r, cap, m, source, store, conn = acquisition
    kanban_db.create_task(conn, title='Measured detail', session_id='S')
    store.register([m.Watch(m.ChatKey('fixture', 'default', 'S'), '', ('S',), 0)])
    first, _ = acquire(acquisition)
    original = r._detail
    seen = []
    returned = []
    connect = sqlite3.connect
    owner_thread = threading.get_ident()

    class SpyConnection(sqlite3.Connection):
        def __setattr__(self, name, value):
            if name == 'row_factory' and value is not None:
                factory = value
                def spy(cursor, values):
                    returned.append(values)
                    return factory(cursor, values)
                value = spy
            super().__setattr__(name, value)

    def owned_connection(*args, **kwargs):
        if threading.get_ident() != owner_thread:
            return connect(*args, **kwargs)
        return connect(*args, **kwargs, factory=SpyConnection)

    monkeypatch.setattr(sqlite3, 'connect', owned_connection)

    def enriched(connection, *args, **kwargs):
        rows = connection.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<7) SELECT x FROM n').fetchall()
        assert len(rows) == 7
        seen.extend(rows)
        return original(connection, *args, **kwargs)

    monkeypatch.setattr(r, '_detail', enriched)
    second, _ = acquire(acquisition)
    assert len(seen) == 7
    assert second.metrics['rows_returned'] == len(returned)
    assert second.metrics['selected_logical_rows'] == first.metrics['selected_logical_rows']
    assert first.metrics['rows_returned'] > first.metrics['selected_logical_rows']
    assert second.metrics['vm_step_quantum'] == 100
    assert second.rows == first.rows and second.details == first.details
