"""Attached sampling must not suspend a production process by default."""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('profiler_test_' + name, HERE / (name + '.py'))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ProfilerSafetyTests(unittest.TestCase):
    def test_commands(self):
        guard = load('profiler_safety').blocked
        for cmd in [
            'sudo ssh example.com "py-spy record -p 123"',
            'env bash -lc "py-spy record -p 123"',
            'py-spy record -p 123 > "--nonblocking"',
            'py-spy record -p 123 > --help',
            'py-spy record --pid 123 --duration 60 --rate 20 --idle',
            'sudo -n /opt/bin/py-spy record -p123 -d60',
            'ssh example.com "sudo py-spy record -p 123 -d 60"',
            "bash -lc 'py-spy top --pid=123'",
            'py-spy record -p 123; echo --nonblocking',
            'py-spy record -p 123 --nonblocking && py-spy record -p 456',
        ]:
            with self.subTest(cmd=cmd):
                self.assertTrue(guard('terminal', {'command': cmd}))
        for cmd in [
            'python3 -c "import sys; print(sys.argv)" py-spy record -p 123',
            'cat <<EOF\npy-spy record -p 123\nEOF',
            'py-spy record --pid 123 --nonblocking -d 10',
            'sudo py-spy top -p123 --nonblocking',
            'ssh example.com "py-spy record --pid=123 --nonblocking"',
            'py-spy dump --pid 123',
            'py-spy record -o out.svg -- python fixture.py',
            "echo 'py-spy record -p 123'",
            "grep 'py-spy record -p 123' evidence.txt",
            'py-spy record --help',
        ]:
            with self.subTest(cmd=cmd):
                self.assertFalse(guard('terminal', {'command': cmd}))

    def test_execute_code(self):
        guard = load('profiler_safety').blocked
        self.assertTrue(guard('execute_code', {'code': 'from hermes_tools import terminal\nterminal("py-spy record -p 123")'}))
        self.assertTrue(guard('execute_code', {'code': 'terminal(command="ssh example.com \'py-spy record -p 123\'")'}))
        self.assertFalse(guard('execute_code', {'code': 'print("py-spy record -p 123")'}))
        self.assertFalse(guard('execute_code', {'code': 'terminal("py-spy record -p 123 --nonblocking")'}))
        self.assertFalse(guard('read_file', {'path': 'py-spy record -p 123'}))

    @unittest.skipUnless(importlib.util.find_spec('hermes_constants'), 'core-backed hook test')
    def test_registered_hook(self):
        mod = load('__init__')
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            store = mod._load('core').Store(root=Path(root))
            _, hooks = mod.build(store=store, checkout=None)
            for tool, args in [('terminal', {'command': 'sudo py-spy record -p 123'}),
                               ('execute_code', {'code': 'terminal("py-spy record -p 123")'})]:
                result = hooks['pre_tool_call'](tool_name=tool, args=args)
                self.assertEqual(result['action'], 'block')
                self.assertIn('--nonblocking', result['message'])
            self.assertIsNone(hooks['pre_tool_call'](tool_name='terminal', args={'command': 'py-spy record -p 123 --nonblocking'}))


if __name__ == '__main__':
    unittest.main()
