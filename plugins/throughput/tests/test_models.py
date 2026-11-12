"""Per-model token series: registry ``tokens_by_model`` + local ``session_model_usage`` sampling, scalar parity
(sum of model rows + unreported == scalar, every sampled minute), and the ``by=model`` payload."""
import sqlite3

import pytest

from throughput_under_test import sampler as S
from throughput_under_test import series as SE
from throughput_under_test.store import Store, apportion, model_name

M = 1_800_000_000 // 60 * 60  # a minute boundary
USAGE_DDL = """CREATE TABLE IF NOT EXISTS session_model_usage (
    session_id TEXT NOT NULL, model TEXT NOT NULL, billing_provider TEXT NOT NULL DEFAULT '',
    billing_base_url TEXT NOT NULL DEFAULT '', billing_mode TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task))"""


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "series.sqlite")


def metric_rows(store, gw=None, kind="tokens_io"):
    """{model-or-'': {minute: value}} for the scalar ('') and every ``<kind>:<model>`` metric."""
    out = {}
    with sqlite3.connect(store.path) as c:
        for g, metric, minute, value in c.execute("SELECT gateway, metric, minute, value FROM minutes"):
            if (gw is None or g == gw) and (metric == kind or metric.startswith(kind + ":")):
                out.setdefault(metric[len(kind) + 1:], {})[minute] = value
    return out


def assert_parity(store, gw, kind="tokens_io", expect_models=True):
    """Every minute with a scalar value: model rows + unreported == scalar (within float noise); a NULL scalar
    minute has no non-NULL model row."""
    rows = metric_rows(store, gw, kind)
    scalar = rows.pop("", {})
    checked = 0
    for minute, v in scalar.items():
        parts = [r.get(minute) for r in rows.values()]
        if v is None:
            assert all(p is None for p in parts)
            continue
        if expect_models or "unreported" in rows:
            assert sum(p or 0.0 for p in parts) == pytest.approx(v, abs=1e-6), minute
            assert rows["unreported"][minute] is not None and rows["unreported"][minute] >= 0
            checked += 1
    return checked


# --- helpers units -------------------------------------------------------------------------------------

def test_apportion_residual_scaled_and_empty():
    assert apportion(100, {"a": 30, "b": 20}) == ({"a": 30.0, "b": 20.0}, 50.0)
    parts, un = apportion(60, {"a": 90, "b": 30})  # churn: models exceed the scalar -> scaled to it
    assert parts == {"a": 45.0, "b": 15.0} and un == 0.0
    assert apportion(0, {"a": 5}) == ({"a": 0.0}, 0.0)
    assert apportion(7, None) == ({}, 7.0) and apportion(7, {"a": 0, "b": -1}) == ({}, 7.0)


def test_wire_overflow_names_are_preserved():
    assert model_name("gpt-5") == "gpt-5" and model_name("  ") == "unknown"
    assert model_name("other") == "other" and model_name("unreported") == "unreported"
    assert model_name(model_name("other")) == "other"


def test_registry_models_validation():
    good = {"a": [10, 4], "b": [1.5, 0], "bad1": [True, -1], "bad2": [1], "bad3": "x", 7: [1, 1],
            "inf": [float("inf"), float("nan")]}
    assert S.registry_models(good) == ({"a": 10.0, "b": 1.5}, {"a": 4.0, "b": 0.0})
    assert S.registry_models(None) == ({}, {}) and S.registry_models([1, 2]) == ({}, {})


# --- registry sampling ----------------------------------------------------------------------------------

class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def registry_sampler(tmp_path, metrics):
    reg = {"me": "hub", "machines": {"worker-1": {"metrics": metrics}}}
    clock = Clock(M + 2)
    st = Store(tmp_path / "s.sqlite")
    return S.Sampler(tmp_path / "nohome", st, registry=lambda: reg, clock=clock), metrics, clock


def tick(smp, clock, metrics, minutes, **update):
    clock.t = M + 60 * minutes + 2
    metrics.update(update, at=clock.t)
    smp.tick()


