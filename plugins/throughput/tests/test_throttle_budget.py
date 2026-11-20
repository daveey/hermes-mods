"""Budget mode (``mode: "auto"``): the budget-publish host turns core's ``llm_pacing.json`` ``v2`` into the fleet
level, and every gateway paces each provider pool at its published cap scaled by the slider. Hand-computed fixtures."""
import json
import os
import sys
import threading
import types

import pytest

from throughput_under_test import throttle as T
from throughput_under_test.store import Store

NOW = 1_800_000_030
END = NOW // 60 * 60
CARD = dict(task_id="t_1", lane="ready", assignee="default", board=None)


class Clock:
    def __init__(self):
        self.t, self.sleeps, self.on_sleep = float(NOW), [], None

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


def _series(root, rows=(), me="hub"):
    root.mkdir(parents=True, exist_ok=True)
    st = Store(root / "series.sqlite")
    for gateway, minute, value in rows:
        st.put(gateway, "tokens_io", [(minute, value)])
    if me:
        st.set_meta("me", me)
    return root / "series.sqlite"


def _auto(root, *, percent=0, cap=1000.0, pools=None, budget_at=NOW - 60, set_at=NOW - 100, **extra):
    """A budget level stored on this gateway, as the publisher writes it."""
    data = {"mode": "auto", "percent": percent, "cap_per_s": cap,
            "pool_caps": {"anthropic": 400.0, "openai-codex": 600.0} if pools is None else pools,
            "budget_at": budget_at, "set_at": set_at, **extra}
    T.level_path(root).parent.mkdir(parents=True, exist_ok=True)
    T.level_path(root).write_text(json.dumps(data))
    return data


def _gate(root, db):
    clock = Clock()
    return T.Gate(root, db, sleep=clock.sleep, clock=clock.now), clock


def _v2(computed_at=NOW - 30, total=1000.0, pools=None):
    return {"computed_at": computed_at, "margin_s": 10800, "global_rate_tokens_per_s": total, "events": [], "curve": {},
            "pools": {"anthropic": {"rate_tokens_per_s": 400.0, "at_reserve": False, "ratio": 0.4},
                      "openai-codex": {"rate_tokens_per_s": 600.0, "at_reserve": False, "ratio": 0.6}}
            if pools is None else pools}


def _pacing_file(root, v2):
    path = root / "llm_pacing.json"
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"anthropic": {"samples": []}, "version": 2, "v2": v2}))
    return path


# --- the consuming side: Gate.plan and the middleware ------------------------------------------------------------

def test_pool_cap_zero_holds_a_card_call_until_the_pool_has_a_rate_again(tmp_path, monkeypatch):
    db = _series(tmp_path)
    _auto(tmp_path, pools={"anthropic": 0.0, "openai-codex": 600.0})
    gate, clock = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic") == ("hold",)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    monkeypatch.setattr(T, "_card_heartbeat", lambda waited: None)
    clock.on_sleep = lambda n: _auto(tmp_path, pools={"anthropic": None}, set_at=NOW - 50) if n == 2 else None
    sent = []
    assert gate.middleware(next_call=lambda request: sent.append(request) or "r", request={"m": 1},
                           provider="anthropic", platform="") == "r"
    assert len(clock.sleeps) == 2 and sent == [{"m": 1}]  # held two polls, released when the cap became null


def test_cron_call_on_a_pool_at_reserve_waits_too(tmp_path):
    db = _series(tmp_path)
    _auto(tmp_path, pools={"anthropic": 0.0})
    gate, clock = _gate(tmp_path, db)
    clock.on_sleep = lambda n: _auto(tmp_path, pools={}, set_at=NOW - 50)
    assert gate.middleware(next_call=lambda request: "r", request={}, provider="anthropic", platform="cron") == "r"
    assert clock.sleeps == [T.POLL_S]


def test_chat_call_on_a_pool_capped_at_zero_passes(tmp_path):
    db = _series(tmp_path)
    _auto(tmp_path, percent=100, pools={"anthropic": 0.0})
    gate, clock = _gate(tmp_path, db)
    assert gate.middleware(next_call=lambda request: "chat", request={}, provider="anthropic",
                           platform="desktop") == "chat"
    assert clock.sleeps == []


def test_null_pool_cap_passes(tmp_path):
    db = _series(tmp_path)
    _auto(tmp_path, pools={"anthropic": None, "openai-codex": 0.0})
    gate, clock = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic") == ("pass",)
    assert gate.wait(provider="anthropic") == 0.0 and clock.sleeps == []


def test_ungoverned_provider_passes(tmp_path):
    db = _series(tmp_path)
    _auto(tmp_path, pools={"anthropic": 0.0})
    gate, clock = _gate(tmp_path, db)
    assert gate.plan(NOW, "openrouter") == ("pass",)
    assert gate.plan(NOW, None) == ("pass",)  # a call that names no provider
    assert gate.wait(provider="openrouter") == 0.0 and clock.sleeps == []


def test_positive_pool_cap_paces_that_pool_on_its_own_slot(tmp_path):
    db = _series(tmp_path)  # only this gateway: share 1.0; no use measured: 20k tokens a call
    _auto(tmp_path, pools={"anthropic": 400.0, "openai-codex": 100.0})
    gate, clock = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic") == ("pace", pytest.approx(20_000 / 400.0), NOW - 100, "anthropic")
    assert gate.plan(NOW, "openai-codex") == ("pace", pytest.approx(200.0), NOW - 100, "openai-codex")
    assert gate.wait(provider="anthropic") == 0.0  # idle pool: starts at once
    assert gate.wait(provider="openai-codex") == 0.0  # the other pool has its own slot
    assert gate.wait(provider="anthropic") == pytest.approx(50.0)  # the next anthropic start is one interval on
    state = json.loads(T.pace_path(tmp_path).read_text())
    assert set(state["pools"]) == {"anthropic", "openai-codex"} and "last_start" not in state


