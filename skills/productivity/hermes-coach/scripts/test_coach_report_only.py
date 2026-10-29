"""report_only_rate KPI + bench-v2 builder. Offline: the judge is a stub, no model calls, no private data."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import coach_eval as ce
import coach_report_only as ro


def turn(i, model="claude-opus-5-5", user="why is the sidebar stale?", codex=0):
    return {"id": f"t{i}", "sid": "s", "model": model, "user": user, "tools": {"terminal": 1}, "final": "x" * 80,
            "codex": codex, "assistant_msgs": 1, "ts": time.time()}


def lab(outcome, wanted=False):
    return {"outcome": outcome, "user_wanted_action": wanted, "problem": outcome != "na"}


class Kpi(unittest.TestCase):
    def test_family(self):
        self.assertEqual(ro.family(turn(1, "claude-opus-5-5")), "opus")
        self.assertEqual(ro.family(turn(1, "claude-sonnet-5-5")), "sonnet")
        self.assertEqual(ro.family(turn(1, "gpt-6-astra")), "astra")
        self.assertEqual(ro.family(turn(1, "claude-opus-5-5", codex=2)), "astra")
        self.assertEqual(ro.family(turn(1, "gemini")), "other")

    def test_trigger(self):
        self.assertEqual(ro.trigger(turn(1, user="[chat-closeout] Every card settled"), lab("na")), "event")
        self.assertEqual(ro.trigger(turn(1, user="✔ [t_1] done"), lab("na", wanted=True)), "event")
        self.assertEqual(ro.trigger(turn(1), lab("na", wanted=False)), "human-question")
        self.assertEqual(ro.trigger(turn(1, user="fix it"), lab("na", wanted=True)), "human-action")

    def test_rates_split_by_family_and_trigger(self):
        turns = [turn(i, "gpt-6-astra") for i in range(10)] + [turn(10 + i) for i in range(10)] + \
                [turn(20, "gpt-6-astra", user="[chat-closeout] x"), turn(21, "gpt-6-astra", user="[chat-closeout] y")]
        labels = {f"t{i}": lab("report_only" if i < 6 else "fixed") for i in range(10)}          # astra Q: 6/10
        labels.update({f"t{10 + i}": lab("report_only" if i < 1 else "started") for i in range(10)})  # opus Q: 1/10
        labels.update({"t20": lab("report_only"), "t21": lab("na")})                              # astra event: 1/2
        r = ro.compute(turns, labels)
        self.assertEqual(r["cells"][("astra", "human-question")]["rate"], 0.6)
        self.assertEqual(r["cells"][("opus", "human-question")]["rate"], 0.1)
        self.assertEqual(r["cells"][("astra", "event")]["report_only"], 1)
        self.assertEqual(r["by_trigger"]["human-question"], {"n": 20, "report_only": 7, "rate": 0.35})
        self.assertEqual(r["all"]["n"], 22)
        self.assertEqual(r["by_family"]["astra"]["n"], 12)

    def test_unlabeled_turns_are_not_counted(self):
        r = ro.compute([turn(1), turn(2)], {"t1": lab("report_only")})
        self.assertEqual(r["all"], {"n": 1, "report_only": 1, "rate": 1.0})

    def test_verdict_against_targets(self):
        over = {"human-question": {"n": 20, "report_only": 3, "rate": 0.15}, "event": {"n": 20, "report_only": 4, "rate": 0.2}}
        self.assertEqual(ro.verdict(over), {"human-question": "OVER TARGET", "event": "ok"})
        few = {"human-question": {"n": 3, "report_only": 3, "rate": 1.0}}
        self.assertEqual(ro.verdict(few), {"human-question": "few turns", "event": "n/a"})

    def test_markdown_prints_the_split(self):
        turns = [turn(i, "gpt-6-astra") for i in range(10)]
        res = ro.compute(turns, {t["id"]: lab("report_only") for t in turns[:7]} | {t["id"]: lab("fixed") for t in turns[7:]})
        md = "\n".join(ro.markdown(res, 7, {"turns": 40, "cost": 0.3}))
        self.assertIn("| astra | 70% (7/10) |", md)
        self.assertIn("human-question 70% (7/10) vs ≤ 10% → OVER TARGET", md)

    def test_parse_labels_rejects_bad_ids_and_outcomes(self):
        txt = 'x [{"id":"a","outcome":"report_only"},{"id":"zz","outcome":"fixed"},{"id":"b","outcome":"weird"}]'
        self.assertEqual(set(ro.parse_labels(txt, {"a", "b"})), {"a"})
        self.assertEqual(ro.parse_labels("no json", {"a"}), {})


class Pipeline(unittest.TestCase):
    def db(self, rows):
        d = tempfile.mkdtemp()
        path = Path(d) / "state.db"
        con = sqlite3.connect(path)
        con.executescript("""create table sessions(id text, model text, source text, parent_session_id text,
              last_activity_at real, started_at real);
            create table messages(id integer primary key, session_id text, role text, content text, tool_calls text,
              tool_name text, timestamp real, codex_message_items text, active integer);""")
        now = time.time()
        con.execute("insert into sessions values('sess-aaaa1111','gpt-6-astra','desktop',null,?,?)", (now, now))
        con.execute("insert into sessions values('sess-bbbb2222','claude-opus-5-5','cron',null,?,?)", (now, now))
        n = 0
        for sid, user, final, codex in rows:
            for role, c, cx in (("user", user, None), ("assistant", final, codex)):
                n += 1
                con.execute("insert into messages values(?,?,?,?,?,?,?,?,1)", (n, sid, role, c, None, None, now - 100 + n, cx))
        con.commit()
        con.close()
        return path

    def test_extract_only_desktop_turns_with_final_replies(self):
        reply = "The sidebar badge is stale because the cache is never refreshed; this is the diagnosis."
        db = self.db([("sess-aaaa1111", "why is it stale?", reply, "[1]"), ("sess-aaaa1111", "ok", "short", None),
                      ("sess-bbbb2222", "cron job", reply, None)])
        ts = ro.extract_turns(db, time.time() - 3600)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ro.family(ts[0]), "astra")

    def test_run_judges_with_the_stub_and_logs_the_rollup(self):
        reply = "The sidebar badge is stale because the cache is never refreshed; this is the diagnosis."
        db = self.db([("sess-aaaa1111", f"question {i}", reply, "[1]") for i in range(4)])
        data = Path(tempfile.mkdtemp())

        def ask(prompt):
            ids = [l.split(": ")[1] for l in prompt.splitlines() if l.startswith("### id: ")]
            return json.dumps([{"id": i, "problem": True, "agent_fixable": True, "outcome": "report_only",
                                "user_wanted_action": False, "quote": "q"} for i in ids])
        res, meta = ro.run(db, ask=ask, data=data)
        self.assertEqual(res["cells"][("astra", "human-question")], {"n": 4, "report_only": 4, "rate": 1.0})
        row = json.loads((data / "reports" / "report_only_rate.jsonl").read_text())
        self.assertEqual(row["cells"]["astra/human-question"]["n"], 4)

    def test_out_of_budget_judge_leaves_turns_unlabeled(self):
        db = self.db([("sess-aaaa1111", "q", "y" * 90, "[1]")])
        res, meta = ro.run(db, ask=lambda p: None)
        self.assertEqual(res["all"]["n"], 0)
        self.assertIn("unlabeled", meta["note"])


class DisplayFrame(unittest.TestCase):
    """Turns come from what the chat shows, not only the rows still sent to the model."""

    def db(self):
        path = Path(tempfile.mkdtemp()) / "state.db"
        con = sqlite3.connect(path)
        con.executescript("""create table sessions(id text, model text, source text, parent_session_id text,
              last_activity_at real, started_at real);
            create table messages(id integer primary key, session_id text, role text, content text, tool_calls text,
              tool_name text, timestamp real, codex_message_items text, active int, compacted int,
              _compressed_summary int, display_order int, display_metadata text);""")
        now = time.time()
        con.execute("insert into sessions values('sess-cccc3333','claude-opus-5-5','desktop',null,?,?)", (now, now))
        ask, look = "why is the sidebar stale?", "Looking at the cache first, then the poller, then the store."
        fixed = "Fixed it: the cache now refreshes on every write, and I checked it live."
        later = "B and C both pass their checks now, so nothing else is open here."
        tc = json.dumps([{"function": {"name": "terminal"}}])
        rows = [  # id, role, content, tool_calls, t, active, compacted, summary, display_order, display_metadata
            (1, "user", ask, None, 1, 0, 1, 0, 1, None),             # compacted original
            (2, "assistant", look, tc, 2, 0, 1, 0, 2, None),
            (101, "user", ask, None, 1, 1, 0, 0, 1, None),           # protected-tail copy: same display_order
            (102, "assistant", look, tc, 2, 1, 0, 0, 2, None),
            (103, "user", "[CONTEXT COMPACTION - REFERENCE ONLY] earlier turns...", None, 3, 1, 0, 1, 103, None),
            (104, "user", "[Your active task list was preserved across context compression]\n- [ ] fix it", None, 4,
             1, 0, 0, 104, None),
            (105, "assistant", fixed, None, 5, 1, 0, 0, 105, None),
            (106, "user", "thanks, also check B", None, 6, 1, 0, 0, 106, None),
            (107, "user", "thanks, also check B\n\nand C", None, 7, 1, 0, 0, 107, json.dumps({"model_only": True})),
            (108, "user", "and C", None, 7.5, 0, 0, 0, 108, None),     # rewound: never shown
            (109, "assistant", later, None, 8, 1, 0, 0, 109, None),
        ]
        for i, role, c, tcs, t, a, cp, summ, order, meta in rows:
            con.execute("insert into messages values(?,?,?,?,?,?,?,null,?,?,?,?,?)",
                        (i, "sess-cccc3333", role, c, tcs, None, now - 100 + t, a, cp, summ, order, meta))
        con.commit()
        con.close()
        return path

    def test_compacted_turns_count_once_and_resume_prompts_join_their_turn(self):
        ts = ro.extract_turns(self.db(), time.time() - 3600)
        self.assertEqual([t["user"] for t in ts], ["why is the sidebar stale?", "thanks, also check B"])
        self.assertTrue(ts[0]["final"].startswith("Fixed it"))
        self.assertEqual(ts[0]["tools"], {"terminal": 1})        # the protected-tail copy is not counted twice
        self.assertTrue(ts[1]["final"].startswith("B and C"))

    def test_legacy_store_without_display_columns_reads_live_rows(self):
        ts = Pipeline().db([("sess-aaaa1111", "why is it stale?", "x" * 60, None)])
        self.assertEqual([t["user"] for t in ro.extract_turns(ts, time.time() - 3600)], ["why is it stale?"])


class BenchV2(unittest.TestCase):
    def eval_set(self, n_ro=20, n_ctl=20):
        rows = []
        for i in range(n_ro):
            rows.append({"id": f"A{i}", "session": f"s{i}", "trigger": "human", "user": f"why is thing {i} broken?",
                         "tools": {"terminal": 2}, "reply": "It is broken because of X.", "next_user": "fix it",
                         "label": str({"problem": True, "agent_fixable": True, "outcome": "report_only",
                                       "user_wanted_action": False, "quote": "broken because of X"})})
        for i in range(n_ctl):
            rows.append({"id": f"B{i}", "session": f"c{i}", "trigger": "human", "user": f"fix thing {i}",
                         "tools": {"patch": 1}, "reply": "Fixed.", "next_user": "thanks",
                         "label": {"problem": False, "agent_fixable": None, "outcome": "fixed",
                                   "user_wanted_action": True}})
        rows.append({"id": "E1", "session": "e", "trigger": "event", "user": "[chat-closeout]", "tools": {"k": 1},
                     "reply": "r", "label": {"outcome": "report_only", "agent_fixable": True, "user_wanted_action": False}})
        d = Path(tempfile.mkdtemp())
        (d / "eval_set.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        return d

    def test_builds_15_report_only_and_15_controls_from_human_turns(self):
        d = self.eval_set()
        n, by = ce.bench_build_v2(d / "bench-v2.jsonl", d / "eval_set.jsonl")
        eps = [json.loads(l) for l in (d / "bench-v2.jsonl").read_text().splitlines()]
        self.assertEqual((n, by), (30, {"report_only": 15, "control": 15}))
        self.assertEqual(sum(e["pattern"] == "report_only" for e in eps), 15)
        self.assertTrue(all(e["source"] != "report-only-eval:E1" for e in eps))  # event turns excluded
        self.assertEqual(len({e["id"] for e in eps}), 30)
        self.assertIn("Do not stop at describing", eps[0]["expected"] + eps[-1]["expected"])
        self.assertEqual(ce.patterns_of(e["pattern"] for e in eps), ce.PATTERNS_V2)

    def test_too_few_rows_fails_loudly(self):
        d = self.eval_set(n_ro=10)
        with self.assertRaises(SystemExit):
            ce.bench_build_v2(d / "b.jsonl", d / "eval_set.jsonl")

    def test_v1_coverage_rules_unchanged_and_v2_verdict(self):
        self.assertEqual(ce.patterns_of(["handoff", "control"]), ce.PATTERNS)
        v1 = {p: {"base": 3, "cand": 3, "n": 6} for p in ce.PATTERNS}
        self.assertNotEqual(ce.behavior_verdict(v1, "handoff")[0], "INCOMPLETE")
        v2 = {"report_only": {"base": 3, "cand": 9, "n": 15}, "control": {"base": 14, "cand": 14, "n": 15}}
        self.assertEqual(ce.behavior_verdict(v2, "report_only")[0], "PASS")
        self.assertEqual(ce.behavior_verdict({"report_only": v2["report_only"]}, "report_only")[0], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