def test_registry_models_parity_and_transitions(tmp_path):
    metrics = {"at": 1, "tokens_io_total": 1000, "tokens_cache_total": 5000}  # no map yet: schema/field missing
    smp, metrics, clock = registry_sampler(tmp_path, metrics)
    smp.tick()
    # minute 1: still no map -> the whole scalar delta is unreported
    tick(smp, clock, metrics, 1, tokens_io_total=1100, tokens_cache_total=5300)
    io = metric_rows(smp.store, "worker-1")
    assert io[""] == {M: 100.0} and io["unreported"] == {M: 100.0} and set(io) == {"", "unreported"}
    # minute 2: map appears (missing -> reported): models are baselines only, delta stays unreported
    tick(smp, clock, metrics, 2, tokens_io_total=1300, tokens_cache_total=5400,
         tokens_by_model={"m-a": [900, 4000], "m-b": [400, 1400]})
    io = metric_rows(smp.store, "worker-1")
    assert io["unreported"][M + 60] == 200.0 and not any(k in io for k in ("m-a", "m-b"))
    # minute 3: m-a +150, m-b +20, new model m-c appears (its first reading is a baseline: tokens unreported)
    tick(smp, clock, metrics, 3, tokens_io_total=1500, tokens_cache_total=5600,
         tokens_by_model={"m-a": [1050, 4100], "m-b": [420, 1450], "m-c": [30, 50]})
    io = metric_rows(smp.store, "worker-1")
    assert io["m-a"] == {M + 120: 150.0} and io["m-b"] == {M + 120: 20.0}
    assert io["unreported"][M + 120] == 30.0 and "m-c" not in io
    # minute 4: m-b disappears from the map; its +10 is lost to unreported, m-c +40
    tick(smp, clock, metrics, 4, tokens_io_total=1560, tokens_cache_total=5700,
         tokens_by_model={"m-a": [1060, 4100], "m-c": [70, 60]})
    io = metric_rows(smp.store, "worker-1")
    assert io["m-a"][M + 180] == 10.0 and io["m-c"] == {M + 180: 40.0}
    assert io["unreported"][M + 180] == pytest.approx(10.0)
    # minute 5: m-b comes back (absent last time: baseline only, no stale-cumulative spike)
    tick(smp, clock, metrics, 5, tokens_io_total=1600, tokens_cache_total=5800,
         tokens_by_model={"m-a": [1060, 4100], "m-b": [999999, 1], "m-c": [100, 60]})
    io = metric_rows(smp.store, "worker-1")
    assert "m-b" not in {k for k, v in io.items() if M + 240 in v and k == "m-b"}
    assert io["m-c"][M + 240] == 30.0 and io["unreported"][M + 240] == pytest.approx(10.0)
    # map vanishes again (reported -> missing)
    tick(smp, clock, metrics, 6, tokens_io_total=1650, tokens_cache_total=5900, tokens_by_model=None)
    assert metric_rows(smp.store, "worker-1")["unreported"][M + 300] == 50.0
    assert assert_parity(smp.store, "worker-1") == 6
    assert assert_parity(smp.store, "worker-1", "tokens_cache") == 6


def test_registry_models_reset_and_churn(tmp_path):
    metrics = {"at": 1, "tokens_io_total": 1000, "tokens_cache_total": 0,
               "tokens_by_model": {"a": [600, 0], "b": [400, 0]}}
    smp, metrics, clock = registry_sampler(tmp_path, metrics)
    smp.tick()
    # churn: deleting an old session lowers b's cumulative, a grows 50 -> scalar net +10: models scaled to 10
    tick(smp, clock, metrics, 1, tokens_io_total=1010, tokens_by_model={"a": [650, 0], "b": [360, 0]})
    io = metric_rows(smp.store, "worker-1")
    assert io[""][M] == 10.0 and io["a"][M] == pytest.approx(10.0) and io["unreported"][M] == pytest.approx(0.0)
    # restart/reset: every counter drops -> negative deltas are zero everywhere
    tick(smp, clock, metrics, 2, tokens_io_total=40, tokens_by_model={"a": [30, 0], "b": [10, 0]})
    io = metric_rows(smp.store, "worker-1")
    assert io[""][M + 60] == 0.0 and io["unreported"][M + 60] == 0.0 and io.get("a", {}).get(M + 60, 0) == 0.0
    # after the reset the next growth is normal again
    tick(smp, clock, metrics, 3, tokens_io_total=100, tokens_by_model={"a": [80, 0], "b": [20, 0]})
    io = metric_rows(smp.store, "worker-1")
    assert io["a"][M + 120] == 50.0 and io["b"][M + 120] == 10.0
    assert assert_parity(smp.store, "worker-1") == 3


