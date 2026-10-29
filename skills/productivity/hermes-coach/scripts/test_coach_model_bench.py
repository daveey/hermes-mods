#!/usr/bin/env python3
"""coach_model_bench.py + coach_llm_worker transcript wire, offline (no provider calls).
Run: python3 -m unittest test_coach_model_bench.py   (from this directory)"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import coach_eval as ce  # noqa: E402
import coach_llm_worker as w  # noqa: E402
import coach_model_bench as mb  # noqa: E402

MSGS = [{"role": "user", "content": "why is the badge wrong?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_0", "name": "terminal", "arguments": "{\"command\": \"ls\"}"},
                                                             {"id": "call_1", "name": "read_file", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "call_0", "content": "a b"}]


class Wire(unittest.TestCase):
    def test_codex_input_pairs_every_call_with_an_output(self):
        items = w.codex_input(MSGS)
        calls = [i["call_id"] for i in items if i.get("type") == "function_call"]
        outs = [i["call_id"] for i in items if i.get("type") == "function_call_output"]
        self.assertEqual(sorted(calls), sorted(outs))  # call_1 had no recorded result: a stub output is added
        self.assertEqual(items[0]["role"], "user")

    def test_openai_messages_keep_tool_calls_and_results(self):
        m = w.openai_messages("SYS", MSGS)
        self.assertEqual(m[0], {"role": "system", "content": "SYS"})
        self.assertEqual([c["function"]["name"] for c in m[2]["tool_calls"]], ["terminal", "read_file"])
        self.assertEqual(m[3]["role"], "tool")


class FakeRunner:
    """Offline runner. A transcript replay either ends the turn at once with `stop_text` (act=False) or calls a tool
    first (act=True; after a stub result it ends with `stop_text`). The rubric judge labels a reply that mentions
    'broken' report_only and the rest started; controls all pass."""
    offline_only = True
    max_cost_usd = 0.0

    def __init__(self, stop_text, act=False):
        self.stop_text, self.act, self.calls = stop_text, act, []

    def __call__(self, prompt, **kw):
        self.calls.append(prompt)
        if isinstance(prompt, dict) and "messages" in prompt:
            assert prompt["tools"], "tools must be declared"
            if self.act and prompt["messages"][-1]["role"] != "tool":
                return "", {"tool_calls": [{"name": "terminal", "arguments": "{}"}]}
            if self.act:
                assert mb.STUB_RESULT in prompt["messages"][-1]["content"]
            return self.stop_text, {}
        if isinstance(prompt, dict):  # rubric judge
            ids = re.findall(r"### id: (\w+)", prompt["user"])
            return json.dumps([{"id": i, "outcome": "report_only" if "broken" in prompt["user"].split(f"### id: {i}")[1].split("### id:")[0] else "started"}
                               for i in ids]), {}
        if prompt.startswith(ce.JUDGE_SYS):
            ids = re.findall(r"=== ITEM id=(\w+)", prompt)
            return json.dumps([{"id": i, "pass": 1, "why": "ok"} for i in ids]), {}
        return "-> terminal(fix)\nDone.", {}


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        os.environ["COACH_DATA"] = str(self.tmp / "data")
        (self.tmp / "data").mkdir()
        live = self.tmp / "live"
        (live / "memories").mkdir(parents=True)
        (live / "config.yaml").write_text("model: {}\n")
        self.live = live
        eps = [{"id": "_meta", "pattern": "_meta", "tools": [{"type": "function", "function": {"name": "terminal", "parameters": {}}}]}]
        for i in range(3):
            eps.append({"id": f"report_only:{i}", "pattern": "report_only", "kind": "transcript", "context_model": "astra",
                        "user_ask": "why?", "orig_tools": {"terminal": 1}, "messages": MSGS[:1]})
        eps.append({"id": "control:0", "pattern": "control", "kind": "reply", "user_ask": "do x", "tool_log": [],
                    "label": "fine", "expected": "act", "original_reply": "ok"})
        self.bench = self.tmp / "bench.jsonl"
        self.bench.write_text("".join(json.dumps(e) + "\n" for e in eps))

    def run_with(self, text, act=False, **kw):
        return mb.run(self.bench, "m", repeats=2, runner=FakeRunner(text, act), root=self.tmp / "root", live=self.live,
                      log=lambda *_: None, replies_path=self.tmp / "replies.jsonl", **kw)

    def test_a_model_that_stops_at_the_diagnosis_scores_high(self):
        r = self.run_with("It is still broken; the owner has it.")
        ro = r["by_pattern"]["report_only"]
        self.assertTrue(r["complete"])
        self.assertEqual((ro["n"], ro["fail"], ro["acted"]), (6, 6, 0))
        self.assertEqual(r["by_pattern"]["control"], dict(r["by_pattern"]["control"], n=2, **{"pass": 2}))
        self.assertGreater(ro["ci95"][0], 0.5)

    def test_a_model_that_keeps_acting_is_not_stopped(self):
        # one request per rollout (the default): a tool call at the cut = it continued the turn
        r = self.run_with("It is still broken", act=True)
        ro = r["by_pattern"]["report_only"]
        self.assertEqual((ro["n"], ro["fail"], ro["outcomes"]), (6, 0, {"continued": 6}))

    def test_multi_step_rollout_feeds_stub_results(self):
        r = self.run_with("It is still broken", act=True, max_steps=2)  # FakeRunner asserts the stub result
        rows = [x for x in r["rows"] if x["pattern"] == "report_only"]
        self.assertEqual({x["steps"] for x in rows}, {2})
        self.assertEqual(r["by_pattern"]["report_only"]["fail"], 0)  # it called a tool: continued

    def test_frozen_agents_md_is_the_replay_prompt(self):
        fake = FakeRunner("ok")
        seen = []
        call = fake.__call__
        fake_call = lambda prompt, **kw: (seen.append(Path(kw["cwd"], "AGENTS.md").read_text())  # noqa: E731
                                          if isinstance(prompt, dict) and "messages" in prompt else None) or call(prompt, **kw)

        class R(FakeRunner):
            def __call__(self, prompt, **kw):
                return fake_call(prompt, **kw)
        mb.run(self.bench, "m", repeats=1, runner=R("ok"), root=self.tmp / "root", live=self.live,
               log=lambda *_: None, agents_md="FROZEN RULES")
        self.assertEqual(set(seen), {"FROZEN RULES"})

    def test_a_model_that_acts_scores_low_and_the_reply_cache_is_reused(self):
        r = self.run_with("Fixed it and started a card.")
        self.assertEqual(r["by_pattern"]["report_only"]["fail"], 0)
        again = FakeRunner("It is still broken")
        r2 = mb.run(self.bench, "m", repeats=2, runner=again, root=self.tmp / "root", live=self.live,
                    log=lambda *_: None, replies_path=self.tmp / "replies.jsonl")
        self.assertFalse([p for p in again.calls if isinstance(p, dict) and "messages" in p])  # no new replays
        self.assertEqual(r2["by_pattern"]["report_only"]["fail"], 0)

    def test_wilson(self):
        lo, hi = mb.wilson(36, 57)
        self.assertTrue(lo < 36 / 57 < hi)
        self.assertEqual(mb.wilson(0, 0), (0.0, 1.0))


class TurnStart(unittest.TestCase):
    """cut="start": the replay begins at David's message, the model runs the turn, its tool calls get outputs
    simulated from the original turn's REAL outputs, and the whole turn is labeled (tool calls don't excuse a
    report-only ending)."""

    def setUp(self):
        Run.setUp(self)
        # the original turn: user message, a call with its real output, a trailing call
        self.ep_msgs = [{"role": "user", "content": "earlier ask"}, {"role": "assistant", "content": "earlier reply",
                                                                     "tool_calls": []}] + MSGS

    def runner(self, final_text):
        test = self

        class R(FakeRunner):
            sims, first = [], []

            def __call__(self, prompt, **kw):
                if isinstance(prompt, dict) and prompt.get("system") == mb.SIM_SYS:
                    self.sims.append(prompt)
                    test.assertEqual(prompt["provider"], "openai-codex")
                    return "SIMULATED OUTPUT", {}
                if isinstance(prompt, dict) and "messages" in prompt:
                    ms = prompt["messages"]
                    if ms[-1]["role"] == "user":
                        self.first.append(ms)
                        return "", {"tool_calls": [{"name": "terminal", "arguments": "{\"command\": \"ls\"}"}]}
                    test.assertEqual(ms[-1]["content"], "SIMULATED OUTPUT")
                    test.assertEqual(prompt["system_extra"], "GUIDE")
                    return final_text, {}
                return FakeRunner.__call__(self, prompt, **kw)
        return R(final_text)

    def bench_with_turn(self):
        lines = [json.loads(x) for x in self.bench.read_text().splitlines()]
        for e in lines:
            if e.get("kind") == "transcript":
                e["messages"] = self.ep_msgs
        self.bench.write_text("".join(json.dumps(e) + "\n" for e in lines))

    def test_turn_split_cuts_at_davids_message(self):
        head, body = mb.turn_split({"messages": self.ep_msgs[:3] + [
            {"role": "user", "content": "[context trimmed for length: 2 earlier tool calls of this turn omitted]"}]
            + self.ep_msgs[3:]})
        self.assertEqual(head[-1]["content"], "why is the badge wrong?")
        self.assertEqual([m["role"] for m in body], ["assistant", "tool"])
        self.assertIn("OUTPUT of terminal:\na b", mb.ground_text(body))

    def test_a_turn_that_investigates_then_reports_is_stopped(self):
        self.bench_with_turn()
        r = self.runner("It is still broken; the owner has it.")
        res = mb.run(self.bench, "m", repeats=1, runner=r, root=self.tmp / "root", live=self.live, log=lambda *_: None,
                     cut="start", guidance="GUIDE")
        ro = res["by_pattern"]["report_only"]
        self.assertEqual((ro["n"], ro["fail"], ro["acted"]), (3, 3, 3))  # called a tool, still a report-only turn
        self.assertEqual(res["cut"], "start")
        # the replay starts at David's message: none of the original turn's calls are shown to the model
        self.assertTrue(all(ms[-1]["content"] == "why is the badge wrong?" for ms in r.first))
        # the simulator is grounded in the original turn's real output
        self.assertTrue(all("OUTPUT of terminal:\na b" in p["user"] for p in r.sims))

    def test_a_turn_that_starts_a_fix_is_not_stopped(self):
        self.bench_with_turn()
        res = mb.run(self.bench, "m", repeats=1, runner=self.runner("Started a card to fix it."), root=self.tmp / "root",
                     live=self.live, log=lambda *_: None, cut="start", guidance="GUIDE")
        self.assertEqual(res["by_pattern"]["report_only"]["fail"], 0)


if __name__ == "__main__":
    unittest.main()
