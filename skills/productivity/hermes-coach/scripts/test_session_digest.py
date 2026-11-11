#!/usr/bin/env python3
"""Stdlib tests for session_digest.py:  python3 test_session_digest.py"""
import json, sqlite3, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import session_digest as sd  # noqa: E402

SCHEMA = """
create table sessions (id text primary key, source text, model text, parent_session_id text, started_at real,
  ended_at real, end_reason text, message_count int, tool_call_count int, api_call_count int, input_tokens int,
  output_tokens int, cache_read_tokens int, actual_cost_usd real, estimated_cost_usd real, title text,
  last_activity_at real);
create table messages (id integer primary key autoincrement, session_id text, role text, content text,
  tool_name text, tool_calls text, timestamp real, active int not null default 1, compacted int not null default 0,
  display_kind text, display_metadata text, message_uid text);
"""


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    now = time.time()
    con.execute("insert into sessions (id, source, started_at, last_activity_at) values ('s1','desktop',?,?)", (now, now))
    for r in rows:
        con.execute("insert into messages (session_id, role, content, timestamp, active, compacted, display_kind,"
                    " display_metadata, message_uid) values ('s1',?,?,?,?,?,?,?,?)", r)
    con.commit(); con.close()


class DigestDedupe(unittest.TestCase):
    def test_compaction_copies_and_queued_placeholders_count_once(self):
        t = time.time() - 100
        shot = "redesign it\n@image:`/Users/x/Library/Application Support/Hermes/composer-images/s.png`\n[screenshot]"
        rows = [
            ("user", "first ask", t, 0, 1, None, None, "u1"),
            ("assistant", "ok", t + 1, 0, 1, None, None, "a1"),
            # queued placeholder, re-written on every compaction pass (rewound: active=0, compacted=0)
            ("user", "redesign it", t + 2, 0, 0, None, '{"_queued_prompt": true}', "q1"),
            ("user", "redesign it", t + 2, 0, 0, None, '{"_queued_prompt": true}', "q1"),
            # compaction re-materialized copies of the first turn
            ("user", "first ask", t, 0, 1, None, None, "u1"),
            ("user", "first ask", t, 1, 0, None, None, "u1"),
            # the real drained send, with its screenshot
            ("user", shot, t + 3, 1, 0, None, None, "u2"),
            ("user", "secret plumbing", t + 4, 1, 0, "hidden", None, "h1"),
        ]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        texts = [u["text"] for u in dg["user_turns"]]
        self.assertEqual(texts, ["first ask", "redesign it\n[screenshot]"])
        self.assertEqual(dg["user_turns_total"], 2)
        self.assertEqual(dg["signals"].get("screenshot"), 1)
        self.assertEqual(dg["msgs"], 3)

    def test_legacy_schema_without_active_columns(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            con = sqlite3.connect(db)
            con.executescript(SCHEMA.split("create table messages")[0] +
                              "create table messages (id integer primary key, session_id text, role text,"
                              " content text, tool_name text, tool_calls text, timestamp real);")
            now = time.time()
            con.execute("insert into sessions (id, started_at) values ('s1', ?)", (now,))
            con.execute("insert into messages (session_id, role, content, timestamp) values ('s1','user','hi',?)", (now,))
            con.commit(); con.close()
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        self.assertEqual([u["text"] for u in dg["user_turns"]], ["hi"])
        self.assertIsNone(dg["hidden"])  # no hidden column: unknown, so probe_token_watch never flags it

    def test_hidden_flag_is_carried(self):
        """probe_token_watch flags a leftover agent test chat only when the digest says it is visible."""
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, [("user", "Reply with just the word: x", time.time(), 1, 0, None, None, "u1")])
            con = sqlite3.connect(db)
            con.execute("alter table sessions add column hidden int not null default 0")
            con.execute("insert into sessions (id, source, started_at, last_activity_at, hidden) "
                        "values ('s2','desktop',?,?,1)", (time.time(), time.time()))
            con.commit(); con.close()
            got = {dg["id"]: dg["hidden"] for dg in sd.digest_db("default", db, "test", 0)}
        self.assertEqual(got, {"s1": False, "s2": True})


