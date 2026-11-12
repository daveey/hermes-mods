import os, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import coach_askgate as g

HITS = ["why did you ask me to do cloudlfare work? how do i get you to do it yourself and not ask me?",
        "superhuman has been revoked for a while, i keep completing it",
        "YOU JUST SAID IT WAS CANCELED, WHY DO YOU KEEP ASKING ME",
        "why didnt you do this yourself, without me telling you",
        "are you waiting for me? i dont see any blocked cards",
        "[OUT-OF-BAND USER MESSAGE — x]\nWHY DO YOU KEEP ASKING ME\n[/OUT-OF-BAND USER MESSAGE]"]
MISSES = ["i keep asking what time is it and getting no reply",
          "When contacting someone for the first time, intro yourself as Babybear",
          "i want to give you the rights to do it yourself, give me those instructions",
          "[Slack engagement eng_1 · #x] why are you asking me",
          "Without any other tools: call kanban_comment ... do NOT send it yourself",
          "do it", "yes, figure it out",
          # third-party agent (Baby Bear), not Hermes asking David
          "why didnt the agent figure it out even with failing search? it could have just listed the devices",
          # David handing off unprompted, not correcting an ask
          "done. not sure if i hit the phases, listen yourself and possible split things up"]


class T(unittest.TestCase):
    def test_replan_lines_count_holds_in_window(self):
        import json as _j, tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "logs").mkdir()
            rows = [{"t": 150, "kind": "replan_hold", "task": "t_c21c170e"}, {"t": 160, "kind": "replan_hold"},
                    {"t": 170, "kind": "replan_released"}, {"t": 50, "kind": "replan_hold", "task": "t_old"},
                    {"t": 155, "kind": "verdict"}]
            (Path(d) / "logs" / "ask-gate.jsonl").write_text("\n".join(map(_j.dumps, rows)) + "\nnot json\n")
            line = g.replan_lines([Path(d)], 100, 200)[0]
        self.assertIn("2 held, 1 released", line)
        self.assertIn("t_c21c170e", line)
        self.assertNotIn("t_old", line)

    def test_unfiled_is_not_automatically_a_detector_miss(self):
        finding = g.hit_defect({'class': g.FIX_CLASS, 'quote': 'sidebar is broken',
                                'pattern': 'is-broken', 'session': 'example', 't': 1})
        self.assertNotIn('detector missed', finding['title'])
        self.assertIn('shadow/live', finding['prompt'])
        self.assertIn('reuse existing ownership', finding['prompt'])

    def test_generated_messages_are_not_corrections(self):
        for text in (
            '[PEER MESSAGE from example/default]\nDo harmless work yourself. Root cause and fix.',
            '[Message from another Hermes session] why are you asking me?',
            'Go: Approve the fix: investigate why the sidebar is broken',
            'Go: Approve the PR or add the label yourself',
            '✓ Done: run it yourself',
            '[PEER MESSAGE from example/default]\n[OUT-OF-BAND USER MESSAGE]\nwhy are you asking me\n[/OUT-OF-BAND USER MESSAGE]',
        ):
            with self.subTest(text=text):
                self.assertIsNone(g.classify(g.clean(text)))
                self.assertIsNone(g.classify_fix(g.clean(text)))

    def test_injected_steps_do_not_supply_match_words(self):
        for suffix in ('user-steps', 'chat-closeout'):
            text = 'the top three make no sense\n[screenshot]\n\n[' + suffix + '] Root cause and fix'
            self.assertIsNone(g.classify_fix(g.clean(text)))
            self.assertEqual(g.classify_fix(g.clean('fix this\n[screenshot]\n[' + suffix + '] text')), 'screenshot-fix')

    def test_user_correction_openers_remain(self):
        self.assertTrue(g.classify(g.clean('do it yourself via ssh')))
        self.assertTrue(g.classify_fix(g.clean('I hit do it, why did you not?')))
        self.assertTrue(g.classify(g.clean('[OUT-OF-BAND USER MESSAGE — steering]\nwhy are you asking me\n[/OUT-OF-BAND USER MESSAGE]')))

    def test_hits(self):
        for t in HITS:
            self.assertTrue(g.classify(g.clean(t)), t)

    def test_misses(self):
        for t in MISSES:
            self.assertIsNone(g.classify(g.clean(t)), t)

    def test_opening_turn_skipped(self):
        import json, tempfile, os
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/mini/default")
            turns = [{"t": 2000, "text": "whats this, i want it to stop asking me [screenshot]"},
                     {"t": 2100, "text": "why are you asking me to run things"}]
            json.dump({"id": "s1", "host": "mini", "user_turns": turns}, open(f"{d}/mini/default/s1.json", "w"))
            # text-only opener answering a carried-over ask still counts
            json.dump({"id": "s2", "host": "nous-1", "user_turns": [{"t": 3000, "text": "i dont plan to review. you should merge it yourself"}]},
                      open(f"{d}/mini/default/s2.json", "w"))
            hits, scanned = g.collect(d, 0, 10**10)
            self.assertEqual([h["quote"] for h in hits],
                             ["why are you asking me to run things", "i dont plan to review. you should merge it yourself"])



