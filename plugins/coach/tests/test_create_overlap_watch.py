"""create_overlap_watch: counts kanban_create overlap overrides whose new card was archived (a duplicate let
through); more than THRESHOLD in 7 days files ONE card per host+week; refusals, open cards and old events never count."""
import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("create_overlap_watch_test", ROOT / "create_overlap_watch.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

NOW = 1793200000.0


def _board(path: Path, cards):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
    db.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, kind TEXT, "
               "payload TEXT, created_at INTEGER)")
    for tid, status, outcome, age_days in cards:
        db.execute("INSERT INTO tasks VALUES (?,?,?)", (tid, f"card {tid}", status))
        db.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                   (tid, "create_overlap", json.dumps({"outcome": outcome, "reason": "separate",
                                                       "matches": [{"id": "t_owner"}]}),
                    int(NOW - age_days * 86400)))
    db.commit()
    db.close()


def test_counts_only_recent_archived_overrides_across_boards(tmp_path):
    _board(tmp_path / "kanban.db", [("a1", "archived", "override", 1), ("a2", "running", "override", 1),
                                    ("a3", "archived", "refused", 1), ("a4", "archived", "override", 9)])
    _board(tmp_path / "kanban/boards/x/kanban.db", [("b1", "archived", "override", 2)])
    found = cw.scan(str(tmp_path), now=NOW)
    assert [(f["board"], f["id"]) for f in found] == [("x", "b1"), ("default", "a1")]
    assert found[0]["overrode"] == ["t_owner"]


def test_files_one_card_above_threshold_and_none_at_it(tmp_path, capsys):
    calls = []

    def runner(argv, **kw):
        calls.append((argv, kw["input"]))
        return SimpleNamespace(returncode=0, stdout='{"task_id": "t_x"}', stderr="")

    _board(tmp_path / "kanban.db", [("a1", "archived", "override", 1), ("a2", "archived", "override", 1)])
    assert cw.main(["--home", str(tmp_path)], runner=runner, now=NOW) == 0
    assert calls == []  # exactly THRESHOLD is not "more than"
    _board(tmp_path / "kanban/boards/y/kanban.db", [("c1", "archived", "override", 3)])
    assert cw.main(["--home", str(tmp_path)], runner=runner, now=NOW) == 0
    assert len(calls) == 1
    argv, body = calls[0]
    assert argv[argv.index("--idempotency-key") + 1] == cw.card_key(cw.socket.gethostname().split(".")[0], NOW)
    assert "c1" in body and "a1" in body
    assert cw.main(["--home", str(tmp_path), "--dry-run"], runner=runner, now=NOW) == 0
    assert len(calls) == 1


def test_week_key_is_stable_within_an_iso_week():
    monday = time.mktime((2026, 10, 26, 12, 0, 0, 0, 0, 0))
    assert cw.card_key("h", monday) == cw.card_key("h", monday + 5 * 86400)
    assert cw.card_key("h", monday) != cw.card_key("h", monday + 7 * 86400)


def test_cleanup_archives_are_not_duplicates(tmp_path):
    _board(tmp_path / "kanban.db", [("d1", "archived", "override", 1), ("s1", "archived", "override", 1),
                                    ("p1", "archived", "override", 1), ("w1", "archived", "override", 1)])
    db = sqlite3.connect(tmp_path / "kanban.db")
    db.execute("UPDATE tasks SET title='Scratch: placement live proof (park only)' WHERE id='s1'")
    db.execute("UPDATE tasks SET title='[askrelay-live] Wire scratch integration' WHERE id='p1'")
    for tid, src in (("d1", "ready"), ("s1", "ready"), ("p1", "blocked"), ("w1", "done")):
        db.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                   (tid, "archived", json.dumps({"source_status": src}), int(NOW)))
    db.commit()
    db.close()
    assert [f["id"] for f in cw.scan(str(tmp_path), now=NOW)] == ["d1"]  # only the ready-card archive is a duplicate
