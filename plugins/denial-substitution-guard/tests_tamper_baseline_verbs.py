"""Baseline verb coverage (REVIEW-FLOOR-B1-FINAL nonblocking): text the bounded
shell analysis does not inspect keeps the baseline rule, whose write-keyword
set lacked unlink/rmdir/touch/shred/ln/chmod/chown/sudoedit/rsync/find -delete.
Fixtures are inert text: never run them in a shell.
"""
import unittest

import guard as g

P = 'denial-grants.json'
VERBS = ['unlink', 'rmdir', 'touch', 'shred -u', 'ln -sf /dev/null', 'chmod 000', 'chown nobody',
         'chgrp nobody', 'sudoedit', 'rsync /dev/null', 'find . -name']
PREFIXES = ['case x in *) :;; esac; ', 'echo ${x/)/}; ']


class BaselineVerbs(unittest.TestCase):
    def test_uninspected_remainder_holds_mutating_verbs(self):
        for prefix in PREFIXES:
            for verb in VERBS:
                tail = f'{verb} {P}' + (' -delete' if verb.startswith('find') else '')
                with self.subTest(prefix=prefix, verb=verb):
                    self.assertIsNotNone(g._tamper('shell', prefix + tail))

    def test_reads_after_uninspected_remainder_allowed(self):
        for prefix in PREFIXES:
            for text in (f'cat {P}', f'ls -l {P}', f'grep x {P}', f'stat {P}', 'touch build.stamp', 'chmod +x run.sh'):
                with self.subTest(prefix=prefix, text=text):
                    self.assertIsNone(g._tamper('shell', prefix + text))

    def test_inspected_mutators_hold(self):
        # The precise model lacked these mutators: chmod 000 / ln over / shred /
        # rsync onto / find -delete of a protected file all ALLOWed.
        holds = [f'chmod 000 {P}', f'chmod -R 000 {P}', f'chown nobody {P}', f'chgrp nobody {P}',
                 f'ln -sf /dev/null {P}', f'ln -s {P} alias.json', f'link {P} hard.json', f'shred -u {P}', f'shred -n 1 {P}',
                 f'rsync /dev/null {P}', f'rsync -a x/ {P}', f'rsync --remove-source-files {P} /tmp/',
                 f'find . -name {P} -delete', f'find . -name {P} -exec rm {{}} +', "find . -name 'denial-*' -delete", "find . -iname '*.JSON' -delete", "find . -path '*/x/*grants*' -delete",
                 f'X={P}; chmod 000 "$X"', 'A=denial-; B=grants.json; ln -sf /dev/null "$A$B"',
                 f'if true; then shred {P}; fi', f"bash -c 'chmod 000 {P}'", f'chmod --reference=x {P}']
        for text in holds:
            with self.subTest(text):
                self.assertIsNotNone(g._tamper('shell', text))
        for text in (f'ls -ln {P}', f'find . -name {P}', f'find . -name {P} -print', f'rsync {P} backup/',
                     'chmod +x run.sh', 'ln -s ../lib lib', 'shred -u secret.tmp', 'find . -name "*.o" -delete'):
            with self.subTest(text):
                self.assertIsNone(g._tamper('shell', text))
        py = f"import subprocess\nsubprocess.run(['chmod', '000', '{P}'])"
        self.assertIsNotNone(g._tamper('python', py))

    def test_word_boundaries(self):
        # 'ln'/'touch' inside other words are not verbs.
        self.assertFalse(g._shell_baseline(f'echo kiln retouched {P}'))
        self.assertTrue(g._shell_baseline(f'ln -s x {P}'))


if __name__ == '__main__':
    unittest.main()
