#!/usr/bin/env python3
"""Tests for the fixer lane (coach_fixer.py + coach_apply auto's kind fix):  python3 test_coach_fixer.py
Fake kanban.db / state.db in a temp HERMES_HOME and the fake `hermes` binary from test_coach_apply."""
import json, os, sqlite3, sys, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coach_apply as ca  # noqa: E402
import coach_fixer as fx  # noqa: E402
import coach_proposals as cp  # noqa: E402
from test_coach_apply import Base  # noqa: E402

FIX = {"kind": "fix", "target": "live-checkout-hint-misfire", "title": "live_checkout hint fires on read-only calls",
       "evidence": "macbook/default/20261018_112333_df0f6d: hint on a read-only grep",
       "change": {"sessions": ["macbook/default/20261018_112333_df0f6d", "macbook/default/20261017_203513_6c2edc"],
                  "hypothesis": "the mutation and the live path are matched anywhere in the command",
                  "acceptance": ["read-only commands that name the live tree never get the hint"],
                  "area": "plugin:coach"}}


def kdb(home):
    p = home / "kanban/boards/hermes/kanban.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.executescript("""
      create table if not exists tasks (id text primary key, title text, created_by text, idempotency_key text,
                                        status text, created_at integer);
      create table if not exists task_runs (id integer primary key, task_id text, status text, started_at integer,
                                            ended_at integer);
      create table if not exists worker_recovery_sessions (run_id integer, task_id text, profile text,
                                                           state_db text, session_id text, ts real);""")
    return c


def sdb(home):
    c = sqlite3.connect(home / "state.db")
    c.execute("create table if not exists sessions (id text primary key, source text, title text, parent_session_id "
              "text, started_at real, estimated_cost_usd real, actual_cost_usd real)")
    return c


class Fixer(Base):
    def setUp(self):
        super().setUp()
        self.k, self.s = kdb(self.home), sdb(self.home)
        self.now = time.time()

    def card(self, tid, key, created_by="coach", at=None, cost=0.0, child=0.0, ledger=True, title="t"):
        at = at or self.now
        self.k.execute("insert into tasks values (?,?,?,?,?,?)", (tid, title, created_by, key, "done", at))
        cur = self.k.execute("insert into task_runs (task_id, status, started_at, ended_at) values (?,?,?,?)",
                             (tid, "done", at, at + 600))
        sid = f"s_{tid}"
        if ledger:
            self.k.execute("insert into worker_recovery_sessions values (?,?,?,?,?,?)",
                           (cur.lastrowid, tid, "default", str(self.home / "state.db"), sid, at))
        self.s.execute("insert into sessions values (?,?,?,?,?,?,?)", (sid, "kanban", title, None, at + 5, cost, None))
        if child:
            self.s.execute("insert into sessions values (?,?,?,?,?,?,?)", (sid + "_c", "subagent", "c", sid, at, child, None))
        self.k.commit(); self.s.commit()

    def cards_created(self):
        f = Path(os.environ["FAKE_CFG"] + ".cards")
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_spend_sums_coach_cards_by_run_day_with_subagents(self):
        self.card("t_a", "coach-fix:a", cost=1.5, child=0.5)
        self.card("t_b", "coach-bug:x", cost=1.0, ledger=False, title="Hermes bug: x")  # pre-ledger: title match
        self.card("t_c", "other", created_by="default", cost=9.0)
        self.card("t_d", "coach-fix:d", at=self.now - 3 * 86400, cost=4.0)
        d = fx.spend_by_day(self.home, "hermes", 1, self.now)[fx.utc_day(self.now)]
        self.assertEqual((d["usd"], d["runs"], sorted(d["cards"])), (3.0, 2, ["t_a", "t_b"]))

    def test_log_spend_replaces_rows_idempotently(self):
        self.card("t_a", "coach-fix:a", cost=2.0)
        for _ in range(2):
            fx.log_spend(self.env.data, fx.spend_by_day(self.home, "hermes", 2, self.now), host="mac")
        rows = [json.loads(x) for x in fx.spend_log_path(self.env.data).read_text().splitlines()]
        self.assertEqual([(r["lane"], r["usd"]) for r in rows], [("fixer", 2.0)])
        self.assertEqual(fx.logged_spend(self.env.data, fx.utc_day(self.now)), 2.0)

    def test_auto_files_one_fix_card_with_runtime_cap(self):
        pid = self.add(**FIX)
        self.run_auto(now=self.now)
        p = self.st(pid)
        self.assertEqual((p["status"], p["card"]), ("carded", "t_abc12345"))
        call = self.cards_created()[0]
        for flag, val in (("--created-by", "coach"), ("--idempotency-key", "coach-fix:live-checkout-hint-misfire"),
                          ("--max-runtime", "3600")):
            self.assertEqual(call[call.index(flag) + 1], val)
        body = call[call.index("--body") + 1]
        for part in ("## Sessions (repro)", "20261018_112333_df0f6d", "## Hypothesis", "## Acceptance",
                     "kanban_request_review"):
            self.assertIn(part, body)

    def test_daily_card_cap_defers(self):
        self.card("t_today", "coach-fix:earlier", cost=0.5)
        pid = self.add(**FIX)
        self.run_auto(now=self.now)
        self.assertEqual(self.st(pid)["status"], "pending")
        self.assertEqual(self.cards_created(), [])

    def test_spend_cap_defers(self):
        self.card("t_bug", "coach-bug:x", cost=7.2)
        pid = self.add(**FIX)
        done = self.run_auto(now=self.now)
        self.assertEqual(self.st(pid)["status"], "pending")
        self.assertIn("$7", [d for d in done if d[0] == pid][0][2])

    def test_existing_slug_card_is_reused(self):
        self.card("t_old", "coach-fix:live-checkout-hint-misfire", at=self.now - 5 * 86400)
        pid = self.add(**FIX)
        self.run_auto(now=self.now)
        self.assertEqual((self.st(pid)["status"], self.st(pid)["card"]), ("carded", "t_old"))
        self.assertEqual(self.cards_created(), [])

    def test_apply_force_files_past_daily_cap(self):
        self.card("t_today", "coach-fix:earlier", cost=0.5)
        pid = self.add(**FIX)
        ca.cmd_apply(self.env, type("A", (), {"id": pid, "force": True, "dry_run": False})())
        self.assertEqual((self.st(pid)["status"], self.st(pid)["card"]), ("carded", "t_abc12345"))
        self.assertEqual(len(self.cards_created()), 1)

    def test_validation_needs_recurrence_hypothesis_acceptance(self):
        bad = dict(FIX, change={"sessions": ["one"], "hypothesis": "", "acceptance": []})
        errs = cp.validate(bad)
        self.assertEqual(len(errs), 3, errs)
        self.assertEqual(cp.validate(FIX), [])
        self.assertEqual(cp.default_tier(FIX), "review")
        self.assertIn("slug", " ".join(cp.validate(dict(FIX, target="Not A Slug"))))

    def test_add_skips_slug_already_pending(self):
        rep = self.env.data / "reports/2026-10-19.md"
        block = "```coach-proposal\n" + json.dumps(FIX) + "\n```\n"
        alt = dict(FIX, evidence="other", change=dict(FIX["change"], hypothesis="different words"))
        rep.write_text(block + "```coach-proposal\n" + json.dumps(alt) + "\n```\n")
        cp.cmd_add(type("A", (), {"data": str(self.env.data), "report": str(rep)})())
        fixes = [p for p in cp.state(self.env.data).values() if p["kind"] == "fix"]
        self.assertEqual(len(fixes), 1)
        self.assertIn("evidence", FIX)


class ReadOnlyOpen(unittest.TestCase):
    def test_wal_board_without_shm_is_readable(self):
        """A WAL board nobody holds open has no -shm file; `mode=ro` can't create one and failed with
        'unable to open database file' (found by the real-CLI integration run on a fresh board)."""
        import tempfile
        d = Path(tempfile.mkdtemp())
        p = d / "kanban.db"
        c = sqlite3.connect(p)
        c.execute("pragma journal_mode=wal")
        c.execute("create table tasks (id text, created_by text)")
        c.execute("insert into tasks values ('t_1', 'coach')")
        c.commit(); c.close()
        for side in ("kanban.db-shm", "kanban.db-wal"):
            (d / side).unlink(missing_ok=True)
        with fx._ro(p) as k:
            self.assertEqual(k.execute("select id from tasks").fetchall(), [("t_1",)])
            with self.assertRaises(sqlite3.OperationalError):
                k.execute("insert into tasks values ('t_2', 'x')")


if __name__ == "__main__":
    unittest.main()
