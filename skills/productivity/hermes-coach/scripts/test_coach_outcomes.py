#!/usr/bin/env python3
"""Card-outcome memory: coach-apply auto records what a proposal's linked card ended with, as metadata only.
python3 -m pytest test_coach_outcomes.py  (fixture boards are temp SQLite files; never a real board)."""
import json, sqlite3, sys, time, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import coach_apply as ca  # noqa: E402
import coach_proposals as cp  # noqa: E402
import coach_weekly  # noqa: E402
import coach_yield  # noqa: E402
from test_coach_apply import Base  # noqa: E402

SCHEMA = """
create table tasks (id text primary key, title text not null, body text, assignee text, status text not null,
  created_by text, created_at integer not null, completed_at integer, idempotency_key text, worker_pid integer);
create table task_runs (id integer primary key autoincrement, task_id text not null, profile text, status text not null,
  started_at integer not null, ended_at integer, outcome text, summary text, metadata text, error text);
"""
T0 = 1_790_000_000  # 2026-09-21


def board(home, slug):
    p = home / "kanban.db" if slug == "default" else home / "kanban" / "boards" / slug / "kanban.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        with sqlite3.connect(p) as c:
            c.executescript(SCHEMA)
    return p


def card(db, tid, status, runs=(), completed=T0 + 3600):
    """runs: [(run_status, outcome, summary)] oldest first."""
    with sqlite3.connect(db) as c:
        c.execute("insert or replace into tasks (id, title, status, created_by, created_at, completed_at) "
                  "values (?, ?, ?, 'default', ?, ?)", (tid, "fixture card", status, T0, completed))
        for i, (rs, oc, sm) in enumerate(runs):
            c.execute("insert into task_runs (task_id, status, started_at, ended_at, outcome, summary) "
                      "values (?, ?, ?, ?, ?, ?)", (tid, rs, T0 + i * 60, T0 + i * 60 + 30, oc, sm))


class Fixture(Base):
    def setUp(self):
        super().setUp()
        self.db = board(self.home, "hermes")

    def carded(self, tid, status="carded", **p):
        p.setdefault("kind", "core")
        p.setdefault("target", "agent loop " + tid)
        p.setdefault("change", {"summary": "x"})
        pid = self.add(**p)
        cp.append(self.env.data, {"id": pid, "status": status, "at": ca.now_iso(), "by": "test", "card": tid})
        return pid

    def rows(self):
        return [json.loads(l) for l in (self.env.data / "reports/proposals.jsonl").read_text().splitlines()]

    def auto(self, dry=False):
        log = []
        done = ca.auto(self.env, dry=dry, out=log.append)
        return done, log


