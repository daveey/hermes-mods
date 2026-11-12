"""Inert fixtures for shell reserved words, compound commands, eval, $'..'
quoting and parent variables in command substitutions. Nothing here is passed
to a shell.

Compound bodies (if/while/until/for/select/{ }) are analyzed with join
semantics: any command in a body may or may not run, and loop bodies are
iterated to a bounded fixpoint, so stores inside a body never hide an earlier
protected value from a later read. case and `function` definitions remain
unsupported regions (baseline rule over the remainder).
"""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('compound_candidate', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
P = 'denial-grants.json'


def hold(text):
    return g._tamper('shell', text) is not None


HOLD = [
    ('if_then', f'if true; then rm {P}; fi'),
    ('if_else', f'if c; then echo; else rm {P}; fi'),
    ('if_elif', f'if a; then echo; elif b; then rm {P}; fi'),
    ('brace_group', f'{{ rm {P}; }}'),
    ('brace_in_and', f'true && {{ rm {P}; }}'),
    ('time_prefix', f'time rm {P}'),
    ('time_p_prefix', f'time -p rm {P}'),
    ('bang_prefix', f'! rm {P}'),
    ('function_keyword', f'function f {{ rm {P}; }}; f'),
    ('for_literal', f'for f in {P}; do rm "$f"; done'),
    ('for_second_word', f'for f in a.txt {P}; do rm "$f"; done'),
    ('for_newlines', f'for f in {P}\ndo\n  rm "$f"\ndone'),
    ('while_body', f'while true; do rm {P}; done'),
    ('until_body', f'until false; do rm {P}; done'),
    ('pipe_into_while', f'ls | while read l; do rm {P}; done'),
    ('nested_compound', f'if a; then while b; do rm {P}; done; fi'),
    ('compound_redirect', f'if true; then echo x; fi > {P}'),
    ('loop_redirect', f'while read l; do echo "$l"; done > {P}'),
    ('if_condition_write', f'if rm {P}; then echo; fi'),
    ('while_condition_write', f'while rm {P}; do :; done'),
    # scalar flow through compound bodies
    ('if_store_then_read', f'F={P}; if c; then F=plain; fi; rm "$F"'),
    ('if_store_else_read', f'F={P}; if c; then F=plain; else rm "$F"; fi'),
    ('if_possible_store', f'F=plain; if c; then F={P}; fi; rm "$F"'),
    ('brace_possible', f'F=plain; {{ c && F={P}; }}; rm "$F"'),
    ('loop_carried', f'F=plain; while c; do rm "$F"; F={P}; done'),
    ('loop_carried_chain', f'A=x; B=y; while c; do rm "$A"; A=$B; B={P}; done'),
    ('loop_var_after', f'for f in {P}; do :; done; rm "$f"'),
    # eval
    ('eval_quoted', f'eval "rm {P}"'),
    ('eval_words', f'eval rm {P}'),
    ('eval_scalar', f"F={P}; eval 'rm \"$F\"'"),
    # $'..' quoting
    ('ansi_c_escaped_quote', "echo $'\\'' ; rm " + P + " ; echo \\'"),
    ('ansi_c_literal_operand', f"rm $'{P}'"),
    ('ansi_c_in_substitution', "echo $(echo $'\\''; rm " + P + ")"),
    # parent variables in command substitutions
    ('substitution_parent_var', f'F={P}; echo $(rm "$F")'),
    ('substitution_parent_var_backtick', f'F={P}; echo `rm "$F"`'),
    # function definitions: body runs at calls, assignments reach the caller
    ('function_then_read_var', 'function g { echo; }; read F; rm "$F"'),
    ('function_call_later_value', f'g() {{ rm "$F"; }}; F={P}; g'),
    ('function_assigns_for_caller', f'f() {{ F={P}; }}; f; rm "$F"'),
    ('function_assigns_after_overwrite', f'f() {{ F={P}; }}; F=x; f; rm "$F"'),
    ('function_body_next_line', f'function g\n{{\n  rm {P}\n}}'),
    ('function_paren_keyword', f'function g() {{ rm {P}; }}'),
    ('function_subshell_body', f'g() ( rm {P} )'),
    ('function_called_from_substitution', f'F={P}; g() {{ rm "$F"; }}; echo $(g)'),
    ('function_recursive', f'g() {{ g; F={P}; }}; g; rm "$F"'),
    ('function_in_loop_body', f'for f in a b; do g() {{ rm {P}; }}; done'),
    # a failure inside a compound makes the rest of the text uninspected
    ('compound_body_missing_separator', f'if c; then echo (x); rm {P}; fi'),
    ('compound_body_case_tail', f'if c; then case x in a) echo;; esac; fi; rm {P}'),
]

ALLOW = [
    ('if_plain', 'if true; then echo hi; fi'),
    ('if_read_protected', f'if [ -f {P} ]; then cat {P}; fi'),
    ('for_glob', 'for f in *.txt; do rm "$f"; done'),
    ('for_plain_words', 'for f in a.txt b.txt; do rm "$f"; done'),
    ('for_counter_store', 'for i in 1 2 3; do x=foo; rm "$x"; done'),
    ('loop_read_redirect', f'while read l; do echo "$l"; done < {P}'),
    ('brace_read', f'{{ cat {P}; }}'),
    ('if_var_read', f'F={P}; if c; then cat "$F"; fi'),
    ('overwritten_before_if', f'F={P}; F=plain; if c; then rm "$F"; fi'),
    ('eval_read', f'eval "cat {P}"'),
    ('ansi_c_read', f"echo $'it\\'s'; cat {P}"),
    ('time_plain', 'time ls -la'),
    # case_after_read (`cat P; case x in a) echo y > out;; esac`) moved to
    # FLOOR_PRECISION_COST: the 9caf-equivalent floor (stray `)` -> whole text
    # baseline) holds it; accepted documented precision cost, not a write.
    ('substitution_parent_read', f'F={P}; echo $(cat "$F")'),
    ('substitution_unrelated', f'F={P}; echo $(rm "$G")'),
    ('function_defined_not_matching', 'g() { rm "$F"; }; F=plain; g'),
    ('function_read_protected', f'g() {{ cat {P}; }}; g'),
    ('function_after_read_var', f'function g {{ echo; }}; F={P}; cat "$F"'),
    ('function_plain_defs', 'build() { make all; }; clean() { rm -rf out; }; build && clean'),
    ('not_reserved_brace_word', f'{{rm {P}'),
]


class Compound(unittest.TestCase):
    def test_holds(self):
        for name, text in HOLD:
            with self.subTest(name):
                self.assertTrue(hold(text), name)

    def test_allows(self):
        for name, text in ALLOW:
            with self.subTest(name):
                self.assertFalse(hold(text), name)

    def test_unsupported_still_reported(self):
        for text in ('case x in a) echo hi;; esac', 'function g x { echo hi; }'):
            with self.subTest(text):
                found, issue = g._shell_analysis(text)
                self.assertFalse(found)
                self.assertTrue(issue)


if __name__ == '__main__':
    unittest.main()
