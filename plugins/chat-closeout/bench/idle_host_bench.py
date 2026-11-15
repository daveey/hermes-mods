#!/usr/bin/env python3
"""Idle host-pass replay (card t_01a1deb500fca100): CPU per 30 s tick of each host-pass loop on a gateway with no
work, and the ledger it leaves behind, so a before/after run can be compared for equal output.

Shape of the measured MacBook idle window: ``--missing`` ledger rows (default 11) whose batch holds a card no board
or inventory knows (an unlink clock that ages on every healthy tick), a few settled rows whose close-out is held back
(the user is typing), working rows, a healthy hub replica whose ``last_ok`` moves on every tick. Nothing else moves.

    python3 bench/idle_host_bench.py --plugin plugins/chat-closeout [--ticks 12] [--json out.json]

Needs core importable (PYTHONPATH=<hermes-agent checkout>), like the tests. Prints per-tick thread CPU (ms) of
``wake_tick``, ``owner_wake_tick``, ``watchdog_tick`` and the whole ``_host_pass``, and a digest of the ledger rows
after every tick (``ledger_digests``) plus every wake attempted (``wakes``).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

TICK_S = 30.0


def load_plugin(plugin_dir: Path):
    spec = importlib.util.spec_from_file_location("closeout_plugin_idle_bench", plugin_dir / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ""
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--missing", type=int, default=11)
    ap.add_argument("--settled", type=int, default=3)
    ap.add_argument("--working", type=int, default=6)
    ap.add_argument("--sessions", type=int, default=600)
    ap.add_argument("--messages", type=int, default=60_000)
    ap.add_argument("--tasks", type=int, default=600)
    ap.add_argument("--replica-rows", type=int, default=3000)
    ap.add_argument("--ticks", type=int, default=12)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import replay_bench as rb
    with rb.ReplayClock(wall=1_800_000_000.0, mono=50_000.0) as clock, tempfile.TemporaryDirectory(prefix="closeout-idle-bench-", dir=Path.home()) as tmp:
        home = Path(tmp)
        os.environ.update(HERMES_HOME=str(home), HERMES_KANBAN_HOME=str(home), HERMES_KANBAN_BOARD="default",
                          HERMES_HUB_STATE=str(home), HERMES_MAIL_CONFIG=str(home / "mail.json"))
        os.environ.pop("HERMES_KANBAN_DB", None)
        os.environ.pop("CHAT_CLOSEOUT_OFF", None)
        (home / "mail.json").write_text('{"gateway":"hub"}')
        rng = random.Random(1)
        world = rb.build(home, rng, args.sessions, args.messages, args.tasks, args.replica_rows, 0)
        plugin = load_plugin(args.plugin.resolve())
        core = plugin.core
        led = core.Ledger(home / "ledger.db")
        tids = world["tids"]
        with sqlite3.connect(world["board"]) as conn:
            done = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE status = 'done'")]
            running = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE status != 'done'")]
        sids = iter(world["sids"])
        for i in range(args.missing):
            led.write(next(sids), batch=[f"t_missing{i:02d}"], open=[f"t_missing{i:02d}"])
        for i in range(args.settled):
            batch = rng.sample(done, 2)
            led.write(next(sids), batch=batch, open=[])
        for i in range(args.working):
            batch = rng.sample(running, 2)
            led.write(next(sids), batch=batch, open=batch)
        plugin.ledger = lambda: led
        plugin.board_paths = lambda: [world["board"]]
        plugin.state_dbs = lambda: [home / "state.db"]
        plugin.remote_cards = lambda: []
        plugin.hub_db_path = lambda: world["hub"]
        plugin._compression_tip = lambda sid: sid
        plugin._unseen_counts = lambda: {}
        plugin._host_lease = lambda: None
        plugin._live_ids = lambda: set()
        wakes: list = []

        def wake_session_turn(sid, *a, **k):
            wakes.append((round(clock.wall - 0), sid))
            return "busy"

        plugin._server = lambda: type("Srv", (), {"_wake_session_turn": staticmethod(wake_session_turn)})()
        # The user is typing in every chat for the first half hour, then not: wakes begin when the quiet period ends.
        typing_until = clock.wall + 20 * TICK_S
        real_last_user = core.last_user_at
        core.last_user_at = lambda dbs, ids, **kw: (typing_until - core.USER_QUIET_S) if clock.wall < typing_until else 0.0
        cpu_names = ("wake_tick", "owner_wake_tick", "owner_relay_tick", "watchdog_tick")
        spent = {n: [] for n in cpu_names}
        orig = {n: getattr(plugin, n) for n in cpu_names}

        def timed(name):
            def run(*a, **k):
                c0 = time.thread_time()
                try:
                    return orig[name](*a, **k)
                finally:
                    spent[name].append((time.thread_time() - c0) * 1000.0)
            return run

        for n in cpu_names:
            setattr(plugin, n, timed(n))
        plugin.waiting_source()
        plugin._host_pass(False)  # warm-up: imports, schema reads, first judgments
        for n in cpu_names:
            spent[n].clear()
        digests, totals = [], []
        for _ in range(args.ticks):
            clock.advance(TICK_S)
            with sqlite3.connect(world["hub"]) as conn:  # the hub syncs: last_ok moves, nothing else
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_ok', ?)", (str(clock.wall),))
            c0 = time.thread_time()
            plugin._host_pass(False)
            totals.append((time.thread_time() - c0) * 1000.0)
            rows = sorted(({k: (sorted(v) if isinstance(v, (set, frozenset)) else v) for k, v in r.items()
                            if k not in ("updated_at",)} for r in led.pending()), key=lambda r: r["session_id"])
            blob = json.dumps(rows, sort_keys=True, default=str).replace(str(os.getpid()), "PID")
            digests.append(hashlib.sha1(blob.encode()).hexdigest()[:12])
        core.last_user_at = real_last_user
        report = {
            "ticks": args.ticks,
            "total_ms_per_tick": [round(x, 1) for x in totals],
            "ms_per_tick": {n: [round(x, 1) for x in v] for n, v in spent.items()},
            "mean_ms": {n: round(sum(v) / max(1, len(v)), 1) for n, v in spent.items()},
            "mean_total_ms": round(sum(totals) / max(1, len(totals)), 1),
            "pct_core_at_30s": round(sum(totals) / max(1, len(totals)) / (TICK_S * 1000) * 100, 2),
            "ledger_digests": digests,
            "wakes": wakes,
        }
        try:
            from hermes_cli import loop_accounting as la
            report["accounting"] = {k: v for k, v in la.counters().items() if k.startswith("chat-closeout.wake_tick")}
        except Exception:
            pass
        print(json.dumps(report, indent=1))
        if args.json:
            args.json.write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
