"""Incremental local token reads (state.db ``messages.id`` watermark, design t_01a1d563324d7e8e card L): a pass
re-reads only the sessions the watermark says may have moved, and falls back to the full read on anything the
watermark can't vouch for (a rewind/delete, a deleted session, a replaced file, the FULL_EVERY_S ceiling)."""
import os
import random
import shutil
import sqlite3

import pytest

from throughput_under_test import sampler as S
from throughput_under_test.store import Store

M = 1_800_000_000 // 60 * 60
USAGE_DDL = """CREATE TABLE IF NOT EXISTS session_model_usage (
    session_id TEXT NOT NULL, model TEXT NOT NULL, billing_provider TEXT NOT NULL DEFAULT '',
    billing_base_url TEXT NOT NULL DEFAULT '', billing_mode TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task))"""


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class DB:
    """A state.db with the core's shape: sessions, AUTOINCREMENT messages, session_model_usage."""

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL NOT NULL, "
                      "ended_at REAL, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, "
                      "cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0)")
            c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                      "session_id TEXT NOT NULL, content TEXT)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id)")
            c.execute(USAGE_DDL)

    def conn(self):
        c = sqlite3.connect(self.path)
        c.isolation_level = None
        c.execute("PRAGMA synchronous=OFF")  # fsync dominates otherwise
        return _Tx(c)

    def turn(self, sid, io=0, cache=0, model="m", message=True):
        """One turn: (optionally) a message, then the tokens it used on the session and its model row."""
        with self.conn() as c:
            c.execute("INSERT OR IGNORE INTO sessions(id, source, started_at) VALUES (?, 'cli', ?)", (sid, M - 600))
            if message:
                c.execute("INSERT INTO messages(session_id, content) VALUES (?, 'x')", (sid,))
            c.execute("UPDATE sessions SET input_tokens = input_tokens + ?, cache_read_tokens = cache_read_tokens + ? "
                      "WHERE id = ?", (io, cache, sid))
            c.execute("INSERT INTO session_model_usage(session_id, model, input_tokens, cache_read_tokens) "
                      "VALUES (?,?,?,?) ON CONFLICT DO UPDATE SET input_tokens = input_tokens + excluded.input_tokens, "
                      "cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens", (sid, model, io, cache))

    def sql(self, *stmts):
        with self.conn() as c:
            for s in stmts:
                c.execute(s)


class _Tx:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, exc_type, *a):
        self.c.execute("ROLLBACK" if exc_type else "COMMIT")
        self.c.close()
        return False


def rows(store, kind="tokens_io"):
    """{model-or-'': total} over every stored minute for one gateway."""
    out = {}
    with sqlite3.connect(store.path) as c:
        for metric, value in c.execute("SELECT metric, value FROM minutes WHERE gateway='hub'"):
            if (metric == kind or metric.startswith(kind + ":")) and value is not None:
                key = metric[len(kind) + 1:]
                out[key] = out.get(key, 0) + value
    return {k: round(v, 6) for k, v in out.items() if round(v, 6)}


def minutes(store, kind="tokens_io"):
    with sqlite3.connect(store.path) as c:
        return {m: v for m, v in c.execute("SELECT minute, value FROM minutes WHERE gateway='hub' AND metric=?",
                                           (kind,)) if v}


class Spy:
    """Counts full reads (``session_snapshot`` calls with any db) and incremental reads per pass."""

    def __init__(self, monkeypatch):
        self.full: list[list[str]] = []
        self.inc = 0
        real_full, real_inc = S.session_snapshot, S.read_incremental

        def full(dbs):
            self.full.append([os.path.basename(os.path.dirname(str(d))) for d in dbs])
            return real_full(dbs)

        def inc(*a, **kw):
            got = real_inc(*a, **kw)
            self.inc += got is not None
            return got

        monkeypatch.setattr(S, "session_snapshot", full)
        monkeypatch.setattr(S, "read_incremental", inc)

    def last_full(self):
        return [d for d in self.full[-1] if d] if self.full else []


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "home"
    db = DB(root / "state.db")
    clock = Clock(M + 2)
    smp = S.Sampler(root, Store(tmp_path / "s.sqlite"), registry=lambda: {"me": "hub", "machines": {}}, clock=clock)
    return root, db, clock, smp, Spy(monkeypatch)


