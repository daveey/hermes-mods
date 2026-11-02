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


if __name__ == '__main__':
    unittest.main()
