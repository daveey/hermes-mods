"""Python AST tamper tests: every fixture is inert analyzer input, never executed.

HOLD means `_tamper('python', code)` is non-None. The analysis recognizes actual
AST mutation calls and their path/mode operands; strings are data unless they
are an operand of a recognized operation. This is a bounded mistake guard, not
a sandbox: dynamic calls, reflection, unknown helpers and filesystem aliases
remain explicit limits (see README/SPEC).
"""
import importlib.util
from pathlib import Path
import unittest

S = importlib.util.spec_from_file_location('python_tamper_guard', Path(__file__).with_name('guard.py'))
g = importlib.util.module_from_spec(S); S.loader.exec_module(g)
L, G = '/x/denial-ledger.jsonl', '/x/denial-grants.json'

HOLD = {
    'open_w': f"open('{L}','w')",
    'open_kw_mode': f"open('{G}', mode='a')",
    'open_rplus': f"open('{L}','r+')",
    'open_kw_file': f"open(file='{L}', mode='x')",
    'open_scalar_mode': f"m='w'\nopen('{L}', m)",
    'open_unknown_mode': f"import sys\nopen('{L}', sys.argv[1])",
    'io_open': f"import io\nio.open('{L}','wb')",
    'scalar_concat': "p='/example/denial-'\np=p+'grants.json'\nopen(p,'w')",
    'augassign': "p='/x/denial-'\np+='ledger.jsonl'\nopen(p,'w')",
    'annassign': f"p: str = '{G}'\nopen(p,'w')",
    'later_reassign': f"p='{G}'\nf=open(p,'w')\np='/x/plain'",
    'fstring': "d='/x'\nopen(f'{d}/denial-ledger.jsonl','w')",
    'ifexp': f"import sys\np='/x/plain' if sys.argv else '{G}'\nopen(p,'w')",
    'if_stmt': f"import sys\nif sys.argv:\n    p='{G}'\nelse:\n    p='/x/plain'\nopen(p,'w')",
    'path_write_text': f"from pathlib import Path\nPath('{G}').write_text('[]')",
    'path_home_div': "from pathlib import Path\n(Path.home()/'.hermes'/'denial-ledger.jsonl').write_text('')",
    'path_bound_unlink': "import pathlib\np=pathlib.Path('/x')/'denial-grants.json'\np.unlink()",
    'path_open_append': f"from pathlib import Path\nPath('{L}').open('a')",
    'path_rename_receiver': f"from pathlib import Path\nPath('{L}').rename('/x/old')",
    'path_touch_joinpath': "from pathlib import Path\nPath('/x').joinpath('denial-grants.json').touch()",
    'os_remove_expanduser': "import os\nos.remove(os.path.expanduser('~/.hermes/denial-ledger.jsonl'))",
    'os_unlink_join': "import os\nos.unlink(os.path.join(os.environ['HOME'], '.hermes', 'denial-grants.json'))",
    'from_import_alias': f"from os import remove as rm_\nrm_('{L}')",
    'module_alias': f"import os as o\no.unlink('{G}')",
    'os_rename_src': f"import os\nos.rename('{L}','/x/old')",
    'os_replace_dst': f"import os\nos.replace('/tmp/new','{L}')",
    'os_truncate': f"import os\nos.truncate('{L}', 0)",
    'os_open_write_flags': f"import os\nos.open('{L}', os.O_WRONLY | os.O_TRUNC)",
    'os_open_unknown_flags': f"import os, sys\nos.open('{L}', int(sys.argv[1]))",
    'shutil_move_src': f"import shutil\nshutil.move('{G}','/tmp/g')",
    'shutil_copy_dst': f"import shutil\nshutil.copy('/tmp/plain','{G}')",
    'shutil_copyfile_kw_dst': f"import shutil\nshutil.copyfile(src='/tmp/plain', dst='{L}')",
    'shutil_copy_dir_derived': "import shutil\nshutil.copy('/tmp/denial-ledger.jsonl','/x/hermes/')",
    'terminal_rm': "from hermes_tools import terminal\nterminal('rm -f ~/.hermes/denial-ledger.jsonl')",
    'terminal_kw_redirect': "from hermes_tools import terminal\nterminal(command='> ~/.hermes/denial-grants.json')",
    'terminal_scalar': "from hermes_tools import terminal\nc='rm -f '+'~/.hermes/denial-grants.json'\nterminal(c)",
    'subprocess_list': f"import subprocess\nsubprocess.run(['rm','-f','{L}'])",
    'subprocess_shell_str': f"import subprocess\nsubprocess.run('truncate -s0 {L}', shell=True)",
    'os_system': f"import os\nos.system('truncate -s0 {L}')",
    'write_file_tool': f"from hermes_tools import write_file\nwrite_file('{G}','[]')",
    'patch_tool_kw': f"from hermes_tools import patch\npatch(path='{L}', old_string='a', new_string='b')",
    'function_param': f"def w(p):\n    open(p,'w').write('')\nw('{L}')",
    'function_kw_param': f"def w(path, data):\n    open(path,'w').write(data)\nw(data='', path='{G}')",
    'function_default': f"def w(path='{G}'):\n    open(path,'w')\nw()",
    'global_decl': f"p='/x/plain'\ndef f():\n    global p\n    p='{L}'\nf()\nopen(p,'w')",
    'exec_literal': f"exec(\"open('{L}','w')\")",
    'with_open_w': f"with open('{G}', 'w') as f:\n    f.write('[]')",
    'composed_literal_b3': "open('/example/denial-' + 'grants.json','w').write('[]')",
    'shadow_is_still_held': f"def open(p, m):\n    return None\nopen('{L}','w')",
}

