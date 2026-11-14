"""`red_cards.py waiting`: the Todo + Scheduled ("not ready") audit, separate from red cards.

Regression for agent-23f8b30fa7: David asked to audit "110 not-ready tasks" (the Board's Todo and Scheduled
columns); the skill had only a red-card inventory, so the turn swept blocked cards instead.
"""
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("red_cards", Path(__file__).with_name("red_cards.py"))
red_cards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(red_cards)

NOW = 1_800_000_000


def make_board(path, with_created_on=True):
    c = sqlite3.connect(path)
    cols = "id text primary key, title text, status text, priority int" + (", created_on text" if with_created_on else "")
    c.execute(f"create table tasks ({cols})")
    c.execute("create table task_links (parent_id text, child_id text)")
    c.execute("create table task_events (id integer primary key, task_id text, kind text, created_at int, payload text)")
    rows = [
        ("t_p_done", "parent done", "done"),
        ("t_p_run", "parent running", "running"),
        ("t_p_blk", "parent blocked", "blocked"),
        ("t_p_sched", "parent scheduled", "scheduled"),
        ("t_a", "todo whose parents all finished", "todo"),
        ("t_b", "todo behind a running parent", "todo"),
        ("t_c", "todo behind a blocked parent", "todo"),
        ("t_d", "todo behind a waiting parent", "todo"),
        ("t_e", "todo with a missing parent", "todo"),
        ("t_f", "scheduled, wake long past", "scheduled"),
        ("t_g", "scheduled, awaited card done", "scheduled"),
        ("t_h", "scheduled, soak in progress", "scheduled"),
        ("t_red", "a red card", "blocked"),
    ]
    for tid, title, status in rows:
        vals = (tid, title, status, 0) + (("hub",) if with_created_on else ())
        c.execute(f"insert into tasks values ({','.join('?' * len(vals))})", vals)
    links = [("t_p_done", "t_a"), ("t_p_run", "t_b"), ("t_p_blk", "t_c"), ("t_p_sched", "t_d"), ("t_gone", "t_e")]
    c.executemany("insert into task_links values (?,?)", links)
    ev = [
        ("t_f", "scheduled", NOW - 10, {"reason": "SCHEDULED_UNTIL=...", "wake_at": NOW - 7200}),
        ("t_g", "scheduled", NOW - 10, {"reason": "wait", "wake_at": NOW + 86400,
                                        "awaiting": {"kind": "card", "ref": "t_p_done"}}),
        ("t_h", "scheduled", NOW - 10, {"reason": "24h soak", "wake_at": NOW + 3600}),
        ("t_p_sched", "scheduled", NOW - 10, {"reason": "soak", "wake_at": NOW + 3600}),
    ]
    for tid, kind, at, payload in ev:
        c.execute("insert into task_events (task_id,kind,created_at,payload) values (?,?,?,?)",
                  (tid, kind, at, json.dumps(payload)))
    c.commit()
    c.close()


class WaitingAuditTests(unittest.TestCase):
    def run_waiting(self, home, *argv):
        out = io.StringIO()
        with patch.object(red_cards, "HOME", home), patch.object(red_cards.time, "time", return_value=NOW), \
                patch("sys.argv", ["red_cards.py", "waiting", *argv]), redirect_stdout(out):
            red_cards.main()
        return out.getvalue()

    def test_classifies_todo_and_scheduled_and_skips_red(self):
        with tempfile.TemporaryDirectory() as home:
            make_board(os.path.join(home, "kanban.db"))
            data = json.loads(self.run_waiting(home, "--json"))
            cls = {r["id"]: r["cls"] for r in data["cards"]}
            self.assertNotIn("t_red", cls)  # red cards belong to `list`, not the not-ready audit
            self.assertEqual(cls["t_a"], "todo-parents-done")
            self.assertEqual(cls["t_b"], "todo-parent-active")
            self.assertEqual(cls["t_c"], "todo-parent-blocked")
            self.assertEqual(cls["t_d"], "todo-parent-waiting")
            self.assertEqual(cls["t_e"], "todo-parent-missing")
            self.assertEqual(cls["t_f"], "sched-overdue")
            self.assertEqual(cls["t_g"], "sched-awaited-done")
            self.assertEqual(cls["t_h"], "sched-waiting")
            actionable = {r["id"] for r in data["cards"] if r["actionable"]}
            self.assertEqual(actionable, {"t_a", "t_c", "t_e", "t_f", "t_g"})
            self.assertEqual(data["counts"], {"default": {"hub": {"todo": 5, "scheduled": 4}}})

    def test_text_report_leads_with_actionable_cards(self):
        with tempfile.TemporaryDirectory() as home:
            make_board(os.path.join(home, "kanban.db"), with_created_on=False)
            text = self.run_waiting(home)
            self.assertIn("Todo + Scheduled", text)
            self.assertIn("todo 5, scheduled 4", text)
            self.assertNotIn("t_red", text)
            first_card = text.index("t_")
            self.assertLess(text.index("actionable"), first_card)
            self.assertLess(text.index("t_a"), text.index("t_h"))

    def test_prefers_fleet_replica_over_local_board(self):
        with tempfile.TemporaryDirectory() as home:
            make_board(os.path.join(home, "kanban.db"))
            os.makedirs(os.path.join(home, "kanban", "sync-shadow"))
            shadow = os.path.join(home, "kanban", "sync-shadow", "default.db")
            make_board(shadow)
            with sqlite3.connect(shadow) as c:
                c.execute("insert into tasks values ('t_mac','macbook card','todo',0,'macbook')")
            data = json.loads(self.run_waiting(home, "--json"))
            self.assertEqual(data["counts"]["default"]["macbook"], {"todo": 1})
            self.assertEqual(data["sources"]["default"], shadow)


class SkillRouteTests(unittest.TestCase):
    def test_skill_routes_not_ready_asks_to_waiting(self):
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text()
        step0 = skill[skill.index("## 0. Scope first"):skill.index("## 1. Inventory")]
        for word in ("not-ready", "Todo + Scheduled", "red_cards.py waiting", "Lead the reply"):
            self.assertIn(word, step0)
        self.assertLess(skill.index("## 0. Scope first"), skill.index("Run `scripts/red_cards.py list`"))


if __name__ == "__main__":
    unittest.main()
