"""Inert fixtures for shell analysis limits (unsupported syntax, depth, size,
token and work budgets). Nothing here is passed to a shell.

An analysis limit is never permission: the region the bounded analysis did not
inspect gets the reviewed baseline rule (protected name, or a tracked variable
whose concrete value is protected, together with a shell write keyword). The
inspected prefix keeps its precise verdict, so read/data uses stay allowed.
"""
import importlib.util
import shlex
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('limits_candidate', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
P = 'denial-grants.json'


def hold(text, kind='shell'):
    return g._tamper(kind, text) is not None


def nest_bash(inner, n):
    for _ in range(n):
        inner = 'bash -c ' + shlex.quote(inner)
    return inner


HOLD = [
    # frozen flow-review cases (flow_review_probe.py)
    ('nested7_literal_inside', f'(((((((rm {P})))))))'),
    ('nested20_literal_inside', '(' * 20 + f'rm {P}' + ')' * 20),
    ('lit_case_paren', f'case x in a) rm {P};; esac'),
    ('lit_leading_and', f'&& rm {P}'),
    ('token_budget_16385', 'a;' * 8200 + f'rm {P}'),
    ('oversize_text', ' ' * 262145 + f'rm {P}'),
    ('expansion_budget_1MiB', ('x=' + 'a' * 200000 + '; ') * 6 + f'rm {P}'),
    ('func_def', f'f() {{ rm {P}; }}; f'),
    ('redir_after_group', f'(echo x) > {P}'),
    # tracked concrete values reach the uninspected region
    ('nested7_var', f'F={P}; (((((((rm "$F")))))))'),
    ('case_var', f'F={P}; case x in a) rm "$F";; esac'),
    ('func_var', f'F={P}; f() {{ rm "$F"; }}; f'),
    ('group_redirect_var', f'F={P}; (echo x) > "$F"'),
    ('group_redirect_braced_var', f'F={P}; (echo x) >"${{F}}"'),
    ('group_append', f'(echo x) >> {P}'),
    # lexer errors: a trailing escape leaves executable text (bash drops it);
    # this lexer's 'unclosed quote' can be valid bash it does not model
    # ($'...' escapes, heredoc bodies), so the remainder is uninspected.
    ('trailing_escape_tail', f'rm {P} \\'),
    ('ansi_c_quote_then_write', "echo $'it\\'s'; rm " + P),
    ('heredoc_apostrophe_then_write', f"cat <<EOF\nit's\nEOF\nrm {P}"),
    ('unclosed_quote_same_command', f"rm {P} '"),
    # nesting/size bounds inside interpreters
    ('bash_c_depth7', nest_bash(f'rm {P}', 8)),
    ('substitution_case', f'echo $(case x in a) rm {P};; esac)'),
    ('oversize_python_c', 'echo $a; ' * 30000 + 'python3 -c ' + shlex.quote(f"import os; os.remove('{P}')")),
]

ALLOW = [
    # region-scoped: an inspected read/redirect before an unsupported suffix
    ('read_then_case', f'cat {P}; case x in a) echo hi;; esac'),
    ('copy_out_then_func', f'cat {P} > /tmp/copy; f() {{ :; }}; f'),
    ('var_read_then_case', f'F={P}; cat "$F"; case x in a) echo hi;; esac'),
    ('case_subject_var', f'F={P}; case $F in a) echo hi;; esac'),
    # unsupported syntax without protected operands
    ('group_redirect_other', '(echo x) > out.txt'),
    ('group_redirect_data', f'(echo {P}) > out.txt'),
    ('case_plain', 'case x in a) rm other;; esac'),
    ('func_plain', 'f() { rm other; }; f'),
    ('nested7_plain', '(((((((rm other)))))))'),
    ('nested7_plain_var', 'F=plain; (((((((rm "$F")))))))'),
    ('unclosed_quote_plain', "echo 'abc"),
    ('bash_c_depth7_plain', nest_bash('rm other', 8)),
]


class ShellLimits(unittest.TestCase):
    def test_uninspected_region_holds(self):
        for name, text in HOLD:
            with self.subTest(name):
                self.assertTrue(hold(text), name)

    def test_region_scoped_controls_allow(self):
        for name, text in ALLOW:
            with self.subTest(name):
                self.assertFalse(hold(text), name)

    def test_issue_still_reported(self):
        # The baseline verdict is not a claim of support: the limit stays visible.
        for text in ('case x in a) echo hi;; esac', '(echo x) > out.txt', ' ' * 262145 + 'echo hi'):
            with self.subTest(text[:30]):
                found, issue = g._shell_analysis(text)
                self.assertFalse(found)
                self.assertTrue(issue)

    def test_python_kind_nested_shell_limits(self):
        # python -> shell text past the shell nesting bound still holds.
        code = 'import os\nos.system(%r)\n' % nest_bash(f'rm {P}', 8)
        self.assertTrue(hold(code, 'python'))
        code = 'import subprocess\nsubprocess.run(["bash", "-c", %r])\n' % f'case x in a) rm {P};; esac'
        self.assertTrue(hold(code, 'python'))


if __name__ == '__main__':
    unittest.main()