def test_registry_models_gap_and_stale_report(tmp_path):
    metrics = {"at": 1, "tokens_io_total": 10, "tokens_cache_total": 0, "tokens_by_model": {"a": [10, 0]}}
    smp, metrics, clock = registry_sampler(tmp_path, metrics)
    smp.tick()
    # a 3-minute gap is spread evenly over scalar AND model rows (same minutes)
    tick(smp, clock, metrics, 3, tokens_io_total=100, tokens_by_model={"a": [70, 0]})
    io = metric_rows(smp.store, "worker-1")
    assert [io["a"][M + 60 * i] for i in range(3)] == [20.0] * 3 and [io[""][M + 60 * i] for i in range(3)] == [30.0] * 3
    assert [io["unreported"][M + 60 * i] for i in range(3)] == [10.0] * 3
    # the same report time again (machine stopped reporting): nothing new
    clock.t = M + 60 * 4 + 2
    smp.tick()  # `at` unchanged from the last tick's
    assert max(io[""]) == M + 120 and max(metric_rows(smp.store, "worker-1")[""]) == M + 120
    # back after 20 minutes: gap stored as missing for the scalar and unreported, never 0
    tick(smp, clock, metrics, 25, tokens_io_total=900, tokens_by_model={"a": [800, 0]})
    io = metric_rows(smp.store, "worker-1")
    assert all(io["unreported"][M + 60 * i] is None for i in range(3, 25))
    assert all(io[""][M + 60 * i] is None for i in range(3, 25))
    assert assert_parity(smp.store, "worker-1") == 3


def test_registry_malformed_maps_never_break_scalar(tmp_path):
    metrics = {"at": 1, "tokens_io_total": 100, "tokens_cache_total": 100, "tokens_by_model": "oops"}
    smp, metrics, clock = registry_sampler(tmp_path, metrics)
    smp.tick()
    tick(smp, clock, metrics, 1, tokens_io_total=150, tokens_cache_total=130,
         tokens_by_model={"a": ["x", None], "b": [1, 2, 3], "c": [10, -5]})
    io = metric_rows(smp.store, "worker-1")
    assert io[""] == {M: 50.0} and io["unreported"] == {M: 50.0}
    assert metric_rows(smp.store, "worker-1", "tokens_cache")["unreported"] == {M: 30.0}


def test_registry_model_cap(tmp_path):
    big = {f"m{i}": [i, i] for i in range(200)}
    metrics = {"at": 1, "tokens_io_total": 1, "tokens_cache_total": 1, "tokens_by_model": big}
    smp, metrics, clock = registry_sampler(tmp_path, metrics)
    smp.tick()
    with sqlite3.connect(smp.store.path) as c:
        n = c.execute("SELECT COUNT(*) FROM counters WHERE gateway='worker-1' AND metric GLOB 'tokens_io:*'").fetchone()[0]
    assert n == 64


# --- local sampling from session_model_usage --------------------------------------------------------------

