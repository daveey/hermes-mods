"""budget_view: the cost of a burst per pool (``project``), the chart's view of ``v2`` and the hourly spend history.
Hand-computed fixtures."""
import pytest

from throughput_under_test import budget_view as B
from throughput_under_test.store import Store

NOW = 1_800_000_030
UNTIL = NOW // 60 * 60  # 1_800_000_000
SINCE = (UNTIL - 7 * 86400) // 3600 * 3600  # 1_799_395_200: 168 whole hours


def _v2(pools, **extra):
    return {"computed_at": NOW - 30, "margin_s": 10800, "global_rate_tokens_per_s": 300.0, "pools": pools, **extra}


def test_project_hand_computed():
    v2 = _v2({
        # 100/s, 1M left, plan ends in 10 000 s. 2x for an hour: +360k tokens, 200/s needs 5000 s to empty: no
        # exhaustion in the burst; 3600 s earlier; after it (1M - 720k) / (10 000 - 3600) = 43.75/s.
        "anthropic": {"rate_tokens_per_s": 100.0, "remaining_tokens": 1_000_000.0, "exhaust_plan_at": NOW + 10_000,
                      "at_reserve": False},
        # 100/s, 300k left, plan says 2500 s: at 200/s it is empty after 1500 s, inside the burst. Extra capped by
        # remaining (300k), earlier capped by the plan's 2500 s, and the re-planned rate is 0 (the plan ends first).
        "openai-codex": {"rate_tokens_per_s": 100.0, "remaining_tokens": 300_000.0, "exhaust_plan_at": NOW + 2500,
                         "at_reserve": False},
    })
    out = B.project(v2, 2.0, 3600.0, NOW)
    assert out["anthropic"] == {"rate_now": 100.0, "extra_tokens": pytest.approx(360_000.0),
                                "exhausts_during_burst_at": None, "earlier_s": pytest.approx(3600.0),
                                "rate_after": pytest.approx(43.75)}
    assert out["openai-codex"] == {"rate_now": 100.0, "extra_tokens": pytest.approx(300_000.0),
                                   "exhausts_during_burst_at": pytest.approx(NOW + 1500.0),
                                   "earlier_s": pytest.approx(2500.0), "rate_after": 0.0}
    # 1.5x for 30 min on the first pool: +90k, 900 s earlier, (1M - 270k) / (10 000 - 1800) = 89.02/s
    half = B.project(v2, 1.5, 1800.0, NOW)["anthropic"]
    assert half["extra_tokens"] == pytest.approx(90_000.0) and half["earlier_s"] == pytest.approx(900.0)
    assert half["rate_after"] == pytest.approx(730_000.0 / 8200.0)


def test_project_after_rate_is_floored_and_plan_end_after_burst():
    # the burst spends everything exactly at its end: nothing left to plan, 0/s
    v2 = _v2({"xai-oauth": {"rate_tokens_per_s": 50.0, "remaining_tokens": 360_000.0, "exhaust_plan_at": NOW + 7200,
                            "at_reserve": False}})
    out = B.project(v2, 2.0, 3600.0, NOW)["xai-oauth"]
    assert out["exhausts_during_burst_at"] is None  # exactly at the end is not before it
    assert out["rate_after"] == 0.0 and out["extra_tokens"] == pytest.approx(180_000.0)
    assert out["earlier_s"] == pytest.approx(3600.0)


def test_project_edge_cases_reserve_null_and_malformed():
    v2 = _v2({"anthropic": {"rate_tokens_per_s": 0.0, "remaining_tokens": 0.0, "at_reserve": True, "until": NOW + 99},
              "openai-codex": {"rate_tokens_per_s": None, "remaining_tokens": None, "at_reserve": False},
              "xai-oauth": {"rate_tokens_per_s": 10.0, "remaining_tokens": None, "at_reserve": False},
              "bad key!": {"rate_tokens_per_s": 1.0, "remaining_tokens": 1.0}, "list": [1]})
    out = B.project(v2, 3.0, 7200.0, NOW)
    assert set(out) == {"anthropic", "openai-codex", "xai-oauth"}
    assert out["anthropic"] == {"rate_now": 0.0, "extra_tokens": 0.0, "exhausts_during_burst_at": None,
                                "earlier_s": 0.0, "rate_after": 0.0}
    nulls = {"rate_now": None, "extra_tokens": None, "exhausts_during_burst_at": None, "earlier_s": None,
             "rate_after": None}
    assert out["openai-codex"] == nulls and out["xai-oauth"] == nulls
    for bad in (None, [], "v2", {}, {"pools": {}}, {"computed_at": "now", "pools": {}},
                {"computed_at": NOW, "pools": []}, {"computed_at": float("nan"), "pools": {}}):
        assert B.project(bad, 2.0, 3600.0, NOW) == {}
    # a pool planning 0/s without being at reserve: a burst of 0 costs nothing
    idle = B.project(_v2({"anthropic": {"rate_tokens_per_s": 0.0, "remaining_tokens": 5.0, "exhaust_plan_at": None}}),
                     2.0, 3600.0, NOW)["anthropic"]
    assert idle == {"rate_now": 0.0, "extra_tokens": 0.0, "exhausts_during_burst_at": None, "earlier_s": 0.0,
                    "rate_after": 0.0}