class DigestFeatures(unittest.TestCase):
    def test_prev_tools_counts_tool_calls_after_the_agents_last_text(self):
        """Defect agent-b6540f8eef: a reply ending "Reading the card first:" looked the same whether tools ran after
        it or the agent stalled, because `prev` keeps only text. `prev_tools` is the count of tool calls since it."""
        t = time.time() - 100
        call = lambda name: {"type": "function", "function": {"name": name, "arguments": "{}"}}
        rows = [("user", "verify the card", t, 1, 0, None, None, "u1"),
                ("assistant", "Reading the card first:", t + 1, 1, 0, None, None, "a1"),
                ("assistant", "", t + 2, 1, 0, None, None, "a2"),
                ("user", "what's going on with this?", t + 3, 1, 0, None, None, "u2"),
                ("assistant", "Checking the PR now:", t + 4, 1, 0, None, None, "a3"),
                ("user", "and?", t + 5, 1, 0, None, None, "u3")]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            con = sqlite3.connect(db)
            con.execute("update messages set tool_calls=? where message_uid='a1'", (json.dumps([call("kanban_show")]),))
            con.execute("update messages set tool_calls=? where message_uid='a2'", (json.dumps([call("terminal")]),))
            con.commit(); con.close()
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        turns = dg["user_turns"]
        self.assertNotIn("prev_tools", turns[0])  # nothing before the first message
        self.assertEqual(turns[1]["prev"], "Reading the card first:")
        self.assertEqual(turns[1]["prev_tools"], 2)  # text, then 2 tool calls, then the user
        self.assertEqual(turns[2]["prev_tools"], 0)  # text, then the user: a real no-tool stall

    def test_feature_counts_from_tool_args_and_tool_call_wrapper(self):
        t = time.time() - 100
        call = lambda name, args: {"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        calls = [
            call("kanban_create", {"title": "x", "assignee": "reviewer", "parents": ["t_1"], "max_runtime_seconds": 3600,
                                   "workspace_kind": "worktree", "goal_mode": True}),
            call("kanban_create", {"title": "y", "assignee": "default", "triage": True}),
            call("terminal", {"command": "hermes kanban boards create babybear && git worktree add ../wt"}),
            call("terminal", {"command": "grep -w foo ~/.hermes/config.yaml"}),
            call("tool_call", {"calls": [{"name": "todo_list", "arguments": {"todos": []}},
                                         {"name": "cronjob_manage", "arguments": {"action": "create"}}]}),
        ]
        rows = [("user", "go", t, 1, 0, None, None, "u1"),
                ("assistant", "", t + 1, 1, 0, None, None, "a1")]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            con = sqlite3.connect(db)
            con.execute("update messages set tool_calls=? where message_uid='a1'", (json.dumps(calls),))
            con.commit(); con.close()
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        f = dg["features"]
        self.assertEqual(f["kanban_create"], 2)
        for k in ("max_runtime", "parents", "worktree", "goal_mode", "specialist", "triage"):
            self.assertEqual(f[f"kanban_create:{k}"], 1, k)
        self.assertEqual((f["kanban_board_create"], f["git_worktree"], f["todo"], f["cron_create"]), (1, 1, 1, 1))
        self.assertNotIn("hermes_worktree", f)  # `grep -w` on a ~/.hermes path is not `hermes -w`
        self.assertEqual(dg["tools"].get("tool_call"), 1)  # inner calls feed features, not tool counts
        self.assertNotIn("todo_list", dg["tools"])
        self.assertEqual(dg["digest_v"], sd.DIGEST_V)


