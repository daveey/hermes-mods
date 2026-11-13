"""Only session rows capable of changing a projection invalidate the mirror key."""

import sqlite3

from test_kanban_mirror import OWNER
from test_kanban_mirror_keyed import keyed, board, runs, settle  # noqa: F401


def test_unrelated_worker_session_churn_skips(keyed):
    k = keyed
    settle(k)
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('worker', NULL, NULL, 'kanban', 0)")
    assert not runs(k)
    with sqlite3.connect(k.state) as c:
        c.execute("UPDATE sessions SET end_reason='compression' WHERE id='worker'")
    assert not runs(k)
    with sqlite3.connect(k.state) as c:
        c.execute("DELETE FROM sessions WHERE id='worker'")
    assert not runs(k)


def test_owner_source_changes_with_unchanged_user_session_shape(keyed):
    k = keyed
    settle(k)
    # Keep COUNT and MAX(rowid) of user sessions unchanged while the creating
    # session loses user provenance. Exact relevant rows must still invalidate.
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('worker', NULL, NULL, 'kanban', 0)")
        c.execute("INSERT INTO sessions VALUES ('last-user', NULL, NULL, 'desktop', 0)")
    settle(k)
    with sqlite3.connect(k.state) as c:
        c.execute("UPDATE sessions SET source='kanban' WHERE id='session-a'")
        c.execute("UPDATE sessions SET source='desktop' WHERE id='worker'")
    assert runs(k)
    assert k.store.requests(OWNER) == []


def test_custom_source_local_subscriber_deletion_is_seen(keyed):
    k = keyed
    board(k, "UPDATE tasks SET session_id='missing-worker'")
    board(k, "INSERT INTO kanban_notify_subs VALUES ('t_test','desktop','session-a','default',0)")
    with sqlite3.connect(k.state) as c:
        c.execute("UPDATE sessions SET source='custom' WHERE id='session-a'")
    settle(k)
    assert len(k.store.requests(OWNER)) == 1
    with sqlite3.connect(k.state) as c:
        c.execute("DELETE FROM sessions WHERE id='session-a'")
    assert runs(k)
    assert k.store.requests(OWNER) == []


def test_missing_custom_subscriber_arrival_is_seen(keyed):
    k = keyed
    board(k, "UPDATE tasks SET session_id='missing-worker'")
    board(k, "INSERT INTO kanban_notify_subs VALUES ('t_test','desktop','session-a','default',0)")
    with sqlite3.connect(k.state) as c:
        c.execute("DELETE FROM sessions WHERE id='session-a'")
    settle(k)
    assert k.store.requests(OWNER) == []
    with sqlite3.connect(k.state) as c:
        c.execute("INSERT INTO sessions VALUES ('session-a',NULL,NULL,'custom',0)")
    assert runs(k)
    assert len(k.store.requests(OWNER)) == 1
