"""render_cards shows the live status of a forwarded card's placed copy (fake replica, no hub call)."""
import json
import sqlite3


def _db(tmp_path):
    db = tmp_path / "k.db"
    c = sqlite3.connect(db)
    c.executescript("""create table tasks(id text, title text, status text, assignee text, created_at int, completed_at int, session_id text);
        create table task_events(id integer primary key, task_id text, kind text, payload text);""")
    c.execute("insert into tasks values('t_origin','origin','archived','a',2,null,'chat')")
    c.execute("insert into tasks values('t_plain','plain','running','a',1,null,'chat')")
    for kind, p in [("placement_deferred", {"target": "macbook"}),
                    ("placement_forwarded", {"request_id": "r", "remote_task_id": "t_remote"}),
                    ("placement_archive_forwarded", {"gateway": "macbook", "remote_task_id": "t_remote", "status": "pending"})]:
        c.execute("insert into task_events(task_id, kind, payload) values('t_origin', ?, ?)", (kind, json.dumps(p)))
    c.commit(); c.close()
    return db


def test_archived_origin_shows_still_scheduled_remote_copy(tmp_path, gate):
    core = gate.core
    cards = core.linked_cards("chat", db_paths=[_db(tmp_path)])
    asked = []

    def replica(gw):
        asked.append(gw)
        return [{"id": "t_remote", "status": "scheduled"}]
    out = core.render_cards(cards, replica=replica).splitlines()
    assert any(l.startswith("- t_origin [archived") and l.endswith("-> macbook:t_remote scheduled") for l in out)
    assert [l for l in out if "t_plain" in l][0].endswith("plain")  # unforwarded card unchanged
    assert asked == ["macbook"]


def test_unreachable_or_missing_replica_is_stated_not_hidden(tmp_path, gate):
    core = gate.core
    cards = core.linked_cards("chat", db_paths=[_db(tmp_path)])
    assert core.render_cards(cards, replica=lambda gw: None).count("status unknown") == 1
    assert "not in replica" in core.render_cards(cards, replica=lambda gw: [])

    def boom(gw):
        raise RuntimeError("x")
    assert "status unknown" in core.render_cards(cards, replica=boom)