def step(smp, clock, n):
    clock.t = M + 60 * n + 2
    smp.tick()


def full_count(spy):
    return sum(1 for dbs in spy.full if dbs)


def test_message_turn_is_read_incrementally(env):
    root, db, clock, smp, spy = env
    db.turn("s1", 100, 10)
    db.turn("s2", 50)
    step(smp, clock, 0)  # baseline: a full read
    assert full_count(spy) == 1
    db.turn("s1", 30, 3, model="m2")
    db.turn("s3", 7)  # a new session
    step(smp, clock, 1)
    assert full_count(spy) == 1 and spy.inc == 1  # no full read: the watermark vouched for the file
    assert minutes(smp.store) == {M: 37}
    assert minutes(smp.store, "tokens_cache") == {M: 3}
    assert rows(smp.store) == {"": 37, "m2": 30, "m": 7}


def test_token_write_after_the_message_is_picked_up_while_hot(env):
    root, db, clock, smp, spy = env
    db.turn("s1", 100)
    step(smp, clock, 0)
    db.turn("s1", 10)
    step(smp, clock, 1)
    db.turn("s1", 25, message=False)  # the usage write lands a minute after its message
    step(smp, clock, 2)
    assert full_count(spy) == 1
    assert minutes(smp.store) == {M: 10, M + 60: 25}


def test_full_read_seeds_the_newest_messages_sessions_as_hot(env):
    root, db, clock, smp, spy = env
    db.turn("old", 5)
    for _ in range(3):
        db.turn("recent", 1)
    step(smp, clock, 0)
    db.turn("recent", 9, message=False)  # the usage write of a message the full read already saw
    db.turn("old", 9, message=False)
    step(smp, clock, 1)
    assert full_count(spy) == 1
    assert rows(smp.store) == {"": 18, "m": 18}  # both inside the HOT_SEED_ROWS window here


def run(smp, clock, first, last):
    """Tick every minute from ``first`` to ``last`` inclusive (the store keeps a skipped minute as missing)."""
    for n in range(first, last + 1):
        step(smp, clock, n)


def test_cold_session_write_without_a_message_waits_for_the_ceiling(env, monkeypatch):
    root, db, clock, smp, spy = env
    monkeypatch.setattr(S, "HOT_SEED_ROWS", 0)  # a full read seeds no hot sessions
    monkeypatch.setattr(S, "HOT_SEED_SESSIONS", 0)
    db.turn("s1", 100)
    db.turn("idle", 5)
    step(smp, clock, 0)
    db.turn("idle", 40, message=False)  # e.g. an auxiliary call on an idle session: no message
    ceiling = S.FULL_EVERY_S // 60
    run(smp, clock, 1, ceiling - 1)
    assert rows(smp.store) == {} and full_count(spy) == 1  # not seen by the incremental reads
    step(smp, clock, ceiling)  # the P1 fallback ceiling: a full read
    assert full_count(spy) == 2
    assert minutes(smp.store) == {M + 60 * (ceiling - 1): 40} and rows(smp.store) == {"": 40, "m": 40}


def test_rewind_of_the_newest_messages_falls_back_to_a_full_read(env):
    root, db, clock, smp, spy = env
    db.turn("s1", 100)
    db.turn("s2", 100)
    step(smp, clock, 0)
    db.turn("s2", 20)
    db.sql("DELETE FROM messages WHERE id = (SELECT MAX(id) FROM messages)")  # rewound: s2's new message is gone
    step(smp, clock, 1)
    assert full_count(spy) == 2  # the new id range has fewer rows than ids: full read
    assert rows(smp.store) == {"": 20, "m": 20}


