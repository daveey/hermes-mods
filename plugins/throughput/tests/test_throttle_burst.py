"""Burst: ``factor`` x every budget cap until ``until``, riding on the budget level. Hand-computed fixtures."""
import json

import pytest

from throughput_under_test import throttle as T
from throughput_under_test.store import Store

NOW = 1_800_000_030
END = NOW // 60 * 60
CARD = dict(task_id="t_1", lane="ready", assignee="default", board=None)


def _auto(root, *, percent=0, cap=1000.0, pools=None, budget_at=NOW - 60, set_at=NOW - 100, burst=None, **extra):
    data = {"mode": "auto", "percent": percent, "cap_per_s": cap,
            "pool_caps": {"anthropic": 400.0, "openai-codex": 600.0} if pools is None else pools,
            "budget_at": budget_at, "set_at": set_at, **extra}
    if burst is not None:
        data["burst"] = burst
    T.level_path(root).parent.mkdir(parents=True, exist_ok=True)
    T.level_path(root).write_text(json.dumps(data))
    return data


def _burst(factor=2.0, until=NOW + 3600, set_at=NOW - 10):
    return {"factor": factor, "until": until, "set_at": set_at}


def _db(root, rows=()):
    root.mkdir(parents=True, exist_ok=True)
    st = Store(root / "series.sqlite")
    for gateway, model, minute, value in rows:
        st.put(gateway, f"tokens_io:{model}", [(minute, value)])
    st.set_meta("me", "hub")
    return root / "series.sqlite"


def _gate(root, db):
    return T.Gate(root, db, sleep=lambda s: None, clock=lambda: float(NOW))


def test_burst_multiplies_every_budget_cap_on_top_of_the_slider_and_stops_at_until(tmp_path):
    db = _db(tmp_path)
    _auto(tmp_path, percent=25, cap=1000.0, pools={"anthropic": 400.0, "openai-codex": 600.0, "xai-oauth": None},
          burst=_burst(factor=2.0, until=NOW + 600))
    level = T.effective(tmp_path, None, NOW)
    b = T.budget(level, NOW)
    # 1000 x 0.75 x 2 = 1500; anthropic 400 x 0.75 x 2 = 600; a null cap stays null
    assert b["cap_per_s"] == pytest.approx(1500.0) and b["burst_factor"] == 2.0
    assert b["pool_caps"] == {"anthropic": pytest.approx(600.0), "openai-codex": pytest.approx(900.0), "xai-oauth": None}
    assert b["budget_per_s"] == 1000.0 and b["pool_budgets"]["anthropic"] == 400.0  # unscaled budgets unchanged
    assert T.status(tmp_path, db, NOW)["cap_per_s"] == pytest.approx(1500.0)
    assert _gate(tmp_path, db).plan(NOW, "anthropic")[1] == pytest.approx(20_000 / 600.0)
    out = T.status(tmp_path, db, NOW)["burst"]
    assert out == {"factor": 2.0, "until": NOW + 600, "set_at": NOW - 10, "remaining_s": 600.0, "active": True}
    # at until the burst is over: the slider's caps again, and the payload says it ended
    after = T.budget(level, NOW + 600)
    assert after["cap_per_s"] == pytest.approx(750.0) and after["burst_factor"] == 1.0
    assert after["pool_caps"]["anthropic"] == pytest.approx(300.0)
    assert _gate(tmp_path, db).plan(NOW + 600, "anthropic")[1] == pytest.approx(20_000 / 300.0)
    assert T.status(tmp_path, db, NOW + 600)["burst"]["active"] is False
    assert T.status(tmp_path, db, NOW + 600)["burst"]["remaining_s"] == 0.0


def test_burst_never_lifts_a_reserve_cap_of_zero_and_100_percent_still_holds(tmp_path):
    db = _db(tmp_path)
    _auto(tmp_path, pools={"anthropic": 0.0, "openai-codex": 600.0}, burst=_burst(factor=4.0))
    level = T.effective(tmp_path, None, NOW)
    assert T.budget(level, NOW)["pool_caps"] == {"anthropic": 0.0, "openai-codex": pytest.approx(2400.0)}
    gate = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic") == ("hold",)
    _auto(tmp_path, percent=100, pools={"anthropic": 0.0, "openai-codex": 600.0}, burst=_burst(factor=4.0))
    assert T.status(tmp_path, db, NOW)["cap_per_s"] == 0.0
    assert _gate(tmp_path, db).plan(NOW, "openai-codex") == ("hold",)
    assert T.status(tmp_path, db, NOW)["holding"] is True