class Outcomes(Fixture):
    def test_done_card_records_latest_completed_summary_and_keeps_status(self):
        card(self.db, "t_aaa111", "done", [("crashed", "crashed", None), ("done", "completed", "first try"),
                                           ("done", "completed", "  shipped the gate\nchange  "),
                                           ("released", "reclaimed", None)])
        pid = self.carded("t_aaa111")
        done, _ = self.auto()
        p = self.st(pid)
        self.assertEqual(p["status"], "carded")  # never inferred from done or the summary
        o = p["card_outcome"]
        self.assertEqual((o["card"], o["board"], o["card_status"], o["run_status"], o["evidence"]),
                         ("t_aaa111", "hermes", "done", "completed", "run_summary"))
        self.assertEqual((o["run_id"], o["summary"]), (3, "shipped the gate change"))
        self.assertEqual(o["completed_at"], "2026-09-21T15:13:20Z")  # the card's completed_at (T0 + 1 h)
        self.assertIn((pid, "card outcome", "t_aaa111 done run 3 completed (run_summary)"), done)
        last = self.rows()[-1]
        self.assertEqual(set(last), {"id", "date", "card_outcome"})  # metadata only: no status / at / by

    def test_archived_card_without_summary_is_explicitly_unknown(self):
        card(self.db, "t_bbb222", "archived", [("crashed", "crashed", None), ("done", "completed", "   ")],
             completed=None)
        card(self.db, "t_bbb333", "archived", [], completed=None)
        a, b = self.carded("t_bbb222"), self.carded("t_bbb333")
        self.auto()
        oa, ob = self.st(a)["card_outcome"], self.st(b)["card_outcome"]
        self.assertEqual((oa["card_status"], oa["run_id"], oa["run_status"], oa["summary"], oa["evidence"]),
                         ("archived", 2, "completed", None, "unknown"))
        self.assertEqual(oa["completed_at"], "2026-09-21T14:14:50Z")  # falls back to the named run's end (T0 + 90 s)
        self.assertEqual((ob["run_id"], ob["run_status"], ob["summary"], ob["evidence"], ob["completed_at"]),
                         (None, None, None, "unknown", None))
        self.assertEqual(self.st(a)["status"], "carded")

    def test_open_card_records_nothing(self):
        card(self.db, "t_ccc444", "running", [("running", None, None)])
        pid = self.carded("t_ccc444")
        self.auto()
        self.assertNotIn("card_outcome", self.st(pid))

    def test_explicit_prior_decision_is_preserved(self):
        card(self.db, "t_ddd555", "done", [("done", "completed", "merged abc123")])
        card(self.db, "t_ddd666", "done", [("done", "completed", "already exists upstream")])
        app = self.carded("t_ddd555")
        rej = self.carded("t_ddd666")
        ca.mark(self.env, app, "applied", by="kanban t_ddd555", result="shipped abc123")
        ca.mark(self.env, rej, "rejected", by="kanban t_ddd666", note="already exists")
        self.auto()
        self.assertEqual((self.st(app)["status"], self.st(app)["result"]), ("applied", "shipped abc123"))
        self.assertEqual((self.st(rej)["status"], self.st(rej)["note"]), ("rejected", "already exists"))
        self.assertEqual(self.st(rej)["card_outcome"]["summary"], "already exists upstream")
        before = self.st(app)["at"]
        self.auto()
        self.assertEqual(self.st(app)["at"], before)  # the outcome row never moves the decision time

    def test_idempotent_and_appends_again_only_when_the_snapshot_changes(self):
        card(self.db, "t_eee777", "done", [("done", "completed", "fixed it")])
        pid = self.carded("t_eee777")
        self.auto()
        n = len(self.rows())
        done, _ = self.auto()
        self.assertEqual(len(self.rows()), n)
        self.assertFalse([d for d in done if d[1] == "card outcome"])
        with sqlite3.connect(self.db) as c:  # the card is later archived: one new snapshot
            c.execute("update tasks set status = 'archived' where id = 't_eee777'")
        self.auto()
        self.assertEqual(len(self.rows()), n + 1)
        self.assertEqual(self.st(pid)["card_outcome"]["card_status"], "archived")

    def test_dry_run_reports_and_writes_nothing(self):
        card(self.db, "t_fff888", "done", [("done", "completed", "done")])
        pid = self.carded("t_fff888")
        f = self.env.data / "reports/proposals.jsonl"
        before = f.read_bytes()
        db_before = self.db.read_bytes()
        done, _ = self.auto(dry=True)
        self.assertIn((pid, "would record outcome", "t_fff888 done run 1 completed (run_summary)"), done)
        self.assertEqual(f.read_bytes(), before)
        self.assertEqual(self.db.read_bytes(), db_before)

    def test_board_scoping_reads_only_the_explicit_board(self):
        other = board(self.home, "other")
        card(other, "t_999aaa", "done", [("done", "completed", "on another board")])
        card(board(self.home, "default"), "t_999bbb", "done", [("done", "completed", "default board")])
        a, b = self.carded("t_999aaa"), self.carded("t_999bbb")
        with patch.dict("os.environ", {"HERMES_KANBAN_DB": str(other), "HERMES_KANBAN_BOARD": "other"}):
            self.auto()  # env.board = hermes: worker pins never reroute the explicit board
        self.assertNotIn("card_outcome", self.st(a))
        self.assertNotIn("card_outcome", self.st(b))
        self.env.board = "other"
        self.auto()
        self.assertEqual(self.st(a)["card_outcome"]["board"], "other")
        self.assertNotIn("card_outcome", self.st(b))

    def test_missing_board_db_is_no_outcomes_not_the_default_board(self):
        card(board(self.home, "default"), "t_777ccc", "done", [("done", "completed", "default")])
        pid = self.carded("t_777ccc")
        self.env.board = "nosuchboard"
        self.auto()
        self.assertNotIn("card_outcome", self.st(pid))

    def test_board_db_routing(self):
        h = Path("/x/.hermes")
        self.assertEqual(cp.board_db(h, "hermes", {}), h / "kanban/boards/hermes/kanban.db")
        self.assertEqual(cp.board_db(h, "default", {}), h / "kanban.db")
        self.assertEqual(cp.board_db(h / "profiles/coach", "hermes", {}), h / "kanban/boards/hermes/kanban.db")
        self.assertEqual(cp.board_db(h, "hermes", {"HERMES_KANBAN_HOME": "/k", "HERMES_KANBAN_DB": "/pin.db"}),
                         Path("/k/kanban/boards/hermes/kanban.db"))
        with self.assertRaises(ValueError):
            cp.board_db(h, "../etc", {})

    def test_summary_is_bounded_and_scrubbed(self):
        card(self.db, "t_123abc", "done", [("done", "completed", "token ghp_" + "A" * 30 + " " + "word " * 200)])
        pid = self.carded("t_123abc")
        self.auto()
        s = self.st(pid)["card_outcome"]["summary"]
        self.assertLessEqual(len(s), cp.OUTCOME_SUMMARY_MAX)
        self.assertNotIn("ghp_A", s)
        self.assertTrue(s.endswith("…"))

    def test_outcome_rows_keep_coach_yield_dates_whole(self):
        card(self.db, "t_456def", "done", [("done", "completed", "ok")])
        self.carded("t_456def")
        self.auto()
        notes = set()
        rows = coach_yield.rows(self.env.data / "reports/proposals.jsonl", notes)
        self.assertTrue(all(coach_yield.timestamp(r.get("at") or r.get("date")) for r in rows))


