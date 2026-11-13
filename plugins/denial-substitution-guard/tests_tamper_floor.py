"""Legacy-floor regressions (REVIEW-COMPOUND-PARAMS B1-B10).

Every HOLD fixture is a real write that the accepted 9caf analysis held and the
precise compound/params pass alone allowed. Fixtures are inert text: never run
them in a shell.
"""
import time
import unittest

import guard as g

P = 'denial-grants.json'
TAIL = '; X=denial-; Y=grants.json; rm "$X$Y"'


def call_tree(depth, fan):
    defs = ['function h0 { Z=$Z$1; }']
    for k in range(1, depth + 1):
        defs.append(f'function h{k} {{ ' + '; '.join(f'h{k-1} a{j}' for j in range(fan)) + '; }')
    return '; '.join(defs) + f'; h{depth} x'


HOLD = [
    ('pos10_fn', f'f() {{ rm "${{10}}"; }}; f 1 2 3 4 5 6 7 8 9 {P}'),
    ('pos10_set', f'f() {{ :; }}; set -- 1 2 3 4 5 6 7 8 9 {P}; rm "${{10}}"'),
    ('pos_ifs_star', f'f() {{ :; }}; set -- denial- grants.json; IFS=; rm "$*"; echo {P}'),
    ('call_prefix_env', f'f() {{ rm "$X"; }}; X={P} f'),
    ('call_indirect_globals', f'f() {{ rm "$A$B"; }}; A=denial-; B=grants.json; n=f; $n; echo {P}'),
    ('fn_underscore', f'f() {{ rm "$_"; }}; echo {P}; f'),
    ('fd_trap', f"f() {{ :; }}; trap 'rm {P}' EXIT"),
    ('fd_printf_v', f'f() {{ :; }}; printf -v F %s {P}; rm "$F"'),
    ('fd_default', f'f() {{ :; }}; echo {P}; rm denial-${{X:-grants}}.json'),
    ('fd_for_second', f'f() {{ :; }}; for p in x denial-; do rm "${{p}}grants.json"; done; echo {P}'),
    ('case_trap', f"trap 'rm {P}' EXIT; case x in *) :;; esac"),
    ('case_eval_newname', f'eval "V={P}"; rm -f "$V"; case x in *) :;; esac'),
    ('paren_in_param', f'echo {P}; rm "$_"; echo ${{x/)/}}'),
    ('if_depth7', 'if a; then ' * 7 + ':; ' + 'fi; ' * 7 + 'X=denial-; Y=grants.json; rm "$X$Y"'),
    ('call_fanout_budget', call_tree(3, 12) + TAIL),
    ('brace_exp_scalar_charge', ' '.join(f'V{k}=1' for k in range(1100)) + '; echo '
     + ' '.join('${V1%x}' for _ in range(1100)) + TAIL),
]

ALLOW = [
    ('fn_benign', 'clean() { rm -rf "$1"; }; clean build'),
    ('set_benign', 'set -euo pipefail; rm -f out.log'),
    ('loop_benign', 'for f in *.o; do rm "$f"; done'),
]