def test_rewind_then_new_messages_still_falls_back(env, monkeypatch):
    root, db, clock, smp, spy = env
    monkeypatch.setattr(S, "HOT_S", 90)
    db.turn("cold", 100)
    db.turn("s1", 100)
    run(smp, clock, 0, 3)  # the hot set has aged out
    assert full_count(spy) == 1
    db.turn("cold", 30)
    db.sql("DELETE FROM messages WHERE session_id='cold'")  # rewind of the whole session, tokens stay
    db.turn("s1", 1)  # a later message keeps MAX(id) above the last watermark
    step(smp, clock, 4)
    assert full_count(spy) == 2
    assert rows(smp.store) == {"": 31, "m": 31}


def test_deleted_session_falls_back_to_a_full_read(env):
    root, db, clock, smp, spy = env
    db.turn("s1", 100)
    db.turn("s2", 100)
    step(smp, clock, 0)
    db.turn("s3", 9)  # +1 session ...
    db.sql("DELETE FROM sessions WHERE id='s1'", "DELETE FROM session_model_usage WHERE session_id='s1'")  # ... -1
    step(smp, clock, 1)
    assert full_count(spy) == 2  # the row count is not last count + new rows
    assert rows(smp.store) == {"": 9, "m": 9}
    db.turn("s2", 1)
    step(smp, clock, 2)
    assert full_count(spy) == 2 and rows(smp.store) == {"": 10, "m": 10}  # the new baseline is incremental again


def test_replaced_file_falls_back_to_a_full_read(env, tmp_path):
    root, db, clock, smp, spy = env
    db.turn("s1", 100)
    step(smp, clock, 0)
    other = DB(tmp_path / "other" / "state.db")
    other.turn("s1", 100)
    other.turn("s1", 100)
    other.turn("s1", 15)
    shutil.copyfile(other.path, root / "state.db.new")
    os.replace(root / "state.db.new", root / "state.db")  # a restore: new inode
    step(smp, clock, 1)
    assert full_count(spy) == 2
    assert rows(smp.store) == {"": 115, "m": 115}


def test_new_profile_db_is_a_baseline_then_incremental(env):
    root, db, clock, smp, spy = env
    db.turn("s1", 100)
    step(smp, clock, 0)
    prof = DB(root / "profiles" / "p" / "state.db")
    prof.turn("x", 500)
    step(smp, clock, 1)
    assert spy.last_full() == ["p"] and rows(smp.store) == {}  # first sight is a baseline
    prof.turn("x", 5)
    db.turn("s1", 1)
    step(smp, clock, 2)
    assert spy.last_full() == [] and rows(smp.store) == {"": 6, "m": 6}


def test_model_read_failure_forces_full_reads_until_it_recovers(env, monkeypatch):
    root, db, clock, smp, spy = env
    monkeypatch.setattr(S, "HOT_SEED_ROWS", 0)
    monkeypatch.setattr(S, "HOT_SEED_SESSIONS", 0)
    db.turn("s1", 100)
    step(smp, clock, 0)
    real = S.model_snapshot
    monkeypatch.setattr(S, "model_snapshot", lambda dbs: ({}, set()))
    db.turn("s1", 10, message=False)
    ceiling = S.FULL_EVERY_S // 60
    run(smp, clock, 1, ceiling)  # the ceiling's full read finds the model table unreadable
    fulls = full_count(spy)
    monkeypatch.setattr(S, "model_snapshot", real)
    db.turn("s1", 5)
    step(smp, clock, ceiling + 1)
    assert full_count(spy) == fulls + 1  # no cursor after a failed model read: full again (fresh model baseline)
    db.turn("s1", 3)
    step(smp, clock, ceiling + 2)
    assert full_count(spy) == fulls + 1  # and incremental from there
    assert rows(smp.store)["m"] == 3 and rows(smp.store)[""] == 18


def test_db_without_messages_table_is_always_read_in_full(env, tmp_path):
    root, db, clock, smp, spy = env
    os.unlink(db.path)
    c = sqlite3.connect(db.path)
    c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL NOT NULL, ended_at REAL, "
              "input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0, "
              "cache_write_tokens INTEGER DEFAULT 0)")
    c.execute("INSERT INTO sessions VALUES ('s', 'cli', 0, NULL, 10, 0, 0, 0)")
    c.commit()
    step(smp, clock, 0)
    c.execute("UPDATE sessions SET input_tokens = 25")
    c.commit()
    c.close()
    step(smp, clock, 1)
    assert full_count(spy) == 2 and rows(smp.store) == {"": 15, "unreported": 15}