def test_chart_v2_drops_account_readings_and_unknown_events():
    v2 = _v2({"anthropic": {"rate_tokens_per_s": 40.0, "remaining_tokens": 9.0, "exhaust_plan_at": NOW + 9,
                            "at_reserve": False, "accounts": [{"id": "abc", "used": 3.0}, {"id": "def"}]}},
             events=[{"ts": NOW - 5, "provider": "anthropic", "account": "abc", "type": "reset_early",
                      "detail": {"kind": "five_hour", "used_before": 100.0}},
                     {"ts": NOW - 4, "provider": "anthropic", "type": "weird"}, {"ts": "x", "type": "reset"}, 7],
             curve={"anthropic": [[NOW + 10, 9.0, 1.0], [NOW, 9.0, 0.0], ["bad", 1, 2], [1, 2]],
                    "gone": [[NOW, 1.0, 0.0]]})
    out = B.chart_v2(v2)
    assert out["pools"]["anthropic"]["accounts"] == 2 and "abc" not in repr(out)
    assert out["pools"]["anthropic"]["rate_tokens_per_s"] == 40.0
    assert out["events"] == [{"ts": NOW - 5.0, "provider": "anthropic", "type": "reset_early", "kind": "five_hour"}]
    assert out["curve"] == {"anthropic": [[NOW, 9.0, 0.0], [NOW + 10, 9.0, 1.0]]}
    assert out["computed_at"] == NOW - 30 and out["margin_s"] == 10800
    assert B.chart_v2(None) == {"computed_at": None, "margin_s": None, "global_rate_tokens_per_s": None, "pools": {},
                                "events": [], "curve": {}}


def test_history_hourly_rates_and_cumulative_per_provider(tmp_path):
    st = Store(tmp_path / "series.sqlite")
    put = lambda gw, model, minute, value: st.put(gw, f"tokens_io:{model}", [(minute, value)])  # noqa: E731
    put("hub", "claude-opus-5-5", SINCE + 60, 1800.0)  # bucket 0
    put("hub", "unreported", SINCE + 60, 5.0)
    put("hub", "claude-opus-5-5", UNTIL - 60, 3600.0)  # last bucket
    put("hub", "unreported", UNTIL - 60, 100.0)
    put("worker-1", "gpt-6-astra", UNTIL - 120, 7200.0)
    put("worker-1", "unreported", UNTIL - 120, 0.0)
    put("hub", "claude-opus-5-5", SINCE - 60, 99_999.0)  # before the window
    put("hub", "claude-opus-5-5", UNTIL, 99_999.0)  # the current minute
    st.put("hub", "tokens_io", [(UNTIL - 60, 3700.0)])  # the scalar row is not a model row
    out = B.history(tmp_path / "series.sqlite", NOW)
    assert out["bucket_s"] == 3600 and out["since"] == SINCE and out["until"] == UNTIL
    assert len(out["t"]) == 168 and out["t"][0] == SINCE and out["t"][-1] == UNTIL - 3600
    a, o = out["providers"]["anthropic"], out["providers"]["openai-codex"]
    assert set(out["providers"]) == {"anthropic", "openai-codex"}  # unreported belongs to no provider
    assert a["rate"][0] == pytest.approx(0.5) and o["rate"][0] == 0.0  # bucket 0 was sampled: 0, not missing
    assert a["rate"][1] is None and o["rate"][1] is None  # nobody sampled hour 1
    assert a["rate"][-1] == pytest.approx(1.0) and o["rate"][-1] == pytest.approx(2.0)
    assert a["cumulative"][0] == 1800.0 and a["cumulative"][1] == 1800.0 and a["cumulative"][-1] == 5400.0
    assert o["cumulative"][-1] == 7200.0 and o["cumulative"][-2] == 0.0
    # no series: the buckets, no providers
    empty = B.history(tmp_path / "missing.sqlite", NOW)
    assert empty["providers"] == {} and len(empty["t"]) == 168
