"""Replay benchmark for card L (design t_01a1d563324d7e8e §3): ``sample_local_tokens`` on a hub-sized state.db must
cost <= 0.3% of one core (180 ms of thread CPU per 60 s tick, averaged over a replay that includes the
FULL_EVERY_S fallback reads), and the incremental passes must cost a small fraction of a full read."""
import os
import random
import sqlite3
import time

from throughput_under_test import sampler as S
from throughput_under_test.store import Store

M = 1_800_000_000 // 60 * 60
BUDGET_CORE = 0.003  # the card's budget: 0.3% of one core
SESSIONS = int(os.environ.get("THROUGHPUT_BENCH_SESSIONS", "23000"))  # the hub: 23k sessions in 11 files
MESSAGES = int(os.environ.get("THROUGHPUT_BENCH_MESSAGES", "200000"))
TICKS = 30


def build(path):
    rng = random.Random(7)
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=OFF")
    c.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL NOT NULL, ended_at REAL, "
              "input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0, "
              "cache_write_tokens INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, content TEXT)")
    c.execute("CREATE INDEX idx_messages_session_id ON messages(session_id, id)")
    c.execute("""CREATE TABLE session_model_usage (session_id TEXT NOT NULL, model TEXT NOT NULL,
        billing_provider TEXT NOT NULL DEFAULT '', billing_base_url TEXT NOT NULL DEFAULT '',
        billing_mode TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task))""")
    c.execute("CREATE INDEX idx_session_model_usage_session ON session_model_usage(session_id)")
    c.executemany("INSERT INTO sessions VALUES (?, 'cli', 0, NULL, ?, ?, ?, 0)",
                  ((f"s{i}", rng.randint(0, 10**6), rng.randint(0, 10**5), rng.randint(0, 10**6))
                   for i in range(SESSIONS)))
    c.executemany("INSERT INTO session_model_usage(session_id, model, billing_provider, input_tokens, "
                  "cache_read_tokens) VALUES (?, ?, ?, ?, ?)",
                  ((f"s{i}", f"m{k}", p, rng.randint(0, 10**6), rng.randint(0, 10**6))
                   for i in range(SESSIONS) for k in range(1 + i % 2) for p in ("a", "b")))
    c.executemany("INSERT INTO messages(session_id, content) VALUES (?, 'x')",
                  ((f"s{rng.randrange(SESSIONS)}",) for _ in range(MESSAGES)))
    c.commit()
    return c


def turns(c, rng, n):
    for _ in range(n):
        sid = f"s{rng.randrange(SESSIONS - 200, SESSIONS)}"  # recent sessions are the active ones
        c.execute("INSERT INTO messages(session_id, content) VALUES (?, 'x')", (sid,))
        c.execute("UPDATE sessions SET input_tokens = input_tokens + 100 WHERE id = ?", (sid,))
        c.execute("UPDATE session_model_usage SET input_tokens = input_tokens + 100 "
                  "WHERE session_id = ? AND model = 'm0' AND billing_provider = 'a'", (sid,))
    c.commit()


def replay(tmp_path, incremental: bool, monkeypatch):
    root = tmp_path / ("inc" if incremental else "full") / "home"
    root.mkdir(parents=True)
    c = build(root / "state.db")
    clock = [M + 2.0]
    smp = S.Sampler(root, Store(root.parent / "s.sqlite"), registry=lambda: {"me": "hub", "machines": {}},
                    clock=lambda: clock[0])
    if not incremental:
        monkeypatch.setattr(S, "read_incremental", lambda *a, **kw: None)
    rng = random.Random(1)
    cpu = []
    for n in range(TICKS + 1):
        clock[0] = M + 60 * n + 2
        turns(c, rng, 10)
        t0 = time.thread_time()
        smp.sample_local_tokens("hub", M + 60 * n)
        cpu.append(time.thread_time() - t0)
    monkeypatch.undo()
    c.close()
    return cpu[1:]  # the first pass is the baseline read in both


def test_sample_local_tokens_replay_budget(tmp_path, monkeypatch):
    inc = replay(tmp_path, True, monkeypatch)
    full = replay(tmp_path, False, monkeypatch)
    mean_inc, mean_full = sum(inc) / len(inc), sum(full) / len(full)
    steady = sorted(inc)[len(inc) // 2]  # the median pass is incremental
    print(f"\nsample_local_tokens: incremental mean {mean_inc * 1000:.1f} ms/tick (median {steady * 1000:.2f} ms), "
          f"full-read mean {mean_full * 1000:.1f} ms/tick; {mean_inc / 60 * 100:.3f}% of a core "
          f"(budget {BUDGET_CORE * 100}%), was {mean_full / 60 * 100:.3f}%")
    assert mean_inc / 60 <= BUDGET_CORE
    assert steady <= mean_full / 10  # the change-keyed pass is O(change), not O(state)
    assert mean_inc <= mean_full / 3  # including the FULL_EVERY_S ceiling reads
