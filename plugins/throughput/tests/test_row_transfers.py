"""The hot reads of the /v1/series build transfer O(1) SQLite rows into Python with unchanged results.

Each SQLite row a cursor returns releases and re-takes the GIL; inside the busy Desktop backend that is up to a
5 ms switch interval per row. ``store.batch_rows`` moves a whole result as one row; these tests pin that the
answers equal the plain per-row reads and count the rows that actually cross into Python."""
import json
import random
import sqlite3
import struct
from contextlib import closing

import pytest

from throughput_under_test import cards as C
from throughput_under_test import store as ST
from throughput_under_test.store import Store

from test_cards import M, NOW, build_fixture, replica

def _full_mantissa(rnd, lo_exp, hi_exp):
    """A random double with all 53 significant bits random, 2**lo_exp <= |x| < 2**(hi_exp + 1)."""
    bits = rnd.getrandbits(52) | ((rnd.randint(lo_exp, hi_exp) + 1023) << 52) | (rnd.getrandbits(1) << 63)
    return struct.unpack("d", struct.pack("Q", bits))[0]


def _table(c, vals):
    c.execute("CREATE TABLE t (i INTEGER, v REAL, s TEXT, x)")
    c.executemany("INSERT INTO t VALUES (?, ?, ?, ?)",
                  [(n, v, f"s{n}\"雪", v if n % 3 else n) for n, v in enumerate(vals)])
    c.execute("INSERT INTO t VALUES (NULL, NULL, NULL, NULL)")
    c.execute("INSERT INTO t VALUES (?, 1.5, '1.5', '1')", (2 ** 63 - 1,))


def _same(got, expected):
    assert [tuple(r) for r in got] == expected
    assert [tuple(map(type, r)) for r in got] == [tuple(map(type, r)) for r in expected]


@pytest.mark.parametrize("width", [1, 3, 6])
def test_batch_encoding_does_not_expand_flags_per_output_column(width):
    """Flag validation must not re-evaluate every source expression for every column."""
    calls = []
    with closing(sqlite3.connect(":memory:")) as c:
        c.execute("CREATE TABLE t (v)")
        c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(10)])

        def observe(value):
            calls.append(value)
            return value
        c.create_function("observe", 1, observe)
        sql = "SELECT " + ",".join("observe(v)" for _ in range(width)) + " FROM t"
        assert ST.batch_rows(c, sql, width) == [[i] * width for i in range(10)]
        # One type check for the flag, one for the payload and one payload read.
        # Flattening enc duplicates the whole flag expression for each fallback
        # predicate, making this O(width**2) instead of O(width) source work.
        assert len(calls) <= 3 * width * 10


def test_batch_rows_reals_exact_in_one_row():
    """Every double with 2**-38 <= |x| < 2**63 travels exactly, whatever this SQLite's printf does."""
    rnd = random.Random(7)
    vals = [_full_mantissa(rnd, -38, 62) for _ in range(20000)]
    vals += [rnd.random() * 10 ** rnd.randint(-8, 15) for _ in range(5000)]
    vals += [1.8e9 + rnd.random() for _ in range(5000)]  # timestamps
    vals += [2.0 ** -38, -(2.0 ** 63 - 1024), 0.1 + 0.2, 9007199254740992.0, 0.0]
    with closing(sqlite3.connect(":memory:")) as c:
        _table(c, vals)
        assert c.execute(ST.batch_query("SELECT * FROM t", 4)).fetchone()[0] is not None  # the batched path
        _same(ST.batch_rows(c, "SELECT * FROM t", 4), c.execute("SELECT * FROM t").fetchall())


@pytest.mark.parametrize("odd", [5e-324, 2.2250738585072014e-308, 1.7976931348623157e308, 2.0 ** 63, 1e23,
                                 float("inf"), -float("inf"), 2.0 ** -60 + 2.0 ** -112])
def test_batch_rows_unencodable_real_takes_row_path(odd):
    vals = [1.5, odd, 2.25]
    with closing(sqlite3.connect(":memory:")) as c:
        _table(c, vals)
        assert c.execute(ST.batch_query("SELECT * FROM t", 4)).fetchone()[0] is None
        _same(ST.batch_rows(c, "SELECT * FROM t", 4), c.execute("SELECT * FROM t").fetchall())


BLOBS = [b"\x00", b"\x14\xf3", b"\xff\xfe\xfd", b"[1]", b"\x0c", b"", b"\xc3\x28"]


