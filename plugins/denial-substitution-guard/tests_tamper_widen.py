"""Tagged widening tests: all shell text is inert analyzer input."""
import importlib.util
from pathlib import Path
import unittest
S = importlib.util.spec_from_file_location('widen_guard', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
P = 'denial-grants.json'
Q = ''.join('maybe && F=v' + str(i) + '; ' for i in range(9))
B = 'maybe && A=1; maybe && B=1; maybe && C=1; maybe && D=1; '
PREFIX = 'F=' + P + '; ' + Q

class Widen(unittest.TestCase):
    def test_join_domain(self):
        states = [{'F': str(i), 'same': 'plain', 'top': g._UNCERTAIN} for i in range(9)]
        states[0]['missing'] = 'present'
        self.assertEqual(g._join_environments(states), [{'F': g._UNCERTAIN, 'same': 'plain', 'top': g._UNCERTAIN, 'missing': g._UNCERTAIN}])
        self.assertEqual(g._join_environments(states[:8]), states[:8])
        self.assertEqual(g._join_environments([states[0]] * 9), [states[0]])
        self.assertEqual(g._scalar_cost(g._UNCERTAIN), 1)

    def test_consumer_policies(self):
        cases = [
            ('bash -c "$F"', False, 'uncertain interpreter argument unsupported'),
            ('python3 -c "$F"', False, 'uncertain interpreter argument unsupported'),
            ('bash "$F" plain', False, 'uncertain interpreter argument unsupported'),
            ('dd "${F}=denial-grants.json"', True, None),
            ('dd if="$F" of=plain', False, None),
            ('sed "$F" s/a/b/ denial-grants.json', True, None),
            ('sed -- s/a/b/ "$F"', False, None),
            ('echo ok >& "2${F}"', True, None),
            ('command "$F" plain', False, 'uncertain shell executable unsupported'),
        ]
        for suffix, found, issue in cases:
            with self.subTest(suffix=suffix):
                self.assertEqual(g._shell_analysis(PREFIX+suffix), (found, issue))

    def test_w01_to_w16(self):
        cases = [
            (PREFIX+'rm "$F"', True),
            ('F='+P+'; '+B+'rm "$F"', True),
            ('F=plain; '+B+'rm "$F"', False),
            (Q+'rm "$F"', True),
            (PREFIX+'F=plain; rm "$F"', False),
            (PREFIX+'maybe && F=plain; rm "$F"', True),
            (PREFIX+'(F=plain); rm "$F"', True),
            (PREFIX+'F=plain rm "$F"', True),
            (PREFIX+'G=pre${F}post; rm "$G"', True),
            (PREFIX+'G=$F; F=plain; rm "$G"', True),
            (PREFIX+'printf "%s" "$F"; cat "$F"', False),
            (PREFIX+'echo ok > "$F"', True),
            (PREFIX+'dd if=plain of="prefix${F}"', True),
            (PREFIX+'cp "$F" plain', False),
            (PREFIX+'cp plain --target-directory="prefix${F}"', True),
            (PREFIX+'cat "$F" > plain', False),
        ]
        for i, (text, expected) in enumerate(cases, 1):
            with self.subTest(case=f'W{i:02d}'):
                found, issue = g._shell_analysis(text)
                self.assertEqual(found, expected)
                self.assertIsNone(issue)

if __name__ == '__main__': unittest.main()