ALLOW = {
    'print_docs': f"print(\"open('{G}', 'w')\")",
    'comment_docs': f"# open('{L}','w')\nprint(1)",
    'mention_remove_text': "msg = 'remove denial-grants entry manually'\nprint(msg)",
    'open_read_default': f"print(open('{L}').read())",
    'open_rb': f"open('{L}','rb')",
    'open_read_kw': f"open(file='{G}', mode='r')",
    'with_open_read': f"with open('{L}') as f:\n    print(f.read())",
    'json_load': f"import json\njson.load(open('{L}'))",
    'read_then_unrelated_write': f"data=open('{L}').read()\nopen('/x/backup.jsonl','w').write(data)",
    'write_payload_text': "open('/x/plain','w').write('denial-grants')",
    'path_write_payload': "from pathlib import Path\nPath('/x/plain').write_text('denial-ledger')",
    'path_read_text': f"from pathlib import Path\nprint(Path('{L}').read_text())",
    'path_exists': f"from pathlib import Path\nprint(Path('{G}').exists())",
    'os_path_exists': f"import os\nprint(os.path.exists('{G}'))",
    'os_open_rdonly': f"import os\nos.open('{L}', os.O_RDONLY)",
    'shutil_copy_src': f"import shutil\nshutil.copy('{L}','/x/backup.jsonl')",
    'str_replace_method': f"s='{G}'.replace('x','y')\nprint(s)",
    'unknown_write_method': f"logger.write('{L}')",
    'independent_locals': f"def a():\n    p='{L}'\n    print(open(p).read())\ndef b():\n    p='/x/plain'\n    open(p,'w')",
    'terminal_read': "from hermes_tools import terminal\nterminal('cat ~/.hermes/denial-ledger.jsonl')",
    'terminal_docs': "from hermes_tools import terminal\nterminal(\"printf '%s' 'rm -f ~/.hermes/denial-grants.json'\")",
    'subprocess_read': f"import subprocess\nsubprocess.run(['cat','{L}'])",
    'write_file_unrelated': f"from hermes_tools import write_file\nwrite_file('/x/notes.md','rm {L}')",
    'function_param_unrelated': f"def w(p):\n    open(p,'w')\nw('/x/plain')\nprint('{L}')",
}


class PythonTamper(unittest.TestCase):
    def test_recognized_mutations_hold(self):
        for name, code in HOLD.items():
            with self.subTest(name=name):
                self.assertIsNotNone(g._tamper('python', code))

    def test_data_and_read_controls_allow(self):
        for name, code in ALLOW.items():
            with self.subTest(name=name):
                self.assertIsNone(g._tamper('python', code))

    def test_end_to_end_execute_code_and_nested_tool_call(self):
        hold = HOLD['terminal_rm']
        allow = ALLOW['print_docs']
        self.assertIsNotNone(g.classify('execute_code', {'code': hold}))
        self.assertIsNotNone(g.classify('tool_call', {'calls': [{'name': 'execute_code', 'arguments': {'code': hold}}]}))
        self.assertIsNone(g._tamper('python', allow))

    def test_unanalyzable_input_keeps_baseline_conservative_rule(self):
        # A syntax error or analysis limit establishes no operand; to avoid a
        # coverage loss versus the reviewed baseline (interpreter version skew,
        # bounds) the old co-occurrence rule applies ONLY to unanalyzable code.
        self.assertIsNotNone(g._tamper('python', f"open('{L}','w'\n"))
        self.assertIsNone(g._tamper('python', "print('denial-grants' \n"))  # no write word
        self.assertIsNotNone(g._tamper('python', f"open('{L}','w')\n" + "x=1\n" * 70000))


if __name__ == '__main__':
    unittest.main()
