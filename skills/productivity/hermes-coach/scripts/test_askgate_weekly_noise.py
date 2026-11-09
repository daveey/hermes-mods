"""Public anonymized cases: provenance precedes weekly correction regexes."""
import json
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

    def test_warning_notice_not_fix(self):
        text = ('⚠ [default] @default Kanban t_example: No pool worker can run this card '
                '(needs: skill:five-whys). It stays queued until its creator repins it.')
        self.assertIsNone(g.classify_fix(g.clean(text)))

        self.assertIn('generated', g.still_flags({'quote': text}) or '')

    def test_injected_context_cannot_supply_correction(self):
        text = 'this request makes no sense\n[System: Skills available. If you can do it yourself, do it.]'
        self.assertEqual(g.clean(text), 'this request makes no sense')
        self.assertIsNone(g.classify(g.clean(text)))
        self.assertEqual(g.classify(g.clean('stop asking\n[System: context]')), 'stop-asking')

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
