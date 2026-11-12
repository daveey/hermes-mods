"""Bucketing and unit conversion against hand-computed fixtures, and the full /v1/series payload shape."""
import pytest

from throughput_under_test import series as SE
from throughput_under_test.store import Store

NOW = 1_800_000_030  # 30 s into a minute: the current minute is excluded
END = NOW // 60 * 60


def test_bucket_counts_hand_computed():
    since = END - 3600
    per_min = {since: 1, since + 60: 2, since + 299: 99, since + 300: 4, since + 3540: 5, END: 1000}
    # 6h-style 5-minute buckets over the last hour: 12 buckets
    b = SE.bucket_counts(per_min, since, END, 300, True)
    assert len(b) == 12
    assert b[0] == 1 + 2 + 99 and b[1] == 4 and b[11] == 5 and b[2:11] == [0] * 9  # END is outside [since, END)
    # a metric that can be missing: empty buckets are None, not 0
    b = SE.bucket_counts({since: None, since + 60: 3}, since, END, 300, False)
    assert b[0] == 3 and b[1:] == [None] * 11


def test_rate_units_hand_computed():
    # 30 cards in a 5-minute bucket = 0.1/s = 6/m = 360/h
    assert SE.rate(30, 300, "s") == pytest.approx(0.1)
    assert SE.rate(30, 300, "m") == pytest.approx(6.0)
    assert SE.rate(30, 300, "h") == pytest.approx(360.0)
    # 90 000 tokens in one minute = 1500/s = 90k/m = 5.4M/h
    assert SE.rate(90_000, 60, "s") == 1500 and SE.rate(90_000, 60, "h") == 5_400_000
    assert SE.rate(None, 60, "m") is None


def test_ranges_and_bucket_sizes():
    assert SE.RANGES == {"1h": (60, 60), "6h": (360, 300), "24h": (1440, 900), "7d": (10080, 3600)}


def test_store_bucketed_sums_and_missing(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    since = END - 3600
    st.put("hub", "tokens_io", [(since, 10.0), (since + 60, 20.0), (since + 120, None), (since + 900, 7.0)])
    b = st.bucketed(since, END, 900)  # 24h-style 15-minute buckets
    assert b[("hub", "tokens_io")] == [30.0, 7.0, None, None]


def test_build_payload(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    since = END - 3600
    st.put("hub", "tokens_io", [(since + 60 * i, 100.0) for i in range(60)])
    st.put("hub", "tokens_cache", [(since + 60 * i, 1000.0) for i in range(60)])
    st.put("hub", "sess_in", [(END - 60, 2.0)])
    st.put("hub", "sess_out", [(END - 60, 1.0)])
    st.put("worker-1", "tokens_io", [(since, None)])  # reports, but every sample in range is missing
    events = {"created": [(END - 30, "hub"), (END - 90, "worker-2"), (END + 5, "hub")],
              "done": [(END - 10, "ephemeral"), (since - 1, "hub")]}
    p = SE.build(st, events, ["worker-2", "hub", "worker-1", "macbook"], "hub", "1h", now=NOW)
    assert p["now"] == END and p["since"] == since and p["bucket_s"] == 60 and p["recent_minutes"] == 15
    assert p["gateways"] == ["hub", "macbook", "worker-1", "worker-2", "ephemeral", "unattributed"]
    assert set(p["series"]) == {"tokens_io", "tokens_cache", "sess_in", "sess_out", "cards_in", "cards_out"}
    s = p["series"]
    assert s["tokens_io"]["hub"] == [100.0] * 60
    assert s["tokens_io"]["macbook"] is None  # doesn't report: null, never zeros
    assert s["tokens_io"]["worker-1"] == [None] * 60  # reports, but missing in range
    assert s["tokens_cache"]["ephemeral"] is None
    assert s["sess_in"]["hub"][-1] == 2.0 and s["sess_out"]["hub"][-1] == 1.0
    assert s["cards_in"]["hub"][-1] == 1 and sum(s["cards_in"]["hub"]) == 1  # END+5 is in the running minute
    assert s["cards_in"]["worker-2"][-2] == 1
    assert sum(s["cards_out"]["hub"]) == 0 and s["cards_out"]["ephemeral"][-1] == 1
    assert s["cards_in"]["macbook"] == [0] * 60  # cards are known for every gateway
    assert p["recent"]["tokens_io"]["hub"] == [100.0] * 15
    assert p["recent"]["tokens_io"]["macbook"] is None
    assert len(p["recent"]["cards_in"]["hub"]) == 15
    with pytest.raises(ValueError):
        SE.build(st, events, [], "hub", "2h", now=NOW)


def test_build_7d_hourly(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    p = SE.build(st, {"created": [(END - 3600 * 24 * 6 - 1, "hub")]}, ["hub"], "hub", "7d", now=NOW)
    assert p["bucket_s"] == 3600 and len(p["series"]["cards_in"]["hub"]) == 168
    assert sum(p["series"]["cards_in"]["hub"]) == 1