@pytest.mark.parametrize("seed", range(8))
def test_incremental_totals_match_full_reads(tmp_path, monkeypatch, seed):
    """Replay one random workload (turns, late usage writes, new sessions and profiles, rewinds, deletes) through
    an incremental and a full-read-only sampler: per-minute totals match whenever every write had a message
    within HOT_S, and the totals always match after a full read."""
    rng = random.Random(seed)
    plan = []
    sids = ["s0"]
    last_turn = 0
    for minute in range(40):
        ops = []
        for _ in range(rng.randint(0, 4)):
            r = rng.random()
            if r < 0.55:
                ops.append(("turn", rng.choice(sids), rng.randint(1, 50), rng.choice("abc"), True))
                last_turn = minute
            elif r < 0.7:
                sid = f"s{len(sids)}"
                sids.append(sid)
                ops.append(("turn", sid, rng.randint(1, 50), "a", True))
                last_turn = minute
            elif r < 0.8:
                if minute - last_turn < 5:  # late usage write on the session of a recent turn (still hot)
                    ops.append(("late", rng.randint(1, 9)))
            elif r < 0.86:
                ops.append(("rewind",))
            elif r < 0.9 and len(sids) > 2:
                ops.append(("delete", sids.pop(rng.randrange(1, len(sids)))))
            elif r < 0.93:
                ops.append(("profile", rng.randint(1, 30)))
        plan.append(ops)
    plan.append([])  # a final quiet minute
    monkeypatch.setattr(S.Sampler, "sample_local_sessions", lambda self, me, minute: None)  # not under test; slow
    results = []
    for incremental in (True, False):
        root = tmp_path / ("inc" if incremental else "full") / "home"
        db = DB(root / "state.db")
        db.turn("s0", 1)
        prof = DB(root / "profiles" / "p" / "state.db")
        clock = Clock(M + 2)
        smp = S.Sampler(root, Store(root.parent / "s.sqlite"), registry=lambda: {"me": "hub", "machines": {}},
                        clock=clock)
        with monkeypatch.context() as mp:
            if not incremental:
                mp.setattr(S, "read_incremental", lambda *a, **kw: None)
            last_sid = "s0"
            for n, ops in enumerate(plan):
                for op in ops:
                    if op[0] == "turn":
                        db.turn(op[1], op[2], op[2] // 3, model=op[3])
                        last_sid = op[1]
                    elif op[0] == "late":
                        db.turn(last_sid, op[1], message=False)
                    elif op[0] == "rewind":
                        db.sql("DELETE FROM messages WHERE id = (SELECT MAX(id) FROM messages)")
                    elif op[0] == "delete":
                        db.sql(f"DELETE FROM sessions WHERE id='{op[1]}'",
                               f"DELETE FROM session_model_usage WHERE session_id='{op[1]}'")
                    elif op[0] == "profile":
                        prof.turn("p1", op[1])
                step(smp, clock, n)
        results.append((minutes(smp.store), minutes(smp.store, "tokens_cache"), rows(smp.store)))
    for inc, full in zip(*results):
        diff = {k: (inc.get(k), full.get(k)) for k in set(inc) | set(full) if inc.get(k) != full.get(k)}
        assert not diff, (seed, sorted(diff.items()))


def test_range_query_reads_the_rowid_range_not_a_session_index(tmp_path):
    db = DB(tmp_path / "state.db")
    db.turn("s", 1)
    with sqlite3.connect(db.path) as c:
        plan = " ".join(r[-1] for r in c.execute("EXPLAIN QUERY PLAN " + S._RANGE_SQL, (0, 10)))
    assert "INTEGER PRIMARY KEY" in plan or "rowid" in plan.lower(), plan
