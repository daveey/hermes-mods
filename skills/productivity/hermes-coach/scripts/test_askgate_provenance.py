"""Public, anonymized regressions for generated-message KPI pollution."""
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