def test_percent_scales_global_and_pool_caps(tmp_path):
    db = _series(tmp_path)
    _auto(tmp_path, percent=25, cap=1000.0, pools={"anthropic": 400.0, "openai-codex": 0.0, "xai-oauth": None})
    level = T.effective(tmp_path, None, NOW)
    b = T.budget(level, NOW)
    assert b["state"] == "fresh" and b["budget_per_s"] == 1000.0
    assert b["cap_per_s"] == pytest.approx(750.0)
    assert b["pool_caps"] == {"anthropic": pytest.approx(300.0), "openai-codex": 0.0, "xai-oauth": None}
    gate, _ = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic")[1] == pytest.approx(20_000 / 300.0)
    assert T.status(tmp_path, db, NOW)["cap_per_s"] == pytest.approx(750.0)
    _auto(tmp_path, percent=0)  # 0 % in budget mode is the full budget, not "off"
    assert T.status(tmp_path, db, NOW)["cap_per_s"] == pytest.approx(1000.0)
    _auto(tmp_path, percent=100)
    assert T.status(tmp_path, db, NOW)["cap_per_s"] == 0.0 and gate.plan(NOW, "openrouter") == ("hold",)


def _models(root, rows, me="hub"):
    """A series with per-model rows: ``[(gateway, model, minute, tokens)]`` (``tokens_io:<model>``)."""
    db = _series(root, me=me)
    st = Store(db)
    for gateway, model, minute, value in rows:
        st.put(gateway, f"tokens_io:{model}", [(minute, value)])
    return db


def test_start_gate_holds_when_governed_pools_are_over_their_scaled_caps(tmp_path):
    # anthropic 400/s + codex 200/s = 600/s on governed pools, against (400 + 600) x 0.5 = 500/s
    rows = [("hub", "claude-opus-5-5", END - 60, 18_000.0), ("worker-1", "claude-sonnet-5-5", END - 60, 6_000.0),
            ("hub", "gpt-6-astra", END - 60, 12_000.0)]
    db = _models(tmp_path / "o", rows)
    _auto(tmp_path / "o", percent=50, cap=1000.0)
    assert _gate(tmp_path / "o", db)[0].start_gate(**CARD) == {"hold": True, "reason": "token_throttle"}
    b = T.status(tmp_path / "o", db, NOW)["budget"]
    assert b["governed_rate_per_s"] == pytest.approx(600.0) and b["governed_cap_per_s"] == pytest.approx(500.0)
    under = _models(tmp_path / "u", rows)
    _auto(tmp_path / "u", percent=0, cap=1000.0)  # full budget: 1000/s
    assert _gate(tmp_path / "u", under)[0].start_gate(**CARD) is None


def test_start_gate_ignores_ungoverned_and_chat_traffic(tmp_path):
    # 9M tokens/min on providers without a pool cap (an OpenRouter chat model, unattributed tokens), 60/s governed
    rows = [("hub", "deepseek-v4", END - 60, 9e6), ("hub", "unreported", END - 60, 9e6),
            ("mini", "gemini-3-pro", END - 60, 9e6), ("hub", "claude-opus-5-5", END - 60, 3_600.0)]
    db = _models(tmp_path, rows)
    Store(db).put("hub", "tokens_io", [(END - 60, 27e6 + 3_600.0)])  # the scalar the old gate compared
    _auto(tmp_path, cap=1000.0, pools={"anthropic": 100.0})
    out = T.status(tmp_path, db, NOW)
    assert out["rate_per_s"] > 400_000 and out["budget"]["governed_rate_per_s"] == pytest.approx(60.0)
    assert out["holding"] is False and _gate(tmp_path, db)[0].start_gate(**CARD) is None


def test_reserve_plus_unknown_pools_never_hold_card_starts(tmp_path):
    # the global budget is 0 (anthropic at reserve, codex rate unknown) and the fleet is busy: starts still go
    rows = [("hub", "claude-opus-5-5", END - 60, 60_000.0), ("hub", "gpt-6-astra", END - 60, 60_000.0)]
    db = _models(tmp_path, rows)
    Store(db).put("hub", "tokens_io", [(END - 60, 120_000.0)])  # 2000/s over a global budget of 0
    _auto(tmp_path, cap=0.0, pools={"anthropic": 0.0, "openai-codex": None})
    out = T.status(tmp_path, db, NOW)
    assert out["cap_per_s"] == 0.0 and out["holding"] is False
    assert out["budget"]["governed_cap_per_s"] is None
    gate = _gate(tmp_path, db)[0]
    assert gate.start_gate(**CARD) is None
    assert gate.plan(NOW, "anthropic") == ("hold",)  # the reserve hold stays on that pool's calls
    _auto(tmp_path, cap=0.0, pools={})  # no pools at all
    assert T.status(tmp_path, db, NOW)["holding"] is False
    _auto(tmp_path, percent=100, cap=0.0, pools={"anthropic": 0.0})  # the slider at 100 % still stops starts
    assert T.status(tmp_path, db, NOW)["holding"] is True


def test_provider_rates_attribute_models_like_core(tmp_path):
    rows = [("hub", "claude-opus-5-5", END - 60, 6_000.0), ("hub", "anthropic/claude-haiku-5-5", END - 120, 6_000.0),
            ("hub", "gpt-6-luna", END - 60, 1_200.0), ("hub", "grok-4.7", END - 60, 600.0),
            ("hub", "unreported", END - 60, 9e9), ("hub", "claude-opus-5-5", END, 9e9)]  # current minute: excluded
    db = _models(tmp_path, rows)
    rates = T.provider_rates(db, NOW)
    # two sampled minutes (120 s): anthropic 12k, codex 1.2k, xai 0.6k
    assert rates == {"anthropic": pytest.approx(100.0), "openai-codex": pytest.approx(10.0),
                     "xai-oauth": pytest.approx(5.0)}
    assert T.provider_rates(tmp_path / "missing.sqlite", NOW) is None
    assert T.provider_rates(_series(tmp_path / "e"), NOW) is None


