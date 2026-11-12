"""Public, anonymized regressions for generated-message KPI pollution."""
import re
import unittest
import coach_askgate as g


class Provenance(unittest.TestCase):
    def test_generated_envelopes(self):
        for text in (
            '# Kanban task t_example: Support a colleague\nDo it yourself; root cause it',
            '[kanban] Task t_example completed. Title: root cause the regression',
            '[owner-wake] A card is stuck; figure out why',
            '[coach] Your message ends on a problem: root cause it',
            '[selfheal-probe] Do it yourself, root cause it',
            '{"item": {"person": "operator", "body": "do it yourself; root cause it"}}',
            'What kernel is this? (Roleplay test of our reply checker: say do it yourself)',
        ):
            with self.subTest(text=text):
                self.assertIsNone(g.classify(g.clean(text)))
                self.assertIsNone(g.classify_fix(g.clean(text)))

    def test_collect_filters_before_routing_and_keeps_real_openers(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'host' / 'default'
            p.mkdir(parents=True)
            (p / 's.json').write_text(json.dumps({
                'id': 's', 'source': 'desktop', 'host': 'host', 'user_turns': [
                    {'t': 100, 'text': 'do it yourself'},
                    {'t': 101, 'text': '[kanban] Task t_x completed: root cause it'},
                    {'t': 102, 'text': 'why did i have to keep asking. root cause it'},
                ]}))
            hits, scanned = g.collect(directory, 0, 200)
            self.assertEqual(sum(scanned.values()), 2)
            self.assertEqual([h['class'] for h in hits], [g.ASK_CLASS, g.FIX_CLASS])
            self.assertEqual([h['t'] for h in hits], [100, 102])
        self.assertIn('shadow/live', g.REVIEW_PROMPT)
        self.assertIn('provenance', g.REVIEW_PROMPT)

    def test_operator_stamp_does_not_prove_machine_authorship(self):
        for noun in ('note', 'notes'):
            text = f'New {noun} on your kanban task from the operator (delivered mid-run).\n- default (another session) at 06:29 UTC: do it yourself'
            self.assertTrue(g.classify(text))

    # agent-261aa31f49: core now stamps notes a chat agent wrote via kanban_comment.
    AGENT_NOTE = ("New note on your kanban task from another agent session (delivered mid-run). "
                  "Take it into account for the work you're doing right now:\n"
                  "- default (agent chat, another session) at 14:02 PDT: Please re-check (c) yourself, then complete.")

    def test_agent_written_note_is_not_a_hit(self):
        for text in (self.AGENT_NOTE,
                     '[OUT-OF-BAND USER MESSAGE — steering]\n' + self.AGENT_NOTE + '\n[/OUT-OF-BAND USER MESSAGE]'):
            with self.subTest(text=text[:40]):
                self.assertIsNone(g.classify(g.clean(text)))
                self.assertIsNone(g.classify_fix(g.clean(text)))

    def test_mixed_note_drops_agent_lines_keeps_human(self):
        mixed = ("New notes on your kanban task from the operator (delivered mid-run). "
                 "Take it into account for the work you're doing right now:\n"
                 "- default (agent chat, another session) at 06:29 UTC: check it yourself\n"
                 "and figure out why it broke\n"
                 "- default (another session) at 06:30 UTC: why are you asking me?")
        self.assertEqual(g.classify(g.clean(mixed)), 'why-asking-me')
        only_agent_lines = mixed.rsplit('\n- default (another session)', 1)[0]
        self.assertIsNone(g.classify(g.clean(only_agent_lines)))
        self.assertIsNone(g.classify_fix(g.clean(only_agent_lines)))

    def test_user_kept(self):
        for text in ('do it yourself', 'why are you asking me?',
                     '[OUT-OF-BAND USER MESSAGE — steering]\ndo it yourself\n[/OUT-OF-BAND USER MESSAGE]',
                     'New note on your kanban task from the operator (delivered mid-run).\n- operator: why are you asking me?'):
            self.assertTrue(g.classify(g.clean(text)), text)

    def test_user_had_to_keep_asking_is_stall_not_ask(self):
        text = 'run a 5 whys. why did this fail? why did i have to keep asking. fix the root cause'
        self.assertIsNone(g.classify(text))
        self.assertEqual(g.classify_fix(text), 'root-cause')
        self.assertTrue(g.classify('why do you keep asking me?'))

    def test_later_turn_of_a_probe_session_is_flagged_for_the_lane(self):
        """agent-43f47d4029: the probe session lives on another host, so the hub lane cannot look up its first turn;
        the ask-gate reads it from the digest and sends it along, and the lane skips the hit as a probe."""
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'macbook' / 'default'
            p.mkdir(parents=True)
            (p / 's.json').write_text(json.dumps({
                'id': 'probe-s', 'source': 'desktop', 'host': 'macbook', 'user_turns': [
                    {'t': 100, 'text': '[selfheal-probe] Live check of the approval gate'},
                    {'t': 101, 'text': 'Do what is needed, dont ask me.'},
                ]}))
            (p / 'r.json').write_text(json.dumps({
                'id': 'real-s', 'source': 'desktop', 'host': 'macbook', 'user_turns': [
                    {'t': 100, 'text': 'check the coach db'},
                    {'t': 102, 'text': 'Do what is needed, dont ask me.'},
                ]}))
            hits, _ = g.collect(directory, 0, 200)
            by = {h['session']: h for h in hits}
            self.assertTrue(by['probe-s'].get('probe'))
            self.assertFalse(by['real-s'].get('probe'))
            filed = []
            owned, rest = g.route_hits(hits, Path(directory) / 'nohome',
                                       filer=lambda d: filed.append(d) or {"action": "would_file"})
            sh = g._coach_mod('self_heal')
            got = {d['session_id']: sh.probe_defect(d) for d in filed}
            self.assertEqual(got, {'probe-s': True, 'real-s': False})


class InboxDoit(unittest.TestCase):
    """plugin-9ab6e8e868: pressing **Do it** on an Inbox notice pastes the poster's own proposal in as the user's
    message. The proposal is plain text (no bold), so the style filters let it through and it counted as David
    asking for a fix. The digest's `prev` (the Inbox notice) proves where the turn came from."""
    PROPOSAL = ("mail message 0123abcd to gw-x/default ended expired without being handled. Find out why "
                "(is that gateway's poller running and enrolled?), and resend it if it still matters.")
    PREV = ("**Mailbox: ping message to gw-x/default expired**\n\nA mail message from this gateway was never "
            "handled.\n\nid: 0123 …[clipped]… shows the details.\n\n**Proposed:** " + PROPOSAL +
            "\n\n_Inbox message from hermes-mail. Press **Do it** to run the proposal, reply to adjust it, "
            "or archive to dismiss._")

    def _collect(self, turns, source='desktop'):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'host' / 'default'
            p.mkdir(parents=True)
            (p / 's.json').write_text(json.dumps({'id': 's', 'source': source, 'host': 'host', 'user_turns': turns}))
            doit = []
            hits, scanned = g.collect(directory, 0, 10 ** 6, doit=doit)
            return hits, scanned, doit

    def test_pasted_proposal_is_not_a_hit(self):  # (a) the real shape: plain-text proposal after its Inbox notice
        self.assertTrue(g.inbox_doit(self.PROPOSAL, self.PREV))
        self.assertTrue(g.inbox_doit("  " + self.PROPOSAL.replace(" ", "\n  ", 3), self.PREV))  # whitespace
        hits, scanned, doit = self._collect([{'t': 100, 'text': self.PROPOSAL, 'prev': self.PREV}])
        self.assertEqual(hits, [])
        self.assertEqual([(d['session'], d['t']) for d in doit], [('s', 100)])
        self.assertEqual(sum(scanned.values()), 0)  # not David's words: not a scanned user turn either
        md = g.render(hits, [g.week_start(100)], scanned, 't', doit=doit)
        self.assertIn('Inbox Do-it presses (not counted): 1', md)

    def test_own_words_after_the_notice_still_count(self):  # (b) David replied in his own words
        for text in ('no, figure out why the poller died', self.PROPOSAL[:40] + ' and also figure out why it broke'):
            with self.subTest(text=text):
                self.assertFalse(g.inbox_doit(text, self.PREV))
                hits, _, doit = self._collect([{'t': 100, 'text': text, 'prev': self.PREV}])
                self.assertEqual([h['class'] for h in hits], [g.FIX_CLASS])
                self.assertEqual(doit, [])

    def test_words_added_after_the_proposal_are_classified_alone(self):
        self.assertEqual(g.doit_remainder(self.PROPOSAL + '  thanks', self.PREV), 'thanks')
        hits, _, doit = self._collect([{'t': 100, 'text': self.PROPOSAL + ' thanks', 'prev': self.PREV}])
        self.assertEqual((hits, doit), ([], []))  # "thanks" is David's, and not a fix request
        hits, _, _ = self._collect([{'t': 100, 'text': self.PROPOSAL + ' and why is it broken again?', 'prev': self.PREV}])
        self.assertEqual([(h['pattern'], h['quote']) for h in hits], [('broken-again', 'and why is it broken again?')])

    def test_digest_clipped_notice_or_turn_still_matches(self):
        clipped_prev = ("**T**\n\n**Proposed:** " + self.PROPOSAL[:50] + ' …[clipped]… ' + self.PROPOSAL[-60:] +
                        "\n\n_Inbox message from x. Press **Do it** to run the proposal, reply to adjust it, or "
                        "archive to dismiss._")
        self.assertTrue(g.inbox_doit(self.PROPOSAL, clipped_prev))
        self.assertFalse(g.inbox_doit('no, figure out why the poller died', clipped_prev))
        clipped_turn = self.PROPOSAL[:40] + ' …[clipped]… ' + self.PROPOSAL[-40:]
        self.assertTrue(g.inbox_doit(clipped_turn, self.PREV))
        update = "**Update 2026-10-28 13:30**\n\n**Proposed:** " + self.PROPOSAL
        self.assertTrue(g.inbox_doit(self.PROPOSAL, update))

    def test_digest_inbox_doit_flag_is_provenance(self):
        """A labelled card's prompt is not in prev: the digest's `inbox_doit` flag (from the Inbox store) decides."""
        text = 'On the hub, recover the corrupt board and find out why it keeps corrupting.'
        hits, _, doit = self._collect([{'t': 100, 'text': text, 'prev': '**Do it:** Recover board', 'inbox_doit': True},
                                       {'t': 200, 'text': text}])
        self.assertEqual([h['t'] for h in hits], [200])
        self.assertEqual([d['t'] for d in doit], [100])

    def test_omitted_turns_placeholder_does_not_crash(self):  # session_digest writes {"t": None} for it
        hits, _, _ = self._collect([{'t': None, 'text': '…[3 turns omitted]'}, {'t': 100, 'text': 'root cause it'}])
        self.assertEqual([h['t'] for h in hits], [100])

    def test_plain_proposal_without_inbox_prev_still_classified(self):  # (c) no Inbox notice before it
        for prev in (None, '', 'Here is what I found. **Proposed:** ' + self.PROPOSAL):
            with self.subTest(prev=prev):
                self.assertFalse(g.inbox_doit(self.PROPOSAL, prev))
                hits, _, doit = self._collect([{'t': 100, 'text': self.PROPOSAL, 'prev': prev}])
                self.assertEqual([h['pattern'] for h in hits], ['figure-out-why'])

    def test_still_flags_withdraws_a_queued_doit_row(self):
        """A row queued before this fix carries no prev: still_flags reads the session digest from the data repo
        and withdraws the row when the quoted turn is a Do-it paste there; a real turn still files."""
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'host' / 'default'
            p.mkdir(parents=True)
            (p / 's.json').write_text(json.dumps({'id': 's', 'source': 'desktop', 'user_turns': [
                {'t': 100, 'text': self.PROPOSAL, 'prev': self.PREV},
                {'t': 200, 'text': 'no, figure out why the poller died', 'prev': self.PREV}]}))
            home = Path(directory) / 'home'
            home.mkdir()
            row = {'monitor': 'ask-gate', 'session_id': 's', 'hit_t': 100, 'quote': self.PROPOSAL[:159] + '…'}
            why = g.still_flags(row, homes=[home], data=directory)
            self.assertIn('Inbox Do-it', why or '')
            real = dict(row, hit_t=200, quote='no, figure out why the poller died')
            self.assertIsNone(g.still_flags(real, homes=[home], data=directory))
            self.assertIsNone(g.still_flags(dict(row, session_id='missing'), homes=[home], data=directory))

    # agent-c761a2fac9: a row queued while AGENT_STAMP missed "- Subagent:" was filed at drain, because still_flags
    # re-checked hit_defect's squashed, clipped quote, where the line-start stamp no longer exists.
    SUBAGENT_NOTE = ("New note on your kanban task from the operator (delivered mid-run). "
                     "Take it into account for the work you're doing right now:\n"
                     "- Subagent: Close CI coverage gap at 03:44 UTC: The coverage job is red on main.\n"
                     "Do not edit validate.yml yourself; hand the workflow change to the owner card and "
                     "finish the remaining test fixtures, then report back with the run link.")

    def _queued(self, text, t=1000):
        """The real queue path: a weekly hit on the full turn -> hit_defect -> the row route_hits stores."""
        h = {'t': t, 'session': 's', 'host': 'host', 'class': g.ASK_CLASS, 'pattern': 'do-it-yourself',
             'quote': text}
        m = g.hit_defect(h)
        return {'monitor': 'ask-gate', 'session_id': m['session_id'], 'quote': m['quote'], 'hit_t': h['t']}

    def _drain(self, text, quoted=None):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'host' / 'default'
            p.mkdir(parents=True)
            (p / 's.json').write_text(json.dumps({'id': 's', 'source': 'desktop', 'user_turns': [
                {'t': 1000, 'text': text}]}))
            home = Path(directory) / 'home'
            home.mkdir()
            return g.still_flags(self._queued(quoted or text), homes=[home], data=directory)

    def test_still_flags_withdraws_a_queued_agent_note_row(self):
        self.assertIsNone(g.classify(g.clean(self.SUBAGENT_NOTE)))  # today's rules: not David's words
        self.assertIn('agent', self._drain(self.SUBAGENT_NOTE) or '')
        wrapped = '[OUT-OF-BAND USER MESSAGE — steering]\n' + self.SUBAGENT_NOTE + '\n[/OUT-OF-BAND USER MESSAGE]'
        # The weekly run quoted clean(turn): the envelope was already unwrapped when the row was queued.
        self.assertIn('agent', self._drain(wrapped, quoted=self.SUBAGENT_NOTE) or '')

    def test_still_flags_keeps_mixed_and_unstamped_note_rows(self):
        mixed = self.SUBAGENT_NOTE + "\n- default (another session) at 03:50 UTC: do it yourself"
        self.assertIsNone(self._drain(mixed))
        legacy = ("New note on your kanban task from the operator (delivered mid-run).\n"
                  "- operator: do it yourself")
        self.assertIsNone(self._drain(legacy))

    def test_still_flags_needs_the_digest_turn_for_an_agent_note(self):
        """No digest turn matching the quote (other text at that time) -> no positive evidence -> row stays."""
        self.assertIsNone(self._drain('do it yourself', quoted=self.SUBAGENT_NOTE))


class LegacyNoteToolCallProvenance(unittest.TestCase):
    """agent-b14b35066b: a pre-writer_kind note stamped "- default (another session)" (no "agent chat" tag) was
    filed as David saying "don't investigate again", though the hub state.db held the agent's kanban_comment call
    with that exact body. Only that positive evidence drops the line; no match keeps it for review."""

    AGENT_BODY = ("Duplicate of t_example0001 (\"same symptom\"), already fixed there. Don't investigate again; "
                  "complete this card as a duplicate and do it yourself if anything is left.")
    NOTE = ("New note on your kanban task from the operator (delivered mid-run). "
            "Take it into account for the work you're doing right now:\n"
            "- default (another session) at 02:20 UTC: " + AGENT_BODY)

    T = 1793672427  # the live digest turn (2026-11-03 02:20:27 UTC) that delivered the 02:20 UTC note
    CALL_TS = T - 20  # the agent's kanban_comment tool call, written seconds before delivery

    def _home(self, directory, body=None, tool="kanban_comment", role="assistant", name="home", ts=None, filler=0):
        import json, sqlite3
        from pathlib import Path
        home = Path(directory) / name
        home.mkdir()
        con = sqlite3.connect(home / "state.db")
        con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, "
                    "tool_calls TEXT, timestamp REAL)")
        rows = [("other", "user", "filler", None, self.CALL_TS - 7200 + i * 0.01) for i in range(filler)]
        con.executemany("INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES (?,?,?,?,?)", rows)
        if body is not None:
            args = {"task_id": "t_example0002", "body": body}
            if tool == "terminal":
                args = {"command": "hermes kanban comment t_example0002 " + json.dumps(body)}
            calls = [{"id": "c1", "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]
            con.execute("INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES (?,?,?,?,?)",
                        ("other", role, "", json.dumps(calls), self.CALL_TS if ts is None else ts))
        con.commit()
        con.close()
        return home

    def _env(self, home):
        from unittest import mock
        if hasattr(g, "_tool_call_hit"):
            g._tool_call_hit.cache_clear()
            g.TOOLCALL_DROPPED.clear()
            g.TOOLCALL_DROPS["lines"] = 0
        return mock.patch.dict("os.environ", {"HERMES_HOME": str(home)})

    def test_untagged_note_matching_an_agent_tool_call_is_dropped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY)
            with self._env(home):
                self.assertTrue(g.note_line_by_agent(self.AGENT_BODY, home, t=self.T, at=("02", "20", "UTC")))
                self.assertEqual(g.clean(self.NOTE, t=self.T), "")

    def test_still_flags_withdraws_the_queued_legacy_row(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY)
            control = self._home(directory, name="control")
            p = Path(directory) / "host" / "default"
            p.mkdir(parents=True)
            (p / "s.json").write_text(json.dumps({"id": "s", "source": "kanban", "user_turns": [
                {"t": self.T, "text": self.NOTE}]}))
            m = g.hit_defect({"t": self.T, "session": "s", "host": "host", "class": g.ASK_CLASS,
                              "pattern": "stop-asking", "quote": self.NOTE})
            row = {"monitor": "ask-gate", "session_id": m["session_id"], "quote": m["quote"], "hit_t": self.T}
            with self._env(home):
                self.assertIn("written by an agent", g.still_flags(row, homes=[home], data=directory) or "")
            with self._env(control):
                self.assertIsNone(g.still_flags(row, homes=[control], data=directory))

    def test_shelled_out_kanban_comment_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY, tool="terminal")
            with self._env(home):
                self.assertEqual(g.clean(self.NOTE, t=self.T), "")

    def test_controls_keep_the_note(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            for name, home in (
                    ("unrelated call", self._home(directory, "Something unrelated about another card.", name="a")),
                    ("user-role row", self._home(directory, self.AGENT_BODY, role="user", name="b")),
                    ("other tool", self._home(directory, self.AGENT_BODY, tool="send_message", name="c")),
                    ("no state.db", Path(directory) / "missing")):
                with self.subTest(name), self._env(home):
                    self.assertEqual(g.clean(self.NOTE, t=self.T), self.NOTE)
                    self.assertTrue(g.classify(g.clean(self.NOTE, t=self.T)))

    def test_mixed_note_keeps_the_human_line(self):
        import tempfile
        mixed = self.NOTE + "\n- default (another session) at 02:25 UTC: why are you asking me?"
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY)
            with self._env(home):
                out = g.clean(mixed, t=self.T + 300)
                self.assertNotIn("Duplicate of", out)
                self.assertEqual(g.classify(out), "why-asking-me")

    def test_weekly_counts_tool_call_withdrawals(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY)
            with self._env(home):
                before = g.TOOLCALL_DROPS.get("lines", 0)
                g.clean(self.NOTE, t=self.T)
                self.assertGreater(g.TOOLCALL_DROPS["lines"], before)
                self.assertTrue(any("tool call" in line for line in g.toolcall_drop_lines()))

    # The live hub miss (t_01a1cf33013881c8): the digest clips a long note body at 74 chars, and the hub's 11 GB
    # state.db made the unbounded LIKE scan run 252 s, past the 15 s deadline. Both kept David's "stop asking".
    def test_clipped_digest_body_is_dropped(self):
        import tempfile
        clipped = self.NOTE.split(": Duplicate", 1)[0] + ": " + self.AGENT_BODY[:74] + "  " + g.CLIP + " " + \
            self.AGENT_BODY[-40:] + "…"
        self.assertLess(clipped.index(g.CLIP) - clipped.index("Duplicate"), g.NOTE_PREFIX)
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY)
            with self._env(home):
                self.assertEqual(g.clean(clipped, t=self.T), "")

    def _traced(self):
        from unittest import mock
        seen, real = [], g.sqlite3.connect

        def connect(*a, **k):
            con = real(*a, **k)
            con.set_trace_callback(seen.append)
            return con
        return seen, mock.patch.object(g.sqlite3, "connect", connect)

    def test_scan_is_rowid_bounded(self):
        import sqlite3, tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY, filler=5000)
            seen, patch = self._traced()
            with self._env(home), patch:
                self.assertEqual(g.clean(self.NOTE, t=self.T), "")
            scans = [q for q in seen if "tool_calls LIKE" in q]
            self.assertEqual(len(scans), 1, seen)
            self.assertRegex(scans[0], r"\bid BETWEEN \d+ AND \d+")
            lo, hi = map(int, re.search(r"BETWEEN (\d+) AND (\d+)", scans[0]).groups())
            self.assertLess(hi - lo, 10, "the window excludes the 5000 filler rows two hours earlier")
            con = sqlite3.connect(home / "state.db")
            plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + scans[0]))
            con.close()
            self.assertIn("INTEGER PRIMARY KEY (rowid>? AND rowid<?)", plan)

    def test_matching_call_outside_the_window_keeps_the_line(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY, ts=self.CALL_TS - 3 * 3600)
            with self._env(home):
                self.assertEqual(g.clean(self.NOTE, t=self.T), self.NOTE)
            later = self._home(directory, self.AGENT_BODY, ts=self.CALL_TS + 3 * 3600, name="later")
            with self._env(later):
                self.assertEqual(g.clean(self.NOTE, t=self.T), self.NOTE)

    def test_no_turn_time_keeps_the_line_without_a_query(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory, self.AGENT_BODY)
            seen, patch = self._traced()
            with self._env(home), patch:
                self.assertEqual(g.clean(self.NOTE), self.NOTE)
                self.assertFalse(g.note_line_by_agent(self.AGENT_BODY, home))
            self.assertEqual(seen, [])


