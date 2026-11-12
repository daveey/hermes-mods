"""Inert analyzer transaction tests; injected local errors never execute shell."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
S = importlib.util.spec_from_file_location('transaction_guard', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
P = 'denial-grants.json'

class Transaction(unittest.TestCase):
    def test_recoverable_assignment_failure(self):
        # Real assignment loop updates F, then the RHS lexer fails locally on G.
        original = g._tamper_words
        budgets = []
        def failing(text, scalars, **kwargs):
            if text == 'value TRIP':
                budgets.append(kwargs['budget'][0])
                raise g._TamperUnsupported('injected recoverable RHS failure')
            yield from original(text, scalars, **kwargs)
        cases = [
            ('F='+P+'; F=plain G=TRIP; rm "$F"', True),
            ('F=plain G=TRIP NEW='+P+'; rm "$NEW"', True),
            ('F=plain G=TRIP; rm '+P, True),
            ('F=plain G=TRIP; cat "$F"', False),
            ('F=plain G=TRIP; F=plain; rm "$F"', False),
            ('F=plain; (F='+P+' G=TRIP); rm "$F"', False),
        ]
        for source, expected in cases:
            with self.subTest(source=source), patch.object(g, '_tamper_words', failing):
                found, issue = g._shell_analysis(source)
                self.assertEqual(found, expected)
                self.assertIn('injected recoverable RHS failure', issue or '')
        self.assertEqual(len(budgets), len(cases))

    def test_opaque_transfers(self):
        cases = [
            ('F='+P+'; unset OTHER; rm "$F"', True, False),
            ('F=plain; unset OTHER; rm "$F"', False, False),
            ('F='+P+'; unset F; rm "$F"', True, False),
            ('F='+P+'; unset -f F; rm "$F"', True, False),
            ('F=plain; read F; rm "$F"', True, False),
            ('read NEW; rm "$NEW"', True, False),
            ('read; rm "$REPLY"', True, False),
            ('F='+P+'; read OTHER; rm "$F"', True, False),
            ('F=plain; read OTHER; rm "$F"', False, False),
            ('F='+P+'; eval "$CODE"; rm "$F"', True, True),
            ('F=plain; source plain; rm "$F"', True, True),
            ('F='+P+'; . plain; cat "$F"', False, True),
            ('F='+P+'; command unset OTHER; rm "$F"', True, False),
            ('F=plain; (read F); rm "$F"', False, False),
            ('F=plain; read F; F=plain; rm "$F"', False, False),
        ]
        for text, expected, opaque in cases:
            with self.subTest(text=text):
                found, issue = g._shell_analysis(text)
                self.assertEqual(found, expected)
                self.assertEqual(bool(issue), opaque)

    def test_exhaustion_is_not_recovered_or_replenished(self):
        original = g._command_tamper
        seen = []
        budget = [1000]
        def exhausted(text, scalars, depth, shared, issues=None):
            seen.append(text.strip())
            shared[0] = -1
            raise g._TamperUnsupported('injected exhausted budget')
        with patch.object(g, '_command_tamper', exhausted):
            try:
                g._shell_tamper('F=plain; rm '+P, budget=budget)
            except g._TamperUnsupported:
                pass
        self.assertEqual(seen, ['F=plain'])
        self.assertLess(budget[0], 0)

    def test_real_scalar_limit_recovers_but_not_shared_exhaustion(self):
        source = 'A=' + 'x' * 40000 + '; F=plain G=$A$A$A$A$A$A$A NEW=' + P
        found, issue = g._shell_analysis(source + '; rm "$NEW"')
        self.assertTrue(found)
        self.assertIn('shell scalar length budget', issue)
        found, issue = g._shell_analysis(source + '; F=plain; rm "$F"')
        self.assertFalse(found)
        self.assertIn('shell scalar length budget', issue)

    def test_recovery_union_and_budget_identity(self):
        incoming, partial = {'OLD': P}, {'OLD': 'plain', 'PARTIAL': 'plain'}
        shared = [1000]
        text = 'NEW=$ABSENT > plain LAST=value printf "NOT=assignment"'
        result = g._failed_command_state(text, incoming, partial, shared)
        self.assertEqual(result, dict.fromkeys(('OLD', 'PARTIAL', 'NEW', 'LAST'), g._UNCERTAIN))
        self.assertEqual(incoming, {'OLD': P})
        self.assertEqual(partial, {'OLD': 'plain', 'PARTIAL': 'plain'})
        self.assertLess(shared[0], 1000 - len(text))
        self.assertGreaterEqual(shared[0], 0)
        original_words, original_recover = g._tamper_words, g._failed_command_state
        marks = []
        def failing(text, scalars, **kwargs):
            if text == 'value TRIP':
                marks.append((id(kwargs['budget']), kwargs['budget'][0]))
                raise g._TamperUnsupported('injected local failure')
            yield from original_words(text, scalars, **kwargs)
        def recover(text, before, after, budget):
            marks.append((id(budget), budget[0]))
            result = original_recover(text, before, after, budget)
            marks.append((id(budget), budget[0]))
            return result
        with patch.object(g, '_tamper_words', failing), patch.object(g, '_failed_command_state', recover):
            self.assertTrue(g._shell_tamper('F=plain G=TRIP; rm "$F"', budget=shared))
        self.assertEqual([m[0] for m in marks], [id(shared)] * 3)
        self.assertEqual(marks[0][1], marks[1][1])
        self.assertLess(marks[2][1], marks[1][1])
        self.assertLess(shared[0], marks[2][1])
        # With a precise-only construct the legacy floor reruns on the same
        # caller list from the same start (never a fresh budget).
        caller = [100000]
        self.assertFalse(g._shell_tamper('f() { :; }; F=plain; rm "$F"', budget=caller))
        self.assertLess(caller[0], 100000)

    def test_opaque_dynamic_names_are_explicit_gap(self):
        found, issue = g._shell_analysis('eval "NEW=denial-grants.json"; rm "$NEW"')
        self.assertFalse(found)  # limitation, NOT an enforcement success
        self.assertIn('dynamic/new names unsupported', issue)

    def test_programming_errors_not_swallowed(self):
        with patch.object(g, '_command_tamper', side_effect=TypeError('bug')):
            with self.assertRaises(TypeError): g._shell_analysis('true')

if __name__ == '__main__': unittest.main()
