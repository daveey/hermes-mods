"""Public anonymized cases: provenance precedes weekly correction regexes."""
import json
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
import coach_askgate as g


class WeeklyNoise(unittest.TestCase):
    def collect(self, source, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'host' / 'profile'
            p.mkdir(parents=True)
            (p / 's.json').write_text(json.dumps({'id': 's', 'source': source,
                'user_turns': [{'t': 100, 'text': text}]}))
            return g.collect(tmp, 0, 200)[0]

    def test_oneshot_review_briefs_not_corrections(self):
        for text in (
            'You are an independent adversarial code reviewer. Run the tests yourself.',
            'You are the independent primary reviewer. Do not write the review file yourself.',
            'Review the COMPLETE exact PR123 base abc to headdef. Do not claim tests ran yourself.',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.collect('oneshot', text), [])

    def test_oneshot_real_correction_and_interactive_review_kept(self):
        self.assertTrue(self.collect('oneshot', 'do it yourself'))
        self.assertTrue(self.collect('desktop', 'Review the COMPLETE exact PR123 yourself'))

    def test_queued_review_requires_matching_source_quote_and_time(self):
        text = 'You are an independent code reviewer. Run tests yourself.'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'host' / 'profile'
            p.mkdir(parents=True)
            (p / 's.json').write_text(json.dumps({'id': 's', 'source': 'oneshot',
                'user_turns': [{'t': 100, 'text': text}]}))
            row = {'session_id': 's', 'hit_t': 100, 'quote': text[:40] + '…'}
            self.assertIn('review brief', g.still_flags(row, homes=[], data=tmp) or '')
            for changed in ({'hit_t': 1000}, {'quote': 'do it yourself'}, {'session_id': 'absent'}):
                self.assertIsNone(g.still_flags(dict(row, **changed), homes=[], data=tmp))

    def test_runtime_and_restart_notices_are_not_human(self):
        for text in (
            '[runtime] Your draft above was NOT shown to the user. Do it yourself.',
            '⏳ [default] @default Kanban t_example: broken again',
            '⚠ subagent Subagent: root cause was stopped by a restart before finishing',
            '[System note: Your previous turn was interrupted mid-run. The paused request was:] broken again',
            'Canonical user decision. Act only on the frozen revision below. root cause',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.collect('desktop', text), [])
                self.assertIn('generated', g.still_flags({'quote': text}, homes=[], data='/absent') or '')

    def test_canonical_suffix_cannot_supply_matching_words(self):
        suffix = '\n[Canonical requests awaiting a user decision: root cause; do it yourself]'
        self.assertEqual(self.collect('desktop', 'What is in this image?' + suffix), [])
        self.assertTrue(self.collect('desktop', 'why are you asking me' + suffix))

    def test_named_review_launcher_not_desktop(self):
        text = '# Independent bounded review\nYou are independent mandated reviewer. Verify it yourself.'
        self.assertEqual(self.collect('rollout-latency-method-review', text), [])
        self.assertTrue(self.collect('desktop', text))

    def test_erased_restart_wrapper_requires_exact_raw_evidence(self):
        import sqlite3
        from unittest.mock import patch
        from session_digest import clean_user
        raw = '[System note: Your previous turn was interrupted mid-run. The paused request was:] broken again'
        text = clean_user(raw)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            con = sqlite3.connect(home / 'state.db')
            con.execute('create table messages(session_id text, role text, content text, timestamp real)')
            con.execute('insert into messages values (?,?,?,?)', ('s', 'user', raw, 100))
            con.commit()
            row = {'session_id': 's', 'hit_t': 100, 'quote': text}
            self.assertTrue(g.row_raw_generated(row, [home]))
            self.assertIn('generated', g.still_flags(row, homes=[home], data=home) or '')
            with patch.dict('os.environ', {'HERMES_HOME': str(home)}):
                self.assertEqual(self.collect('desktop', text), [])
            for changed in ({'hit_t': 200}, {'session_id': 'other'}, {'quote': 'why are you asking me'}):
                self.assertFalse(g.row_raw_generated(dict(row, **changed), [home]))
            self.assertFalse(g.row_raw_generated(row, []))
            # A real matching turn in the window makes attribution ambiguous.
            con.execute('insert into messages values (?,?,?,?)', ('s', 'user', text, 100))
            con.commit()
            self.assertFalse(g.row_raw_generated(row, [home]))
            con.close()

    def test_raw_provenance_overflow_and_cross_home_ambiguity(self):
        import sqlite3
        raw = '[System note: Your previous turn was interrupted mid-run. The paused request was:] broken again'
        row = {'session_id': 's', 'hit_t': 100, 'quote': 'broken again'}
        with tempfile.TemporaryDirectory() as tmp:
            homes = [Path(tmp) / name for name in ('a', 'b')]
            for home, contents in zip(homes, ([raw] * 32 + ['broken again'], ['broken again'])):
                home.mkdir()
                with closing(sqlite3.connect(home / 'state.db')) as con, con:
                    con.execute('create table messages(session_id text, role text, content text, timestamp real)')
                    con.executemany('insert into messages values (?,?,?,?)',
                                    [('s', 'user', text, 100) for text in contents])
            self.assertFalse(g.row_raw_generated(row, [homes[0]]))
            with closing(sqlite3.connect(homes[0] / 'state.db')) as con, con:
                con.execute('delete from messages')
                con.execute('insert into messages values (?,?,?,?)', ('s', 'user', raw, 100))
            for order in (homes, homes[::-1]):
                self.assertFalse(g.row_raw_generated(row, order))
                self.assertIsNone(g.still_flags(row, homes=order, data=tmp))

    def test_legacy_minute_timestamp_covers_whole_minute(self):
        import sqlite3
        raw = '[System note: Your previous turn was interrupted mid-run. The paused request was:] broken again'
        row = {'session_id': 's', 'details': 'on 2026-11-10 09:25 PST', 'quote': 'broken again'}
        t = g.row_hit_t(row)
        assert t is not None
        with tempfile.TemporaryDirectory() as tmp:
            with closing(sqlite3.connect(Path(tmp) / 'state.db')) as con, con:
                con.execute('create table messages(session_id text, role text, content text, timestamp real)')
                con.execute('insert into messages values (?,?,?,?)', ('s', 'user', raw, t + 45))
            self.assertTrue(g.row_raw_generated(row, [tmp]))
            self.assertFalse(g.row_raw_generated(dict(row, hit_t=t), [tmp]))
            with closing(sqlite3.connect(Path(tmp) / 'state.db')) as con, con:
                con.execute('insert into messages values (?,?,?,?)', ('s', 'user', 'broken again', t + 55))
            self.assertFalse(g.row_raw_generated(row, [tmp]))

    def test_warning_notice_not_fix(self):
        text = ('⚠ [default] @default Kanban t_example: No pool worker can run this card '
                '(needs: skill:five-whys). It stays queued until its creator repins it.')
        self.assertIsNone(g.classify_fix(g.clean(text)))

        self.assertIn('generated', g.still_flags({'quote': text}) or '')

    def test_injected_context_cannot_supply_correction(self):
        text = 'this request makes no sense\n[System: Skills available. If you can do it yourself, do it.]'
        self.assertEqual(g.clean(text), 'this request makes no sense')
        # The appended context must not supply 'do-it-yourself'; the human words are judged on their own.
        self.assertNotEqual(g.classify(g.clean(text)), 'do-it-yourself')
        self.assertIsNone(g.classify(g.clean('the build broke again\n[System: If you can do it yourself, do it.]')))
        self.assertEqual(g.classify(g.clean('stop asking\n[System: context]')), 'stop-asking')

    def test_unclear_ask_is_an_ask_me_correction_not_a_fix_request(self):
        # A complaint that a Needs-you request is unreadable is about the ask, whatever product the session is on.
        text = ('this request makes no sense, what do youneed me to do? why is it so cryptuc? '
                '@image:/tmp/x.png [screenshot]\n[System: Skills available. If you can do it yourself, do it.]')
        self.assertEqual(g.classify(g.clean(text)), 'unclear-ask')
        self.assertIsNone(g.classify_fix(g.clean(text)))
        hits = self.collect('desktop', text)
        self.assertEqual([(h['class'], h['pattern']) for h in hits], [('ask_me', 'unclear-ask')])
        # The popup-opener rule (screenshot + "stop asking me" = an OS popup) is unchanged.
        self.assertEqual(self.collect('desktop', 'stop asking me @image:/tmp/x.png [screenshot]'), [])
        for text in ('what do you need from me here?', 'why is this ask so cryptic'):
            with self.subTest(text=text):
                self.assertEqual(g.classify(text), 'unclear-ask')
        for text in ('this error makes no sense, figure out why', 'the cryptic log line is from the parser',
                     'tell me what you need me to do after the deploy and I will do it',
                     'what do I need to do to run it'):
            with self.subTest(text=text):
                self.assertIsNone(g.classify(text))

    def test_subagent_note_dropped_but_human_note_kept(self):
        header = 'New notes on your kanban task from the operator (delivered mid-run).\n'
        agent = '- Subagent: Close CI coverage gap at 03:44 UTC: Do not edit the workflow yourself.\n'
        human = '- operator (another session) at 03:45 UTC: why are you asking me?'
        self.assertEqual(g.clean(header + agent), '')
        self.assertIn('generated', g.still_flags({'quote': header + agent}) or '')
        self.assertEqual(g.classify(g.clean(header + agent + human)), 'why-asking-me')
        self.assertIsNone(g.classify_fix(g.clean(header + agent + 'root cause this\n')))


if __name__ == '__main__':
    unittest.main()
