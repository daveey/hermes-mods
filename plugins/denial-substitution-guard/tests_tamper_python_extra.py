"""Python AST tamper: indirect identities, containers, exec family. Inert data only.

Fixtures are analyzer input and are never executed. HOLD means non-None.
"""
import importlib.util
import os
from pathlib import Path
import unittest

GUARD = Path(os.environ.get('DENIAL_GUARD_UNDER_TEST') or Path(__file__).with_name('guard.py'))
S = importlib.util.spec_from_file_location('python_tamper_extra_guard', GUARD)
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
L, G = '/x/denial-ledger.jsonl', '/x/denial-grants.json'

HOLD = {
    'dunder_import': f"__import__('os').remove('{L}')",
    'import_module': f"import importlib\nimportlib.import_module('shutil').move('{G}','/tmp/g')",
    'star_import': f"from os import *\nremove('{L}')",
    'bound_argv_list': f"import subprocess\ncmd=['rm','-f','{L}']\nsubprocess.run(cmd)",
    'bash_c_argv': f"import subprocess\nsubprocess.run(['bash','-c','rm -f {L}'])",
    'execvp_list': f"import os\nos.execvp('rm', ['rm', '{L}'])",
    'execlp_variadic': f"import os\nos.execlp('truncate','truncate','-s0','{G}')",
    'async_exec': f"import asyncio\nasyncio.create_subprocess_exec('rm','{L}')",
    'for_over_bound_list': f"import os\nfiles=['/x/plain','{G}']\nfor f in files:\n    os.remove(f)",
    'dict_subscript': f"d={{'p':'{L}'}}\nopen(d['p'],'w')",
    'starred_path_args': f"import os\nos.rename(*['{L}','/x/old'])",
    'kwargs_unknown_mode': f"open('{L}', **{{'mode':'w'}})",
    'kwargs_path': f"import shutil\nshutil.copyfile('/tmp/p', **{{'dst':'{G}'}})",
    'percent_format': "import os\nos.remove('/x/%s/denial-ledger.jsonl' % 'h')",
    'str_format': "import os\nos.remove('{}/denial-grants.json'.format('/x'))",
    'path_with_name': f"from pathlib import Path\nPath('/x/plain').with_name('denial-grants.json').unlink()",
    'os_open_creat': f"import os\nos.open('{L}', os.O_RDONLY | os.O_CREAT)",
    'nested_exec_depth': "exec(\"exec(\\\"open('/x/denial-ledger.jsonl','w')\\\")\")",
    'symlink_dst': f"import os\nos.symlink('/tmp/fake', '{G}')",
    'rmtree_dir': "import shutil\nshutil.rmtree('/x/denial-ledger.jsonl')",
    'path_replace_target': f"from pathlib import Path\nPath('/tmp/new').replace('{L}')",
}

ALLOW = {
    'dunder_import_read': f"print(__import__('os').path.exists('{L}'))",
    'bound_argv_read': f"import subprocess\ncmd=['cat','{L}']\nsubprocess.run(cmd)",
    'execvp_read': f"import os\nos.execvp('cat', ['cat', '{L}'])",
    'dict_unrelated': f"d={{'p':'/x/plain','doc':'see {L}'}}\nprint(d)\nopen('/x/plain','w')",
    'os_open_read_flags': f"import os\nos.open('{L}', os.O_RDONLY | os.O_CLOEXEC)",
    'exec_docs': f"exec(\"print('rm {L}')\")",
    'star_import_unrelated': "from os import *\nremove('/x/plain')",
    'path_copy_source': f"from pathlib import Path\nPath('{L}').copy('/x/backup')",
}


class PythonTamperExtra(unittest.TestCase):
    def test_holds(self):
        for name, code in HOLD.items():
            with self.subTest(name=name):
                self.assertIsNotNone(g._tamper('python', code))

    def test_allows(self):
        for name, code in ALLOW.items():
            with self.subTest(name=name):
                self.assertIsNone(g._tamper('python', code))

    def test_issues_are_not_holds(self):
        found, issue = g._python_analysis("import sys\nexec(sys.argv[1])")
        self.assertFalse(found)
        self.assertIn('dynamic code', issue)


if __name__ == '__main__':
    unittest.main()
