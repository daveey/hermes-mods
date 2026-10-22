"""Synthetic, offline coach_mine KPI fixtures: only human sources count as interactive."""
import unittest

import coach_mine


def digest(source, msgs, host="mini", cost=0.1, turns=1, sid=None):
    return {"host": host, "profile": "p", "id": sid or f"{source}-{msgs}", "source": source, "msgs": msgs,
            "user_turns_total": turns, "cost_usd": cost, "children": {"n": 0, "cost_usd": 0}}


class InteractiveSources(unittest.TestCase):
    def setUp(self):
        self.human = [digest("desktop", 100, host="macbook"), digest("cli", 300, host="macbook"),
                      digest("slack", 50, host="nous-1")]
        self.bots = [digest("bb-reviewer", 2, cost=0.2), digest("bb-judge", 2, cost=0.3),
                     digest("taxprep-run-01", 2, host="macbook", cost=1.5)]
        self.machine = [digest("kanban", 40), digest("cron", 10), digest("api_server", 3), digest("oneshot", 2)]

    def test_allowlist_constant(self):
        self.assertEqual(set(coach_mine.INTERACTIVE_SOURCES),
                         {"desktop", "tui", "cli", "slack", "telegram", "discord", "whatsapp", "signal"})

    def test_bot_sources_excluded(self):
        for d in self.bots + self.machine:
            self.assertFalse(coach_mine.is_interactive(d), d["source"])
        for d in self.human:
            self.assertTrue(coach_mine.is_interactive(d), d["source"])
        self.assertFalse(coach_mine.is_interactive(digest("desktop", 5, turns=0)))

    def test_kpis_ignore_bot_sessions(self):
        k = coach_mine.kpis(self.human + self.bots + self.machine)
        self.assertEqual(k["sessions"], 3)
        self.assertEqual(k["msgs_p50"], 100)
        self.assertEqual(k["msgs_p90"], 300)
        self.assertEqual(coach_mine.kpis(self.bots)["sessions"], 0)

    def test_other_sources_visible(self):
        o = coach_mine.other_sources(self.human + self.bots + self.machine + [digest("bb-reviewer", 2, cost=0.2, sid="x")])
        self.assertEqual(o["bb-reviewer"], {"sessions": 2, "cost_usd": 0.4})
        self.assertEqual(o["taxprep-run-01"]["sessions"], 1)
        self.assertIn("bb-judge", o)
        for s in ("desktop", "cli", "slack", "kanban", "cron", "api_server", "oneshot"):
            self.assertNotIn(s, o)
        line = coach_mine.fmt_other_sources(o)
        self.assertTrue(line.startswith("other_sources:"))
        self.assertIn("bb-reviewer=2 ($0.4)", line)

    def test_adoption_interactive_uses_allowlist(self):
        ds = [dict(digest("bb-reviewer", 2), started_at=1000, features={"x": 1}),
              dict(digest("desktop", 9), started_at=1000, features={"x": 1})]
        count, _, ev = coach_mine.adoption_test({"feature": "x", "sources": "interactive", "days": 1}, ds, 2000)
        self.assertEqual(count, 1)
        self.assertEqual(ev, ["mini/p/desktop-9"])


class LoadSkipsNonDigests(unittest.TestCase):
    def test_relative_data_skips_reports_marker_and_idless_files(self):
        import json, os, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel, body in [("mini/default/s1.json", {"id": "s1", "host": "mini"}),
                              ("reports/askgate/2026-10-12.json", {"host": "hub", "posted_at": 1}),
                              ("eval/reports/x.json", {"host": "hub", "id": "e"}),
                              ("mini/default/noid.json", {"host": "mini"})]:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(json.dumps(body))
            cwd = os.getcwd()
            try:
                os.chdir(d)
                rel_ids = sorted(x["id"] for x in coach_mine.load(Path(".")))
            finally:
                os.chdir(cwd)
            self.assertEqual(rel_ids, ["s1"])
            self.assertEqual(sorted(x["id"] for x in coach_mine.load(root)), ["s1"])


if __name__ == "__main__":
    unittest.main()