# had_to_ask_fix: paraphrased from real asks (no private names); each must land in the fix class, not ask-me.
FIX_HITS = {"make a card to fix": "card-to-fix",
            "and make a card to rootcause and fix the log shipper": "card-to-fix",
            "create a task to investigate why you did not do that, and fix it": "card-to-fix",
            "root cause it, and fix the plugin": "root-cause",
            "ROOT CAUSE WHY YOU KEPT GETTING THIS WRONG": "root-cause",
            "figure out why the plugin stopped working after the update": "figure-out-why",
            "dont just start working, do an analysis and fix for the problem": "analyze-and-fix",
            "i fixed this once, but now it's broken again": "broken-again",
            "i thought we mostly fixed it but it still shows up": "broken-again",
            "i hit do it, why did you not?": "why-did-you-not",
            "why are all the dots red? [screenshot]": "why-did-you-not",
            "the sessions keep getting re-shuffled, i think the sort is broken": "is-broken",
            "fix [screenshot]": "screenshot-fix",
            "fix this [Image attached: a.png]": "screenshot-fix"}
FIX_MISSES = ["fix the typo in the README",                      # an ordinary task, not a defect report
              "make cards to fix 3, 4, 5, 6, 7, 8, 9",             # a go on the agent's own numbered list
              "create tasks for 2 and 3",
              # week of 2026-10-12 review: a status question, a conditional plan, two specs for card-filing mechanisms
              "hows the mini perf doing now, do we expect that we fixed the root causes",
              "dont build it from scratch, make babybear do it, and if it fails figure out why and fix it",
              "can we modify bb-tuner to measure the relevant timing and quality of watch coms, and then file issues to "
              "investigate and optimize?",
              "i want a process that looks at all sessions every 10 minutes, and makes sure their status (green|red) and "
              "linked tasks are correct. if not, it should create a kanban task to fix",
              "what is the root of this repo?",
              "[screenshot] here is the design i want",
              "work kanban task t_abc123",
              "Investigate this hermes-hub cron output and fix whatever it reports: x",
              "**Background:** find out why the service returns 403",   # a Hermes-written brief
              "some cards say \"still broken\" as a button",
              "why didnt you do this yourself, without me telling you"]  # ask-me class wins


