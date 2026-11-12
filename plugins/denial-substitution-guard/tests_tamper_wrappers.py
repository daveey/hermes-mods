"""Literal wrapper grammar tests: all shell text is inert analyzer input.

Covers enumerated env/sudo/command/exec/token-broker grammars, lookup-only
controls, child-scope isolation, explicit unsupported issues and direct
interpreter-child seeding from known command-line assignments. An issue is a
reported analysis limit, never authorization or containment.
"""
import importlib.util
from pathlib import Path
import unittest
S = importlib.util.spec_from_file_location('wrapper_guard', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
P = '/example/denial-grants.json'


class Wrappers(unittest.TestCase):
    def check(self, cases):
        for text, found, issue in cases:
            with self.subTest(text=text):
                actual_found, actual_issue = g._shell_analysis(text)
                self.assertIs(actual_found, found)
                self.assertIs(g._tamper('shell', text) is not None, found)
                if issue is None:
                    self.assertIsNone(actual_issue)
                else:
                    self.assertIn(issue, actual_issue or '')

    def test_enumerated_grammars(self):
        writes = ['env --', 'env -i', 'env --ignore-environment', 'env -u EXAMPLE', 'env --unset=EXAMPLE',
                  'env -C /example', 'env --chdir=/example', 'env F=plain', 'env a-b=c', 'sudo -n -H -E',
                  'sudo -u example --', 'sudo --user=example', 'sudo -g example -H', 'sudo F=plain',
                  'command -p --', 'exec -a example', 'exec -c -l', 'env -i sudo -n --',
                  'token-broker exec --scope example/test --reason fixture --',
                  'token-broker exec --scope=example/test --reason=fixture --',
                  '/usr/bin/env -- /usr/bin/sudo -n -- command -p exec -a example']
        cases = []
        for wrapper in writes:
            cases.append((wrapper + ' rm -- ' + P, True, None))
            cases.append((wrapper + ' cat ' + P, False, None))
            cases.append((wrapper + ' printf "%s" "rm ' + P + '"', False, None))
        cases.append(('env -- ' * 100 + 'rm ' + P, True, None))
        cases.append(('env "A=$UNSET" rm ' + P, True, None))
        self.check(cases)

    def test_option_values_and_lookups(self):
        self.check([
            ('env --unset rm cat ' + P, False, None),
            ('sudo --user rm cat ' + P, False, None),
            ('exec -a rm cat ' + P, False, None),
            ('command -v rm ' + P, False, None),
            ('command -V rm ' + P, False, None),
            ('command -p -v rm ' + P, False, None),
            ('command -v rm > ' + P, True, None),
            ('command -v "$(rm ' + P + ')"', True, None),
            ('sudo -u "$WHO" rm ' + P, True, None),
        ])

    def test_unsupported_forms_are_reported_not_guessed(self):
        self.check([
            ('env -S rm ' + P, False, 'wrapper option unsupported'),
            ('sudo -ufoo rm ' + P, False, 'wrapper option unsupported'),
            ('command -pv rm ' + P, False, 'wrapper option unsupported'),
            ('token-broker exec --scope example rm ' + P, False, 'token-broker delimiter missing'),
            ('token-broker read rm ' + P, False, 'token-broker form unsupported'),
            ('sudo -u', False, 'missing or uncertain wrapper option value'),
            ('read WHO; sudo -u "$WHO" rm ' + P, False, 'missing or uncertain wrapper option value'),
            ('env "$OPTIONS" rm ' + P, False, 'uncertain wrapper argument unsupported'),
            ('command -- "$EXE" ' + P, False, 'uncertain shell executable unsupported'),
            ('"$EXE" ' + P, False, 'uncertain shell executable unsupported'),
            ('sudo --mystery true > ' + P, True, None),
            ('env =x rm ' + P, False, 'wrapper assignment form unsupported'),
            ('sudo =x rm ' + P, False, 'wrapper assignment form unsupported'),
            ('sudo -e ' + P, False, 'wrapper option unsupported'),
        ])

    def test_sudoedit_operands_are_writes(self):
        self.check([
            ('sudoedit ' + P, True, None),
            ('sudoedit -u root ' + P, True, None),
            ('/usr/bin/sudoedit -- /example/denial-ledger.jsonl', True, None),
            ('sudoedit /example/notes.txt', False, None),
        ])

    def test_child_scope_does_not_change_parent_state(self):
        self.check([
            ('F=plain; env F=' + P + ' true; rm "$F"', False, None),
            ('F=plain; env unset F; rm "$F"', False, None),
            ('F=plain; sudo read F; rm "$F"', False, None),
            # Child-scoped opaque code still reports its limitation; parent F stays plain.
            ('F=plain; token-broker exec -- eval ignored; rm "$F"', False, 'opaque shell eval'),
            ('F=plain; command read F; rm "$F"', True, None),
            ('F=' + P + '; env F=plain rm "$F"', True, None),
            ('F=plain; F=' + P + ' env rm "$F"', False, None),
        ])

    def test_interpreter_child_seeded_by_known_assignments(self):
        self.check([
            ("env F=" + P + " sh -c 'rm \"$F\"'", True, 'wrapper child environment unsupported'),
            ("F=" + P + " sh -c 'rm \"$F\"'", True, None),
            ("F=" + P + " env sh -c 'rm \"$F\"'", True, 'wrapper child environment unsupported'),
            ("F=plain sh -c 'rm \"$F\"'", False, None),
            ("F=" + P + " env -u F sh -c 'rm \"$F\"'", False, 'wrapper child environment unsupported'),
            ("F=" + P + " env --unset=F sh -c 'rm \"$F\"'", False, 'wrapper child environment unsupported'),
            ("F=" + P + " env -i sh -c 'rm \"$F\"'", False, 'wrapper child environment unsupported'),
            ("F=plain env -i F=" + P + " sh -c 'rm \"$F\"'", True, 'wrapper child environment unsupported'),
            ("sudo F=" + P + " sh -c 'cat \"$F\"'", False, 'wrapper child environment unsupported'),
            ("F=plain; env sh -c 'F=" + P + "'; rm \"$F\"", False, 'wrapper child environment unsupported'),
        ])


if __name__ == '__main__':
    unittest.main()
