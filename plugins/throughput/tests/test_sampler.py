"""Sampler deltas: normal, counter reset, missing sample, deleted session; local state.db sampling."""
import sqlite3

import pytest

from throughput_under_test import sampler as S
from throughput_under_test.store import Store, counter_delta, spread

M = 1_800_000_000 // 60 * 60  # a minute boundary


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "series.sqlite")


def rows(store, gw, metric):
    with sqlite3.connect(store.path) as c:
        return c.execute("SELECT minute, value FROM minutes WHERE gateway=? AND metric=? ORDER BY minute",
                         (gw, metric)).fetchall()


def test_counter_delta_normal_and_reset():
    assert counter_delta(100, 250) == 150
    assert counter_delta(250, 40) == 0  # reset (or deleted rows): never negative
    assert counter_delta(5, 5) == 0


def test_spread_one_minute_short_gap_long_gap():
    assert spread(M, M + 60, 7) == [(M, 7)]
    assert spread(M, M + 180, 9) == [(M, 3), (M + 60, 3), (M + 120, 3)]
    long = spread(M, M + 600, 100)  # 10 minutes > SPREAD_MAX_MIN: missing, never 0
    assert long == [(M + 60 * i, None) for i in range(10)]
    assert spread(M, M, 5) == []


def test_observe_counter_normal(store):
    store.observe_counter("worker-1", "tokens_io", M, 1000, at=1)
    assert rows(store, "worker-1", "tokens_io") == []  # first reading is only a baseline
    store.observe_counter("worker-1", "tokens_io", M + 60, 1600, at=2)
    store.observe_counter("worker-1", "tokens_io", M + 120, 1700, at=3)
    assert rows(store, "worker-1", "tokens_io") == [(M, 600.0), (M + 60, 100.0)]


def test_observe_counter_reset_is_zero(store):
    store.observe_counter("worker-1", "sess_in", M, 500, at=1)
    store.observe_counter("worker-1", "sess_in", M + 60, 3, at=2)  # gateway restarted / counter reset
    store.observe_counter("worker-1", "sess_in", M + 120, 10, at=3)
    assert rows(store, "worker-1", "sess_in") == [(M, 0.0), (M + 60, 7.0)]


def test_missing_sample_is_null_not_zero(store):
    store.observe_counter("hub", "tokens_io", M, 0, at=1)
    store.observe_counter("hub", "tokens_io", M + 60, 10, at=2)
    # the machine stops reporting: the same report time comes back, nothing is written
    store.observe_counter("hub", "tokens_io", M + 120, 10, at=2)
    assert rows(store, "hub", "tokens_io") == [(M, 10.0)]
    # it comes back 20 minutes later: the gap is stored as missing
    store.observe_counter("hub", "tokens_io", M + 60 * 21, 500, at=3)
    r = rows(store, "hub", "tokens_io")
    assert r[0] == (M, 10.0)
    assert r[1:] == [(M + 60 * i, None) for i in range(1, 21)]
    b = store.bucketed(M, M + 60 * 21, 60)[("hub", "tokens_io")]
    assert b[0] == 10.0 and b[1:] == [None] * 20


def test_short_gap_is_spread(store):
    store.observe_counter("hub", "tokens_io", M, 0, at=1)
    store.observe_counter("hub", "tokens_io", M + 180, 90, at=2)
    assert rows(store, "hub", "tokens_io") == [(M, 30.0), (M + 60, 30.0), (M + 120, 30.0)]


def test_gap_fill_never_erases_data(store):
    store.put("hub", "tokens_io", [(M + 60, 5.0)])
    store.record_delta("hub", "tokens_io", M, M + 600, None)
    assert dict(rows(store, "hub", "tokens_io"))[M + 60] == 5.0


def test_snapshot_delta_deleted_session():
    prev = {("a", "s1"): (100, 10), ("a", "s2"): (50, 0)}
    cur = {("a", "s1"): (130, 25), ("a", "s3"): (7, 1)}  # s2 deleted, s3 new
    assert S.snapshot_delta(prev, cur) == (30 + 7, 15 + 1)
    assert S.snapshot_delta({("a", "s1"): (100, 10)}, {("a", "s1"): (90, 10)}) == (0, 0)  # rewritten lower


