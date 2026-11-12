"""Regressions for independent REVIEW-PYTHON.md findings F1-F5. Inert analyzer input only."""
import importlib.util
import os
from pathlib import Path
import time
import unittest

GUARD = Path(os.environ.get('DENIAL_GUARD_UNDER_TEST') or Path(__file__).with_name('guard.py'))
S = importlib.util.spec_from_file_location('python_tamper_review_guard', GUARD)
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
G = '/h/denial-grants.json'


class PythonReviewFindings(unittest.TestCase):
    def test_f1_nested_containers_bounded_time(self):
        for opener, closer in (('[', ']'), ('(', ',)'), ('{', '}'), ("{'k':", '}')):
            code = 'x=' + opener * 22 + "'a'" + closer * 22
            start = time.monotonic()
            g._tamper('python', code)
            self.assertLess(time.monotonic() - start, 2.0, opener)

    def test_f2_nested_recursion_bound_keeps_outer_sites(self):
        code = "exec(%r)\nopen('%s','w')" % ('x=1' + '+1' * 3000, G)
        self.assertIsNotNone(g._tamper('python', code))
        # Bounded nested analysis must not mask later sites at other term counts.
        for terms in (50, 500, 20000):
            self.assertIsNotNone(g._tamper('python', "exec(%r)\nopen('%s','w')" % ('x=1' + '+1' * terms, G)), terms)

    def test_f3_function_only_global(self):
        self.assertIsNotNone(g._tamper('python', f"def f():\n global p\n p='{G}'\nf()\nopen(p,'w')"))
        self.assertIsNotNone(g._tamper('python', f"def f():\n global p\n p='{G}'\ndef h():\n open(p,'w')\nf()\nh()"))
        self.assertIsNone(g._tamper('python', f"def f():\n global p\n p='/h/plain'\nf()\nopen(p,'w')\nprint('{G}')"))

    def test_f4_argv_unpack_and_concatenation(self):
        self.assertIsNotNone(g._tamper('python', f"import subprocess\nfs=['{G}']\nsubprocess.run(['rm','-f',*fs])"))
        self.assertIsNotNone(g._tamper('python', f"import subprocess\nsubprocess.run(['rm','-f'] + ['{G}'])"))
        self.assertIsNone(g._tamper('python', f"import subprocess\nsubprocess.run(['cat'] + ['{G}'])"))

    def test_f5_str_folds(self):
        self.assertIsNotNone(g._tamper('python', f"p=str('{G}')\nopen(p,'w')"))
        self.assertIsNone(g._tamper('python', f"p=str('{G}')\nopen(p)"))

    def test_w1_wide_sequences_bounded_time(self):
        n = 30000
        cases = {
            'list_ints': 'x=[' + '1,' * n + ']',
            'list_strs': 'x=[' + "'a'," * n + ']',
            'argv_call': "import subprocess\nsubprocess.run([" + "'a'," * n + "])",
            'fstring': "a='x'\nf'" + '{a}' * (n // 2) + "'",
            'path_args': "from pathlib import Path\nPath(" + "'a'," * n + ")",
            'join_args': "import os\nos.path.join(" + "'a'," * n + ")",
            'two_valued': "import sys\nc=sys.argv\nx=[" + "('a' if c else 'b')," * 5000 + ']',
        }
        for name, code in cases.items():
            start = time.monotonic()
            g._tamper('python', code)
            self.assertLess(time.monotonic() - start, 3.0, name)
        # Width still keeps the protected word and command shape.
        wide = "import subprocess\nsubprocess.run(['rm','-f'," + "'/x/plain'," * 2000 + f"'{G}'])"
        self.assertIsNotNone(g._tamper('python', wide))
        wide_read = "import subprocess\nsubprocess.run(['cat'," + "'/x/plain'," * 2000 + f"'{G}'])"
        self.assertIsNone(g._tamper('python', wide_read))

    def test_w1b_reused_wide_values_bounded_time(self):
        wide = '[' + "'a'," * 8000 + ']'
        long_s = '"' + 'a' * 20000 + '"'
        cases = {
            'plus_self': 'x=' + wide + '\n' + 'x=x+x\n' * 2000,
            'aug_self': 'x=' + wide + '\n' + 'x+=x\n' * 2000,
            'wrap_copies': 'x=' + wide + '\n' + 'y=[x,x,x]\n' * 2000,
            'argv_reuse': 'import subprocess\nx=[' + "'a'," * 5000 + ']\n' + 'subprocess.run(x)\n' * 2000,
            'plus_copies': 'x=' + long_s + '\n' + '\n'.join(f'y{i}=x+x' for i in range(3000)),
            'mod_copies': 'x="' + 'a%s' * 8000 + '"\n' + '\n'.join(f'y{i}=x%1' for i in range(3000)),
            'format_copies': 'x="' + 'a{}' * 8000 + '"\n' + '\n'.join(f'y{i}=x.format(1)' for i in range(3000)),
        }
        for name, code in cases.items():
            start = time.monotonic()
            self.assertIsNone(g._tamper('python', code), name)
            self.assertLess(time.monotonic() - start, 3.0, name)
        # Work-bound exhaustion applies the baseline rule, never "no mutation".
        self.assertIsNotNone(g._tamper('python', cases['plus_self'] + f"open('{G}','w')"))

    def test_l1_nested_subscript(self):
        self.assertIsNotNone(g._tamper('python', f"x=[['{G}']]\nopen(x[0][0],'w')"))
        self.assertIsNone(g._tamper('python', f"x=[['{G}']]\nprint(open(x[0][0]).read())"))


if __name__ == '__main__':
    unittest.main()