def test_stale_budget_keeps_its_caps_between_20_and_60_minutes(tmp_path):
    db = _series(tmp_path, [("hub", END - 60, 48_000.0)])  # 800/s
    _auto(tmp_path, cap=500.0, pools={"anthropic": 0.0}, budget_at=NOW - 30 * 60)
    out = T.status(tmp_path, db, NOW)
    assert out["mode"] == "auto" and out["budget"]["state"] == "stale"
    assert out["cap_per_s"] == 500.0 and out["budget"]["pool_caps"] == {"anthropic": 0.0}
    assert _gate(tmp_path, db)[0].plan(NOW, "anthropic") == ("hold",)
    _auto(tmp_path, cap=500.0, pools={"anthropic": 0.0}, budget_at=NOW - 20 * 60)  # exactly 20 min: still fresh
    assert T.status(tmp_path, db, NOW)["budget"]["state"] == "fresh"
    _auto(tmp_path, cap=500.0, pools={"anthropic": 0.0}, budget_at=NOW - 60 * 60)  # exactly 60 min: kept
    assert T.status(tmp_path, db, NOW)["budget"]["state"] == "stale"
    assert _gate(tmp_path, db)[0].plan(NOW, "anthropic") == ("hold",)


def test_budget_older_than_60_minutes_fails_open_and_says_so(tmp_path):
    db = _series(tmp_path, [("hub", END - 60, 48_000.0)])  # 800/s, far over the old 500/s cap
    _auto(tmp_path, cap=500.0, pools={"anthropic": 0.0}, budget_at=NOW - 60 * 60 - 1)
    out = T.status(tmp_path, db, NOW)
    assert out["budget"]["state"] == "expired" and out["budget"]["age_s"] == pytest.approx(3601)
    assert out["cap_per_s"] is None and out["holding"] is False and out["budget"]["pool_caps"] == {}
    gate, clock = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic") == ("pass",) and gate.start_gate(**CARD) is None
    _auto(tmp_path, cap=500.0, pools={"anthropic": 0.0}, budget_at=None)  # no budget_at at all: expired too
    assert T.status(tmp_path, db, NOW)["budget"]["state"] == "expired"


def test_legacy_level_without_mode_behaves_as_before(tmp_path):
    db = _series(tmp_path, [("hub", END - 60, 48_000.0)])
    T.level_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    legacy = {"percent": 50, "cap_per_s": 1000.0, "set_at": NOW - 100,
              "pool_caps": {"anthropic": 0.0}, "budget_at": NOW}  # pool caps without mode: ignored
    T.level_path(tmp_path).write_text(json.dumps(legacy))
    assert T.effective(tmp_path, None, NOW) == {"percent": 50.0, "cap_per_s": 1000.0, "set_at": NOW - 100,
                                                "source": None}
    gate, _ = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic") == gate.plan(NOW) == ("pace", pytest.approx(20.0), NOW - 100)
    out = T.status(tmp_path, db, NOW)
    assert out["mode"] == "manual" and out["budget"] is None and out["cap_per_s"] == 1000.0
    T.level_path(tmp_path).write_text(json.dumps({**legacy, "mode": "manual"}))
    # a person's cap (``mode: "manual"``) carries the budget's pool caps it was set under: a reserve still holds
    assert T.status(tmp_path, db, NOW)["mode"] == "manual" and gate.plan(NOW + 31, "anthropic")[0] == "hold"
    assert gate.plan(NOW + 31, "openai-codex")[0] == "pace"


def test_malformed_pool_caps_values_are_ignored(tmp_path):
    db = _series(tmp_path)
    raw = {"anthropic": "0", "openai-codex": -5, "xai-oauth": float("nan"), "ok": 200.0, "nul": None, "t": True,
           "bad key!": 0.0, "x" * 65: 0.0, "": 0.0, "-lead": 0.0, "clé": 0.0}
    assert T.pool_caps_of(raw) == {"ok": 200.0, "nul": None}
    assert T.pool_caps_of([0.0]) == {} and T.pool_caps_of("anthropic") == {}
    T.level_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    T.level_path(tmp_path).write_text(json.dumps({"mode": "auto", "percent": 0, "cap_per_s": 1000.0,
                                                  "pool_caps": {"anthropic": "0", "openai-codex": -1, "ok": 200.0},
                                                  "budget_at": NOW - 60, "set_at": NOW - 100}))
    gate, _ = _gate(tmp_path, db)
    assert gate.plan(NOW, "anthropic") == ("pass",) and gate.plan(NOW, "openai-codex") == ("pass",)
    assert gate.plan(NOW, "ok")[0] == "pace"
    _auto(tmp_path, pools="not a map")
    assert gate.plan(NOW + 31, "anthropic") == ("pass",)


def test_published_budget_level_is_obeyed_from_another_gateway(tmp_path):
    import sqlite3
    hub = tmp_path / "hub.db"
    c = sqlite3.connect(hub)
    c.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, "
              "updated_at INTEGER, PRIMARY KEY (gw, coll, key))")
    c.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    block = {"mode": "auto", "percent": 0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 0.0},
             "budget_at": NOW - 60, "set_at": NOW - 10}
    c.execute("INSERT INTO replica VALUES ('hub', 'gateway', 'info', 1, 1, ?, ?)",
              (json.dumps({"throttle": block}), (NOW - 5) * 1000))
    c.commit()
    c.close()
    db = _series(tmp_path / "w", me="worker-1")
    level = T.effective(tmp_path / "w", hub, NOW)
    assert level["source"] == "hub" and level["mode"] == "auto" and level["pool_caps"] == {"anthropic": 0.0}
    gate = T.Gate(tmp_path / "w", db, hub_db=hub)
    assert gate.plan(NOW, "anthropic") == ("hold",)


