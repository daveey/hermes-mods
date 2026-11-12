"""Offline operand tests: fixture strings are never executed."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('tamper_candidate', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

CASES = [
 ('docs', "printf '%s' 'rm -f ~/.hermes/denial-grants.json'", False),
 ('comment', 'true # rm -f ~/.hermes/denial-ledger.jsonl', False),
 ('copy_source', 'cp ~/.hermes/denial-ledger.jsonl /example/backup.jsonl', False),
 ('quote_concat', 'rm -f ~/.hermes/denial-"grants".json', True),
 ('escape', r'rm -f ~/.hermes/denial-\grants.json', True),
 ('glob_active', 'rm ~/.hermes/denial-l*.jsonl', True),
 ('glob_single', "rm '~/.hermes/denial-l*.jsonl'", False),
 ('glob_escape', r'rm ~/.hermes/denial-l\*.jsonl', False),
 ('scalar_active', 'F=denial-; rm ~/.hermes/${F}grants.json', True),
 ('scalar_single', "F=denial-; rm '~/.hermes/${F}grants.json'", False),
 ('scalar_double', 'F=denial-; rm "~/.hermes/${F}grants.json"', True),
 ('scalar_order', 'F=denial-grants.json; rm "$F"; F=plain', True),
 ('prefix_not_expansion', 'F=plain; F=denial-grants.json rm "$F"', False),
 ('prefix_old_expansion', 'F=denial-grants.json; F=plain rm "$F"', True),
 ('redirect_only', '> /x/denial-ledger.jsonl', True),
 ('redirect_reader', 'cat 2>/x/denial-ledger.jsonl', True),
 ('redirect_read', 'cat < /x/denial-ledger.jsonl', False),
 ('redirect_data', "printf '%s' '> /x/denial-ledger.jsonl'", False),
 ('copy_dest', 'cp /x/plain /x/denial-grants.json', True),
 ('move_source', 'mv /x/denial-ledger.jsonl /x/backup.jsonl', True),
 ('tee', 'tee -a /x/denial-grants.json', True),
 ('dd_read', 'dd if=/x/denial-ledger.jsonl of=/x/backup.jsonl', False),
 ('dd_write', 'dd if=/x/plain of=/x/denial-ledger.jsonl', True),
 ('substitution', 'echo "$(rm /x/denial-grants.json)"', True),
 ('substitution_data', "echo '$(rm /x/denial-grants.json)'", False),
 ('unrelated_glob', 'rm /example/*', False),
 ('unknown_directory', 'rm $UNKNOWN/denial-g*', True),
 ('read_reference', 'truncate --reference /x/denial-ledger.jsonl /x/plain', False),
 ('sed_read', "sed 's/x/y/' /x/denial-ledger.jsonl", False),
 ('sed_write', "sed -i 's/x/y/' /x/denial-ledger.jsonl", True),
]

class TamperOperands(unittest.TestCase):
    def test_review_assignment_provenance(self):
        for text in ('F=denial-grants.json; "F=plain"; rm "$F"',
                     r'F=denial-grants.json; F\=plain; rm "$F"',
                     'F="denial-grants.json"; rm "$F"'):
            with self.subTest(text=text):
                self.assertIsNotNone(g._tamper('shell', text))

    def test_review_assignment_sequence(self):
        self.assertIsNotNone(g._tamper('shell', 'F=denial- G=${F}grants.json; rm "$G"'))

    def test_review_options_and_redirect(self):
        for text in ('mv --backup /synthetic/denial-ledger.jsonl /synthetic/plain',
                     'mv --backup=numbered /synthetic/denial-ledger.jsonl /synthetic/plain',
                     'cp -t/synthetic/denial-grants.json /synthetic/plain',
                     'printf x >& /synthetic/denial-ledger.jsonl'):
            with self.subTest(text=text):
                self.assertIsNotNone(g._tamper('shell', text))

def make_test(text, expected):
    def test(self):
        self.assertEqual(g._tamper('shell', text) is not None, expected, text)
    return test

for name, text, expected in CASES:
    setattr(TamperOperands, 'test_' + name, make_test(text, expected))

if __name__ == '__main__':
    unittest.main()