@pytest.mark.parametrize("blob", BLOBS + ["random"])
def test_batch_rows_any_blob_takes_row_path(blob):
    """SQLite 3.45+ reads some BLOBs as JSONB (x'00' -> null, x'14f3' -> 0) or fails on others; a BLOB anywhere in
    the result must never reach a JSON function: the whole result is read row by row."""
    rnd = random.Random(5)
    blobs = [rnd.randbytes(rnd.randint(1, 8)) for _ in range(2000)] if blob == "random" else [blob]
    with closing(sqlite3.connect(":memory:")) as c:
        c.execute("CREATE TABLE t (a, b)")
        c.executemany("INSERT INTO t VALUES (?, ?)", [(i, b) for i, b in enumerate(blobs)] + [(1.5, "x"), (None, 2)])
        assert c.execute(ST.batch_query("SELECT a, b FROM t", 2)).fetchone()[0] is None
        _same(ST.batch_rows(c, "SELECT a, b FROM t", 2), c.execute("SELECT a, b FROM t").fetchall())
        for b in blobs[:50]:  # one BLOB at a time, alone in its result
            got = ST.batch_rows(c, "SELECT b FROM t WHERE b = ?", 1, (b,))
            assert [tuple(r) for r in got] == c.execute("SELECT b FROM t WHERE b = ?", (b,)).fetchall()


def test_batch_rows_invalid_utf8_text_answers_like_fetchall():
    with closing(sqlite3.connect(":memory:")) as c:
        c.execute("CREATE TABLE t (v)")
        c.execute("INSERT INTO t VALUES (CAST(x'c328' AS TEXT))")
        with pytest.raises(sqlite3.OperationalError) as plain:
            c.execute("SELECT v FROM t").fetchall()
        with pytest.raises(sqlite3.OperationalError) as batched:
            ST.batch_rows(c, "SELECT v FROM t", 1)
        assert str(batched.value) == str(plain.value)


def test_batch_rows_undecodable_batch_falls_back(monkeypatch):
    with closing(sqlite3.connect(":memory:")) as c:
        c.execute("CREATE TABLE t (v)")
        c.execute("INSERT INTO t VALUES (1)")

        def bad(_):
            raise json.JSONDecodeError("bad", "", 0)
        monkeypatch.setattr(ST, "decode_batch", bad)
        assert ST.batch_rows(c, "SELECT v FROM t", 1) == [(1,)]


def test_cards_replica_read_error_contributes_nothing(tmp_path, monkeypatch):
    d = build_fixture(tmp_path)
    real = C.batch_rows

    def failing(c, sql, width, parameters=()):
        if "completed_at >=" in sql and c.execute("PRAGMA database_list").fetchone()[2].endswith("default.db"):
            raise sqlite3.OperationalError("disk I/O error")
        return real(c, sql, width, parameters)
    monkeypatch.setattr(C, "batch_rows", failing)
    ev = C.Cards(tmp_path, clock=lambda: NOW, replica_dir=d).events()
    # only bb.db counts: default.db's created rows are not kept when its done read fails
    assert sorted(ev["created"]) == [(M + 5, "ephemeral"), (M + 6, "unattributed")]
    assert len(ev["done"]) == 2


def test_batch_rows_width_one_and_order():
    with closing(sqlite3.connect(":memory:")) as c:
        c.execute("CREATE TABLE t (v)")
        c.executemany("INSERT INTO t VALUES (?)", [(x,) for x in (3, "b", 2.5, None, 1, 2 ** -70)])
        assert [tuple(r) for r in ST.batch_rows(c, "SELECT v FROM t ORDER BY rowid DESC", 1)] == \
            c.execute("SELECT v FROM t ORDER BY rowid DESC").fetchall()
        assert ST.batch_rows(c, "SELECT v FROM t WHERE 0", 1) == []


class RowCounter:
    """Counts SQLite result rows that cross into Python on every connection opened through ``connect``."""

    def __init__(self):
        self.rows = 0

    def wrap(self, connect):
        def opened(*a, **kw):
            c = connect(*a, **kw)

            def count(cursor, row):
                self.rows += 1
                return row
            c.row_factory = count
            return c
        return opened


def _plain_bucketed(path, since, until, bucket_s):
    """The pre-batching read, for comparison."""
    n = (until - since) // bucket_s
    out, cover = {}, {}
    with closing(sqlite3.connect(path)) as c:
        rows = c.execute("SELECT gateway, metric, (minute - ?) / ?, SUM(value), COUNT(value) FROM minutes "
                         "WHERE minute >= ? AND minute < ? AND metric NOT GLOB '*:*' GROUP BY 1, 2, 3",
                         (since, bucket_s, since, until)).fetchall()
    for g, m, b, total, sampled in rows:
        arr = out.setdefault((g, m), [None] * n)
        cov = cover.setdefault((g, m), [0] * n)
        if 0 <= b < n and sampled:
            arr[b] = float(total)
            cov[b] = int(sampled)
    return out, cover