class Fix(unittest.TestCase):
    def test_fix_hits(self):
        for t, want in FIX_HITS.items():
            self.assertIsNone(g.classify(g.clean(t)), t)
            self.assertEqual(g.classify_fix(g.clean(t)), want, t)

    def test_fix_misses(self):
        for t in FIX_MISSES:
            self.assertIsNone(g.classify_fix(g.clean(t)), t)

    def _data(self, d):
        import json, os
        os.makedirs(f"{d}/macbook/default")
        turns = [{"t": 2000, "text": "fix [screenshot]"},                       # opener: counts for the fix class
                 {"t": 2100, "text": "why are you asking me to run things"},     # ask-me
                 {"t": 2200, "text": "root cause and fix"}]
        json.dump({"id": "s1", "host": "macbook", "source": "desktop", "user_turns": turns},
                  open(f"{d}/macbook/default/s1.json", "w"))
        # a cron prompt that says "root cause" is not David asking
        json.dump({"id": "c1", "host": "macbook", "source": "cron", "user_turns": [{"t": 2300, "text": "root cause it"}]},
                  open(f"{d}/macbook/default/c1.json", "w"))

    def test_collect_classes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._data(d)
            hits, _ = g.collect(d, 0, 10**10)
            self.assertEqual([(h["class"], h["pattern"]) for h in hits],
                             [(g.FIX_CLASS, "screenshot-fix"), (g.ASK_CLASS, "why-asking-me"), (g.FIX_CLASS, "root-cause")])
            body = g.post_body(hits, g.week_start(2000), ["## Self-heal lane", "none"])
            self.assertIn("**2** times David had to ask for a Hermes/agent fix", body)
            self.assertIn("in 1 session,", body)
            self.assertIn("**1** correction where", body)
            self.assertIn("## Self-heal lane", body)
            md = g.render(hits, [g.week_start(2000)], {g.week_start(2000): 4}, "t")
            self.assertIn("| 1 | 2 (1) | 0 | 4 |", md)

    def test_marker_one_post_per_week(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            w = g.week_start(2000)
            self.assertIsNone(g.posted_elsewhere(d, w, "hub"))
            p = g.marker_path(d, w)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"posted_by": "hub"}))
            self.assertIsNone(g.posted_elsewhere(d, w, "hub"))       # our own marker: re-post updates the same card
            self.assertEqual(g.posted_elsewhere(d, w, "laptop"), "hub")
            p.write_text(json.dumps({"host": "hub"}))                # marker written before the rename
            self.assertEqual(g.posted_elsewhere(d, w, "laptop"), "hub")