# --- the publishing side -----------------------------------------------------------------------------------------

def test_budget_of_v2_maps_reserve_unknown_and_malformed():
    v2 = _v2(pools={"anthropic": {"rate_tokens_per_s": 400.0, "at_reserve": True},
                    "openai-codex": {"rate_tokens_per_s": None, "at_reserve": False},
                    "xai-oauth": {"rate_tokens_per_s": 50.0, "at_reserve": False},
                    "weird": {"rate_tokens_per_s": "fast"}, "bad key!": {"rate_tokens_per_s": 1.0}, "list": [1]})
    assert T.budget_of_v2(v2) == {"cap_per_s": 1000.0, "budget_at": NOW - 30,
                                  "pool_caps": {"anthropic": 0.0, "openai-codex": None, "xai-oauth": 50.0,
                                                "weird": None}}
    assert T.budget_of_v2(_v2(total=None))["cap_per_s"] is None
    for bad in (None, [], "v2", {"pools": {}}, _v2(computed_at="now"), _v2(computed_at=float("nan")),
                {**_v2(), "pools": []}, _v2(total=-1.0), _v2(total="lots")):
        assert T.budget_of_v2(bad) is None


def test_publish_writes_auto_level_keeping_the_percent(tmp_path):
    T.write_level(tmp_path, 30, now=NOW - 500, reference=2000.0)  # the user's slider: 30 %
    pacing = _pacing_file(tmp_path, _v2())
    assert T.publish_budget(tmp_path, None, pacing, NOW) is True
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data == {"mode": "auto", "percent": 30.0, "cap_per_s": 1000.0,
                    "pool_caps": {"anthropic": 400.0, "openai-codex": 600.0}, "budget_at": NOW - 30,
                    "set_at": NOW - 500 + 0.001}
    assert T.status(tmp_path, tmp_path / "none.sqlite", NOW)["cap_per_s"] == pytest.approx(700.0)


def test_publish_hysteresis_5_percent_or_15_minutes(tmp_path):
    pacing = _pacing_file(tmp_path, _v2())
    assert T.publish_budget(tmp_path, None, pacing, NOW) is True
    first = json.loads(T.level_path(tmp_path).read_text())
    os.utime(T.level_path(tmp_path), (NOW, NOW))
    # every cap within 5 %: no rewrite, even with a newer computed_at
    _pacing_file(tmp_path, _v2(computed_at=NOW + 30, total=1049.0,
                               pools={"anthropic": {"rate_tokens_per_s": 419.0}, "openai-codex": {"rate_tokens_per_s": 630.0}}))
    assert T.publish_budget(tmp_path, None, pacing, NOW + 60) is False
    assert json.loads(T.level_path(tmp_path).read_text()) == first
    # one pool moves by more than 5 %: rewrite at once
    _pacing_file(tmp_path, _v2(computed_at=NOW + 90, total=1000.0,
                               pools={"anthropic": {"rate_tokens_per_s": 421.0}, "openai-codex": {"rate_tokens_per_s": 600.0}}))
    assert T.publish_budget(tmp_path, None, pacing, NOW + 120) is True
    second = json.loads(T.level_path(tmp_path).read_text())
    assert second["pool_caps"]["anthropic"] == 421.0 and second["set_at"] == pytest.approx(first["set_at"] + 0.001)
    os.utime(T.level_path(tmp_path), (NOW + 120, NOW + 120))
    # unchanged caps: no rewrite before 15 minutes, a rewrite at 15 minutes
    _pacing_file(tmp_path, _v2(computed_at=NOW + 800, total=1000.0,
                               pools={"anthropic": {"rate_tokens_per_s": 421.0}, "openai-codex": {"rate_tokens_per_s": 600.0}}))
    assert T.publish_budget(tmp_path, None, pacing, NOW + 120 + 899) is False
    assert T.publish_budget(tmp_path, None, pacing, NOW + 120 + 900) is True
    assert json.loads(T.level_path(tmp_path).read_text())["budget_at"] == NOW + 800
    os.utime(T.level_path(tmp_path), (NOW + 1020, NOW + 1020))
    # a pool appears / a cap turns null: rewrite
    _pacing_file(tmp_path, _v2(computed_at=NOW + 1030, pools={"anthropic": {"rate_tokens_per_s": None},
                                                             "openai-codex": {"rate_tokens_per_s": 600.0}}))
    assert T.publish_budget(tmp_path, None, pacing, NOW + 1040) is True
    os.utime(T.level_path(tmp_path), (NOW + 1040, NOW + 1040))
    _pacing_file(tmp_path, _v2(computed_at=NOW + 1050, pools={"anthropic": {"rate_tokens_per_s": None},
                                                             "openai-codex": {"rate_tokens_per_s": 600.0},
                                                             "xai-oauth": {"rate_tokens_per_s": 1.0}}))
    assert T.publish_budget(tmp_path, None, pacing, NOW + 1060) is True


def test_publish_folds_in_a_newer_slider_level_at_once(tmp_path):
    pacing = _pacing_file(tmp_path, _v2())
    assert T.publish_budget(tmp_path, None, pacing, NOW) is True
    os.utime(T.level_path(tmp_path), (NOW, NOW))
    T.write_level(tmp_path, 40, now=NOW + 10, reference=2000.0)  # the user moved the slider on this host
    assert T.publish_budget(tmp_path, None, pacing, NOW + 60) is True  # caps unchanged, but the level is not ours
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["mode"] == "auto" and data["percent"] == 40.0 and data["set_at"] == pytest.approx(NOW + 10.001)


