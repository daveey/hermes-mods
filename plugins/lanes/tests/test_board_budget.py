"""The per-board read budget covers a normal flow and still interrupts a wedged read."""
import importlib.util
import pathlib
import sqlite3

import pytest

HERE = pathlib.Path(__file__).resolve().parents[1] / 'dashboard' / 'board_merge.py'
SLOW = 'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 200000) SELECT count(*) FROM n'


def load():
    spec = importlib.util.spec_from_file_location('lanes_board_merge_budget', HERE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def armed(monkeypatch, elapsed):
    mod = load()
    clock = [100.0]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    db = sqlite3.connect(':memory:')
    mod.arm_deadline(db)
    clock[0] += elapsed(mod)
    return db


def test_read_slower_than_two_seconds_completes(monkeypatch):
    db = armed(monkeypatch, lambda mod: 3)  # the hub's flow routinely takes just over 2 s
    assert db.execute(SLOW).fetchone()[0] == 200000


def test_wedged_read_is_still_interrupted(monkeypatch):
    db = armed(monkeypatch, lambda mod: getattr(mod, 'BOARD_READ_BUDGET_S', 2) + 1)
    with pytest.raises(sqlite3.OperationalError, match='interrupted'):
        db.execute(SLOW).fetchone()