def make_state_db(path, sessions):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL NOT NULL, "
              "ended_at REAL, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, "
              "cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0)")
    c.executemany("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?)", sessions)
    c.commit()
    c.close()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_local_sampler_tokens_and_sessions(tmp_path):
    root = tmp_path / "home"
    make_state_db(root / "state.db", [("s1", "cli", M - 3600, M - 3000, 100, 50, 1000, 0),
                                      ("s2", "cli", M + 10, None, 0, 0, 0, 0)])
    make_state_db(root / "profiles" / "coach" / "state.db", [("p1", "cron", M + 20, M + 70, 5, 5, 0, 2)])
    clock = Clock(M + 2)
    reg = {"me": "hub", "machines": {"hub": {"metrics": {}}, "worker-1": {"metrics": {"at": 1}}}}
    smp = S.Sampler(root, Store(tmp_path / "s.sqlite"), registry=lambda: reg, clock=clock)
    smp.tick()
    st = smp.store
    assert rows(st, "hub", "tokens_io") == []  # first pass is the baseline
    # sessions are counted from started_at/ended_at, backfilled over the retention window
    sess_in = dict(rows(st, "hub", "sess_in"))
    sess_out = dict(rows(st, "hub", "sess_out"))
    assert sess_in[M - 3600] == 1 and sess_out[M - 3000] == 1
    assert sum(sess_in.values()) == 1  # s2/p1 started in the current, unfinished minute
    # a minute later: s2 used tokens, p1 ended, s1 was deleted
    c = sqlite3.connect(root / "state.db")
    c.execute("UPDATE sessions SET input_tokens=400, output_tokens=100, cache_read_tokens=3000 WHERE id='s2'")
    c.execute("DELETE FROM sessions WHERE id='s1'")
    c.commit()
    c.close()
    clock.t = M + 62
    smp.tick()
    assert rows(st, "hub", "tokens_io") == [(M, 500.0)]  # s1's deletion never subtracts
    assert rows(st, "hub", "tokens_cache") == [(M, 3000.0)]
    assert dict(rows(st, "hub", "sess_in"))[M] == 2
    assert dict(rows(st, "hub", "sess_out")).get(M + 60) is None  # p1 ended in the still-running minute
    assert ("worker-1", "tokens_io") not in st.reporters(M + 62)  # no counters reported: nothing stored


def test_unreadable_or_new_profile_db_never_spikes(tmp_path):
    root = tmp_path / "home"
    make_state_db(root / "state.db", [("s1", "cli", M - 600, None, 10, 0, 0, 0)])
    prof = root / "profiles" / "coach" / "state.db"
    make_state_db(prof, [("p1", "cron", M - 600, None, 1_000_000, 0, 0, 0)])
    good = prof.read_bytes()
    clock = Clock(M + 2)
    st = Store(tmp_path / "s.sqlite")
    smp = S.Sampler(root, st, registry=lambda: {"me": "hub", "machines": {}}, clock=clock)
    smp.tick()
    prof.write_bytes(b"not a database" * 100)  # unreadable for a minute
    c = sqlite3.connect(root / "state.db")
    c.execute("UPDATE sessions SET input_tokens=20 WHERE id='s1'")
    c.commit()
    c.close()
    clock.t = M + 62
    smp.tick()
    prof.write_bytes(good)  # back, with 5 more tokens
    c = sqlite3.connect(prof)
    c.execute("UPDATE sessions SET input_tokens=1000005 WHERE id='p1'")
    c.commit()
    c.close()
    make_state_db(root / "profiles" / "new" / "state.db", [("n1", "cli", M - 600, None, 777, 0, 0, 0)])
    clock.t = M + 122
    smp.tick()
    assert rows(st, "hub", "tokens_io") == [(M, 10.0), (M + 60, 5.0)]


def test_registry_counters_skip_self_and_bad_values(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    clock = Clock(M + 2)
    metrics = {"at": 1, "tokens_io_total": 100, "tokens_cache_total": -5, "sessions_started_total": True,
               "sessions_ended_total": "7"}
    reg = {"me": "hub", "machines": {"hub": {"metrics": {"at": 1, "tokens_io_total": 1}},
                                     "worker-2": {"metrics": metrics}}}
    smp = S.Sampler(tmp_path / "nohome", st, registry=lambda: reg, clock=clock)
    smp.tick()
    metrics.update(at=2, tokens_io_total=160)
    clock.t = M + 62
    smp.tick()
    assert rows(st, "worker-2", "tokens_io") == [(M, 60.0)]
    for bad in ("tokens_cache", "sess_in", "sess_out"):
        assert rows(st, "worker-2", bad) == []
    assert rows(st, "hub", "tokens_io") == []  # the local gateway comes from its state.db, not the registry


def test_registry_unreachable_still_samples_local(tmp_path):
    root = tmp_path / "home"
    make_state_db(root / "state.db", [("s1", "cli", M - 120, None, 1, 1, 0, 0)])

    def boom():
        raise OSError("down")

    st = Store(tmp_path / "s.sqlite")
    st.set_meta("me", "macbook")
    S.Sampler(root, st, registry=boom, clock=Clock(M + 2)).tick()
    assert dict(rows(st, "macbook", "sess_in"))[M - 120] == 1


def test_eligible_process():
    assert S.eligible_process(["hermes", "gateway", "run"], {})
    assert S.eligible_process(["hermes", "serve", "--isolated"], {})
    assert S.eligible_process(["main.py", "--quiet", "dashboard"], {})
    assert not S.eligible_process(["hermes", "chat", "-q", "hi"], {})
    assert not S.eligible_process(["hermes"], {})
    assert not S.eligible_process(["hermes", "gateway", "run"], {"HERMES_KANBAN_TASK": "t_1"})


def test_one_sampler_per_root(tmp_path, monkeypatch):
    """Two sampler threads on one root: the file lock lets only one tick."""
    import threading
    import time
    ticks = []
    monkeypatch.setattr(S.Sampler, "tick", lambda self: ticks.append(threading.current_thread().ident))
    stops = [threading.Event(), threading.Event()]
    threads = [threading.Thread(target=S._loop, args=(ev, tmp_path), daemon=True) for ev in stops]
    for t in threads:
        t.start()
    time.sleep(0.5)
    assert len(set(ticks)) == 1
    for ev in stops:
        ev.set()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads)