def test_malformed_or_missing_v2_writes_nothing(tmp_path):
    T.write_level(tmp_path, 30, now=NOW - 500)
    before = T.level_path(tmp_path).read_text()
    for content in ("{not json", json.dumps([1]), json.dumps({"version": 2}), json.dumps({"v2": {"pools": {}}}),
                    json.dumps({"v2": _v2(total=-3.0)})):
        (tmp_path / "llm_pacing.json").write_text(content)
        assert T.publish_budget(tmp_path, None, tmp_path / "llm_pacing.json", NOW) is False
    assert T.publish_budget(tmp_path, None, tmp_path / "missing.json", NOW) is False
    assert T.level_path(tmp_path).read_text() == before


def _fake_core(monkeypatch, section):
    pkg = types.ModuleType("hermes_cli")
    tier = types.ModuleType("hermes_cli.kanban_model_tier")
    tier.load_tier_config = lambda: section
    pkg.kanban_model_tier = tier
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_model_tier", tier)


def test_budget_publisher_reads_core_config(monkeypatch):
    _fake_core(monkeypatch, {"llm_pacing": {"budget_publish": True}})
    assert T.budget_publisher() is True
    for section in ({}, {"llm_pacing": {}}, {"llm_pacing": {"budget_publish": "true"}},
                    {"llm_pacing": {"budget_publish": False}}, {"llm_pacing": None}, None):
        _fake_core(monkeypatch, section)
        assert T.budget_publisher() is False


def _one_loop_pass(tmp_path, monkeypatch):
    from throughput_under_test import runway, sampler
    stop = threading.Event()

    class OneTick:
        def __init__(self, root):
            pass

        def tick(self):
            pass

    monkeypatch.setattr(sampler, "Sampler", OneTick)
    monkeypatch.setattr(stop, "wait", lambda timeout=None: True)
    monkeypatch.setattr(runway, "pacing_path", lambda root: tmp_path / "llm_pacing.json")
    sampler._loop(stop, tmp_path)


def test_non_publisher_host_never_writes(tmp_path, monkeypatch):
    _pacing_file(tmp_path, _v2())
    _fake_core(monkeypatch, {"llm_pacing": {"budget_publish": False}})
    _one_loop_pass(tmp_path, monkeypatch)
    assert not T.level_path(tmp_path).exists()
    monkeypatch.delitem(sys.modules, "hermes_cli", raising=False)  # no core at all: not a publisher either
    monkeypatch.delitem(sys.modules, "hermes_cli.kanban_model_tier", raising=False)
    monkeypatch.setattr(T, "budget_publisher", lambda: False)
    _one_loop_pass(tmp_path, monkeypatch)
    assert not T.level_path(tmp_path).exists()


def test_publisher_host_writes_from_the_sampler_loop_and_acknowledges_it(tmp_path, monkeypatch):
    _pacing_file(tmp_path, _v2())
    _fake_core(monkeypatch, {"llm_pacing": {"budget_publish": True}})
    _one_loop_pass(tmp_path, monkeypatch)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["mode"] == "auto" and data["pool_caps"] == {"anthropic": 400.0, "openai-codex": 600.0}
    assert json.loads(T.seen_path(tmp_path).read_text())["set_at"] == data["set_at"]


# --- the slider on a budget level ---------------------------------------------------------------------------------

def test_slider_move_on_a_budget_level_keeps_the_pool_caps(tmp_path):
    db = _series(tmp_path)
    _auto(tmp_path, percent=0, cap=1000.0, pools={"anthropic": 0.0, "openai-codex": 600.0}, budget_at=NOW - 60)
    level = T.effective(tmp_path, None, NOW)
    assert T.set_level(tmp_path, level, 40, NOW + 1, reference=5000.0) == 40.0
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data == {"mode": "auto", "percent": 40.0, "cap_per_s": 1000.0,
                    "pool_caps": {"anthropic": 0.0, "openai-codex": 600.0}, "budget_at": NOW - 60, "set_at": NOW + 1}
    gate, _ = _gate(tmp_path, db)
    assert gate.plan(NOW + 1, "anthropic") == ("hold",)  # the reserve hold survives the slider
    assert gate.plan(NOW + 1, "openai-codex")[1] == pytest.approx(20_000 / 360.0)


def test_slider_move_on_a_manual_level_is_unchanged(tmp_path):
    T.write_level(tmp_path, 10, now=NOW - 100, reference=2000.0)
    level = T.effective(tmp_path, None, NOW)
    T.set_level(tmp_path, level, 30, NOW, reference=2000.0)
    assert json.loads(T.level_path(tmp_path).read_text()) == {"percent": 30.0, "cap_per_s": pytest.approx(1400.0),
                                                              "set_at": NOW}
    T.set_level(tmp_path, {"percent": 0.0, "cap_per_s": None, "set_at": 0.0, "source": None}, 0, NOW + 1)
    assert json.loads(T.level_path(tmp_path).read_text()) == {"percent": 0.0, "cap_per_s": None, "set_at": NOW + 1}


def test_slider_move_on_an_expired_budget_level_returns_to_manual(tmp_path):
    _auto(tmp_path, percent=0, cap=1000.0, pools={"anthropic": 0.0}, budget_at=NOW - T.BUDGET_EXPIRE_S - 60)
    level = T.effective(tmp_path, None, NOW)
    T.set_level(tmp_path, level, 30, NOW, reference=2000.0)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert "mode" not in data and "pool_caps" not in data
    assert data["percent"] == 30.0 and data["cap_per_s"] == pytest.approx(1400.0)


# --- the budget pacing switch: an explicit manual level is pacing off ------------------------------------------

def _replica(path, blocks, me=None):
    """A hermes-hub replica holding each gateway's published throttle block."""
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT, "
              "updated_at INTEGER, PRIMARY KEY (gw, coll, key))")
    c.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    if me:
        c.execute("INSERT INTO meta VALUES ('gateway', ?)", (me,))
    for gw, block in blocks.items():
        c.execute("INSERT INTO replica VALUES (?, 'gateway', 'info', 1, 1, ?, ?)",
                  (gw, json.dumps({"throttle": block}), (NOW - 5) * 1000))
    c.commit()
    c.close()
    return path