class Render(Fixture):
    def test_renderer_last_tried_watch_and_bounds(self):
        card(self.db, "t_aa0001", "done", [("done", "completed", "fixed the loop")], completed=T0 + 100)
        card(self.db, "t_aa0002", "archived", [], completed=T0 + 200)
        card(self.db, "t_aa0003", "done", [("done", "completed", "already exists")], completed=T0 + 300)
        w1 = self.carded("t_aa0001", kind="fix", target="slug-one")
        w2 = self.carded("t_aa0002", kind="trial", target="idea-two")
        d = self.carded("t_aa0003", target="gate")
        ca.mark(self.env, d, "rejected", note="exists")
        self.auto()
        text = "\n".join(cp.render_outcomes(self.env.data))
        last, tried, watch = (text.split("Last outcomes")[1].split("Tried and decided")[0],
                              text.split("Tried and decided")[1].split("Watch:")[0], text.split("Watch:")[1])
        self.assertIn("(3):", last)
        self.assertLess(last.index(d), last.index(w2))  # newest first
        self.assertIn("core gate -> rejected (card t_aa0003 done)", tried)
        self.assertNotIn("slug-one", tried)
        self.assertIn(f'{w1} fix slug-one [carded]: card t_aa0001 done 2026-09-21, run 1 completed — "fixed the loop"',
                      watch)
        self.assertIn(f"{w2} trial idea-two [carded]: card t_aa0002 archived 2026-09-21, run unknown — summary unknown",
                      watch)
        self.assertNotIn("fixed]", text)  # no inferred statuses
        capped = cp.render_outcomes(self.env.data, limit=1)
        self.assertIn("- (+2 more)", capped)
        self.assertLessEqual(len(capped), 14)

    def test_renderer_empty_and_stale_card_link(self):
        self.assertEqual(cp.render_outcomes(self.env.data).count("- none"), 3)
        card(self.db, "t_bb0001", "done", [("done", "completed", "old card")])
        pid = self.carded("t_bb0001")
        self.auto()
        cp.append(self.env.data, {"id": pid, "status": "carded", "at": ca.now_iso(), "card": "t_bb0002"})
        self.assertEqual(cp.render_outcomes(self.env.data).count("- none"), 3)  # outcome of a superseded card

    def test_cli_outcomes(self):
        card(self.db, "t_cc0001", "done", [("done", "completed", "cli ok")])
        self.carded("t_cc0001")
        self.auto()
        with patch("builtins.print") as out:
            self.assertEqual(cp.main(["outcomes", "--data", str(self.env.data), "--limit", "2"]), 0)
        self.assertIn('"cli ok"', out.call_args.args[0])

    def test_weekly_main_includes_outcomes(self):
        card(self.db, "t_dd0001", "done", [("done", "completed", "weekly ok")])
        pid = self.carded("t_dd0001")
        self.auto()
        argv = ["coach_weekly.py", "--data", str(self.env.data), "--no-report-only", "--guard-home", str(self.home),
                "--home", str(self.home)]
        with patch.object(coach_weekly.sys, "argv", argv), patch.object(coach_weekly.coach_mine, "load", return_value=[]), \
                patch.object(coach_weekly, "section_kpis", return_value=[]), \
                patch.object(coach_weekly, "section_findings", return_value=[]), \
                patch.object(coach_weekly.coach_yield, "section_yield", return_value=["YIELD"]), \
                patch("builtins.print") as out:
            coach_weekly.main()
        text = out.call_args.args[0]
        self.assertIn("## Card outcomes", text)
        self.assertIn(f'{pid} core agent loop t_dd0001 [carded]', text)
        self.assertLess(text.index("## Proposal state"), text.index("## Card outcomes"))
        self.assertLess(text.index("## Card outcomes"), text.index("YIELD"))


if __name__ == "__main__":
    unittest.main()