class Floor(unittest.TestCase):
    def test_holds(self):
        for name, text in HOLD:
            with self.subTest(name):
                self.assertIsNotNone(g._tamper('shell', text), name)

    def test_allows(self):
        for name, text in ALLOW:
            with self.subTest(name):
                self.assertIsNone(g._tamper('shell', text), name)

    def test_shift_with_uncertain_positionals(self):
        # REVIEW-FLOOR: TypeError in _shift_positionals crashed the analysis
        # (plugin hook then allowed). Writes after it must hold; no crash.
        holds = [
            'f() { set -- $2 "$@"; shift; }; f a; rm denial-grants.json',
            'function f { set -- $* denial-; shift; }; f $1 $2; rm denial-grants.json',
            'function f { shift; set -- $* $1; }; f $1 $2; rm denial-grants.json',
            "bash -c 'f() { set -- $2 \"$@\"; shift; }; f a; rm denial-grants.json'",
            'function f { set -- $2 "$@"; shift; }; f a; truncate -s0 denial-grants.json',
            'f() { set -- $2 "$@"; shift; }; f a; echo x > denial-grants.json',
        ]
        for text in holds:
            with self.subTest(text):
                self.assertIsNotNone(g._tamper('shell', text))
        self.assertIsNone(g._tamper('shell', 'f() { set -- $2 "$@"; shift; }; f a'))

    def test_empty_vars_inline_calls_bounded(self):
        text = ('; '.join(f'E{k}=' for k in range(1000))
                + '; function g { :; :; :; :; :; :; :; :; :; :; }; function f { g; g; g; g; g; g; g; g; g; g; }; '
                + '; '.join('f' for _ in range(100)))
        start = time.thread_time()
        g._tamper('shell', text)
        self.assertLess(time.thread_time() - start, 3.0)

    def test_work_exhaustion_fails_closed_on_write_keyword(self):
        # REVIEW-FLOOR B1: the per-command +len(state) charge exhausts the
        # shared budget earlier than 9caf; the literal-only baseline then
        # allowed a name split across variables. Exhaustion must hold.
        holds = [
            '; '.join(f'E{k}=v' for k in range(1200)) + TAIL,
            '; '.join(f'E{k}=' for k in range(1500)) + TAIL,
            '; '.join(f'E{k}=' for k in range(4000)) + TAIL,
            '; '.join(f'E{k}=' for k in range(4000)) + '; X=denial-; Y=grants.json; echo x > "$X$Y"',
            "bash -c '" + '; '.join(f'E{k}=' for k in range(4000)) + TAIL.replace('"', '') + "'",
            "eval '" + '; '.join(f'E{k}=' for k in range(4000)) + TAIL.replace('"', '') + "'",
        ]
        py_holds = [
            'import subprocess\nsubprocess.run(' + repr('; '.join(f'E{k}=' for k in range(4000)) + TAIL) + ', shell=True)',
            'import os\nos.system(' + repr('; '.join(f'E{k}=' for k in range(4000)) + TAIL) + ')',
        ]
        for text in py_holds:
            with self.subTest(text[-60:]):
                self.assertIsNotNone(g._tamper('python', text))
        for text in holds:
            with self.subTest(text[-60:]):
                start = time.thread_time()
                self.assertIsNotNone(g._tamper('shell', text))
                self.assertLess(time.thread_time() - start, 3.0)
        # Exhaustion holds unconditionally: verbs outside WRITE_SHELL and
        # split command names (REVIEW-FLOOR-B1-CORRECTION F1), and a Python
        # string after an exhausting one (F2), must not escape.
        many = '; '.join(f'E{k}=' for k in range(1500))
        for verb in ('unlink', 'rmdir', 'touch', 'sudoedit', 'A=r; B=m; $A$B'):
            with self.subTest(verb):
                self.assertIsNotNone(g._tamper('shell', many + f'; X=denial-; Y=grants.json; {verb} "$X$Y"'))
        f2 = 'import os\nos.system(' + repr(many + ';') + ')\nos.system(\'X=denial-; Y=grants.json; rm "$X$Y"\')'
        self.assertIsNotNone(g._tamper('python', f2))
        self.assertIsNotNone(g._tamper('python', 'import os\nos.system(' + repr(many + '; X=denial-; Y=grants.json; unlink "$X$Y"') + ')'))
        # Precision cost (documented): an exhausting text holds even without a write.
        self.assertIsNotNone(g._tamper('shell', '; '.join(f'E{k}=' for k in range(4000)) + '; echo "$E1"'))
        # Normal-sized scripts are unaffected.
        self.assertIsNone(g._tamper('shell', '; '.join(f'E{k}=' for k in range(200)) + '; echo "$E1"; rm -f out.log'))
        found, issue = g._shell_analysis('; '.join(f'E{k}=' for k in range(4000)) + TAIL)
        self.assertTrue(found)
        self.assertIn('shell work exhausted', issue)

    def test_floor_runs_first_and_charges_caller(self):
        budget = [1048576]
        self.assertTrue(g._shell_tamper(f'f() {{ :; }}; trap "rm {P}" EXIT', budget=budget))
        self.assertLess(budget[0], 1048576)


if __name__ == '__main__':
    unittest.main()