class DigestSynthetic(unittest.TestCase):
    def test_kanban_notices_cron_prompts_and_continuation_notes_are_not_user_turns(self):
        t = time.time() - 100
        texts = [
            "✔ [default] @default Kanban t_x done — still failing",
            "⏸ [default] @default Kanban t_x blocked: stuck waiting on the hub, same error again",
            "✖ [default] @default Kanban t_x worker crashed (pid gone); dispatcher will retry",
            "⏱ Kanban t_x timed out (max_runtime=60s); will retry",
            '[Cronjob "[bot:coach] Daily mine" output — scheduled job, not the user.] still broken, stuck again',
            "[Continuing toward this kanban task — judge says it is not done yet]\nReason: still broken",
            "[Your active task list was preserved across context compression]\n- [>] fix the stuck thing again",
            "[Current user objective preserved from compacted history]\nit still doesn't work",
            "[Turn stopped by Desktop before it finished.]",
        ]
        rows = [("user", s, t + i, 1, 0, None, None, f"u{i}") for i, s in enumerate(texts)]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        self.assertEqual(dg["user_turns"], [])
        self.assertEqual(dg["user_turns_total"], 0)
        s = dg["signals"]
        self.assertNotIn("repeat_fix", s)
        self.assertNotIn("stuck", s)
        self.assertEqual(s.get("synthetic_kanban"), 4)
        self.assertEqual(s.get("synthetic_cron"), 1)
        self.assertEqual((s.get("synthetic_kanban_continue"), s.get("synthetic_todo_preserved"),
                          s.get("synthetic_objective_preserved"), s.get("synthetic_turn_stopped")), (1, 1, 1, 1))

    def test_user_turn_mentioning_kanban_is_kept(self):
        t = time.time() - 100
        rows = [("user", "Kanban t_x is still broken", t, 1, 0, None, None, "u1"),
                ("user", "✔ looks good, ship it", t + 1, 1, 0, None, None, "u2")]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        self.assertEqual(dg["user_turns_total"], 2)
        self.assertEqual(dg["signals"].get("repeat_fix"), 1)

    def test_each_user_turn_carries_the_agent_reply_before_it(self):
        t = time.time() - 100
        long = "Shipped the fix to /Users/x/code/app. " + "detail " * 200 + "Want me to also restart the pod?"
        rows = [("user", "fix the sidebar", t, 1, 0, None, None, "u1"),
                ("assistant", long, t + 1, 1, 0, None, None, "a1"),
                ("assistant", "", t + 2, 1, 0, None, None, "a2"),  # an empty tool-call turn is not the reply
                ("user", "nope, still broken", t + 3, 1, 0, None, None, "u2")]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        first, second = dg["user_turns"]
        self.assertNotIn("prev", first)
        self.assertTrue(second["prev"].startswith("Shipped the fix to ~/code/app."))
        self.assertTrue(second["prev"].endswith("Want me to also restart the pod?"))
        self.assertIn("…[clipped]…", second["prev"])
        self.assertLessEqual(len(second["prev"]), sd.PREV_HEAD + sd.PREV_TAIL + 20)
        self.assertEqual(dg["digest_v"], sd.DIGEST_V)

    def test_turn_sent_by_an_inbox_do_it_press_is_flagged(self):
        """plugin-9ab6e8e868: a labelled Inbox card shows only "**Do it:** <label>", so the pasted prompt is not in
        `prev`; the Inbox store (<home>/inbox/inbox.db items.accept_prompt for that session) is the provenance."""
        t = time.time() - 100
        prompt = "On the hub, recover the corrupt board and find why it keeps corrupting."
        rows = [("user", prompt, t, 1, 0, None, None, "u1"),
                ("assistant", "On it.", t + 1, 1, 0, None, None, "a1"),
                ("user", "no, find out why it broke first", t + 2, 1, 0, None, None, "u2")]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))
            self.assertNotIn("inbox_doit", dg["user_turns"][0])  # no Inbox store: nothing is claimed
            (Path(d) / "inbox").mkdir()
            con = sqlite3.connect(Path(d) / "inbox" / "inbox.db")
            con.execute("create table items (session_id text primary key, accept_prompt text not null, state text)")
            con.execute("insert into items values ('s1', ?, 'accepted')", ("  " + prompt.replace(" ", "\n ", 2),))
            con.commit(); con.close()
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        first, second = dg["user_turns"]
        self.assertIs(first.get("inbox_doit"), True)
        self.assertNotIn("inbox_doit", second)  # David's own words after the card still count

    def test_inbox_prompts_reads_through_the_query_only_fallback(self):
        """When the read-only open of the Inbox store fails, inbox_prompts still reads it, through a mode=rw
        connection with query_only on, and writes nothing. A bare ?mode=ro open lost the provenance there ({})."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "inbox").mkdir()
            store = Path(d) / "inbox" / "inbox.db"
            con = sqlite3.connect(store)
            con.execute("create table items (session_id text, accept_prompt text)")
            con.execute("insert into items values ('s1', 'ask 1')")
            con.commit(); con.close()
            real_connect, opened = sqlite3.connect, []

            def connect(target, *args, **kwargs):
                opened.append(str(target))
                if "mode=ro" in str(target):
                    raise sqlite3.OperationalError("unable to open database file")
                return real_connect(target, *args, **kwargs)

            with mock.patch.object(sd.sqlite3, "connect", connect):
                self.assertEqual(sd.inbox_prompts(Path(d) / "state.db"), {"s1": "ask 1"})
                fallback = sd._ro_connect(store)
            try:
                with self.assertRaises(sqlite3.OperationalError):  # query_only: the fallback refuses writes
                    fallback.execute("insert into items values ('s2', 'ask 2')")
            finally:
                fallback.close()
            self.assertTrue(any("mode=rw" in o for o in opened), opened)
            self.assertEqual(real_connect(store).execute("select count(*) from items").fetchone()[0], 1)

    def test_user_turn_after_an_interrupted_model_wait_carries_its_reason(self):
        """Core stamps display_metadata.interrupt on the interrupted row; the next user turn names why the turn
        stopped (self-heal agent-38bad5aced: Stop vs backend exit vs reaper were indistinguishable)."""
        t = time.time() - 100
        meta = json.dumps({"interrupt": {"reason": "user_stop", "waited_s": 106.6, "prompt_tokens": 320000}})
        rows = [("user", "do it", t, 1, 0, None, None, "u1"),
                ("assistant", "Operation interrupted: waiting for model response (106.6s elapsed).", t + 1, 1, 0,
                 None, meta, "a1"),
                ("user", "resume", t + 2, 1, 0, None, None, "u2"),
                ("assistant", "Done.", t + 3, 1, 0, None, None, "a2"),
                ("user", "thanks, next", t + 4, 1, 0, None, None, "u3")]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        _first, nudge, later = dg["user_turns"]
        self.assertEqual(nudge["prev_interrupt"], {"reason": "user_stop", "waited_s": 106.6, "prompt_tokens": 320000})
        self.assertNotIn("prev_interrupt", later)  # a real reply after it clears the reason


class ExcludeProfileGlob(unittest.TestCase):
    def test_glob_drops_follower_profile(self):
        self.assertTrue(sd.excluded_profile("alpha-follow", ["alpha*"]))
        self.assertTrue(sd.excluded_profile("alpha", ["alpha*"]))
        self.assertFalse(sd.excluded_profile("default", ["alpha*"]))
        self.assertTrue(sd.excluded_profile("alpha", ["alpha"]))  # exact names still work
        self.assertFalse(sd.excluded_profile("alpha-follow", ["alpha"]))




class DigestUserTurnClipping(unittest.TestCase):
    def test_injected_context_does_not_displace_user_correction(self):
        user = "I vetoed this: " + "quoted warning " * 50 + " by accident, it's approved"
        for marker in ("Attached Context", "Context Warnings"):
            text = user + "\n\n--- " + marker + " ---\n\n" + "external page " * 100
            t = time.time() - 100
            with tempfile.TemporaryDirectory() as d:
                db = Path(d) / "state.db"
                make_db(db, [("user", text, t, 1, 0, None, None, "u1")])
                (dg,) = list(sd.digest_db("default", db, "test", 0))
            turn = dg["user_turns"][0]
            self.assertTrue(turn["text"].endswith("by accident, it's approved"))
            self.assertNotIn("external page", turn["text"])
            self.assertEqual(turn["text_original_chars"], len(user))
        self.assertEqual(sd.clean_user("Discuss Attached Context in prose"), "Discuss Attached Context in prose")

    def test_long_user_correction_preserves_head_tail_and_budget(self):
        text = "I vetoed this: " + "quoted background " * 40 + " by accident, it is approved"
        clipped = sd.clip_user_turn(text)
        self.assertTrue(clipped.startswith("I vetoed this:"))
        self.assertTrue(clipped.endswith("by accident, it is approved"))
        self.assertEqual(len(clipped), sd.TURN_CHARS)
        self.assertEqual(sd.clip_user_turn("short"), "short")

    def test_distinct_long_turns_keep_correction_and_metadata(self):
        prefix = "quoted material " * 50
        a, b = prefix + "actually approved", prefix + "actually vetoed"
        t = time.time() - 100
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, [("user", a, t, 1, 0, None, None, "u1"),
                         ("user", b, t + 1, 1, 0, None, None, "u2")])
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        self.assertEqual(len(dg["user_turns"]), 2)
        self.assertTrue(dg["user_turns"][0]["text"].endswith("actually approved"))
        self.assertTrue(dg["user_turns"][1]["text"].endswith("actually vetoed"))
        self.assertTrue(dg["user_turns"][0]["text_truncated"])
        self.assertEqual(dg["user_turns"][0]["text_original_chars"], len(a))

class DigestPluginStartedOpener(unittest.TestCase):
    def test_turn_written_by_the_session_start_api_is_flagged(self):
        """plugin-4731e7cdc3: core's session-start API (a plugin opening a chat) records the user row it wrote in
        session_start_requests.user_row_id; that turn is the plugin's text, not the user's."""
        t = time.time() - 100
        rows = [("user", "Diagnosis only. Inspect this runner/host.", t, 1, 0, None, None, "u1"),
                ("assistant", "Looking.", t + 1, 1, 0, None, None, "a1"),
                ("user", "why did it fail? root cause it", t + 2, 1, 0, None, None, "u2")]
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.db"
            make_db(db, rows)
            (dg,) = list(sd.digest_db("default", db, "test", 0))  # no table: nothing is claimed
            self.assertNotIn("generated", dg["user_turns"][0])
            self.assertEqual(dg["user_turns_total"], 2)
            con = sqlite3.connect(db)
            uid = con.execute("select id from messages where message_uid='u1'").fetchone()[0]
            con.execute("create table session_start_requests (namespace text, stored_session_id text,"
                        " user_row_id integer)")
            con.execute("insert into session_start_requests values ('runners', 's1', ?)", (uid,))
            con.commit(); con.close()
            (dg,) = list(sd.digest_db("default", db, "test", 0))
        first, second = dg["user_turns"]
        self.assertEqual(first.get("generated"), "start:runners")
        self.assertNotIn("generated", second)
        self.assertEqual(dg["user_turns_total"], 1)


class DigestIncremental(unittest.TestCase):
    """A rerun re-reads only sessions that changed since the last run (self-heal cron-c89ed44e95: the hub feed
    re-digested all ~14k sessions every 30 min and hit coach_common's 600 s kill)."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.db = self.d / "state.db"
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        now = time.time()
        for i, sid in enumerate(("s1", "s2", "s3")):
            con.execute("insert into sessions (id, source, started_at, last_activity_at, message_count)"
                        " values (?, 'desktop', ?, ?, 2)", (sid, now - 100, now - 100))
            con.execute("insert into messages (session_id, role, content, timestamp, message_uid)"
                        " values (?, 'user', ?, ?, ?)", (sid, f"ask {i}", now - 100, f"u{sid}"))
            con.execute("insert into messages (session_id, role, content, timestamp, message_uid)"
                        " values (?, 'assistant', 'ok', ?, ?)", (sid, now - 99, f"a{sid}"))
        con.commit(); con.close()
        self.out = self.d / "out"
        self.calls = []
        real = sd.transcript
        def counting(con, sid, mcols):
            self.calls.append(sid)
            return real(con, sid, mcols)
        self._real, sd.transcript = real, counting

    def tearDown(self):
        sd.transcript = self._real

    def run_main(self, *extra):
        import contextlib, io
        self.calls.clear()
        buf = io.StringIO()
        argv = ["session_digest.py", "--host", "h", "--out", str(self.out), "--db", f"default={self.db}", *extra]
        old, sys.argv = sys.argv, argv
        try:
            with contextlib.redirect_stdout(buf):
                sd.main()
        finally:
            sys.argv = old
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_unchanged_rerun_reads_no_transcript(self):
        first = self.run_main()
        self.assertEqual((first["written"], first.get("skipped")), (3, 0))
        self.assertEqual(sorted(self.calls), ["s1", "s2", "s3"])
        self.assertIn("elapsed_s", first)
        second = self.run_main()
        self.assertEqual(second.get("skipped"), 3)
        self.assertEqual(second["written"], 0)
        self.assertEqual(self.calls, [])

    def test_only_the_changed_session_is_redigested(self):
        self.run_main()
        con = sqlite3.connect(self.db)  # a new row with no sessions-row bump still counts as a change
        con.execute("insert into messages (session_id, role, content, timestamp, message_uid)"
                    " values ('s2', 'user', 'one more', ?, 'u2b')", (time.time(),))
        con.commit(); con.close()
        st = self.run_main()
        self.assertEqual(self.calls, ["s2"])
        self.assertEqual((st["written"], st["skipped"]), (1, 2))
        dg = json.loads((self.out / "h" / "default" / "s2.json").read_text())
        self.assertEqual([u["text"] for u in dg["user_turns"]][-1], "one more")

    def test_digest_version_bump_and_full_redigest_everything(self):
        self.run_main()
        old = sd.DIGEST_V
        sd.DIGEST_V = old + 1
        try:
            self.run_main()
            self.assertEqual(sorted(self.calls), ["s1", "s2", "s3"])
        finally:
            sd.DIGEST_V = old
        self.run_main()  # back on the old version: everything again
        self.run_main("--full")
        self.assertEqual(sorted(self.calls), ["s1", "s2", "s3"])

    def test_missing_output_file_is_redigested(self):
        self.run_main()
        (self.out / "h" / "default" / "s3.json").unlink()
        st = self.run_main()
        self.assertEqual(self.calls, ["s3"])
        self.assertTrue((self.out / "h" / "default" / "s3.json").exists())
        self.assertEqual(st["skipped"], 2)

    def test_stale_full_rebuild_and_kill_resume(self):
        self.run_main()
        cur = self.out / "h" / ".digest-cursor.json"
        c = json.loads(cur.read_text())
        self.assertEqual(len(c["keys"]), 3)
        c["full_at"] = time.time() - 8 * 86400  # last full rebuild over a week ago -> rebuild in full
        cur.write_text(json.dumps(c))
        st = self.run_main()
        self.assertTrue(st["full"])
        self.assertEqual(sorted(self.calls), ["s1", "s2", "s3"])
        self.assertFalse(self.run_main()["full"])
        self.assertEqual(self.calls, [])
        cur.write_text("{not json")  # a corrupt cursor is a full run, never a crash
        self.assertEqual(self.run_main()["skipped"], 0)

    def test_title_or_inbox_prompt_change_is_redigested(self):
        self.run_main()
        con = sqlite3.connect(self.db)
        con.execute("update sessions set title='renamed' where id='s1'")
        con.commit(); con.close()
        self.run_main()
        self.assertEqual(self.calls, ["s1"])
        inbox = self.d / "inbox"
        inbox.mkdir()
        con = sqlite3.connect(inbox / "inbox.db")
        con.execute("create table items (session_id text, accept_prompt text)")
        con.execute("insert into items values ('s3', 'ask 2')")
        con.commit(); con.close()
        self.run_main()
        self.assertEqual(self.calls, ["s3"])


if __name__ == "__main__":
    unittest.main()
