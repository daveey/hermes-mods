"""Read-only inventory must survive WAL sidecar recovery."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("red_cards", Path(__file__).with_name("red_cards.py"))
red_cards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(red_cards)


class ReadOnlyTests(unittest.TestCase):
    def test_normal_and_fallback_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "board?#.db"
            with sqlite3.connect(db) as c:
                c.execute("create table sample(value)")
                c.execute("insert into sample values (42)")
            connect = sqlite3.connect
            for fail_probe in (False, True):
                with self.subTest(fail_probe=fail_probe):
                    probe = unittest.mock.MagicMock()
                    probe.execute.side_effect = sqlite3.OperationalError("unable to open database file")
                    calls = []
                    def open_db(database, **kwargs):
                        calls.append(database)
                        if fail_probe and len(calls) == 1:
                            return probe
                        return connect(database, **kwargs)
                    with patch.object(red_cards.sqlite3, "connect", side_effect=open_db):
                        c = red_cards._ro_connect(db)
                    try:
                        self.assertEqual(c.execute("select * from sample").fetchall(), [(42,)])
                        with self.assertRaises(sqlite3.OperationalError):
                            c.execute("insert into sample values (0)")
                    finally:
                        c.close()
                    if fail_probe:
                        probe.close.assert_called_once()
                        self.assertTrue(calls[-1].endswith("?mode=rw"))

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing.db"
            with self.assertRaises(sqlite3.OperationalError):
                red_cards._ro_connect(db)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
