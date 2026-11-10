"""worker_share_watch: one card when the hub is full, a pool worker idles and unmarked cards wait (kanban-12ee0e4b65)."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coach_worker_share_watch_test", ROOT / "worker_share_watch.py")
w = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = w
spec.loader.exec_module(w)

NOW_MS = 1794295000000


def registry(hub_runs=7, worker_runs=0, worker_at=NOW_MS, worker_state="active"):
    return {"me": "hub", "machines": {
        "hub": {"state": "active", "max_card_runs": 7, "metrics": {"at": NOW_MS, "active_card_runs": hub_runs}},
        "worker-1": {"state": worker_state, "max_card_runs": 16,
                     "metrics": {"at": worker_at, "active_card_runs": worker_runs}},
        "mini": {"state": "active", "pinned_only": True, "max_card_runs": None,
                 "metrics": {"at": NOW_MS, "active_card_runs": 0}},
    }}


@pytest.fixture
def home(tmp_path):
    con = sqlite3.connect(tmp_path / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, assignee TEXT, requires TEXT, skills TEXT,"
                " runs_on TEXT, workspace_kind TEXT, workspace_path TEXT, status TEXT, placement_policy TEXT,"
                " created_at INTEGER)")
    rows = [("t_a1", "self-heal fix: a", "x", "default", '["host_repo"]', None, None, "ready", None, 1),
            ("t_a2", "self-heal fix: b", "x", "default", '["host_repo"]', None, None, "ready", None, 2),
            ("t_b1", "tuner", "y", "bb-tuner", None, None, None, "ready", None, 3),
            ("t_c1", "marked", "z", "default", None, None, None, "ready", "worker-first-v1", 4),
            ("t_d1", "pinned", "z", "default", None, None, '["machine:mini"]', "ready", None, 5),
            ("t_e1", "running", "z", "default", None, None, None, "running", None, 6)]
    con.executemany("INSERT INTO tasks (id,title,body,assignee,requires,skills,runs_on,status,placement_policy,"
                    "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return tmp_path


def reason(card):
    if "host_repo" in (card.get("requires") or []):
        return "uncarried requirement"
    if card.get("assignee") == "bb-tuner":
        return "assignee not allowed"
    return None


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": "t_0000beef"}), stderr="")


def go(home, runner, reg, i, **kw):
    """Sample i, 10 min apart; the registry's metric stamps move with the clock (staleness stays relative)."""
    import copy
    reg = copy.deepcopy(reg)
    for m in reg["machines"].values():
        m["metrics"]["at"] += i * 600000
    return w.run(home, registry_fn=lambda: reg, reason_fn=reason, now_ms=NOW_MS + i * 600000, runner=runner, **kw)


def test_only_unmarked_unpinned_ready_cards_count(home):
    assert [c["id"] for c in w.waiting_unmarked(home / "kanban.db")] == ["t_a1", "t_a2", "t_b1"]


def test_three_starved_samples_file_one_card_with_top_reasons(home):
    r = Runner()
    outs = [go(home, r, registry(), i) for i in range(5)]
    assert [o["streak"] for o in outs] == [1, 2, 3, 4, 5]
    assert [o["filed"] for o in outs] == [None, None, "t_0000beef", None, None]  # once per day
    (argv,) = r.calls
    body = argv[argv.index("--body") + 1]
    assert "`uncarried requirement`: 2" in body and "`assignee not allowed`: 1" in body
    assert "worker-1 (16 free)" in body and "t_a1" in body
    assert argv[argv.index("--idempotency-key") + 1].startswith("worker-share-watch:")
    assert "(top: uncarried requirement)" in argv[3]


@pytest.mark.parametrize("reg", [
    registry(hub_runs=6),                        # hub has a free slot
    registry(worker_runs=16),                    # worker full too: nothing to gain
    registry(worker_at=NOW_MS - 3600 * 1000),    # worker metrics stale: unknown load proves no room
    registry(worker_state="draining"),
])
def test_no_starvation_resets_the_streak(home, reg):
    r = Runner()
    go(home, r, registry(), 0)
    go(home, r, registry(), 1)
    out = go(home, r, reg, 2)
    assert out["streak"] == 0 and not out["sample"]["starved"]
    assert go(home, r, registry(), 3)["streak"] == 1 and r.calls == []


def test_nothing_waiting_is_not_starvation(home):
    con = sqlite3.connect(home / "kanban.db")
    con.execute("UPDATE tasks SET status='done' WHERE status='ready'")
    con.commit()
    con.close()
    assert not w.sample(registry(), w.waiting_unmarked(home / "kanban.db"), NOW_MS, reason)["starved"]


def test_dry_run_saves_and_files_nothing(home):
    r = Runner()
    for i in range(4):
        out = go(home, r, registry(), i, dry_run=True)
    assert out["streak"] == 1 and r.calls == [] and not w.state_path(home).exists()


def test_a_failing_reason_check_is_counted_not_fatal(home):
    s = w.sample(registry(), w.waiting_unmarked(home / "kanban.db"), NOW_MS, lambda c: 1 / 0)
    assert s["reasons"] == {"check failed: ZeroDivisionError": 3} and s["starved"]
