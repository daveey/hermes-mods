"""Inert scalar flow fixtures; no fixture is passed to a shell."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('flow_candidate', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
CASES = [
 ('subshell_restore', 'F=denial-grants.json; (F=plain); rm "$F"', True),
 ('subshell_local', 'F=plain; (F=denial-grants.json); rm "$F"', False),
 ('subshell_effect', 'F=denial-grants.json; (rm "$F")', True),
 ('subshell_nested', 'F=denial-grants.json; ((F=plain)); rm "$F"', True),
 ('and_prior', 'F=denial-grants.json; false && F=plain; rm "$F"', True),
 ('and_possible', 'F=plain; maybe && F=denial-grants.json; rm "$F"', True),
 ('or_prior', 'F=denial-grants.json; true || F=plain; rm "$F"', True),
 ('pipeline_store', 'F=denial-grants.json; F=plain | cat; rm "$F"', True),
 ('pipeline_local', 'F=plain; F=denial-grants.json | cat; rm "$F"', False),
 ('pipeline_effect', 'F=denial-grants.json; printf x | rm "$F"', True),
 ('background_store', 'F=denial-grants.json; F=plain & rm "$F"', True),
 ('straight_replace', 'F=denial-grants.json; F=plain; rm "$F"', False),
 ('assignment_rhs', 'F=denial- G=${F}grants.json; rm "$G"', True),
 ('prefix_prestate', 'F=denial-grants.json; F=plain rm "$F"', True),
 ('quoted_delimiters', "F='plain;(denial-grants.json)'; printf '%s' \"$F\"", False),
 ('correlated_no_mixing', 'A=denial-; B=plain; maybe && (A=plain; B=grants.json); rm "$A$B"', False),
 ('conditional_replace', 'F=plain; maybe && F=denial-grants.json; F=plain; rm "$F"', False),
 ('conditional_effect', 'F=plain; maybe && F=denial-grants.json && rm "$F"', True),
]
class ScalarFlow(unittest.TestCase):
    pass

def case(text, expected):
    def test(self):
        self.assertEqual(g._tamper('shell', text) is not None, expected, text)
    return test
for name, text, expected in CASES:
    setattr(ScalarFlow, 'test_' + name, case(text, expected))