def runners_brief():
    """The real opener the runners plugin writes into a 'Repair runner' chat (plugins/runners/repairs.py)."""
    import sys
    from pathlib import Path
    plugins = str(Path(__file__).resolve().parents[4] / 'plugins')
    sys.path.insert(0, plugins)
    try:
        from runners.repairs import RepairService
    finally:
        sys.path.remove(plugins)
    runner = {'id': 'r1', 'host_id': 'h1', 'scope_id': 's1', 'display_name': 'runner-1'}
    return RepairService._make_brief({'id': 'repair_x', 'owner': {}, 'incident_id': 'i1', 'entity_id': 'e1'},
                                     {'meta': {}, 'data': {'runners': [], 'scopes': []}}, runner, None, {})['text']


class PluginStartedOpener(unittest.TestCase):
    """plugin-4731e7cdc3: the runners plugin opens a desktop-source chat whose first user row is its fixed brief
    ('Diagnosis only. ... use five-whys ...'); ask-gate filed it as David asking for a root-cause fix."""

    def test_real_runners_brief_is_generated(self):
        # Detection guard: a reworded brief must fail here, not leak into ask-gate as a fix request.
        text = g.clean(runners_brief())
        self.assertTrue(g.generated(text))

    def test_collect_skips_plugin_written_openers_keeps_typed_turns(self):
        import json, tempfile
        from pathlib import Path
        brief = runners_brief()
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'host' / 'default'
            p.mkdir(parents=True)
            (p / 'a.json').write_text(json.dumps({'id': 'a', 'source': 'desktop', 'user_turns': [
                {'t': 100, 'text': brief}]}))  # old digest: no writer flag, text only
            (p / 'b.json').write_text(json.dumps({'id': 'b', 'source': 'desktop', 'user_turns': [
                {'t': 110, 'text': 'root cause this, use five-whys', 'generated': 'start:example'},
                {'t': 120, 'text': 'root cause this'}]}))
            hits, scanned = g.collect(directory, 0, 200)
        self.assertEqual([(h['session'], h['t']) for h in hits], [('b', 120)])
        self.assertEqual(sum(scanned.values()), 1)


if __name__ == '__main__':
    unittest.main()
