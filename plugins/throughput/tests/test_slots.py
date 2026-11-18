"""Token cost per slot and the slots each limit allows, against hand-computed fixtures and replica-shaped sqlite."""
import json
import sqlite3

import pytest

from throughput_under_test import slots as S
from throughput_under_test import throttle as T
from throughput_under_test.store import Store

NOW = 1_800_000_030
END = NOW // 60 * 60
START = END - T.SHARE_WINDOW_S  # 900 s

SCHEMA = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, current_run_id INTEGER);
CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, started_at REAL, ended_at REAL);
CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, payload TEXT,
                          created_at REAL);
"""


def _replica(tmp_path, runs, name="default.db"):
    """runs: (task, status, started, ended, claim payload dict or None, is_current_run)."""
    db = tmp_path / name
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    for n, (task, status, started, ended, claim, current) in enumerate(runs, 1):
        c.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?)", (task, status, n if current else None))
        c.execute("INSERT INTO task_runs VALUES (?,?,?,?)", (n, task, started, ended))
        if claim is not None:
            c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                      (task, "claimed", json.dumps(claim), started - 1))
    c.commit()
    c.close()
    return db


def test_busy_seconds_clips_to_the_window_and_attributes_by_claim(tmp_path):
    db = _replica(tmp_path, [
        ("a", "done", START + 100, START + 400, {"holder": "worker-1"}, False),          # 300 s inside
        ("b", "done", START - 500, START + 200, {"lock": "hermes-worker-1:77"}, False),   # clipped at the start: 200
        ("c", "running", END - 60, None, {"holder": "worker-a1"}, True),                  # open, current: 60
        ("d", "done", END - 30, None, {"holder": "worker-a1"}, False),                    # dangling open row: 0
        ("e", "done", START - 900, START - 10, {"holder": "worker-1"}, False),            # before the window: 0
        ("f", "done", START + 10, START + 20, None, False),                               # no claim: unattributed
        ("g", "done", START + 10, START + 110, {"lock": "hwp-probe:1"}, False),           # ephemeral: skipped
    ])
    assert S.busy_seconds([db], START, END) == {"worker-1": 500.0, "worker-a1": 60.0}
    assert S.busy_seconds([tmp_path / "missing.db"], START, END) == {}


def test_busy_seconds_takes_the_claim_at_the_run_start_not_a_later_one(tmp_path):
    db = _replica(tmp_path, [("a", "done", START + 100, START + 400, {"holder": "worker-1"}, False)])
    c = sqlite3.connect(db)
    c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES ('a','claimed',?,?)",
              (json.dumps({"holder": "worker-2"}), START + 500))  # a later run's claim on another machine
    c.commit()
    c.close()
    assert S.busy_seconds([db], START, END) == {"worker-1": 300.0}


def test_plan_hand_computed():
    # 900 s window. worker-1: 900k tokens over 9000 busy slot-seconds = 100 tokens per slot-second, 10 busy slots.
    # hub: 100k over 1000 = 100. worker-a1: 20k tokens but only 120 busy seconds: no cost of its own -> median 100.
    usage = {"worker-1": 900_000.0, "hub": 100_000.0, "worker-a1": 20_000.0}
    busy = {"worker-1": 9000.0, "hub": 1000.0, "worker-a1": 120.0}
    got = S.plan(1000.0, usage, busy)
    total, even = 1_020_000.0, 1 / 3
    share_w1 = T.EVEN_SHARE * even + (1 - T.EVEN_SHARE) * 900_000.0 / total
    assert got["worker-1"]["share"] == pytest.approx(share_w1, abs=1e-4)
    assert got["worker-1"]["tokens_per_slot_per_s"] == 100.0 and got["worker-1"]["cost_source"] == "measured"
    assert got["worker-1"]["by_throttle"] == int(share_w1 * 1000.0 / 100.0) == 7
    assert got["worker-1"]["avg_busy"] == 10.0 and "by_pacing" not in got["worker-1"]
    assert got["worker-1"]["allowed"] == 7
    assert got["worker-a1"]["cost_source"] == "fleet_median" and got["worker-a1"]["tokens_per_slot_per_s"] == 100.0
    assert sum(g["share"] for g in got.values()) == pytest.approx(1.0, abs=1e-3)


def test_plan_no_cap_zero_cap_and_unknown_cost():
    usage, busy = {"worker-1": 900_000.0}, {"worker-1": 9000.0}
    assert S.plan(None, usage, busy)["worker-1"]["allowed"] is None            # throttle off: nothing crossed out
    zero = S.plan(0.0, usage, busy)["worker-1"]
    assert (zero["by_throttle"], zero["allowed"]) == (0, 0)                    # level 100 %: every slot crossed out
    assert S.plan(1000.0, usage, busy)["worker-1"]["allowed"] == 10            # 1000 tokens/s / 100 per slot
    cold = S.plan(1000.0, {"worker-a3": 0.0}, {})["worker-a3"]                 # nothing measured anywhere
    assert cold["tokens_per_slot_per_s"] is None and cold["cost_source"] is None and cold["allowed"] is None


def test_pacing_holds_counts_distinct_cards_in_the_window(tmp_path):
    db = _replica(tmp_path, [])
    c = sqlite3.connect(db)
    for task, at in (("a", NOW - 10), ("a", NOW - 20), ("b", NOW - 299), ("c", NOW - 301), ("d", NOW - 5)):
        c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                  (task, "claimed" if task == "d" else "llm_paced", "{}", at))
    c.commit()
    c.close()
    assert S.pacing_holds([db], NOW - 300, NOW + 1) == 2  # a (twice) and b; c is too old, d is not a pacing hold
    assert S.pacing_holds([tmp_path / "missing.db"], NOW - 300, NOW + 1) == 0


def test_payload_reads_series_and_replicas(tmp_path):
    S._cache.clear()
    st = Store(tmp_path / "series.sqlite")
    st.put("worker-1", "tokens_io", [(END - 60 * m, 60_000.0) for m in range(1, 16)])  # 900k in the window
    db = _replica(tmp_path, [(f"t{n}", "done", START, END, {"holder": "worker-1"}, False) for n in range(10)])
    c = sqlite3.connect(db)
    c.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES ('t0','llm_paced','{}',?)", (NOW - 30,))
    c.commit()
    c.close()
    got = S.payload(tmp_path, tmp_path / "series.sqlite", 500.0, now=NOW, replicas=[db])
    assert got["window_s"] == 900 and got["pacing"] == {"held_starts": 1, "window_s": 300.0}
    row = got["gateways"]["worker-1"]
    assert row["tokens_per_slot_per_s"] == 100.0 and row["avg_busy"] == 10.0 and row["share"] == 1.0
    assert row["by_throttle"] == 5 and row["allowed"] == 5 and row["rate_per_s"] == 1000.0
