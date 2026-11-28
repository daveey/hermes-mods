"""Exercise both standalone attribution readers on disposable SQLite stores."""
import ast
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = Path(__file__).parent
READERS = ("context_carry.py", "token_attribution_agg.py")


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "usage?#.db"
        with sqlite3.connect(self.db) as con:
            con.executescript("""
                CREATE TABLE sessions (id TEXT, source TEXT, model TEXT,
                  billing_provider TEXT, title TEXT, input_tokens INTEGER,
                  output_tokens INTEGER, cache_read_tokens INTEGER,
                  cache_write_tokens INTEGER, api_call_count INTEGER,
                  started_at REAL, estimated_cost_usd REAL, profile_name TEXT);
                CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT,
                  content TEXT, tool_calls TEXT, tool_name TEXT, active INTEGER);
            """)
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        ("s", "kanban", "model", "provider", "example", 10,
                         2, 100, 4, 1, time.time(), 0.0, "default"))
            con.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", [
                (1, "s", "user", "hello", None, None, 1),
                (2, "s", "assistant", "ok", None, None, 1)])

    def helper(self, reader):
        tree = ast.parse((SCRIPTS / reader).read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_ro_connect")
        ns = {"sqlite3": sqlite3}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), reader, "exec"), ns)
        return ns["_ro_connect"]

    def test_cli_results_and_unchanged_database(self):
        before = self.db.read_bytes()
        for reader in READERS:
            with self.subTest(reader=reader):
                proc = subprocess.run([sys.executable, str(SCRIPTS / reader),
                                       str(self.db), "3"], capture_output=True,
                                      text=True, check=True)
                result = json.loads(proc.stdout)
                if reader == "context_carry.py":
                    self.assertEqual(result["sessions"], 1)
                    self.assertEqual(result["role_chars"][0], [["kanban", "user"], 1, 5, 5])
                else:
                    self.assertEqual(result["n"], 1)
                    self.assertEqual(result["total"], [10, 2, 100, 4, 35])
                self.assertEqual(before, self.db.read_bytes())

    def test_normal_and_fallback_connections_forbid_writes(self):
        real_connect = sqlite3.connect
        for reader in READERS:
            for fallback in (False, True):
                with self.subTest(reader=reader, fallback=fallback):
                    opened = []
                    def connect(database, **kwargs):
                        if fallback and database.endswith("?mode=ro"):
                            raise sqlite3.OperationalError("simulated WAL open failure")
                        con = real_connect(database, **kwargs)
                        opened.append(con)
                        return con
                    with mock.patch.object(sqlite3, "connect", side_effect=connect):
                        con = self.helper(reader)(self.db)
                    try:
                        self.assertEqual(con.execute("PRAGMA query_only").fetchone(), (1,))
                        self.assertEqual(con.execute("SELECT count(*) FROM sessions").fetchone(), (1,))
                        with self.assertRaises(sqlite3.OperationalError):
                            con.execute("DELETE FROM sessions")
                    finally:
                        con.close()

    def test_missing_database_is_not_created(self):
        missing = Path(self.tmp.name) / "missing.db"
        for reader in READERS:
            with self.subTest(reader=reader):
                with self.assertRaises(sqlite3.OperationalError):
                    self.helper(reader)(missing)
                self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