@pytest.mark.parametrize("bucket_s", [60, 300, 3600])
def test_bucketed_sampled_one_row_same_answer(tmp_path, monkeypatch, bucket_s):
    until = NOW // 60 * 60
    since = until - 6 * 3600
    st = Store(tmp_path / "s.sqlite")
    rnd = random.Random(bucket_s)
    for g in ("hub", "worker-1", 'odd "gw" 雪'):
        for metric in ("tokens_io", "tokens_cache", "sess_in", "tokens_io:model-a"):
            st.put(g, metric, [(m, None if rnd.random() < 0.1 else rnd.random() * 1e6 / 7)
                               for m in range(since - 600, until + 120, 60)])
    expected = _plain_bucketed(st.path, since, until, bucket_s)
    probe = RowCounter()
    monkeypatch.setattr(ST.sqlite3, "connect", probe.wrap(sqlite3.connect))
    got = st.bucketed_sampled(since, until, bucket_s)
    assert got == expected
    assert probe.rows == 1, "every (gateway, metric, bucket) group must cross as one batched row"


def _plain_events(replica_dir, since):
    """The pre-batching per-row read of ``Cards._build``, for comparison (raw created + done rows and claims)."""
    created, done, per_db = [], [], []
    for db in sorted(replica_dir.glob("*.db")):
        with closing(sqlite3.connect(db)) as c:
            created += c.execute("SELECT created_at, created_on FROM tasks WHERE created_at >= ?", (since,)).fetchall()
            rows = c.execute("SELECT id, completed_at, created_on FROM tasks WHERE completed_at >= ? "
                             "AND status IN ('done','archived')", (since,)).fetchall()
            per_db.append(len(rows))
            for tid, t, on in rows:
                row = c.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='claimed' "
                                "ORDER BY created_at DESC, id DESC LIMIT 1", (tid,)).fetchone()
                done.append((t, C.parse_claim(row[0] if row else None), on))
    return created, done, per_db


def _big_replica(d, n):
    rnd = random.Random(n)
    tasks, events = [], []
    for i in range(n):
        tid = f"t{i}"
        status = rnd.choice(["done", "archived", "running", "blocked"])
        tasks.append((tid, status, M + i % 600, rnd.choice(["hub", "mini", "hwp-w-1", None]), M + 60 + i % 900))
        for k in range(rnd.randint(0, 3)):
            events.append((tid, rnd.choice(["claimed", "commented"]),
                           rnd.choice([{"holder": "worker-4"}, {"lock": "hermes-hub:9"},
                                       {"lock": "boot-0a0a0a0a-1111-4222-8333-444444444444:1"}]), M + k))
    replica(d / "big.db", tasks, events)


def test_cards_build_batched_same_events(tmp_path, monkeypatch):
    d = build_fixture(tmp_path)
    _big_replica(d, 1200)  # more than one _CLAIM_CHUNK of done cards
    known = {"hub", "macbook", "mini", "worker-1", "worker-2", "worker-4"}
    created, done_raw, done_per_db = _plain_events(d, NOW - C.RETENTION_S)
    assert max(done_per_db) > C._CLAIM_CHUNK
    probe = RowCounter()
    monkeypatch.setattr(C, "ro_connect", probe.wrap(C.ro_connect))
    t = [NOW]
    cards = C.Cards(tmp_path, known=lambda: known, clock=lambda: t[0], replica_dir=d)
    ev = cards.events()
    assert sorted(ev["created"], key=repr) == sorted(((t_, C.classify(on)) for t_, on in created), key=repr)
    bmap = C.learn_boot_map([(C.boot_uuid(lk), on) for _, (h, lk), on in done_raw if C.boot_uuid(lk) and not h],
                            known)
    assert ev["boot_map"] == bmap
    assert sorted(ev["done"], key=repr) == sorted(
        ((t_, C.attribute(h, lk, bmap, known)) for t_, (h, lk), _ in done_raw), key=repr)
    dbs = len(done_per_db)
    # created + done per replica, plus one claim batch per chunk of new done cards: never one row per card.
    assert probe.rows == 2 * dbs + sum(-(-n // C._CLAIM_CHUNK) for n in done_per_db), \
        f"{probe.rows} rows for {len(done_raw)} done cards"
    probe.rows = 0
    t[0] += C.CACHE_S + 1
    again = cards.events()
    assert sorted(again["done"], key=repr) == sorted(ev["done"], key=repr)
    assert probe.rows <= 2 * dbs, "memoized claims must not be looked up again"


def test_cards_claim_memo_holds_first_seen_claim(tmp_path):
    """A completed card's claim is read once: a later claim event does not re-attribute it (memo unchanged)."""
    d = build_fixture(tmp_path)
    t = [NOW]
    cards = C.Cards(tmp_path, clock=lambda: t[0], replica_dir=d)
    first = dict((tuple(x) for x in cards.events()["done"]))
    with closing(sqlite3.connect(d / "default.db")) as c:
        c.execute("INSERT INTO task_events(task_id, kind, payload, created_at) VALUES ('t1', 'claimed', ?, ?)",
                  (json.dumps({"holder": "worker-9"}), M + 500))
        c.commit()
    t[0] += C.CACHE_S + 1
    assert dict((tuple(x) for x in cards.events()["done"])) == first