def test_publisher_skips_an_explicit_manual_level_and_folds_a_legacy_one(tmp_path):
    pacing = _pacing_file(tmp_path, _v2())
    assert T.publish_budget(tmp_path, None, pacing, NOW)
    off = {"mode": "manual", "percent": 30.0, "cap_per_s": 1400.0, "set_at": NOW + 10}
    T._write_json(T.level_path(tmp_path), off)
    user = T.user_cap_path(tmp_path).read_text()
    for t in (NOW + 60, NOW + 20 * 60, NOW + 2 * 3600):  # every pass, however long it stays off
        assert T.publish_budget(tmp_path, None, pacing, t) is False
    assert json.loads(T.level_path(tmp_path).read_text()) == off
    assert T.user_cap_path(tmp_path).read_text() == user  # not folded as a person's cap either
    legacy = {"percent": 30.0, "cap_per_s": 1400.0, "set_at": NOW + 3 * 3600}  # no mode: folded as before
    T._write_json(T.level_path(tmp_path), legacy)
    assert T.publish_budget(tmp_path, None, pacing, NOW + 3 * 3600 + 30) is True
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["mode"] == "auto" and data["percent"] == pytest.approx(30.0)  # 30 % held back of the budget
    assert data["set_at"] == pytest.approx(NOW + 3 * 3600 + 0.001) and data["pool_caps"]


def test_publisher_skips_pacing_switched_off_on_another_gateway(tmp_path):
    hub_root, pacing = tmp_path / "hub", _pacing_file(tmp_path / "hub", _v2())
    assert T.publish_budget(hub_root, None, pacing, NOW)
    off = {"mode": "manual", "percent": 0.0, "cap_per_s": None, "set_at": NOW + 10}
    hub_db = _replica(tmp_path / "hub.db", {"laptop": off}, me="hub")
    assert T.effective(hub_root, hub_db, NOW + 60)["source"] == "laptop"
    assert T.publish_budget(hub_root, hub_db, pacing, NOW + 60) is False
    assert json.loads(T.level_path(hub_root).read_text())["set_at"] == NOW  # its own budget level, untouched
    # a person's cap set on the laptop while pacing was on carries pool_caps: still folded in (lower of the two)
    cap = {"mode": "manual", "percent": 75.0, "cap_per_s": 250.0, "pool_caps": {}, "set_at": NOW + 20}
    hub_db.unlink()
    _replica(hub_db, {"laptop": cap}, me="hub")
    assert T.publish_budget(hub_root, hub_db, pacing, NOW + 120) is True
    level = T.effective(hub_root, hub_db, NOW + 120)
    assert level["mode"] == "auto" and T.budget(level, NOW + 120)["cap_per_s"] == pytest.approx(250.0)


def test_set_pacing_off_then_on_round_trips_through_the_publisher(tmp_path):
    db = _series(tmp_path)
    pacing = _pacing_file(tmp_path, _v2(pools={"anthropic": {"rate_tokens_per_s": 0.0, "at_reserve": True},
                                               "openai-codex": {"rate_tokens_per_s": 600.0, "at_reserve": False}}))
    assert T.publish_budget(tmp_path, None, pacing, NOW)
    T.set_level(tmp_path, T.effective(tmp_path, None, NOW + 5), 20, NOW + 5)  # hold back 20 %
    level = T.effective(tmp_path, None, NOW + 20)
    assert level["mode"] == "auto" and level["percent"] == 20.0
    stamp = T.set_pacing(tmp_path, level, False, NOW + 20, reference=2000.0)
    assert stamp == NOW + 20
    # the absolute cap in force is kept (20 % held back of a 1000 tokens/s budget), not 20 % of the 2000 reference
    assert json.loads(T.level_path(tmp_path).read_text()) == {"mode": "manual", "percent": pytest.approx(20.0),
                                                              "cap_per_s": pytest.approx(800.0), "set_at": NOW + 20}
    level = T.effective(tmp_path, None, NOW + 21)
    assert T.pacing_off(level) and T.set_pacing(tmp_path, level, False, NOW + 21) is None  # already off: no write
    assert T.publish_budget(tmp_path, None, pacing, NOW + 80) is False
    gate, _ = _gate(tmp_path, db)
    plan = gate.plan(NOW + 80, "anthropic")  # no pool caps: the reserve hold is gone, the manual cap paces
    assert plan[0] == "pace" and len(plan) == 3 and plan[1] == pytest.approx(T.DEFAULT_CALL_TOKENS / 800.0)
    assert T.status(tmp_path, db, NOW + 80)["pacing"]["state"] == "off"
    stamp = T.set_pacing(tmp_path, T.effective(tmp_path, None, NOW + 90), True, NOW + 90, reference=2000.0)
    assert json.loads(T.level_path(tmp_path).read_text()) == {"percent": pytest.approx(20.0),
                                                              "cap_per_s": pytest.approx(1600.0), "set_at": NOW + 90}
    assert stamp == NOW + 90 and T.status(tmp_path, db, NOW + 95)["pacing"]["state"] == "pending"
    assert T.set_pacing(tmp_path, T.effective(tmp_path, None, NOW + 95), True, NOW + 95) is None  # already on its way
    assert T.publish_budget(tmp_path, None, pacing, NOW + 100) is True
    level = T.effective(tmp_path, None, NOW + 100)
    assert level["mode"] == "auto" and level["percent"] == pytest.approx(20.0)
    assert level["pool_caps"] == {"anthropic": 0.0, "openai-codex": 600.0} and level["set_at"] == NOW + 90.001
    assert gate.plan(NOW + 131, "anthropic") == ("hold",)  # the reserve hold is back
    assert T.status(tmp_path, db, NOW + 100)["pacing"] == {"state": "on", "publisher": "hub", "budget_seen": True}
    assert T.set_pacing(tmp_path, level, True, NOW + 110) is None  # on a live budget level: nothing to do


