"""Fixture schemas are published atomically, not one fsync transaction per DDL statement."""
import sqlite3
from types import SimpleNamespace

from sqlite_fixture import transaction
import test_root_cap_guard as fixture


def test_world_schema_is_built_in_one_transaction_per_database(tmp_path, monkeypatch):
    ddl = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.lower().startswith('create table'):
                ddl.append(self.in_transaction)
            return super().execute(sql, *args, **kwargs)

    def connect(path):
        return sqlite3.connect(path, factory=Connection)

    # Patch this fixture's binding, not process-wide SQLite used by core/background threads.
    monkeypatch.setattr(fixture, 'sqlite3', SimpleNamespace(connect=connect))
    world = fixture.World(tmp_path, monkeypatch)
    try:
        world.register()
        assert ddl and all(ddl), 'fixture DDL autocommits a separate durable transaction for each table'
        with transaction(world.kdb) as board:
            assert board.execute('select count(*) from tasks').fetchone()[0] == 3
        with transaction(world.root / 'state.db') as state:
            assert state.execute('select count(*) from sessions').fetchone()[0] == 2
            assert state.execute('select count(*) from messages').fetchone()[0] == 1
    finally:
        world.sdb.close()