def make_db(path, sessions, usage=None, ddl=True):
    """sessions: (id, io, cache); usage: (sid, model, io, cache, provider, task) rows. ``usage=None`` = no table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL NOT NULL, "
              "ended_at REAL, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, "
              "cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0)")
    for sid, io, cache in sessions:
        c.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?)", (sid, "cli", M - 600, None, io, 0, cache, 0))
    if usage is not None:
        c.execute(USAGE_DDL)
        for sid, model, io, cache, provider, task in usage:
            c.execute("INSERT OR REPLACE INTO session_model_usage(session_id, model, billing_provider, task, "
                      "input_tokens, cache_read_tokens) VALUES (?,?,?,?,?,?)", (sid, model, provider, task, io, cache))
    c.commit()
    c.close()


def local_sampler(tmp_path):
    root = tmp_path / "home"
    clock = Clock(M + 2)
    smp = S.Sampler(root, Store(tmp_path / "s.sqlite"), registry=lambda: {"me": "hub", "machines": {}}, clock=clock)
    return root, smp, clock


def step(smp, clock, minutes):
    clock.t = M + 60 * minutes + 2
    smp.tick()


def test_local_models_parity_new_deleted_reset(tmp_path):
    root, smp, clock = local_sampler(tmp_path)
    make_db(root / "state.db", [("s1", 300, 30), ("s2", 100, 0)],
            [("s1", "m-a", 200, 20, "p", ""), ("s1", "m-a", 100, 10, "q", "voice_chat"), ("s2", "m-b", 100, 0, "p", "")])
    smp.tick()  # baseline
    assert metric_rows(smp.store, "hub") .get("unreported", {}) == {}
    # s1/m-a +50 (new row under another provider), s3 is new with m-c, s2 deleted
    make_db(root / "state.db", [("s1", 350, 35), ("s3", 40, 0)],
            [("s1", "m-a", 200, 20, "p", ""), ("s1", "m-a", 150, 15, "q", "voice_chat"), ("s3", "m-c", 40, 0, "p", "")])
    c = sqlite3.connect(root / "state.db")
    c.execute("DELETE FROM sessions WHERE id='s2'")
    c.execute("DELETE FROM session_model_usage WHERE session_id='s2'")
    c.commit()
    c.close()
    step(smp, clock, 1)
    io = metric_rows(smp.store, "hub")
    assert io[""][M] == 90.0 and io["m-a"][M] == 50.0 and io["m-c"][M] == 40.0 and io["unreported"][M] == 0.0
    cache = metric_rows(smp.store, "hub", "tokens_cache")
    assert cache[""][M] == 5.0 and cache["m-a"][M] == 5.0
    # a rewrite to lower totals (reset) is 0 for the scalar and the model
    make_db(root / "state.db", [("s1", 100, 10), ("s3", 40, 0)],
            [("s1", "m-a", 100, 10, "p", ""), ("s3", "m-c", 40, 0, "p", "")])
    c = sqlite3.connect(root / "state.db")
    c.execute("DELETE FROM session_model_usage WHERE task='voice_chat'")
    c.commit()
    c.close()
    step(smp, clock, 2)
    io = metric_rows(smp.store, "hub")
    assert io[""][M + 60] == 0.0 and io["unreported"][M + 60] == 0.0 and "m-a" not in {k for k, v in io.items() if v.get(M + 60)}
    assert assert_parity(smp.store, "hub") == 2


def test_local_models_exceeding_scalar_are_scaled(tmp_path):
    """Aux-call ledger rows the sessions totals never counted: the scalar stays the authority."""
    root, smp, clock = local_sampler(tmp_path)
    make_db(root / "state.db", [("s1", 100, 0)], [("s1", "m-a", 100, 0, "p", "")])
    smp.tick()
    make_db(root / "state.db", [("s1", 200, 0)],
            [("s1", "m-a", 200, 0, "p", ""), ("s1", "cheap", 100, 0, "p", "compression")])
    step(smp, clock, 1)
    io = metric_rows(smp.store, "hub")
    assert io[""][M] == 100.0 and io["m-a"][M] + io["cheap"][M] == pytest.approx(100.0)
    assert io["m-a"][M] == pytest.approx(50.0) and io["unreported"][M] == 0.0


def test_local_schema_missing_then_appears_then_unreadable(tmp_path):
    root, smp, clock = local_sampler(tmp_path)
    make_db(root / "state.db", [("s1", 100, 0)], None)  # old schema: no session_model_usage
    smp.tick()
    make_db(root / "state.db", [("s1", 160, 0)], None)
    step(smp, clock, 1)
    io = metric_rows(smp.store, "hub")
    assert io[""][M] == 60.0 and io["unreported"][M] == 60.0 and set(io) == {"", "unreported"}
    # the table appears holding history (100 for s1): a baseline, not a 100-token spike
    make_db(root / "state.db", [("s1", 170, 0)], [("s1", "m-a", 170, 0, "p", "")])
    step(smp, clock, 2)
    io = metric_rows(smp.store, "hub")
    assert io[""][M + 60] == 10.0 and io["unreported"][M + 60] == 10.0 and "m-a" not in io
    make_db(root / "state.db", [("s1", 200, 0)], [("s1", "m-a", 200, 0, "p", "")])
    step(smp, clock, 3)
    io = metric_rows(smp.store, "hub")
    assert io["m-a"] == {M + 120: 30.0} and io["unreported"][M + 120] == 0.0
    # a broken table (columns missing) is "model-incapable" for a minute: history is carried, not re-counted
    c = sqlite3.connect(root / "state.db")
    c.execute("ALTER TABLE session_model_usage RENAME TO smu_old")
    c.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT)")
    c.execute("UPDATE sessions SET input_tokens=210")
    c.commit()
    c.close()
    step(smp, clock, 4)
    io = metric_rows(smp.store, "hub")
    assert io[""][M + 180] == 10.0 and io["unreported"][M + 180] == 10.0
    c = sqlite3.connect(root / "state.db")
    c.execute("DROP TABLE session_model_usage")
    c.execute("ALTER TABLE smu_old RENAME TO session_model_usage")
    c.execute("UPDATE sessions SET input_tokens=215")
    c.execute("UPDATE session_model_usage SET input_tokens=215")
    c.commit()
    c.close()
    step(smp, clock, 5)
    io = metric_rows(smp.store, "hub")
    assert io[""][M + 240] == 5.0 and io["unreported"][M + 240] == 5.0
    assert io["m-a"] == {M + 120: 30.0}  # recovery re-baselines, not a stale multi-interval delta
    assert assert_parity(smp.store, "hub") == 5


def test_local_unreadable_and_new_profile_never_spike_models(tmp_path):
    root, smp, clock = local_sampler(tmp_path)
    prof = root / "profiles" / "coach" / "state.db"
    make_db(root / "state.db", [("s1", 10, 0)], [("s1", "m-a", 10, 0, "p", "")])
    make_db(prof, [("p1", 1_000_000, 0)], [("p1", "m-big", 1_000_000, 0, "p", "")])
    good = prof.read_bytes()
    smp.tick()
    prof.write_bytes(b"not a database" * 100)
    make_db(root / "state.db", [("s1", 20, 0)], [("s1", "m-a", 20, 0, "p", "")])
    step(smp, clock, 1)
    prof.write_bytes(good)
    c = sqlite3.connect(prof)
    c.execute("UPDATE session_model_usage SET input_tokens=1000005")
    c.execute("UPDATE sessions SET input_tokens=1000005")
    c.commit()
    c.close()
    make_db(root / "profiles" / "new" / "state.db", [("n1", 777, 0)], [("n1", "m-new", 777, 0, "p", "")])
    step(smp, clock, 2)
    io = metric_rows(smp.store, "hub")
    assert io["m-a"] == {M: 10.0} and "m-big" not in io
    assert io["unreported"][M + 60] == 5.0  # another profile was sampled during the outage: fresh model baseline
    assert "m-new" not in io  # a profile first seen is only a baseline
    assert io[""] == {M: 10.0, M + 60: 5.0}
    assert assert_parity(smp.store, "hub") == 2


def test_local_same_minute_and_first_pass_write_nothing(tmp_path):
    root, smp, clock = local_sampler(tmp_path)
    make_db(root / "state.db", [("s1", 10, 0)], [("s1", "m-a", 10, 0, "p", "")])
    smp.tick()
    smp.tick()
    assert metric_rows(smp.store, "hub") == {}


def test_local_real_core_session_model_usage(tmp_path):
    """The real ledger written by core's own SessionDB (schema, task column, per-route rows)."""
    hermes_state = pytest.importorskip("hermes_state")
    root, smp, clock = local_sampler(tmp_path)
    root.mkdir(parents=True)
    db = hermes_state.SessionDB(root / "state.db")
    db.create_session("s1", "cli")
    db.update_token_counts("s1", input_tokens=10, output_tokens=5, model="m-a", cache_read_tokens=3, api_call_count=1)
    smp.tick()
    db.update_token_counts("s1", input_tokens=20, output_tokens=2, model="m-a", cache_read_tokens=1, api_call_count=1)
    db.update_token_counts("s1", input_tokens=7, output_tokens=1, model="m-b", api_call_count=1)
    db.close()
    step(smp, clock, 1)
    io = metric_rows(smp.store, "hub")
    assert io[""][M] == 30.0 and io["m-a"][M] == 22.0 and io["m-b"][M] == 8.0 and io["unreported"][M] == 0.0
    cache = metric_rows(smp.store, "hub", "tokens_cache")
    assert cache[""][M] == 1.0 and cache["m-a"][M] == 1.0