def test_set_pacing_stamps_after_the_level_in_force_and_validates(tmp_path):
    _auto(tmp_path, set_at=NOW + 100)  # a level stamped ahead of this clock
    level = T.effective(tmp_path, None, NOW)
    assert T.set_pacing(tmp_path, level, False, NOW) == pytest.approx(NOW + 100.002)
    for bad in ("false", 0, 1, None):
        with pytest.raises(ValueError):
            T.set_pacing(tmp_path, T.effective(tmp_path, None, NOW), bad, NOW)


def test_slider_and_a_persons_cap_on_an_off_level_keep_pacing_off(tmp_path):
    db = _series(tmp_path)
    pacing = _pacing_file(tmp_path, _v2())
    _auto(tmp_path)
    T.set_pacing(tmp_path, T.effective(tmp_path, None, NOW), False, NOW, reference=2000.0)
    T.set_level(tmp_path, T.effective(tmp_path, None, NOW + 1), 40, NOW + 1, reference=2000.0)
    assert json.loads(T.level_path(tmp_path).read_text()) == {"mode": "manual", "percent": 40.0,
                                                              "cap_per_s": pytest.approx(1200.0), "set_at": NOW + 1}
    assert T.publish_budget(tmp_path, None, pacing, NOW + 30) is False
    gate, _ = _gate(tmp_path, db)
    T.set_level(tmp_path, T.effective(tmp_path, None, NOW + 2), 100, NOW + 2, reference=2000.0)
    assert gate.plan(NOW + 2, "anthropic") == ("hold",) and T.status(tmp_path, db, NOW + 2)["holding"] is True
    T.set_level(tmp_path, T.effective(tmp_path, None, NOW + 3), 0, NOW + 3, reference=2000.0)
    assert gate.plan(NOW + 3, "anthropic") == ("pass",)
    assert T.pacing_off(T.effective(tmp_path, None, NOW + 3))
    T.write_cap(tmp_path, 300.0, NOW + 4, T.effective(tmp_path, None, NOW + 4))  # a person's cap while off
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data["mode"] == "manual" and "pool_caps" not in data and data["cap_per_s"] == 300.0
    assert T.publish_budget(tmp_path, None, pacing, NOW + 60) is False
    assert T.status(tmp_path, db, NOW + 60)["pacing"]["state"] == "off"


def test_a_persons_cap_set_while_pacing_is_on_is_marked_for_the_fold(tmp_path):
    for level in (None, {"percent": 0.0, "cap_per_s": None, "set_at": 0.0, "source": None}):
        T.write_cap(tmp_path, 300.0, NOW, level)
        data = json.loads(T.level_path(tmp_path).read_text())
        assert data["mode"] == "manual" and data["pool_caps"] == {} and not T.pacing_off(T.read_record(tmp_path))


def test_status_pacing_states(tmp_path):
    db = _series(tmp_path)
    pacing = lambda now: T.status(tmp_path, db, now)["pacing"]  # noqa: E731
    # never set: no budget level anywhere (the panel hides the switch)
    assert pacing(NOW) == {"state": "unavailable", "publisher": None, "budget_seen": False}
    _auto(tmp_path)
    assert pacing(NOW) == {"state": "on", "publisher": "hub", "budget_seen": True}
    _auto(tmp_path, budget_at=NOW - 30 * 60)
    assert pacing(NOW) == {"state": "on", "publisher": "hub", "budget_seen": True}  # stale: its caps are kept
    _auto(tmp_path, budget_at=NOW - T.BUDGET_EXPIRE_S - 1)
    # the publisher stopped; it existed within the last day
    assert pacing(NOW) == {"state": "unavailable", "publisher": None, "budget_seen": True}
    _auto(tmp_path, budget_at=NOW - T.BUDGET_SEEN_S - 1)
    assert pacing(NOW)["budget_seen"] is False
    T._write_json(T.level_path(tmp_path), {"mode": "manual", "percent": 0, "cap_per_s": None, "set_at": NOW - 9000})
    assert pacing(NOW) == {"state": "off", "publisher": None, "budget_seen": True}  # off for good, however old
    for record in ({"percent": 10, "cap_per_s": None}, {"mode": "manual", "percent": 10, "pool_caps": {}}):
        T._write_json(T.level_path(tmp_path), {**record, "set_at": NOW - 60})
        assert pacing(NOW) == {"state": "pending", "publisher": None, "budget_seen": False}
        # nothing folded it in, and no budget was ever seen
        assert pacing(NOW + T.PENDING_S) == {"state": "unavailable", "publisher": None, "budget_seen": False}


def test_status_pacing_names_the_publisher_and_waits_longer_while_one_is_seen(tmp_path):
    worker = tmp_path / "w"
    db = _series(worker, me="worker-1")
    budget = {"mode": "auto", "percent": 0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 400.0},
              "budget_at": NOW - 60, "set_at": NOW - 100}
    hub_db = _replica(tmp_path / "hub.db", {"hub": budget, "laptop": {**budget, "budget_at": NOW - 600}})
    assert T.status(worker, db, NOW, hub_db)["pacing"] == {"state": "on", "publisher": "hub", "budget_seen": True}
    T._write_json(T.level_path(worker), {"mode": "manual", "percent": 0, "cap_per_s": None, "set_at": NOW - 50})
    assert T.status(worker, db, NOW, hub_db)["pacing"] == {"state": "off", "publisher": "hub", "budget_seen": True}
    T._write_json(T.level_path(worker), {"percent": 0, "cap_per_s": None, "set_at": NOW - 50})
    later = NOW - 50 + T.PENDING_S + 60  # past PENDING_S, a live publisher seen: the link may still be carrying it
    assert T.status(worker, db, later, hub_db)["pacing"] == {"state": "pending", "publisher": "hub",
                                                            "budget_seen": True}
    assert T.status(worker, db, NOW - 50 + T.PENDING_MAX_S, hub_db)["pacing"]["state"] == "unavailable"


