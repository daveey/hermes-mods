"""Trace both plugin-owned and core-borrowed observers without changing ownership."""
from contextlib import contextmanager


def trace_readers(monkeypatch, core):
    statements, opens = [], []
    original = core.ro_connect
    shared = core._shared_reader()
    seen = set()

    def record(path, conn):
        if conn not in seen:
            seen.add(conn)
            opens.append(path)
        conn.set_trace_callback(statements.append)

    def connect(path, *args, **kwargs):
        conn = original(path, *args, **kwargs)
        record(path, conn)
        return conn

    monkeypatch.setattr(core, 'ro_connect', connect)
    if shared is not None:
        @contextmanager
        def borrow(path):
            with shared(path) as conn:
                record(path, conn)
                try:
                    yield conn
                finally:
                    conn.set_trace_callback(None)
        monkeypatch.setattr(core, '_shared_reader', lambda: borrow)
    return statements, opens
