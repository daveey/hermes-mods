"""Card attribution (holder, lock, ephemeral, boot-* mapped/unattributed, unknown) and per-minute counts from
real replica-shaped sqlite files."""
import json
import sqlite3

from throughput_under_test import cards as C

NOW = 1_800_000_000
M = NOW // 60 * 60 - 3600


def test_parse_claim_forms():
    assert C.parse_claim(json.dumps({"holder": "worker-5", "run_uid": "x"})) == ("worker-5", None)
    assert C.parse_claim(json.dumps({"lock": "hermes-worker-4:22245"})) == (None, "hermes-worker-4:22245")
    assert C.parse_claim("not json") == (None, None)
    assert C.parse_claim(None) == (None, None)


def test_attribute_each_form():
    known = {"hub", "worker-4", "mini"}
    bmap = {"0a0a0a0a-1111-4222-8333-444444444444": "mini"}
    assert C.attribute("worker-5", None, bmap, known) == "worker-5"  # Board lease holder, even if not registered
    assert C.attribute(None, "hermes-worker-4:22245", bmap, known) == "worker-4"  # lock form
    assert C.attribute(None, "hermes-hub:1", bmap, known) == "hub"
    assert C.attribute("hwp-w-c580a9", None, bmap, known) == "ephemeral"
    assert C.attribute("rec-w-67605bef43", None, bmap, known) == "ephemeral"
    assert C.attribute(None, "hwp-w-1:9", bmap, known) == "ephemeral"
    assert C.attribute(None, "boot-0a0a0a0a-1111-4222-8333-444444444444:14991", bmap, known) == "mini"
    assert C.attribute(None, "boot-0b0b0b0b-5555-4666-8777-888888888888:98968", bmap, known) == "unattributed"
    assert C.attribute(None, "ip-10-0-0-1:77", bmap, known) == "unattributed"  # unknown host
    assert C.attribute(None, None, bmap, known) == "unattributed"  # never claimed


def test_learn_boot_map_agreement():
    a, b = "aaaa", "bbbb"
    votes = [(a, "mini")] * 9 + [(a, "hub")] + [(b, "macbook")] * 3 + [(b, "hub")] * 3 + [("cccc", None)]
    assert C.learn_boot_map(votes) == {a: "mini"}  # 90 % agree; b is split; c has no creator
    assert C.learn_boot_map([(a, "ghost")], known={"mini"}) == {}


SCHEMA = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, created_at INTEGER, created_on TEXT, completed_at INTEGER);
CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, payload TEXT,
                          created_at INTEGER);
CREATE INDEX idx_events_task ON task_events(task_id, created_at);
"""


def replica(path, tasks, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", tasks)
    c.executemany("INSERT INTO task_events(task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                  [(t, k, json.dumps(p), at) for t, k, p, at in events])
    c.commit()
    c.close()


def build_fixture(tmp_path):
    d = tmp_path / "sync-shadow"
    boot = "boot-0a0a0a0a-1111-4222-8333-444444444444"
    replica(d / "default.db", [
        ("t1", "done", M, "hub", M + 125),
        ("t2", "archived", M + 30, "macbook", M + 130),
        ("t3", "running", M + 61, "worker-1", None),
        ("t4", "done", M + 62, "mini", M + 190),
        ("t5", "done", M + 63, "mini", M + 191),
        ("t6", "blocked", M + 64, "hwp-w-1", M + 199),  # completed_at set but not done/archived: not counted
        ("t7", "done", M - 9 * 86400, "hub", M - 9 * 86400),  # outside the retention window
    ], [
        ("t1", "claimed", {"lock": "hermes-worker-4:1"}, M + 1),
        ("t1", "claimed", {"holder": "worker-4"}, M + 1),  # same second, later row: the lease holder wins
        ("t2", "claimed", {"holder": "worker-2"}, M + 40),
        ("t2", "claimed", {"lock": "hermes-hub:9"}, M + 100),  # re-claimed later by the hub
        ("t4", "claimed", {"lock": boot + ":1"}, M + 70),
        ("t5", "claimed", {"lock": boot + ":2"}, M + 70),
    ])
    replica(d / "bb.db", [("b1", "done", M + 5, "hwp-w-9", M + 300), ("b2", "done", M + 6, None, M + 301)],
            [("b1", "claimed", {"holder": "hwp-w-c580a9"}, M + 200)])
    return d


def test_cards_counts_and_attribution(tmp_path):
    d = build_fixture(tmp_path)
    cards = C.Cards(tmp_path, known=lambda: {"hub", "macbook", "mini", "worker-1", "worker-2", "worker-4"},
                    clock=lambda: NOW, replica_dir=d)
    ev = cards.events()
    created = C.per_minute(ev["created"], M, NOW)
    done = C.per_minute(ev["done"], M, NOW)
    assert created == {"hub": {M: 1}, "macbook": {M: 1}, "worker-1": {M + 60: 1}, "mini": {M + 60: 2},
                       "ephemeral": {M: 1, M + 60: 1}, "unattributed": {M: 1}}
    assert done == {"worker-4": {M + 120: 1}, "hub": {M + 120: 1}, "mini": {M + 180: 2},
                    "ephemeral": {M + 300: 1}, "unattributed": {M + 300: 1}}  # b2 was never claimed
    assert ev["boot_map"] == {"0a0a0a0a-1111-4222-8333-444444444444": "mini"}


def test_cards_cached_30s(tmp_path):
    d = build_fixture(tmp_path)
    t = [NOW]
    cards = C.Cards(tmp_path, clock=lambda: t[0], replica_dir=d)
    first = cards.events()
    replica(d / "new.db", [("n1", "done", NOW - 10, "hub", NOW - 5)], [])
    assert cards.events() is first
    t[0] += 31
    assert len(cards.events()["created"]) == len(first["created"]) + 1
