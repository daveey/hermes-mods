#!/usr/bin/env python3
"""Tests for engine_budget.py (the $20/day engine cap + in-flight watchdog):  python3 test_engine_budget.py"""
import json, os, sqlite3, sys, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import coach_eval as ce  # noqa: E402
import coach_fixer as fx  # noqa: E402
import coach_trial as ct  # noqa: E402
import engine_budget as eb  # noqa: E402

DAY = 86400


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home, self.data = self.tmp / "home", self.tmp / "data"
        (self.data / "research/state").mkdir(parents=True)
        kp = self.home / "kanban/boards/hermes/kanban.db"
        kp.parent.mkdir(parents=True)
        self.k = sqlite3.connect(kp)
        self.k.executescript("""
          create table tasks (id text primary key, title text, body text, created_by text, idempotency_key text,
                              status text, created_at integer, worker_pid integer);
          create table task_runs (id integer primary key, task_id text, status text, started_at integer, ended_at integer);
          create table worker_recovery_sessions (run_id integer, task_id text, profile text, state_db text,
                                                 session_id text, ts real);""")
        self.s = sqlite3.connect(self.home / "state.db")
        self.s.execute("create table sessions (id text primary key, source text, title text, parent_session_id text, "
                       "started_at real, estimated_cost_usd real, actual_cost_usd real)")
        self.now = time.time()
        os.environ["COACH_INBOX"] = "0"
        self.calls = []

    def tearDown(self):
        os.environ.pop("COACH_INBOX", None)
        self.k.close(); self.s.close()

    def card(self, tid, key=None, by="coach", cost=0.0, status="running", body="", at=None, pid=None):
        at = at or self.now
        self.k.execute("insert into tasks values (?,?,?,?,?,?,?,?)", (tid, f"Fix {tid}", body, by, key, status, at, pid))
        rid = self.k.execute("insert into task_runs (task_id, status, started_at) values (?,?,?)",
                             (tid, status, at)).lastrowid
        self.k.execute("insert into worker_recovery_sessions values (?,?,?,?,?,?)",
                       (rid, tid, "default", str(self.home / "state.db"), f"s_{tid}", at))
        self.s.execute("insert into sessions values (?,?,?,?,?,?,?)", (f"s_{tid}", "kanban", "t", None, at, cost, None))
        self.k.commit(); self.s.commit()

    def ledger(self, *rows):
        with (self.data / "research/state/spend.jsonl").open("a") as fh:
            for lane, usd, *extra in rows:
                r = {"date": fx.utc_day(self.now), "lane": lane, "usd": usd}
                r.update(extra[0] if extra else {})
                fh.write(json.dumps(r) + "\n")

    def run_(self, args, **kw):
        self.calls.append(list(map(str, args)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # ── total cap closes every lane gate ──
    def over_total(self):
        # every lane under its own cap, the engine total >= $20
        self.ledger(("scout", 2.9), ("trial", 9.0, {"kind": "reserve"}), ("bot", 7.0))
        self.card("t_fix1", "coach-fix:x", cost=1.5, status="done")

    def test_total_cap_blocks_fixer_and_trial_gates(self):
        self.over_total()
        snap = eb.snapshot(self.home, "hermes", self.data, self.now)
        self.assertAlmostEqual(snap["total"], 20.4)
        self.assertEqual(snap["lanes"]["fixer"], 1.5)
        ok, why, _ = fx.gate(self.home, "hermes", self.now, data=self.data)
        self.assertFalse(ok); self.assertIn("engine total", why)
        ok, why, _ = ct.gate(self.home, "hermes", self.data, self.now)
        self.assertFalse(ok); self.assertIn("engine total", why)

    def test_total_cap_blocks_eval_budget(self):
        self.over_total()

        class R:
            offline_only, max_cost_usd = True, 0.01

            def __call__(self, prompt, **kw):
                return "ok", {}
        ledger = self.data / "research/state/spend.jsonl"
        b = ce.Budget(5, ledger=ledger, engine=lambda: eb.snapshot(self.home, "hermes", self.data))
        self.assertIsNone(b.call(R(), "hi"))  # live fixer card ($1.50) pushes the total past $20
        self.assertEqual(b.stop_reason, "engine total cap")
        self.assertIsNotNone(ce.Budget(5, ledger=ledger).call(R(), "hi"))  # ledger alone: $18.91 < $20
        self.ledger(("bot", 1.2))
        b2 = ce.Budget(5, ledger=ledger)
        self.assertIsNone(b2.call(R(), "hi"))  # ledger alone past $20 also refuses (no engine)
        self.assertEqual(b2.stop_reason, "engine total cap")

    def test_open_under_caps_and_override_raises_total(self):
        self.ledger(("scout", 1.0))
        ok, why, _ = fx.gate(self.home, "hermes", self.now, data=self.data)
        self.assertTrue(ok, why)
        self.over_total()
        self.assertFalse(eb.check(eb.snapshot(self.home, "hermes", self.data, self.now), "fixer")[0])
        with (self.data / "research/state/budget-overrides.jsonl").open("w") as fh:
            fh.write(json.dumps({"date": fx.utc_day(self.now), "lane": "total", "usd": 30, "why": "t"}) + "\n")
        self.assertTrue(eb.check(eb.snapshot(self.home, "hermes", self.data, self.now), "fixer")[0])

    def test_live_board_replaces_this_hosts_stale_card_rows(self):
        self.ledger(("fixer", 5.0, {"host": "me", "cards": {"t_fix1": 5.0}}),
                    ("fixer", 1.0, {"host": "other", "cards": {"t_o": 1.0}}))
        self.card("t_fix1", "coach-fix:x", cost=6.0)
        snap = eb.snapshot(self.home, "hermes", self.data, self.now, me="me")
        self.assertEqual(snap["lanes"]["fixer"], 7.0)

    # ── watchdog ──
    def test_watchdog_stops_over_budget_engine_card_only(self):
        self.card("t_eng", "coach-fix:loop", cost=7.5, pid=4242)               # over its $7 card budget
        self.card("t_trial", "coach-trial:idea", cost=1.0, body="Budget for this card ≤ $0.5")  # over its own $0.5
        self.card("t_ok", "coach-fix:fine", cost=0.0, status="done")
        self.card("t_human", None, by="default", cost=50.0)                     # non-engine, huge: never touched
        killed = []
        res = eb.watchdog(self.home, "hermes", self.data, "hermes", self.now, out=lambda m: None,
                          kill=lambda p, s: killed.append(p), pids=lambda tid, hint=None: [hint or 1],
                          run=self.run_)
        stopped = sorted(c["id"] for c, *_ in res["stops"])
        self.assertEqual(stopped, ["t_eng", "t_trial"])
        sched = [c for c in self.calls if "schedule" in c]
        self.assertEqual(sorted(c[c.index("schedule") + 1] for c in sched), ["t_eng", "t_trial"])
        self.assertTrue(all("SCHEDULED_UNTIL=" in c[-1] and "resumes next UTC day" in c[-1] for c in sched))
        self.assertIn(4242, killed)
        self.assertFalse(any("t_human" in " ".join(c) for c in self.calls))
        rows = eb.jsonl(self.data / "research/state/budget-stops.jsonl")
        self.assertEqual(sorted(r["card"] for r in rows), ["t_eng", "t_trial"])

    def test_watchdog_lane_or_total_cap_stops_running_card(self):
        self.over_total()
        self.card("t_run", "coach-fix:y", cost=0.1)
        res = eb.watchdog(self.home, "hermes", self.data, "hermes", self.now, out=lambda m: None,
                          kill=lambda p, s: None, pids=lambda tid, hint=None: [], run=self.run_)
        self.assertEqual([c["id"] for c, *_ in res["stops"]], ["t_run"])
        self.assertIn("engine total", res["stops"][0][3])

    def test_dry_run_changes_nothing(self):
        self.card("t_eng", "coach-fix:loop", cost=9.0)
        res = eb.watchdog(self.home, "hermes", self.data, "hermes", self.now, dry=True, out=lambda m: None,
                          kill=lambda p, s: self.fail("killed"), run=self.run_)
        self.assertEqual(len(res["stops"]), 1)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.data / "research/state/budget-stops.jsonl").exists())

    def test_worker_pids_matches_only_the_task_env(self):
        import subprocess as sp
        p = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                     env=dict(os.environ, HERMES_KANBAN_TASK="t_zzz123"))
        try:
            self.assertEqual(eb.worker_pids("t_zzz123", p.pid), [p.pid])
            self.assertEqual(eb.worker_pids("t_zzz12", p.pid), [])
        finally:
            p.kill(); p.wait()

    # ── next UTC day ──
    def test_next_day_reset_reopens_lanes_and_resumes_stopped_cards(self):
        self.over_total()
        self.card("t_run", "coach-fix:y", cost=0.1)
        eb.watchdog(self.home, "hermes", self.data, "hermes", self.now, out=lambda m: None,
                    kill=lambda p, s: None, pids=lambda tid, hint=None: [], run=self.run_)
        self.k.execute("update tasks set status='scheduled' where id='t_run'"); self.k.commit()
        tomorrow = self.now + DAY
        snap = eb.snapshot(self.home, "hermes", self.data, tomorrow)
        self.assertEqual(snap["total"], 0.0)
        self.assertTrue(all(eb.check(snap, ln)[0] for ln in ("scout", "fixer", "trial")))
        self.calls.clear()
        res = eb.watchdog(self.home, "hermes", self.data, "hermes", tomorrow, out=lambda m: None,
                          kill=lambda p, s: None, pids=lambda tid, hint=None: [], run=self.run_)
        self.assertEqual(res["resumed"], [("t_run", "resumed")])
        self.assertTrue(any(c[-2:] == ["unblock", "t_run"] for c in self.calls))
        # resumed once: the next pass leaves it alone
        self.calls.clear()
        eb.watchdog(self.home, "hermes", self.data, "hermes", tomorrow + 900, out=lambda m: None,
                    kill=lambda p, s: None, pids=lambda tid, hint=None: [], run=self.run_)
        self.assertFalse(any("unblock" in c for c in self.calls))

    def test_weekly_budget_section_lists_enforced_stops(self):
        import coach_yield as y
        self.card("t_eng", "coach-fix:loop", cost=7.5)
        eb.watchdog(self.home, "hermes", self.data, "hermes", self.now, out=lambda m: None,
                    kill=lambda p, s: None, pids=lambda tid, hint=None: [], run=self.run_)
        buckets, _, _ = y.spend_buckets([], self.now)
        lines = y.enforced_stops(self.data, buckets)
        self.assertEqual(len(lines), 1)
        self.assertIn("stopped `t_eng`", lines[0]); self.assertIn("card $7.50 of $7", lines[0])
        text = "\n".join(y.section_yield(self.data, self.now))
        self.assertIn("### Enforced budget stops", text); self.assertIn("t_eng", text)

    def test_inbox_posts_once_per_state_change(self):
        os.environ["COACH_INBOX"] = "1"
        self.over_total()
        for _ in range(2):
            eb.watchdog(self.home, "hermes", self.data, "hermes", self.now, out=lambda m: None,
                        kill=lambda p, s: None, pids=lambda tid, hint=None: [], run=self.run_)
        posts = [c for c in self.calls if c[1:3] == ["inbox", "post"]]
        self.assertEqual(len(posts), 1)
        self.assertIn(f"engine-budget:{fx.utc_day(self.now)}", posts[0])
        self.assertIn("budget-overrides.jsonl", posts[0][posts[0].index("--prompt") + 1])


if __name__ == "__main__":
    unittest.main()