class BabyBearSplit(unittest.TestCase):
    """Baby Bear product fix requests count as their own class and go to bb-tuner, not the self-heal lane."""

    def test_is_bb(self):
        for t in ["the camera stream is broken [screenshot]", "look at recent bb feed. HA seems broken. root cause and fix",
                  "my watch didnt show me the controls. figure out why", "the echo-ear has been responding to random sounds",
                  "why did it take so long to turn volume down?"]:
            self.assertTrue(g.is_bb(t), t)
        for t in ["the task-switch detection pluggin is still not working", "nope, dragging still broken",
                  "why did you think you were done? create a task to investigate and fix for hermes",
                  "ask bb-tuner why it stopped"]:
            self.assertFalse(g.is_bb(t), t)
        self.assertTrue(g.is_bb("can we rootcause it?", title="Babybear client not responding"))
        self.assertFalse(g.is_bb("the sidebar is broken", title="Babybear client not responding"))  # own words win
        self.assertTrue(g.is_bb("still broken", profile="bb-sms"))

    def test_is_bb_screenshot_path_and_session_tiebreak(self):
        # week of 2026-10-12: the Desktop attachment path names Hermes, and the BB-ness was only in the session
        shot = ("why didnt this work\n\n[Image attached at: ~/Library/Application Support/Hermes/composer-images/"
                "Screenshot_2026-10-14_at_12.28.29_AM_584695.png]\n[screenshot]")
        bb_session = "make all the fixes Give the end-of-turn detection more patience after Baby Bear; ask on the Dot pods"
        self.assertTrue(g.is_bb(shot, title="Debug failure shown in screenshot", session=bb_session))
        self.assertFalse(g.is_bb(shot, title="Debug failure shown in screenshot", session="the sidebar and kanban cards"))
        self.assertFalse(g.is_bb(shot, title="Debug failure shown in screenshot"))      # no evidence: stays Hermes
        self.assertTrue(g.is_bb("can we figure out why this happen and how to avoid it?",
                                title="Watch not updating and voice input cutting off",
                                session="my watch is not updating; Baby Bear watch app 269"))
        # the turn's own words and the title still win over the session
        self.assertFalse(g.is_bb("root cause it, and fix the pluggin", title="x", session="bb bb bb pods camera"))
        self.assertFalse(g.is_bb("why is this broken", title="Fix the desktop sidebar", session="bb bb bb pods"))

    def test_session_text(self):
        d = {"user_turns": [{"text": "a", "prev": "b"}, "junk", {"text": "c"}], "last_reply": "d"}
        self.assertEqual(g.session_text(d).split(), ["a", "b", "c", "d"])

    def test_collect_splits_and_handoff_is_idempotent(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/macbook/default")
            json.dump({"id": "s1", "host": "macbook", "source": "desktop", "title": "x", "user_turns": [
                {"t": 2000, "text": "the camera stream is broken, fix it"}, {"t": 2100, "text": "root cause the sidebar bug"}]},
                open(f"{d}/macbook/default/s1.json", "w"))
            hits, _ = g.collect(d, 0, 10**10)
            self.assertEqual([h["class"] for h in hits], [g.BB_CLASS, g.FIX_CLASS])
            self.assertIn("**1** Baby Bear fix request (handed to bb-tuner)", g.post_body(hits, g.week_start(2000)))
            md = g.render(hits, [g.week_start(2000)], {g.week_start(2000): 2}, "t")
            self.assertIn("| 0 | 1 (1) | 1 | 2 |", md)
            out = Path(d) / "bbt" / "askgate-bb-misses.jsonl"
            self.assertEqual(g.hand_to_bb_tuner(hits[:1], out), (None, 0))  # bb-tuner not on this host
            out.parent.mkdir()
            self.assertEqual(g.hand_to_bb_tuner(hits[:1], out), (1, 0))
            self.assertEqual(g.hand_to_bb_tuner(hits[:1], out), (0, 1))
            row = json.loads(out.read_text())
            self.assertEqual(row["id"], "askgate-bb:s1:2000")
            self.assertIn("camera stream", row["quote"])


class ContentFreeHandoff(unittest.TestCase):
    """bb-tuner may not carry quotes in its context: the handoff has a twin file with fixed keys only."""
    QUOTE = "the camera stream is broken, fix it"

    def bb_hits(self, d):
        import json
        os.makedirs(f"{d}/macbook/default")
        with open(f"{d}/macbook/default/s1.json", "w") as fh:
            json.dump({"id": "s1", "host": "macbook", "source": "desktop", "title": "x", "user_turns": [
                {"t": 2000, "text": self.QUOTE}, {"t": 2100, "text": "root cause the sidebar bug"}]}, fh)
        hits, _ = g.collect(d, 0, 10**10)
        return [h for h in hits if h["class"] == g.BB_CLASS]

    def test_twin_file_has_fixed_keys_and_the_legacy_ids(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            bb = self.bb_hits(d)
            out = Path(d) / "bbt" / "askgate-bb-misses.jsonl"
            out.parent.mkdir()
            self.assertEqual(g.hand_to_bb_tuner(bb, out), (1, 0))
            twin = out.parent / "askgate-bb-requests.jsonl"
            rows = [json.loads(x) for x in twin.read_text().splitlines()]
            legacy = [json.loads(x) for x in out.read_text().splitlines()]
            self.assertEqual([r["id"] for r in rows], [r["id"] for r in legacy])
            self.assertEqual(set(rows[0]), set(g.BB_REQUEST_KEYS))
            self.assertNotIn("quote", rows[0])
            self.assertNotIn("camera", twin.read_text())
            self.assertEqual(g.hand_to_bb_tuner(bb, out), (0, 1))  # idempotent
            self.assertEqual(len(twin.read_text().splitlines()), 1)

    def test_projection_backfills_old_legacy_rows_without_content(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "askgate-bb-misses.jsonl"
            out.write_text(json.dumps({"id": "askgate-bb:s0:1", "t": 1, "when": "x", "week": "2026-10-12",
                                       "host": "macbook", "session": "s0", "pattern": "is-broken",
                                       "quote": self.QUOTE, "extra": "title text"}) + "\nnot json\n"
                           + json.dumps({"t": 5, "quote": "no id"}) + "\n")
            self.assertEqual(g.project_bb_handoff(out), 1)
            self.assertEqual(g.project_bb_handoff(out), 0)
            twin = Path(d) / "askgate-bb-requests.jsonl"
            self.assertEqual(json.loads(twin.read_text()), {"id": "askgate-bb:s0:1", "t": 1, "week": "2026-10-12",
                                                          "host": "macbook", "session": "s0", "pattern": "is-broken"})
            self.assertIsNone(g.project_bb_handoff(Path(d) / "nope" / "x.jsonl"))

    def test_jsonl_ids_survive_unicode_separators_inside_a_row(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "legacy.jsonl"
            r = {"id": "askgate-bb:s1:5", "t": 5, "quote": "a\u2028b\u0085c\x0cd\x1ce\x1df"}
            p.write_text(json.dumps(r, ensure_ascii=False) + "\n" + json.dumps(dict(r, id="askgate-bb:s2:6")) + "\n",
                         encoding="utf-8")
            self.assertEqual(g._jsonl_ids(p), {"askgate-bb:s1:5", "askgate-bb:s2:6"})
            # so a hand-off of a hit whose id is already in the file does not append it again
            self.assertEqual(g.hand_to_bb_tuner([{"session": "s1", "t": 5, "pattern": "is-broken", "host": "h",
                                                  "text": "x", "turn": 0}], p)[0], 0)

    def test_unicode_separators_in_a_quote_do_not_make_the_handoff_rewrite_the_row(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            old = self.QUOTE
            type(self).QUOTE = old + " \u2028 \u0085 \x0c \x1c \x1d"   # splitlines() would cut the legacy row apart
            try:
                bb = self.bb_hits(d)
            finally:
                type(self).QUOTE = old
            out = Path(d) / "bbt" / "askgate-bb-misses.jsonl"
            out.parent.mkdir()
            self.assertEqual(g.hand_to_bb_tuner(bb, out), (1, 0))
            self.assertEqual(g.hand_to_bb_tuner(bb, out), (0, 1))      # not re-appended on the next run
            self.assertEqual(len(out.read_bytes().split(b"\n")), 2)     # one row + trailing newline
            twin = out.parent / "askgate-bb-requests.jsonl"
            self.assertEqual(len(twin.read_text().splitlines()), 1)

    def test_long_session_ids_hostile_t_values_and_torn_utf8(self):
        import json, tempfile
        sid = "local-" + "ab12" * 16                                   # hub session ids are 70 characters
        good = {"id": f"askgate-bb:{sid}:1793652485", "t": 1793652485, "week": "2026-11-02", "host": "mini",
                "session": sid, "pattern": "is-broken"}
        ugly = dict(good, id="askgate-bb:s2:1793652486", t=1793652486, session="s2",
                    quote="a\u2028b\u0085c\x0cd\x1ce\x1df")                # splitlines() would cut this row apart
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "askgate-bb-misses.jsonl"
            weird = [dict(good, id=["x"], t=1), dict(good, t=float("nan")), dict(good, t=float("inf")),
                     dict(good, t=10 ** 400), dict(good, t="1793652485")]
            with open(out, "wb") as fh:
                for r in weird:
                    fh.write(json.dumps(r).encode() + b"\n")
                fh.write(b'{"quote": "\xe2\x82')                           # torn multi-byte sequence, no newline
                fh.write(b"\n" + json.dumps(ugly, ensure_ascii=False).encode() + b"\n")
                fh.write(json.dumps(good).encode() + b"\n")
            self.assertEqual(g.project_bb_handoff(out), 2)
            got = [json.loads(x) for x in (Path(d) / "askgate-bb-requests.jsonl").read_text().splitlines()]
            self.assertEqual(sorted(r["id"] for r in got), sorted([good["id"], ugly["id"]]))
            self.assertNotIn("quote", json.dumps(got))
            self.assertEqual(g.project_bb_handoff(out), 0)              # idempotent

    def test_values_are_shape_checked_and_a_torn_twin_line_is_closed(self):
        import json, tempfile
        ok = {"id": "askgate-bb:s1:5", "t": 5, "week": "2026-10-12", "host": "macbook", "session": "s1",
              "pattern": "is-broken"}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "askgate-bb-misses.jsonl"
            rows = [dict(ok, host=self.QUOTE, pattern=self.QUOTE, week=self.QUOTE),     # free text in value fields
                    dict(ok, id="askgate-bb:s2:5", session="other"),                   # id disagrees with session
                    dict(ok, id="askgate-bb:s3:6"),                                    # id disagrees with t
                    dict(ok, id="askgate-bb:" + self.QUOTE + ":5", session=self.QUOTE),  # free text as the id
                    dict(ok, id="askgate-bb:s4:5", session="s4", host="\\ud800bad".encode().decode("unicode_escape")),
                    dict(ok, id="askgate-bb:s5:5", session="s5", t=True)]
            out.write_text("".join(json.dumps(r) + "\n" for r in rows))
            twin = Path(d) / "askgate-bb-requests.jsonl"
            twin.write_text('{"id": "torn')  # a previous write died mid-line, no newline
            self.assertEqual(g.project_bb_handoff(out), 2)
            lines = twin.read_text().splitlines()
            self.assertEqual(lines[0], '{"id": "torn')
            got = [json.loads(x) for x in lines[1:]]
            self.assertEqual([r["id"] for r in got], ["askgate-bb:s1:5", "askgate-bb:s4:5"])
            self.assertEqual((got[0]["host"], got[0]["pattern"], got[0]["week"]), (None, None, None))
            self.assertIsNone(got[1]["host"])
            self.assertNotIn("camera", twin.read_text())
            twin.read_text().encode("ascii")  # surrogates etc. were never written raw


class RouteToLane(unittest.TestCase):
    """The weekly review's Do-it work goes to the self-heal lane: each hit is filed as a monitor finding unless the
    lane already has that session; the card keeps its review prompt only for hits the lane did not take."""

    def hits(self):
        return [{"t": 1792600000, "session": "s_fix", "host": "macbook", "source": "cli", "class": g.FIX_CLASS,
                 "pattern": "root_cause", "quote": "root cause and fix this please"},
                {"t": 1792600100, "session": "s_ask", "host": "mini", "source": "cli", "class": g.ASK_CLASS,
                 "pattern": "do_it_yourself", "quote": "why are you asking me, do it yourself"},
                {"t": 1792600200, "session": "s_known", "host": "macbook", "source": "cli", "class": g.FIX_CLASS,
                 "pattern": "make_card", "quote": "make a card to fix"}]

    def home(self, lane):
        import json, tempfile
        h = Path(tempfile.mkdtemp())
        (h / "coach").mkdir()
        (h / "coach" / "selfheal_lane.json").write_text(json.dumps(lane))
        return h

    def test_live_lane_owns_all(self):
        filed = []
        home = self.home({"agent-0123456789": {"status": "filed", "session_id": "s_known"}})
        owned, rest = g.route_hits(self.hits(), home, filer=lambda d: filed.append(d) or {"action": "filed"})
        self.assertEqual(rest, [])
        self.assertEqual(len(owned), 3)
        self.assertEqual([d["session_id"] for d in filed], ["s_fix", "s_ask"])  # the known session is not re-filed
        self.assertEqual(filed[0]["component"], "plugin")
        self.assertIn("unfiled fix request needs review", filed[0]["symptom"])
        self.assertEqual(filed[1]["component"], "agent")
        self.assertTrue(all(d["source"] == "signal" and d["kind"] == "monitor" for d in filed))

    def test_shadow_keeps_the_review(self):
        owned, rest = g.route_hits(self.hits(), self.home({}), filer=lambda d: {"action": "would_file"})
        self.assertEqual(owned, [])
        self.assertEqual(len(rest), 3)

    def detected_home(self, fps):
        import json
        h = self.home({})
        (h / "coach" / "selfheal.json").write_text(json.dumps(fps))
        return h

    def test_shadow_detected_session_is_not_refiled(self):
        # plugin-d55a1b9bb1: the detector saw s_fix's turn in shadow mode (logged, never filed), so the lane has no
        # filing; the weekly pass must not file a fresh "unfiled fix request needs review" card for it.
        t = self.hits()[0]["t"]
        home = self.detected_home({"gateway-4d3f44896c": {"first": t + 2400, "last": t + 2400, "count": 1,
                                                          "sessions": ["s_fix"], "day": "2026-10-26"}})
        filed, detected = [], []
        owned, rest = g.route_hits(self.hits(), home, filer=lambda d: filed.append(d) or {"action": "would_file"},
                                   detected=detected)
        self.assertEqual([d["session_id"] for d in filed], ["s_ask", "s_known"])  # undetected sessions still route
        self.assertEqual([h["session"] for h in rest], ["s_ask", "s_known"])
        self.assertEqual([(h["session"], h["detected_fp"]) for h in detected], [("s_fix", "gateway-4d3f44896c")])
        self.assertNotIn("s_fix", [h["session"] for h in owned + rest])

    def test_detection_on_another_day_still_files(self):
        # a detection of the same session weeks earlier is a different turn: the hit still gets its review
        t = self.hits()[0]["t"]
        home = self.detected_home({"agent-0000000000": {"first": t - 20 * 86400, "last": t - 19 * 86400,
                                                        "count": 2, "sessions": ["s_fix"]}})
        filed, detected = [], []
        owned, rest = g.route_hits(self.hits(), home, filer=lambda d: filed.append(d) or {"action": "would_file"},
                                   detected=detected)
        self.assertEqual(detected, [])
        self.assertEqual([d["session_id"] for d in filed], ["s_fix", "s_ask", "s_known"])
        self.assertEqual(len(rest), 3)

    def seen_data(self, host, fps):
        import json, tempfile
        d = Path(tempfile.mkdtemp())
        (d / host).mkdir()
        (d / host / "selfheal-seen.json").write_text(json.dumps(fps))
        return d

    def test_detection_on_another_host_is_not_refiled(self):
        # plugin-03d4509c8a: the MacBook detector saw s_fix's turn (agent-a193bf9868, shadow), but ask-gate runs on
        # the hub whose own selfheal.json has nothing; the feed's <data>/macbook/selfheal-seen.json must count.
        t = self.hits()[0]["t"]
        data = self.seen_data("macbook", {"agent-a193bf9868": {"first": t + 30, "last": t + 30,
                                                               "sessions": ["s_fix"]}})
        filed, detected = [], []
        owned, rest = g.route_hits(self.hits(), self.home({}), filer=lambda d: filed.append(d) or
                                   {"action": "would_file"}, detected=detected, data=data)
        self.assertEqual([d["session_id"] for d in filed], ["s_ask", "s_known"])
        self.assertEqual([(h["session"], h["detected_fp"]) for h in detected], [("s_fix", "agent-a193bf9868")])
        self.assertIn("agent-a193bf9868", "\n".join(g.detected_lines(detected)))

    def test_other_host_detection_on_another_day_still_files(self):
        t = self.hits()[0]["t"]
        data = self.seen_data("macbook", {"agent-0000000000": {"first": t - 20 * 86400, "last": t - 19 * 86400,
                                                               "sessions": ["s_fix"]}})
        filed, detected = [], []
        g.route_hits(self.hits(), self.home({}), filer=lambda d: filed.append(d) or {"action": "would_file"},
                     detected=detected, data=data)
        self.assertEqual(detected, [])
        self.assertEqual([d["session_id"] for d in filed], ["s_fix", "s_ask", "s_known"])

    def test_still_flags_sees_other_host_detection(self):
        # the drain re-check of a queued row must use the same cross-host detections as the weekly pass
        t = self.hits()[0]["t"]
        data = self.seen_data("macbook", {"agent-a193bf9868": {"first": t + 30, "last": t + 30,
                                                               "sessions": ["s_fix"]}})
        row = {"session_id": "s_fix", "quote": "root cause and fix this please", "hit_t": t}
        why = g.still_flags(row, homes=[self.home({})], data=data)
        self.assertIsNotNone(why)
        self.assertIn("agent-a193bf9868", why)
        self.assertIsNone(g.still_flags(row, homes=[self.home({})], data=self.seen_data("mini", {})))

    def test_queued_payload_carries_hit_t_and_details_fallback_parses(self):
        # still_flags re-checks a queued row by its hit time: new payloads carry hit_t; old rows parse `details`
        hits = self.hits()
        filed = []
        g.route_hits(hits, self.home({}), filer=lambda d: filed.append(d) or {"action": "would_file"})
        self.assertEqual([d["hit_t"] for d in filed], [h["t"] for h in hits])
        old = {k: v for k, v in filed[0].items() if k != "hit_t"}
        self.assertEqual(g.row_hit_t(old), (hits[0]["t"] // 60) * 60)  # details keep minutes, not seconds
        self.assertIsNone(g.row_hit_t({"details": "no time here"}))

    def test_detected_line_counts_skips(self):
        hit = dict(self.hits()[0], detected_fp="gateway-4d3f44896c")
        lines = g.detected_lines([hit])
        self.assertIn("1 hit", lines[0])
        self.assertIn("not a miss", lines[0])
        self.assertIn("gateway-4d3f44896c", "\n".join(lines))
        self.assertEqual(g.detected_lines([]), [])


class Deliver(unittest.TestCase):
    """The weekly report never goes to the Inbox: unrouted hits -> one Kanban card, none -> nothing."""
    def run_(self, rest):
        import datetime as dt, types
        calls = []
        def run(cmd, **kw):
            calls.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        hit = {"class": g.FIX_CLASS, "session": "s1", "quote": "why didnt you do this yourself", "t": 1_760_000_000,
               "pattern": "do it yourself"}
        out = g.deliver([hit], dt.date(2026, 10, 12), None, rest, 1, 0, 0, run=run)
        return out, calls

    def test_unrouted_hits_become_one_kanban_card(self):
        out, calls = self.run_([{"class": g.FIX_CLASS}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ["hermes", "kanban", "create"])
        self.assertIn("ask-gate-weekly:2026-10-12", calls[0])
        self.assertNotIn("inbox", calls[0])

    def test_all_routed_posts_nothing(self):
        out, calls = self.run_([])
        self.assertIsNone(out)
        self.assertEqual(calls, [])


class SkillOutcomes(unittest.TestCase):
    """The weekly Skill outcomes section reads every host's copy in the data repo plus the local home log."""
    def test_section_rolls_up_hosts_and_home_in_the_window(self):
        import json, tempfile
        d = Path(tempfile.mkdtemp())
        data, home = d / "data", d / "home"
        # current-rules lines (rules=4): a and b closed on a real reply, c had none and stays out of the correction rate
        row = lambda host, sid, ts, fp, fr, uc, src="next_message": json.dumps(
            {"ts": ts, "host": host, "session": sid, "skill": "five-whys", "checks_first_pass": fp, "fix_rounds": fr,
             "user_correction": uc, "correction_source": src, "rules": 4})
        (data / "macbook").mkdir(parents=True)
        (data / "macbook" / "skill-outcomes.jsonl").write_text(
            row("macbook", "a", 1000, True, 0, False) + "\n" + row("macbook", "b", 1001, False, 2, True, "keyword")
            + "\n" + row("macbook", "old", 10, True, 0, False) + "\n")
        (home / "logs").mkdir(parents=True)
        # the hub's own log, also already copied into the data repo: counted once
        hub = row("hermes-hub", "c", 1002, None, None, False, "none")
        (home / "logs" / "skill-outcomes.jsonl").write_text(hub + "\n")
        (data / "hermes-hub").mkdir()
        (data / "hermes-hub" / "skill-outcomes.jsonl").write_text(hub + "\n")
        md = "\n".join(g.skill_outcome_lines(data, [home], 500, 2000))
        self.assertIn("## Skill outcomes", md)
        self.assertIn("3 uses of 1 skills on hermes-hub, macbook; 2 with a tick-off (67%)", md)
        self.assertIn("| five-whys | 3 | 2 | 50% | 1.00 | 2 | 50% |", md)

    def test_section_never_raises(self):
        self.assertIn("unavailable", " ".join(g.skill_outcome_lines(None, [Path("/nonexistent")], 0, 1)))


if __name__ == "__main__":
    unittest.main()