# --- series ---------------------------------------------------------------------------------------------

NOW = 1_800_000_030
END = NOW // 60 * 60


def put_models(st, gw, kind, minute, models, unreported=0.0):
    for m, v in models.items():
        st.put(gw, f"{kind}:{m}", [(minute, v)])
    st.put(gw, f"{kind}:unreported", [(minute, unreported)])


def test_by_model_top8_other_unreported_and_union_coverage(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    since = END - 3600
    # hub and worker-1 both sample the same 10 minutes (union coverage is 10, not 20); 10 real models + reserved
    for i in range(10):
        minute = since + 60 * i
        put_models(st, "hub", "tokens_io", minute, {f"m{k}": 100.0 * (k + 1) for k in range(10)}, unreported=5.0)
        put_models(st, "worker-1", "tokens_io", minute, {"m0": 1.0, "other (model)": 1500.0}, unreported=3.0)
    st.put("hub", "tokens_io", [(since + 60 * i, 9999.0) for i in range(10)])  # scalar rows are not model rows
    st.put("hub", "tokens_cache", [(since, 1.0)])  # cache never got model rows
    p = SE.build(st, {}, ["hub", "worker-1"], "hub", "1h", now=NOW, by="model")
    assert p["by"] == "model" and p["gateways"][:2] == ["hub", "worker-1"]
    # window totals: other (model)=15000, m9=1000*10, ... m0=1+100 each -> top 8 are real models by total
    assert p["models"] == ["other (model)", "m9", "m8", "m7", "m6", "m5", "m4", "m3", "other", "unreported"]
    s = p["series"]["tokens_io"]
    assert set(s) == set(p["models"])
    assert all(v == [None] * 60 for v in p["series"]["tokens_cache"].values())  # cache has no model rows at all
    assert s["m9"][0] == 1000.0 and s["m9"][9] == 1000.0 and s["m9"][10] is None  # unsampled minutes: None
    assert s["other"][0] == (1 + 2 + 3) * 100.0 + 1.0  # m0 (hub 100 + worker 1) + m1 + m2
    assert s["unreported"][0] == 8.0
    assert p["sampled"]["tokens_io"]["m9"][:11] == [1] * 10 + [0]  # union: 10 wall minutes, not 20 machine-minutes
    assert p["recent"]["tokens_io"]["m9"] == [None] * 15  # the sampled minutes are older than the last 15
    # parity: the model keys sum to hub+worker rows of the minute (scalar not included here)
    assert sum(s[m][0] for m in p["models"]) == pytest.approx(sum(100.0 * (k + 1) for k in range(10)) + 5 + 1 + 1500 + 3)


def test_by_model_reserved_never_in_top8_and_no_predeploy_fallback(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    since = END - 3600
    # lots of scalar-only history, no model rows at all: by=model has no models, history is not reattributed
    st.put("hub", "tokens_io", [(since + 60 * i, 500.0) for i in range(60)])
    p = SE.build(st, {}, ["hub"], "hub", "1h", now=NOW, by="model")
    assert p["by"] == "model" and p["models"] == [] and p["series"]["tokens_io"] == {} and p["series"]["tokens_cache"] == {}
    # gateway view of the same store is unchanged
    g = SE.build(st, {}, ["hub"], "hub", "1h", now=NOW)
    assert "by" not in g and "models" not in g and g["series"]["tokens_io"]["hub"] == [500.0] * 60
    # only unreported rows (e.g. a worker without a model map): reserved bucket only
    st.put("worker-1", "tokens_io:unreported", [(END - 60, 40.0)])
    p = SE.build(st, {}, ["hub", "worker-1"], "hub", "1h", now=NOW, by="model")
    assert p["models"] == ["unreported"] and p["series"]["tokens_io"]["unreported"][-1] == 40.0
    assert p["series"]["tokens_io"]["unreported"][0] is None


def test_by_model_keeps_gateway_session_card_series(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    since = END - 3600
    put_models(st, "hub", "tokens_io", since, {"a": 5.0})
    st.put("hub", "sess_in", [(END - 60, 2.0)])
    events = {"created": [(END - 30, "hub")], "done": []}
    g = SE.build(st, events, ["hub"], "hub", "1h", now=NOW)
    m = SE.build(st, events, ["hub"], "hub", "1h", now=NOW, by="model")
    for key in ("sess_in", "sess_out", "cards_in", "cards_out"):
        assert m["series"][key] == g["series"][key] and m["recent"][key] == g["recent"][key]
    assert m["gateways"] == g["gateways"] and m["sampled"]["sess_in"] == g["sampled"]["sess_in"]
    assert m["series"]["tokens_io"]["a"][0] == 5.0
    with pytest.raises(ValueError):
        SE.build(st, events, ["hub"], "hub", "1h", now=NOW, by="user")


def test_by_model_7d_hourly_buckets_sum_and_cover(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    for i in range(1, 8):
        put_models(st, "hub", "tokens_io", END - 60 * i, {"a": 2000.0})
    p = SE.build(st, {}, ["hub"], "hub", "7d", now=NOW, by="model")
    assert p["series"]["tokens_io"]["a"][-1] == 14000.0 and p["sampled"]["tokens_io"]["a"][-1] == 7
    assert p["series"]["tokens_io"]["a"][0] is None and p["sampled"]["tokens_io"]["a"][0] == 0


def test_end_to_end_registry_and_local_to_series(tmp_path):
    """Local gateway (models) + a worker without a map in one store: the by=model view sums both, parity holds."""
    root, smp, clock = local_sampler(tmp_path)
    make_db(root / "state.db", [("s1", 100, 0)], [("s1", "m-a", 100, 0, "p", "")])
    worker = {"at": 1, "tokens_io_total": 0, "tokens_cache_total": 0}
    reg = {"me": "hub", "machines": {"worker-1": {"metrics": worker}}}
    smp.registry = lambda: reg
    smp.tick()
    make_db(root / "state.db", [("s1", 160, 0)], [("s1", "m-a", 160, 0, "p", "")])
    worker.update(at=2, tokens_io_total=25)
    step(smp, clock, 1)
    p = SE.build(smp.store, {}, ["hub", "worker-1"], "hub", "1h", now=M + 120 + 5, by="model")
    assert p["models"] == ["m-a", "unreported"]
    assert p["series"]["tokens_io"]["m-a"][-2] == 60.0 and p["series"]["tokens_io"]["unreported"][-2] == 25.0
    g = SE.build(smp.store, {}, ["hub", "worker-1"], "hub", "1h", now=M + 120 + 5)
    assert g["series"]["tokens_io"]["hub"][-2] + g["series"]["tokens_io"]["worker-1"][-2] == 85.0
