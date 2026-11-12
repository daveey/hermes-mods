"""Inert fixtures for shell parameters: function positional arguments,
`set --`, declaration builtins (export/local/declare/typeset/readonly) and
complex parameter expansions (${F%x}, ${F:-x}, ${!G}, ${F[0]}). Nothing here
is passed to a shell.

Positional parameters bound at a call take a protected argument's value when
any argument is protected (top when any is uncertain), otherwise the literal
per-index values. Complex ${...} expansions of a name that holds a protected
or top value (or a protected fragment) are uncertain words.
"""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('params_candidate', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
P = 'denial-grants.json'


def hold(text):
    return g._tamper('shell', text) is not None


HOLD = [
    # functions: positional parameters bound to call arguments
    ('fn_arg1', f'f() {{ rm "$1"; }}; f {P}'),
    ('fn_arg_braced', f'f() {{ rm "${{1}}"; }}; f {P}'),
    ('fn_arg_at', f'f() {{ rm "$@"; }}; f {P}'),
    ('fn_arg_second', f'f() {{ rm "$2"; }}; f a {P}'),
    ('fn_arg_loop', f'f() {{ for x in "$@"; do rm "$x"; done; }}; f {P}'),
    ('fn_arg_local', f'f() {{ local x=$1; rm "$x"; }}; f {P}'),
    ('fn_arg_assign', f'f() {{ x=$1; rm "$x"; }}; f {P}'),
    ('fn_redir_arg', f'f() {{ echo hi > "$1"; }}; f {P}'),
    ('fn_tee_arg', f'f() {{ tee "$1"; }}; f {P}'),
    ('fn_shift', f'f() {{ shift; rm "$1"; }}; f a {P}'),
    ('fn_kw_arg', f'function f {{ rm "$1"; }}; f {P}'),
    ('fn_kw_paren_nl', f'function f() {{ rm "$1"; }}\nf {P}'),
    ('fn_nl_body', f'f()\n{{\n rm "$1"\n}}\nf {P}'),
    ('fn_paren_body', f'f() ( rm "$1" ); f {P}'),
    ('fn_var_arg', f'F={P}; f() {{ rm "$1"; }}; f "$F"'),
    ('fn_indirect_call', f'f() {{ rm "$1"; }}; x=f; $x {P}'),
    ('fn_store_to_caller', f'f() {{ G=$1; }}; f {P}; rm "$G"'),
    ('fn_uncertain_arg', 'read X; f() { rm "$1"; }; f "$X"'),
    # set -- at top level
    ('set_dashdash', f'set -- {P}; rm "$1"'),
    ('set_plain', f'set a {P}; rm "$2"'),
    ('set_for_noin', f'set -- {P}; for f; do rm "$f"; done'),
    # declaration builtins
    ('export_assign', f'export F={P}; rm "$F"'),
    ('local_assign', f'local F={P}; rm "$F"'),
    ('declare_assign', f'declare F={P}; rm "$F"'),
    ('declare_option', f'declare -r F={P}; rm "$F"'),
    ('typeset_assign', f'typeset F={P}; rm "$F"'),
    ('readonly_assign', f'readonly F={P}; rm "$F"'),
    ('export_two', f'export G=x F={P}; rm "$F"'),
    ('export_var_rhs', f'A={P}; export F=$A; rm "$F"'),
    # complex parameter expansions
    ('pe_suffix', f'F={P}; rm "${{F%x}}"'),
    ('pe_default', f'F={P}; rm "${{F:-x}}"'),
    ('pe_indirect', f'F={P}; G=F; rm "${{!G}}"'),
    ('pe_index', f'F={P}; rm "${{F[0]}}"'),
    ('pe_replace', f'F={P}; rm "${{F/x/y}}"'),
    ('pe_strip_bak', f'F={P}.bak; rm "${{F%.bak}}"'),
    ('pe_fragment', f'A=denial-gr; rm "${{A%x}}ants.json"'),
    ('pe_unquoted', f'F={P}; rm ${{F#./}}'),
    ('pe_top', 'read F; rm "${F%x}"'),
    # substitution output naming a protected fragment; brace expansion
    ('cmdsub_protected', f'rm "$(echo {P})"'),
    ('cmdsub_for', f'for f in $(echo {P}); do rm "$f"; done'),
    ('cmdsub_eval', f'eval "$(echo rm {P})"'),
    ('cmdsub_fragment', 'rm "$(printf %s denial- grants.json)"'),
    ('brace_operand', 'rm denial-{grants.json,x}'),
    ('brace_for', 'for f in denial-{grants.json,x}; do rm "$f"; done'),
    ('glob_for', 'for f in denial-gr*.json; do rm "$f"; done'),
]

ALLOW = [
    ('fn_benign_arg', 'f() { rm "$1"; }; f build'),
    ('fn_benign_rf', 'f() { rm -rf "$1"; }; f /tmp/x'),
    ('fn_read_arg', f'f() {{ cat "$1"; }}; f {P}'),
    ('fn_no_call_benign', 'f() { rm "$1"; }; echo hi'),
    ('set_benign', 'set -- a b; rm "$1"'),
    ('set_option', 'set -e; rm build.log'),
    ('export_benign', 'export F=build; rm "$F"'),
    ('declare_read', f'declare F={P}; cat "$F"'),
    ('pe_benign', 'F=x; rm "${F%x}y"'),
    ('pe_home_default', 'rm "${HOME:-/tmp}/x"'),
    ('pe_length', f'F={P}; rm "x${{#F}}"'),
    ('positional_unset', 'rm "$1"'),
    ('cmdsub_benign', 'rm "$(mktemp)"'),
    ('cmdsub_read_to_other', f'echo "$(cat {P})" > out.txt'),
    ('cmdsub_count', f'n=$(wc -l < {P}); echo "$n" > count.txt'),
    ('brace_benign', 'rm build/{a,b}.o'),
    ('set_o', 'set -o pipefail; rm build.log'),
]


class Params(unittest.TestCase):
    def test_holds(self):
        for name, text in HOLD:
            with self.subTest(name):
                self.assertTrue(hold(text), text)

    def test_allows(self):
        for name, text in ALLOW:
            with self.subTest(name):
                self.assertFalse(hold(text), text)

    def test_nameref_issue(self):
        found, issue = g._shell_analysis(f'declare -n R=F; F={P}; rm "$R"')
        self.assertTrue(found or issue, 'nameref must hold or be an explicit issue')


if __name__ == '__main__':
    unittest.main()
