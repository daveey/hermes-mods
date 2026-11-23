"""WAL/heartbeat writes do not make a new lanes projection revision."""
import contextlib
from pathlib import Path

from test_projector import actual_board  # noqa: F401 -- fixture registration


def test_heartbeat_is_not_a_projection_change_but_task_mutation_is(actual_board):
    from hermes_cli import kanban_db_connect as kbc
    api, config = actual_board
    before = api.change_cursor()
    with contextlib.closing(kbc.connect(Path(config['local']))) as db:
        db.execute("INSERT INTO task_events(task_id,kind,payload,created_at) VALUES('t_fixture','heartbeat','{}',200)")
        db.commit()
    assert api.change_cursor() == before
    with contextlib.closing(kbc.connect(Path(config['local']))) as db:
        db.execute("UPDATE tasks SET status='archived'")
        db.commit()
    assert api.change_cursor() != before
