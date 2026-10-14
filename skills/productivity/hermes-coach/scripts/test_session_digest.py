#!/usr/bin/env python3
"""Stdlib tests for session_digest.py:  python3 test_session_digest.py"""
import sqlite3, sys, tempfile, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import session_digest as sd  # noqa: E402

SCHEMA = """
create table sessions (id text primary key, source text, model text, parent_session_id text, started_at real,
  ended_at real, end_reason text, message_count int, tool_call_count int, api_call_count int, input_tokens int,
  output_tokens int, cache_read_tokens int, actual_cost_usd real, estimated_cost_usd real, title text,
  last_activity_at real);
create table messages (id integer primary key autoincrement, session_id text, role text, content text,
  tool_name text, tool_calls text, timestamp real, active int not null default 1, compacted int not null default 0,
  display_kind text, display_metadata text, message_uid text);
"""


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    now = time.time()
    con.execute("insert into sessions (id, source, started_at, last_activity_at) values ('s1','desktop',?,?)", (now, now))
    for r in rows:
        con.execute("insert into messages (session_id, role, content, timestamp, active, compacted, display_kind,"
                    " display_metadata, message_uid) values ('s1',?,?,?,?,?,?,?,?)", r)
    con.commit(); con.close()


class DigestDedupe(unittest.TestCase):
    def test_compaction_copies_and_queued_placeholders_count_once(self):
        t = time.time() - 100
        shot = "redesign it\n@image:`/Users/x/Library/Application Support/Hermes/composer-images/s.png`\n[screenshot]"
        rows = [
            ("user", "first ask", t, 0, 1, None, None, "u1"),
            ("assistant", "ok", t + 1, 0, 1, None, None, "a1"),
            # queued placeholder, re-written on every compaction pass (rewound: active=0, compacted=0)
            ("user", "redesign it", t + 2, 0, 0, None, '{"_queued_prompt": true}', "q1"),
            ("user", "redesign it", t + 2, 0, 0, None, '{"_queued_prompt": true}', "q1"),
            # compaction re-materialized copies of the first turn
            ("user", "first ask", t, 0, 1, None, None, "u1"),
            ("user", "first ask", t, 1, 0, None, None, "u1"),
            # the real drained send, with its screenshot
            ("user", shot, t + 3, 1, 0, None, None, "u2"),
            ("user", "secret plumbing", t + 4, 1, 0, "hidden", None, "h1"),
        ]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        texts = [u["text"] for u in dg["user_turns"]]
        self.assertEqual(texts, ["first ask", "redesign it\n[screenshot]"])
        self.assertEqual(dg["user_turns_total"], 2)
        self.assertEqual(dg["signals"].get("screenshot"), 1)
        self.assertEqual(dg["msgs"], 3)

    def test_legacy_schema_without_active_columns(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            con = sqlite3.connect(db)
            con.executescript(SCHEMA.split("create table messages")[0] +
                              "create table messages (id integer primary key, session_id text, role text,"
                              " content text, tool_name text, tool_calls text, timestamp real);")
            now = time.time()
            con.execute("insert into sessions (id, started_at) values ('s1', ?)", (now,))
            con.execute("insert into messages (session_id, role, content, timestamp) values ('s1','user','hi',?)", (now,))
            con.commit(); con.close()
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        self.assertEqual([u["text"] for u in dg["user_turns"]], ["hi"])


if __name__ == "__main__":
    unittest.main()