def test_on_after_more_than_an_hour_off_stays_pending_while_the_fold_travels(tmp_path):
    """Pacing was off for two hours (switched off on a laptop): the hub's last budget level has expired. A click on On
    on a worker stays pending for PENDING_MAX_S, the time a fold can take over the link, not PENDING_S."""
    worker = tmp_path / "w"
    db = _series(worker, me="worker-1")
    off_at = NOW - 2 * 3600
    budget = {"mode": "auto", "percent": 0, "cap_per_s": 1000.0, "pool_caps": {"anthropic": 400.0},
              "budget_at": off_at - 60, "set_at": off_at - 30}
    off = {"mode": "manual", "percent": 0, "cap_per_s": None, "set_at": off_at}
    hub_db = _replica(tmp_path / "hub.db", {"hub": budget, "laptop": off})
    assert T.status(worker, db, NOW, hub_db)["pacing"] == {"state": "off", "publisher": None, "budget_seen": True}
    assert T.set_pacing(worker, T.effective(worker, hub_db, NOW), True, NOW) == NOW
    for t in (NOW + 60, NOW + T.PENDING_S + 1, NOW + 8 * 60, NOW + T.PENDING_MAX_S - 1):
        assert T.status(worker, db, t, hub_db)["pacing"]["state"] == "pending", t
    assert T.status(worker, db, NOW + T.PENDING_MAX_S, hub_db)["pacing"]["state"] == "unavailable"
    # the same On where no budget (nor an Off) was seen in the last day: unavailable after PENDING_S
    alone = tmp_path / "alone"
    db2 = _series(alone, me="solo")
    T.set_pacing(alone, {"mode": "manual", "percent": 0.0, "cap_per_s": None, "set_at": NOW - 2 * T.BUDGET_SEEN_S},
                 True, NOW)
    assert T.status(alone, db2, NOW + T.PENDING_S - 1)["pacing"]["state"] == "pending"
    assert T.status(alone, db2, NOW + T.PENDING_S)["pacing"] == {"state": "unavailable", "publisher": None,
                                                                 "budget_seen": False}


def test_off_without_a_reference_paces_at_this_gateways_own_reference(tmp_path):
    bucket = (END - 6 * 3600) // 900 * 900
    db = _series(tmp_path, [("hub", bucket, 1_800_000.0)])  # 24-hour peak: 2000 tokens/s
    T.set_pacing(tmp_path, {"percent": 40.0, "cap_per_s": None, "set_at": NOW - 20, "mode": "manual",
                            "pool_caps": {}}, False, NOW - 10, reference=None)  # set where nothing was measured
    stored = json.loads(T.level_path(tmp_path).read_text())
    assert stored["mode"] == "manual" and stored["percent"] == 40.0 and stored["cap_per_s"] is None
    level = T.effective(tmp_path, None, NOW)
    assert T.pacing_off(level) and T.limit_of(level, 2000.0, NOW) == pytest.approx(1200.0)
    gate, _ = _gate(tmp_path, db)
    plan = gate.plan(NOW, "anthropic")
    assert plan[0] == "pace" and plan[1] == pytest.approx(T.DEFAULT_CALL_TOKENS / 1200.0)
    assert T.status(tmp_path, db, NOW)["cap_per_s"] == pytest.approx(1200.0)
    T._write_json(T.level_path(tmp_path), {"mode": "manual", "percent": 0.0, "cap_per_s": None, "set_at": NOW - 5})
    assert gate.plan(NOW + 31, "anthropic") == ("pass",)  # 0 %: still no cap
    # a person's "no cap" (write_cap None: 0 %, no cap_per_s) is unchanged: no cap
    T.write_cap(tmp_path, None, NOW, None)
    assert T.limit_of(T.read_record(tmp_path), 2000.0, NOW) is None


def test_off_keeps_the_persons_absolute_cap_in_force(tmp_path):
    # elsewhere: a budget level whose percent folds in a person's 250 tokens/s cap; a running burst is not kept
    _auto(tmp_path, percent=75.0, cap=1000.0, burst={"factor": 2.0, "until": NOW + 3600, "set_at": NOW - 10})
    T.set_pacing(tmp_path, T.effective(tmp_path, None, NOW), False, NOW, reference=50_000.0)
    data = json.loads(T.level_path(tmp_path).read_text())
    assert data == {"mode": "manual", "percent": pytest.approx(75.0), "cap_per_s": pytest.approx(250.0), "set_at": NOW}
    # the publish host: a person's cap above the budget (published at 0 %) is its recorded cap, not "no cap"
    host = tmp_path / "hub"
    pacing = _pacing_file(host, _v2())
    T.write_cap(host, 2000.0, NOW)
    assert T.publish_budget(host, None, pacing, NOW + 10)
    level = T.effective(host, None, NOW + 20)
    assert level["mode"] == "auto" and level["percent"] == 0.0
    T.set_pacing(host, level, False, NOW + 20, reference=50_000.0)
    data = json.loads(T.level_path(host).read_text())
    assert data["cap_per_s"] == pytest.approx(2000.0) and 0 < data["percent"] < 1  # older code still enforces it
    assert T.limit_of(T.read_record(host), 50_000.0, NOW + 20) == pytest.approx(2000.0)
    # no person's cap at all: Off from a 0 % budget level is no cap
    _auto(tmp_path / "plain")
    T.set_pacing(tmp_path / "plain", T.effective(tmp_path / "plain", None, NOW), False, NOW, reference=50_000.0)
    assert json.loads(T.level_path(tmp_path / "plain").read_text())["cap_per_s"] is None
