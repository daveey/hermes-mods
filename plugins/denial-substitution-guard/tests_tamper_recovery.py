"""Inert parser recovery fixtures; no command is executed."""
import importlib.util
from pathlib import Path
import unittest
S = importlib.util.spec_from_file_location('recovery_guard', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
P = 'denial-grants.json'

class Recovery(unittest.TestCase):
    def test_recovery(self):
        cases = [
            ('rm '+P+'; a |& b', True, True),
            ('rm '+P+' &&', True, True),
            ('true &&\n\n # comment\n rm '+P, True, False),
            ('false ||\n rm '+P, True, False),
            ('echo |\n rm '+P, True, False),
            ("bash -c 'a |& b'; rm "+P, True, True),
            ("bash -c 'rm "+P+"; a |& b'", True, True),
            ('echo $(a |& b); rm '+P, True, True),
            ('F='+P+'; (F=plain; a |& b); rm "$F"', True, True),
            ('F=plain; (F='+P+'; a |& b); rm "$F"', False, True),
            ('printf "%s" "rm '+P+'; a |& b"', False, False),
            ('echo plain # rm '+P+'\ncat '+P, False, False),
            ('rm '+P+'; echo "unterminated', True, True),
            # Changed from False (run8): this lexer's unclosed quote can be valid
            # bash it does not model ($'..', heredocs), so the unlexed remainder
            # gets the baseline rule; see tests_tamper_limits.py.
            ('printf "rm '+P+'; unterminated', True, True),
            ('rm '+P+'; echo \\', True, True),
        ]
        for text, expected, issue in cases:
            with self.subTest(text=text):
                found, why = g._shell_analysis(text)
                self.assertEqual((found, bool(why)), (expected, issue))

if __name__ == '__main__': unittest.main()
