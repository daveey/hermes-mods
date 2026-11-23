import contextlib
import sqlite3
from pathlib import Path
import pytest
from test_projector import actual_board  # noqa: F401


@pytest.mark.parametrize('during_open', [False, True])
def test_replacement_same_counter_invalidates(actual_board, monkeypatch, during_open):
    from hermes_cli import kanban_db_connect as kbc, kanban_activity_revision as activity
    api, config = actual_board
    keeper = sqlite3.connect(config['local'])
    keeper.execute('SELECT * FROM tasks').fetchall()
    replacement = Path(config['fleet']).with_name('replacement.db')
    with contextlib.closing(kbc.connect(replacement)) as db:
        db.execute("UPDATE kanban_sync_state SET mode='primary'")
        db.execute("INSERT INTO tasks(id,title,status,created_at,tenant,sync_version) VALUES('t_fixture','changed replacement','running',100,'work',1)")
        db.execute("INSERT INTO task_events(id,task_id,kind,payload,created_at) VALUES(2,'t_fixture','created','{}',100)")
        db.commit()
        new_key = activity.read_key(db, activity.tables_for(config['fleet']))
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    try:
        old = api.change_cursor()
        for _ in range(5):
            api.change_cursor()
        with contextlib.closing(sqlite3.connect(config['fleet'])) as db:
            old_key = activity.read_key(db, activity.tables_for(config['fleet']))
        assert old_key == new_key, (old_key, new_key)
        if during_open:
            cli = api.core()[2]
            real = cli._fleet
            def racing_open(*args, **kwargs):
                db = real(*args, **kwargs)
                replacement.replace(config['fleet'])
                return db
            monkeypatch.setattr(cli, '_fleet', racing_open)
            # A real native notification admits capture while the actual
            # replacement races SQLite open (not the preceding identity stat).
            with contextlib.closing(sqlite3.connect(config['fleet'])) as db:
                db.execute("INSERT INTO task_comments(task_id,author,body,created_at) VALUES('t_fixture','test','wake',100)")
                db.commit()
            with pytest.raises(RuntimeError, match='replica replaced'):
                api.change_cursor()
            monkeypatch.setattr(cli, '_fleet', real)
        else:
            replacement.replace(config['fleet'])
        values = [api.change_cursor() for _ in range(10)]
        with contextlib.closing(sqlite3.connect(config['fleet'])) as db:
            assert db.execute('SELECT title FROM tasks').fetchone()[0] == 'changed replacement'
        print('old_key=', old_key, 'new_key=', new_key, 'cursor_changed=', values[-1] != old)
        assert values[-1] != old, 'Replaced replica with different task state retained identical projection cursor'
    finally:
        source = getattr(api, '_change_source', None)
        if source is not None and source.events:
            source.events.close()
        keeper.close()
