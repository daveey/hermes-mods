"""Native/WAL regressions reproduced by the independent mitigation review."""
import sqlite3

import pytest
from test_change_source import load
from test_projector import actual_board  # noqa: F401


def test_failed_native_capture_retries_without_another_write(tmp_path):
    pytest.importorskip('hermes_cli.annotation_file_events')
    path = tmp_path / 'board.db'
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE t(v)')
    db.execute('INSERT INTO t VALUES(1)')
    db.commit()
    gate = load().Source()
    def capture():
        return db.execute('SELECT v FROM t').fetchone()[0], (str(path),)
    try:
        assert gate.read(capture) == 1
        for _ in range(3):
            gate.read(capture)
        db.execute('UPDATE t SET v=2')
        db.commit()
        def fail():
            raise sqlite3.OperationalError('injected transient read failure')
        with pytest.raises(sqlite3.OperationalError):
            gate.read(fail)
        assert [gate.read(capture) for _ in range(10)] == [2] * 10
    finally:
        if gate.events:
            gate.events.close()
        db.close()


def test_temporarily_unavailable_fleet_reattaches(actual_board, monkeypatch):
    api, config = actual_board
    cli = api.core()[2]
    real = cli._fleet
    monkeypatch.setattr(cli, '_fleet', lambda *a, **kw: None)
    keeper = sqlite3.connect(config['local'])
    keeper.execute('SELECT * FROM tasks').fetchall()
    try:
        first = api.change_cursor()
        for _ in range(5):
            api.change_cursor()
        monkeypatch.setattr(cli, '_fleet', real)
        db = sqlite3.connect(config['fleet'])
        db.execute("UPDATE tasks SET title='new fleet title'")
        db.commit()
        db.close()
        results = [api.change_cursor() for _ in range(10)]
        fresh = api._capture_cursor()[0]
        assert fresh != first[0]
        assert results[-1][0] == fresh
    finally:
        if api._change_source.events:
            api._change_source.events.close()
        keeper.close()
