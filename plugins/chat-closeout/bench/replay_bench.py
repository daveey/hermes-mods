#!/usr/bin/env python3
"""Replay benchmark of the chat-closeout waiting source and host pass (design t_01a1d563 row C, done-when).

Builds a hub-scale home in a temp dir (state.db transcripts, a board, the hub replica, the close-out ledger), then
replays a 600 s stream in 2 s windows whose change rates are the hub's measured ones (``t01a1d563-chg1-600s.json``
``changed_windows`` and ``t01a1d563-rf2.json``: state.db messages moved in 83 of 300 windows, the default board's
content revision in 89, the hub replica in 111 with 67% of its changed task rows liveness-only). Each window runs one
sidebar waiting pass (the serve's annotation adoption re-runs it) and every 30 s (``TICK_S``) one host pass
(``_host_pass``: wake, owner wake, owner relay, watchdog). Reports each loop's process CPU as % of one core over the
600 s replay. Run it against a plugin dir to compare (``--plugin`` the base checkout for the before number):

    python3 bench/replay_bench.py --plugin plugins/chat-closeout [--messages 400000] [--json out.json]

Needs core importable (PYTHONPATH=<hermes-agent checkout>), like the tests.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

WINDOWS, STEP_S, TICK_S = 300, 2.0, 30.0
BUDGET_PCT = {"waiting": 0.2, "host": 0.5}   # % of one core over the replay (design done-when)
RATES = {"state_msg": 83 / 300, "board_rev": 89 / 300, "hub_seq": 111 / 300}
ACTIVITY_ONLY = 191 / 284          # rf2: liveness-only share of changed replica task rows
WAITING_HIT = 0.1                  # share of transcript writes that land in a waiting chat's lineage


class ReplayClock:
    """Context manager: virtual forward-only wall/monotonic clocks installed on the global ``time`` module.

    Every module calling ``time.time()``/``time.monotonic()`` (plugin, core) ages with ``advance()``, so the 600 s
    replay really crosses production floors, freshness and due deadlines. CPU timers (process_time, thread_time,
    perf_counter) stay real so the measured cost is real. Originals are restored on any exit."""

    _NAMES = ("time", "time_ns", "monotonic", "monotonic_ns")

    def __init__(self, wall: float | None = None, mono: float | None = None):
        self.wall = float(time.time() if wall is None else wall)
        self.mono = float(time.monotonic() if mono is None else mono)
        self._orig: dict = {}

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("replay clock only moves forward")
        self.wall += seconds
        self.mono += seconds

    def __enter__(self):
        if self._orig:
            raise RuntimeError("ReplayClock already installed")
        self._orig = {n: getattr(time, n) for n in self._NAMES}
        time.time = lambda: self.wall
        time.monotonic = lambda: self.mono
        time.time_ns = lambda: round(self.wall * 1e9)
        time.monotonic_ns = lambda: round(self.mono * 1e9)
        return self

    def __exit__(self, *exc):
        for n, fn in self._orig.items():
            setattr(time, n, fn)
        self._orig = {}
        return False


def budget_failures(pct_core: dict, budgets: dict = BUDGET_PCT) -> list:
    return [f"{k} {pct_core[k]:.3f}% > {limit}% of one core" for k, limit in budgets.items()
            if pct_core[k] > limit]


def load_plugin(plugin_dir: Path):
    spec = importlib.util.spec_from_file_location("closeout_plugin_bench", plugin_dir / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ""
    spec.loader.exec_module(mod)
    return mod


def build(home: Path, rng: random.Random, sessions: int, messages: int, tasks: int, replica_rows: int,
          pending: int) -> dict:
    import hermes_state
    db = hermes_state.SessionDB(db_path=home / "state.db")
    sids = [f"s{i:05d}" for i in range(sessions)]
    for sid in sids:
        db.create_session(sid, source="cli")
    db.close()
    per = max(1, messages // sessions)
    with sqlite3.connect(home / "state.db") as conn:
        rows = []
        for sid in sids:
            for j in range(per):
                rows.append((sid, "user" if j % 2 == 0 else "assistant", f"message {j} of {sid}", 1000 + j,
                             None if j % 2 == 0 else "stop"))
        conn.executemany("INSERT INTO messages (session_id, role, content, timestamp, finish_reason, active) "
                         "VALUES (?,?,?,?,?,1)", rows)
    board = home / "kanban.db"
    tids = [f"t_{i:06d}" for i in range(tasks)]
    from hermes_cli.kanban_db_connect import connect
    conn = connect(db_path=board)
    try:
        conn.executemany("INSERT INTO tasks (id, title, status, block_kind, completed_at, created_at) "
                         "VALUES (?,?,?,?,?,?)",
                         [(t, f"task {t}", "done" if rng.random() < 0.7 else "running", None,
                           100.0, 1) for t in tids])
        conn.execute("UPDATE tasks SET completed_at = NULL WHERE status != 'done'")
        conn.commit()
    finally:
        conn.close()
    hub = home / "hub.db"
    with sqlite3.connect(hub) as conn:
        conn.execute("CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER DEFAULT 1, "
                     "seq INTEGER, data TEXT, updated_at INTEGER DEFAULT 0, "
                     "PRIMARY KEY (gw, coll, key))")
        conn.execute("CREATE INDEX replica_coll ON replica (coll)")  # the hub's own indexes (hermes-hub hub.db)
        conn.execute("CREATE INDEX replica_poll_metadata ON replica (coll, gw, seq)")
        conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        seq = 0
        rrows = []
        for i in range(replica_rows):
            seq += 1
            gw = f"worker-{i % 8}"
            rrows.append((gw, "tasks", f"default/default/t_r{i:06d}", seq,
                          json.dumps({"id": f"t_r{i:06d}", "status": "running", "activity_at": 1.0})))
        conn.executemany("INSERT INTO replica (gw, coll, key, seq, data) VALUES (?,?,?,?,?)", rrows)
        for gw in ("hub", "macbook", "mini"):
            seq += 1
            conn.execute("INSERT INTO replica (gw, coll, key, seq, data) VALUES (?,?,?,?,?)", (gw, "gateway", "info", seq, json.dumps({})))
        conn.execute("INSERT INTO meta VALUES ('last_ok', ?)", (str(time.time()),))
    # Ledger: pending rows over the first sessions; half their batches settled and unreported (waiting).
    plugin_core = None
    waiting = sids[:pending]
    return {"sids": sids, "tids": tids, "board": board, "hub": hub, "waiting": waiting, "seq": seq,
            "replica_rows": replica_rows, "core": plugin_core}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--sessions", type=int, default=4000)
    ap.add_argument("--messages", type=int, default=400_000)
    ap.add_argument("--tasks", type=int, default=4000)
    ap.add_argument("--replica-rows", type=int, default=17922)
    ap.add_argument("--pending", type=int, default=36)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--diagnostic", action="store_true", help="report only; do not fail on the CPU budgets")
    args = ap.parse_args()
    env_keys = ('HERMES_HOME', 'HERMES_KANBAN_HOME', 'HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_HUB_STATE',
                'HERMES_MAIL_CONFIG', 'CHAT_CLOSEOUT_OFF')
    previous = {k: os.environ.get(k) for k in env_keys}
    try:
        # Start away from a floor boundary so the measured interval includes a rollover.
        with ReplayClock(wall=(time.time() // 600) * 600 + 30) as clock:
            return _run(args, clock)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run(args, clock: ReplayClock) -> int:
    rng = random.Random(args.seed)
    # Worker TMPDIR may be inside its fenced live Hermes home. Own a fixture outside that root;
    # retain every inherited descendant marker and verify the resolved board before writing.
    with tempfile.TemporaryDirectory(prefix="closeout-bench-", dir=Path.home()) as tmp:
        home = Path(tmp)
        os.environ["HERMES_HOME"] = str(home)
        os.environ["HERMES_KANBAN_HOME"] = str(home)
        # A pinned DB is interpreted as a dispatcher board by core. Isolate through KANBAN_HOME instead.
        os.environ.pop("HERMES_KANBAN_DB", None)
        from hermes_cli.kanban_db import kanban_db_path
        from agent.delegation_context import kanban_path_is_fenced
        if kanban_db_path() != home / "kanban.db" or kanban_path_is_fenced(home / "kanban.db"):
            raise RuntimeError("replay board is not an unfenced, fixture-owned path")
        os.environ["HERMES_KANBAN_BOARD"] = "default"
        os.environ["HERMES_HUB_STATE"] = str(home)
        os.environ["HERMES_MAIL_CONFIG"] = str(home / "mail.json")
        (home / "mail.json").write_text('{"gateway":"hub"}')
        os.environ.pop("CHAT_CLOSEOUT_OFF", None)
        t0 = time.perf_counter()
        world = build(home, rng, args.sessions, args.messages, args.tasks, args.replica_rows, args.pending)
        build_s = time.perf_counter() - t0
        plugin = load_plugin(args.plugin.resolve())
        core = plugin.core
        led = core.Ledger(home / "ledger.db")
        tids = world["tids"]
        batches = {}
        with sqlite3.connect(world["board"]) as conn:
            status = dict(conn.execute("SELECT id, status FROM tasks"))
        for i, sid in enumerate(world["waiting"]):
            batch = rng.sample(tids, 3)
            if i % 2 == 0:  # settled: every card done
                with sqlite3.connect(world["board"]) as conn:
                    conn.executemany("UPDATE tasks SET status = 'done', completed_at = 100 WHERE id = ?",
                                     [(t,) for t in batch])
                for t in batch:
                    status[t] = "done"
            batches[sid] = batch
            led.write(sid, batch=batch, open=[t for t in batch if status[t] != "done"])
        plugin.ledger = lambda: led
        plugin.board_paths = lambda: [world["board"]]
        plugin.state_dbs = lambda: [home / "state.db"]
        plugin.remote_cards = lambda: []
        plugin.hub_db_path = lambda: world["hub"]
        plugin._compression_tip = lambda sid: sid
        plugin._unseen_counts = lambda: {}
        plugin._host_lease = lambda: None
        plugin._live_ids = lambda: set()
        plugin._server = lambda: type("Srv", (), {"_wake_session_turn": staticmethod(lambda *a, **k: "busy")})()
        plugin.owner_wake_tick = getattr(plugin, "owner_wake_tick", lambda **k: None)
        seq = world["seq"]
        trace: dict = {}
        if os.environ.get("CLOSEOUT_BENCH_TRACE") and hasattr(plugin, "wi"):
            real_key = plugin.wi.row_key

            def traced(row, cards, lineage, fields=None, extra=None):
                keys = set(row.get("batch") or []) | set(row.get("covered") or [])
                now = {"row": plugin.wi.digest(sorted(row.items())),
                       "cards": plugin.wi.digest(plugin.wi.card_view(cards, keys)),
                       "lineage": plugin.wi.digest(lineage)}
                who = sys._getframe(1).f_code.co_name
                sid = (who, row["session_id"])
                last = trace.setdefault("last", {}).get(sid)
                if last is not None:
                    for part, value in now.items():
                        if last[part] != value:
                            trace.setdefault("moved", {}).setdefault(f"{who}:{part}", 0)
                            trace["moved"][f"{who}:{part}"] += 1
                            if part == "row" and len(trace.setdefault("row_fields", [])) < 5:
                                trace["row_fields"].append(sorted(k for k in row if trace["rows"][sid].get(k) != row.get(k)))
                trace["last"][sid] = now
                trace.setdefault("rows", {})[sid] = dict(row)
                return real_key(row, cards, lineage, fields, extra)
            plugin.wi.row_key = traced
        # Warm-up (imports, first schema reads, first full reconcile): a process pays it once, not per window.
        plugin.waiting_source()
        plugin._host_pass(False)
        try:
            from hermes_cli import loop_accounting as la
            la.reset()
        except Exception:
            pass
        cpu = {"waiting": 0.0, "host": 0.0}
        wall0, mono0 = clock.wall, clock.mono   # window 0 starts here; window w is exactly w * STEP_S later
        floor_s = getattr(getattr(plugin, "wi", None), "FLOOR_S", 600)
        floors = set()
        passes = {"waiting": 0, "host": 0}
        moved = {"state_msg": 0, "board_rev": 0, "hub_seq": 0}
        next_host = 0.0
        msg_ts = 10_000
        prof = None
        if os.environ.get("CLOSEOUT_BENCH_PROFILE"):
            import cProfile
            prof = cProfile.Profile()
            prof.enable()
        for w in range(WINDOWS):
            sim = w * STEP_S
            if w:
                clock.advance(STEP_S)
            if not (abs(time.time() - (wall0 + sim)) < 1e-6 and
                    abs(time.monotonic() - (mono0 + sim)) < 1e-6):
                raise RuntimeError("replay clock did not advance one step per window")
            floors.add(int(plugin.time.time() // floor_s))
            if rng.random() < RATES["state_msg"]:
                moved["state_msg"] += 1
                sid = rng.choice(world["waiting"]) if rng.random() < WAITING_HIT else rng.choice(world["sids"])
                msg_ts += 1
                with sqlite3.connect(home / "state.db") as conn:
                    conn.execute("INSERT INTO messages (session_id, role, content, timestamp, active) "
                                 "VALUES (?, 'user', 'new message', ?, 1)", (sid, msg_ts))
            if rng.random() < RATES["board_rev"]:
                moved["board_rev"] += 1
                tid = rng.choice(tids)
                with sqlite3.connect(world["board"]) as conn:
                    conn.execute("UPDATE tasks SET title = ? WHERE id = ?", (f"retitled {w}", tid))
            if rng.random() < RATES["hub_seq"]:
                moved["hub_seq"] += 1
                with sqlite3.connect(world["hub"]) as conn:
                    for _ in range(rng.randint(1, 30)):
                        seq += 1
                        i = rng.randrange(world["replica_rows"])
                        data = {"id": f"t_r{i:06d}", "status": "running", "activity_at": float(seq)}
                        if rng.random() > ACTIVITY_ONLY:
                            data["status"] = rng.choice(["running", "blocked", "done"])
                        conn.execute("UPDATE replica SET seq = ?, data = ? WHERE gw = ? AND coll = 'tasks' "
                                     "AND key = ?", (seq, json.dumps(data), f"worker-{i % 8}",
                                                     f"default/default/t_r{i:06d}"))
                    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_ok', ?)", (str(time.time()),))
            plugin._state.pop("waiting_cache", None)
            c0 = time.process_time()
            inputs = getattr(plugin, "waiting_inputs", None)
            if inputs is not None:  # core reads the declared inputs on each annotation fingerprint
                inputs()
            plugin.waiting_source()
            cpu["waiting"] += time.process_time() - c0
            cache = plugin._state.get("waiting_cache")
            if cache is not None and abs(cache[0] - clock.mono) >= 1e-6:
                raise RuntimeError("waiting freshness stamp is not on the replay clock")
            passes["waiting"] += 1
            if sim >= next_host:
                next_host += TICK_S
                c0 = time.process_time()
                plugin._host_pass(False)
                cpu["host"] += time.process_time() - c0
                passes["host"] += 1
        if prof is not None:
            import pstats
            prof.disable()
            stats = pstats.Stats(prof, stream=sys.stderr)
            stats.sort_stats(os.environ.get("CLOSEOUT_BENCH_PROFILE_SORT", "tottime"))
            stats.print_stats(os.environ.get("CLOSEOUT_BENCH_PROFILE_FILTER", ""), 40)
        replay_s = WINDOWS * STEP_S
        if len(floors) < 2:
            raise RuntimeError("replay omitted the production floor rollover")
        if abs(clock.wall - wall0 - (WINDOWS - 1) * STEP_S) >= 1e-6:
            raise RuntimeError("replay clock did not cross the replay")
        if passes != {"waiting": WINDOWS, "host": int(WINDOWS * STEP_S / TICK_S)}:
            raise RuntimeError(f"due clock off: {passes}")
        out = {
            "plugin": str(args.plugin.resolve()),
            "scale": {k: getattr(args, k) for k in ("sessions", "messages", "tasks", "replica_rows", "pending")},
            "build_s": round(build_s, 1), "replay_s": replay_s, "windows_moved": moved, "passes": passes,
            "clock_advance_s": clock.wall - wall0, "floor_buckets": sorted(floors),
            "cpu_s": {k: round(v, 3) for k, v in cpu.items()},
            "pct_core": {k: round(100 * v / replay_s, 3) for k, v in cpu.items()},
            "waiting_rows": len(plugin.waiting_source()),
        }
        if trace:
            out["trace"] = {"moved": trace.get("moved"), "row_fields": trace.get("row_fields")}
        try:
            from hermes_cli import loop_accounting as la
            out["accounting"] = {k: v for k, v in la.counters().items() if k.startswith("chat-closeout")}
        except Exception as exc:
            out["accounting"] = f"unavailable: {exc}"
        text = json.dumps(out, indent=1, default=str)
        print(text)
        if args.json:
            args.json.write_text(text)
    failures = budget_failures({k: 100 * v / replay_s for k, v in cpu.items()})
    for f in failures:
        print(f"BUDGET FAIL: {f}", file=sys.stderr)
    return 1 if failures and not args.diagnostic else 0


if __name__ == "__main__":
    sys.exit(main())