def test_start_gate_compares_against_the_burst_scaled_governed_cap(tmp_path):
    # anthropic 600/s on a 400/s budget pool: over without a burst, under 2x400 = 800 with one
    rows = [("hub", "claude-opus-5-5", END - 60, 36_000.0)]
    db = _db(tmp_path / "a", rows)
    _auto(tmp_path / "a", pools={"anthropic": 400.0})
    assert _gate(tmp_path / "a", db).start_gate(**CARD) == {"hold": True, "reason": "token_throttle"}
    db = _db(tmp_path / "b", rows)
    _auto(tmp_path / "b", pools={"anthropic": 400.0}, burst=_burst(factor=2.0))
    out = T.status(tmp_path / "b", db, NOW)
    assert out["budget"]["governed_cap_per_s"] == pytest.approx(800.0) and out["holding"] is False
    assert _gate(tmp_path / "b", db).start_gate(**CARD) is None


def test_expired_or_malformed_burst_is_ignored(tmp_path):
    db = _db(tmp_path)
    _auto(tmp_path, burst=_burst(until=NOW - 1, set_at=NOW - 3600))
    level = T.effective(tmp_path, None, NOW)
    assert T.budget(level, NOW)["cap_per_s"] == 1000.0 and T.budget(level, NOW)["burst_factor"] == 1.0
    assert T.status(tmp_path, db, NOW)["burst"]["active"] is False
    for bad in ({"factor": 1.0, "until": NOW + 60, "set_at": NOW}, {"factor": 4.5, "until": NOW + 60, "set_at": NOW},
                {"factor": "2", "until": NOW + 60, "set_at": NOW}, {"factor": True, "until": NOW + 60, "set_at": NOW},
                {"factor": 2.0, "until": NOW + 25 * 3600, "set_at": NOW}, {"factor": 2.0, "until": NOW, "set_at": NOW},
                {"factor": 2.0, "until": NOW + 60}, {"factor": float("nan"), "until": NOW + 60, "set_at": NOW},
                [2.0], "2x"):
        assert T.burst_of(bad) is None, bad
        _auto(tmp_path, burst=bad)
        level = T.effective(tmp_path, None, NOW)
        assert "burst" not in level and T.budget(level, NOW)["cap_per_s"] == 1000.0, bad
        assert T.status(tmp_path, db, NOW)["burst"] is None
    # exactly 24 h and exactly 4x are allowed
    assert T.burst_of({"factor": 4, "until": NOW + 86400, "set_at": NOW}) == {"factor": 4.0, "until": NOW + 86400,
                                                                               "set_at": NOW}


def test_burst_running_further_than_24_hours_from_now_is_ignored(tmp_path):
    # a valid record (until - set_at = 24 h) read 1 h before its set_at (a clock behind): 25 h from here, not applied
    db = _db(tmp_path)
    _auto(tmp_path, burst=_burst(factor=2.0, set_at=NOW + 3600, until=NOW + 3600 + 86400), budget_at=NOW + 3000)
    level = T.effective(tmp_path, None, NOW)
    assert level["burst"]["factor"] == 2.0 and T.burst_active(level, NOW) is None
    assert T.budget(level, NOW)["cap_per_s"] == 1000.0
    assert T.status(tmp_path, db, NOW)["burst"]["active"] is False
    assert T.burst_active(level, NOW + 3600) is not None  # within 24 h of until: applied
    assert T.budget(level, NOW + 3600)["cap_per_s"] == pytest.approx(2000.0)


def test_burst_on_a_manual_level_is_ignored_and_refused(tmp_path):
    db = _db(tmp_path)
    T.level_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    T.level_path(tmp_path).write_text(json.dumps({"percent": 50, "cap_per_s": 1000.0, "set_at": NOW - 100,
                                                  "burst": _burst()}))
    level = T.effective(tmp_path, None, NOW)
    assert "burst" not in level and T.status(tmp_path, db, NOW)["cap_per_s"] == 1000.0
    assert T.status(tmp_path, db, NOW)["burst"] is None
    with pytest.raises(T.BurstRefused, match="manual"):
        T.set_burst(tmp_path, level, 2.0, 3600, NOW)
    _auto(tmp_path, budget_at=NOW - T.BUDGET_EXPIRE_S - 1)
    with pytest.raises(T.BurstRefused, match="expired"):
        T.set_burst(tmp_path, T.effective(tmp_path, None, NOW), 2.0, 3600, NOW)
    _auto(tmp_path)
    for factor, duration in ((1.0, 3600), (5, 3600), (2.0, 0), (2.0, 25 * 3600), ("2", 3600), (True, 3600)):
        with pytest.raises(ValueError):
            T.set_burst(tmp_path, T.effective(tmp_path, None, NOW), factor, duration, NOW)


