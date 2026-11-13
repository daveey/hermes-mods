"""Regressions for independent REVIEW-PYTHON-W1B.md (B1, N1, N2, N3). Inert analyzer input only."""
import importlib.util
import os
from pathlib import Path
import threading
import time
import unittest

GUARD = Path(os.environ.get('DENIAL_GUARD_UNDER_TEST') or Path(__file__).with_name('guard.py'))
S = importlib.util.spec_from_file_location('python_tamper_w1b_guard', GUARD)
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
G = '/h/denial-grants.json'


def sysfam(unit, width, calls, tail=''):
    return "import os\nx='%s'\n" % (unit * width) + 'os.system(x)\n' * calls + tail


class W1bReviewFindings(unittest.TestCase):
    def timed(self, kind, code, limit=3.0):
        # The bound is on the analyzer's own work, so it is CPU time of this thread: wall
        # time under the full parallel suite selection measures the scheduler (4.5 s seen).
        start = time.thread_time()
        result = g._tamper(kind, code)
        self.assertLess(time.thread_time() - start, limit, code[:60])
        return result

    def test_b1_reused_shell_text_shares_python_budget(self):
        for unit, width, calls in (('$a ', 60000, 100), ('"$(a)" ', 20000, 300), ('${a} ', 30000, 150)):
            with self.subTest(unit=unit):
                self.assertIsNone(self.timed('python', sysfam(unit, width, calls)))
                # A protected write after exhausted nested-shell work is still held.
                self.assertIsNotNone(self.timed('python', sysfam(unit, width, calls, "open('%s','w')\n" % G)))
                self.assertIsNotNone(self.timed('python', sysfam(unit, width, calls, "os.system('rm -f %s')\n" % G)))

    def test_b1_distinct_shell_texts_share_python_budget(self):
        code = "import os\nx='%s'\n" % ('$a ' * 60000) + ''.join("os.system(x+'%d')\n" % i for i in range(60))
        # These distinct shell texts exhaust the shared shell budget. Since
        # REVIEW-FLOOR-B1-CORRECTION F2 exhaustion holds the whole code
        # (documented precision cost); it must still be fast.
        self.assertIsNotNone(self.timed('python', code))

    def test_b1_identical_shell_text_still_analyzed(self):
        # Repeated legitimate commands (below the node bound) do not exhaust anything; precise verdicts stay.
        code = "import os\n" + "os.system('ls /tmp')\n" * 3000 + "os.system('cat %s')\n" % G
        self.assertIsNone(self.timed('python', code))
        self.assertIsNotNone(g._tamper('python', "import os\n" + "os.system('ls')\n" * 3000 + "os.system('rm %s')\n" % G))

    def test_n1_bound_fallback_keeps_shell_delete(self):
        exhaust = "x='%s'\n" % ('a' * 20000) + ''.join('y%d=x+x\n' % i for i in range(3000))
        pad = 'x=[' + ','.join('1' for _ in range(33000)) + ']\n'
        for prefix in (exhaust, pad):
            for tail in ("import subprocess\nsubprocess.run(['rm','-f','%s'])\n" % G,
                         "import os\nos.system('rm -f %s')\n" % G,
                         "import os\nos.system('echo x > %s')\n" % G):
                with self.subTest(prefix=len(prefix), tail=tail):
                    self.assertIsNotNone(self.timed('python', prefix + tail))

    def test_n2_nested_bound_fallback_folds_literals(self):
        exhaust = "x='%s'\n" % ('a' * 20000) + ''.join('y%d=x+x\n' % i for i in range(3000))
        code = 'exec(%r)' % (exhaust + "open('denial-'+'grants.json','w')\n")
        self.assertIsNotNone(self.timed('python', code))

    def test_n3_python_c_children_share_one_budget(self):
        inner = "x='%s';" % ('a' * 20000) + ';'.join('y%d=x+x' % i for i in range(2500))
        shell = ' ; '.join('python3 -c "%s"' % inner for _ in range(4))
        self.assertLess(len(shell), 262144)
        start = time.thread_time()
        g._tamper('shell', shell)
        single = time.thread_time() - start
        self.assertLess(single, 3.0)
        g._tamper('python', 'x=1')
        self.assertIsNotNone(g._tamper('shell', shell + ' ; rm -f ' + G))

    def test_c1_shell_cache_is_depth_specific(self):
        from shlex import quote
        code = "import os\ng='denial-'\ng+='grants.json'\nos.remove('/h/'+g)\n"
        inner = 'python3 -c ' + quote(code)
        for levels in range(0, 7):
            wrapped = inner
            for _ in range(levels):
                wrapped = 'python3 -c ' + quote('import os\nos.system(%r)\n' % wrapped)
            with self.subTest(levels=levels):
                # The deep copy may only be baseline-checked; the shallow copy is precise.
                self.assertIsNotNone(g._tamper('python', 'import os\nos.system(%r)\nos.system(%r)\n' % (wrapped, inner)))
        self.assertIsNotNone(g._tamper('shell', inner))

    def test_exhausted_nested_child_escalates_to_outer_baseline(self):
        exhaust = "x='%s'\n" % ('a' * 20000) + ''.join('y%d=x+x\n' % i for i in range(3000))
        # exec child exhausts the shared work; outer text holds a protected write keyword pair.
        code = "p='/h/denial-grants.json'\nexec(%r)\nimport shutil\nshutil.copy('/tmp/x', p)\n" % exhaust
        self.assertIsNotNone(g._tamper('python', code))

    def test_budget_state_isolated_between_calls_and_threads(self):
        exhausting = sysfam('$a ', 60000, 100)
        self.assertIsNone(g._tamper('python', exhausting))
        self.assertIsNone(g._tamper('python', "import os\nos.system('cat %s')\n" % G))
        results = []
        worker = threading.Thread(target=lambda: results.append(g._tamper('python', "print(open('%s').read())" % G)))
        worker.start(); worker.join()
        self.assertEqual(results, [None])


if __name__ == '__main__':
    unittest.main()
