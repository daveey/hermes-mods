"""Waiting asks carry newer evidence, never this chat's own echo."""
from contextlib import closing
import json
import sqlite3

import pytest

CARD = "t_aaaaaaaaaaaaaaaa"
BLOCK = 1794456000  # 2026-11-11 20:00 PT
PLAIN = f"- {CARD}: Approve the cleanup"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "board.sqlite"
    with closing(sqlite3.connect(path)) as c, c:
        c.executescript("""
            create table tasks(id text, title text, status text, assignee text,
                created_at real, completed_at real, session_id text,
                block_kind text, human_request text);
            create table task_events(id integer primary key, task_id text,
                kind text, payload text, created_at real);
            create table task_comments(id integer primary key, task_id text,
                author text, body text, created_at real);
        """)
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?)", (
            CARD, "Cleanup", "blocked", "default", BLOCK - 100, None,
            "chat", "needs_input", json.dumps({"action": "Approve the cleanup"})))
        c.execute("insert into task_events values (1,?,'blocked','{}',?)", (CARD, BLOCK))
    return path


def comment(db, *, body="Target is now absent", author="worker", writer="other", ts=BLOCK + 60, event=True):
    with closing(sqlite3.connect(db)) as c, c:
        cid = c.execute("insert into task_comments(task_id,author,body,created_at) values (?,?,?,?)",
                        (CARD, author, body, ts)).lastrowid
        if event:
            c.execute("insert into task_events(task_id,kind,payload,created_at) values (?,'commented',?,?)",
                      (CARD, json.dumps({"comment_id": cid, "writer_session": writer}), ts))


def line(gate, db):
    out = gate.core.l1_block("chat", None, db_paths=[db])
    return out.split("Waiting on David now:\n", 1)[1].split("\n", 1)[0]


@pytest.mark.parametrize("event", [True, False])
def test_newer_other_writer(db, gate, event):
    comment(db, event=event)
    assert line(gate, db) == (PLAIN + ' — newer comment 20:01 PT from worker: "Target is now absent" '
                             '(the premise may have changed: read it before reporting this card as waiting)')


def test_own_session_not_shown(db, gate):
    comment(db, writer="chat")
    assert line(gate, db) == PLAIN


@pytest.mark.parametrize("delta", [-1, 0])
def test_not_after_block(db, gate, delta):
    comment(db, ts=BLOCK + delta)
    assert line(gate, db) == PLAIN


@pytest.mark.parametrize("damage", ["missing_table", "missing_column", "malformed", "non_object"])
def test_fail_open(db, gate, damage):
    comment(db)
    with closing(sqlite3.connect(db)) as c, c:
        if damage == "missing_table":
            c.execute("drop table task_comments")
        elif damage == "missing_column":
            c.execute("alter table task_comments rename column body to old_body")
        else:
            c.execute("update task_events set payload=? where kind='commented'",
                      ("{" if damage == "malformed" else "[]",))
    assert line(gate, db) == PLAIN


def test_one_line_and_truncation(db, gate):
    body = "first\nsecond\r\n" + "x" * 200
    comment(db, body=body, author="a" * 60)
    out = line(gate, db)
    assert f'from {"a" * 40}: "{" ".join(body.split())[:160]}"' in out
    assert "x" * 161 not in out


def test_latest_eligible_not_latest_own(db, gate):
    comment(db, body="older evidence", ts=BLOCK + 10)
    comment(db, body="newest evidence", ts=BLOCK + 20)
    comment(db, body="own echo", writer="chat", ts=BLOCK + 30)
    out = line(gate, db)
    assert '"newest evidence"' in out
    assert "older evidence" not in out and "own echo" not in out
    assert out.count("newer comment") == 1


def test_latest_block_is_boundary(db, gate):
    comment(db)
    with closing(sqlite3.connect(db)) as c, c:
        c.execute("insert into task_events(task_id,kind,payload,created_at) values (?,'blocked','{}',?)",
                  (CARD, BLOCK + 120))
    assert line(gate, db) == PLAIN