def test_set_and_clear_burst_write_newer_levels_keeping_everything_else(tmp_path):
    _auto(tmp_path, percent=20, pools={"anthropic": 0.0, "openai-codex": 600.0})
    level = T.effective(tmp_path, None, NOW)
    burst = T.set_burst(tmp_path, level, 2.0, 7200, NOW + 0.0004)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert burst == {"factor": 2.0, "until": NOW + 7200, "set_at": NOW}
    assert data == {"mode": "auto", "percent": 20.0, "cap_per_s": 1000.0,
                    "pool_caps": {"anthropic": 0.0, "openai-codex": 600.0}, "budget_at": NOW - 60, "set_at": NOW,
                    "burst": {"factor": 2.0, "until": NOW + 7200, "set_at": NOW}}
    assert T.clear_burst(tmp_path, T.effective(tmp_path, None, NOW + 1), NOW + 1) is True
    data = json.loads(T.level_path(tmp_path).read_text())
    assert "burst" not in data and data["set_at"] == NOW + 1 and data["percent"] == 20.0
    assert data["pool_caps"] == {"anthropic": 0.0, "openai-codex": 600.0}
    assert T.clear_burst(tmp_path, T.effective(tmp_path, None, NOW + 2), NOW + 2) is False  # nothing to end


def test_slider_move_keeps_the_burst_and_manual_write_drops_it(tmp_path):
    _auto(tmp_path, burst=_burst())
    T.set_level(tmp_path, T.effective(tmp_path, None, NOW), 40, NOW + 1)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["percent"] == 40.0 and data["burst"] == _burst()
    assert T.budget(T.effective(tmp_path, None, NOW + 1), NOW + 1)["cap_per_s"] == pytest.approx(1000 * 0.6 * 2)
    T.write_level(tmp_path, 10, now=NOW + 2, reference=2000.0)
    assert "burst" not in json.loads(T.level_path(tmp_path).read_text())


def _pacing_file(root, total=1000.0):
    v2 = {"computed_at": NOW - 30, "global_rate_tokens_per_s": total, "events": [], "curve": {},
          "pools": {"anthropic": {"rate_tokens_per_s": 400.0, "at_reserve": False},
                    "openai-codex": {"rate_tokens_per_s": 600.0, "at_reserve": False}}}
    path = root / "llm_pacing.json"
    path.write_text(json.dumps({"v2": v2}))
    return path


def test_publisher_keeps_an_active_burst_and_drops_an_expired_one(tmp_path):
    _auto(tmp_path, burst=_burst(until=NOW + 600))
    pacing = _pacing_file(tmp_path, total=2000.0)  # moved: a rewrite
    assert T.publish_budget(tmp_path, None, pacing, NOW) is True
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["cap_per_s"] == 2000.0 and data["burst"] == _burst(until=NOW + 600)
    assert data["set_at"] == pytest.approx(NOW - 100 + 0.001)
    _pacing_file(tmp_path, total=3000.0)
    assert T.publish_budget(tmp_path, None, pacing, NOW + 600) is True  # at until: the burst is over
    assert "burst" not in json.loads(T.level_path(tmp_path).read_text())


def test_burst_set_on_another_gateway_is_folded_in_by_the_publisher(tmp_path):
    import sqlite3
    _auto(tmp_path, set_at=NOW - 100)
    hub = tmp_path / "hub.db"
    c = sqlite3.connect(hub)
    c.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, "
              "updated_at INTEGER, PRIMARY KEY (gw, coll, key))")
    c.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    block = {"mode": "auto", "percent": 0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 400.0},
             "budget_at": NOW - 60, "set_at": NOW - 10, "burst": _burst(factor=3.0)}
    c.execute("INSERT INTO replica VALUES ('mac', 'gateway', 'info', 1, 1, ?, ?)",
              (json.dumps({"throttle": block}), (NOW - 5) * 1000))
    c.commit()
    c.close()
    level = T.effective(tmp_path, hub, NOW)
    assert level["source"] == "mac" and level["burst"]["factor"] == 3.0
    assert T.budget(level, NOW)["pool_caps"] == {"anthropic": pytest.approx(1200.0)}
    assert T.publish_budget(tmp_path, hub, _pacing_file(tmp_path), NOW) is True
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["burst"]["factor"] == 3.0 and data["set_at"] == pytest.approx(NOW - 10 + 0.001)
