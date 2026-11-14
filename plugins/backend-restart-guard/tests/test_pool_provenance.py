"""Pool bootstrap is not backend provenance. No signal payload is executed."""
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pool_guard_test', HERE / '__init__.py')
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def command(entry=None):
    module = str(HERE.parent / 'pool-llm' / 'pool_llm.py')
    entry = entry or f"sys.argv = [{module!r}] + sys.argv[1:]; runpy.run_path({module!r}, run_name='__main__')"
    code = ("import os, sys, runpy; os.environ.pop('PYTHONHOME', None); "
            "os.environ.pop('PYTHONPATH', None); os.environ.pop('VIRTUAL_ENV', None); "
            "sys.path.insert(0, '/opt/hermes-agent'); sys._hermes_pin_default_home = True; "
            "import hermes_bootstrap; os.environ.get('HERMES_HOME') or "
            "os.environ.__setitem__('HERMES_HOME', str(__import__('hermes_constants').get_default_hermes_root())); " + entry)
    return ['/usr/bin/python3', '-I', '-c', code, 'serve', '--port', '8765']


class PoolProvenance(unittest.TestCase):
    def test_canonical_pool_not_backend(self):
        for render in (' '.join, shlex.join):
            self.assertFalse(guard._backend_argv(render(command())))

    def assert_noncanonical_pool_protected(self, argv):
        # Classification only: never execute these payloads or send signals.
        for render in (' '.join, shlex.join):
            with self.subTest(render=render.__qualname__):
                canonical = render(command())
                self.assertTrue(guard._canonical_pool_bootstrap(canonical))
                self.assertFalse(guard._backend_argv(canonical))
                text = render(argv)
                self.assertFalse(guard._canonical_pool_bootstrap(text))
                self.assertTrue(guard._backend_argv(text))

    def test_relative_bootstrap_root_remains_protected(self):
        argv = command()
        argv[3] = argv[3].replace('/opt/hermes-agent', 'relative-root')
        self.assert_noncanonical_pool_protected(argv)

    def test_equivalent_noncanonical_import_spacing_remains_protected(self):
        # Low-severity lexical policy: the AST is equivalent, not malicious.
        argv = command()
        argv[3] = argv[3].replace('import os, sys, runpy;', 'import os,sys,runpy;')
        self.assertEqual(guard.ast.dump(guard.ast.parse(argv[3])),
                         guard.ast.dump(guard.ast.parse(command()[3])))
        self.assert_noncanonical_pool_protected(argv)

    def test_nonpython_executable_remains_protected(self):
        argv = command()
        argv[0] = '/usr/bin/notpython'
        self.assert_noncanonical_pool_protected(argv)

    def test_extra_gateway_tail_remains_protected(self):
        argv = command()
        argv[4:] = ['serve', 'gateway']
        self.assert_noncanonical_pool_protected(argv)

    def test_nonnumeric_port_remains_protected(self):
        argv = command()
        argv[-1] = 'not-a-port'
        self.assert_noncanonical_pool_protected(argv)

    def test_oversized_equivalent_payload_rejected_before_ast(self):
        argv = command()
        argv[3] += ' ' * 5000
        self.assert_noncanonical_pool_protected(argv)
        for render in (' '.join, shlex.join):
            with self.subTest(render=render.__qualname__), patch.object(
                    guard.ast, 'parse', wraps=guard.ast.parse) as parse:
                self.assertFalse(guard._canonical_pool_bootstrap(render(argv)))
                parse.assert_not_called()

    def test_backend_and_unknown_payload_remain_protected(self):
        backend = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
        for entry in (backend, "import hermes_bootstrap; custom()", command()[3].split('; ')[-1] + '; ' + backend):
            for render in (' '.join, shlex.join):
                self.assertTrue(guard._backend_argv(render(command(entry))))
        extra = command()
        extra[3] += '; dangerous_unknown_entry()'
        self.assertTrue(guard._backend_argv(' '.join(extra)))

    def test_unknown_wrappers_are_not_exempt(self):
        for prefix in ('hermes serve ', 'env HERMES_HOME=/x '):
            self.assertFalse(guard._canonical_pool_bootstrap(prefix + ' '.join(command())))
            self.assertTrue(guard._backend_argv(prefix + ' '.join(command())))

    def test_parser_failure_cannot_erase_gateway_discovery(self):
        ps = '81234 1 81234 ' + ' '.join(command()) + '\n81235 1 81235 hermes serve\n'
        for error in (RecursionError, MemoryError):
            with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'HERMES_HOME': home}), patch.object(
                    guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, ps)), patch.object(
                    guard.ast, 'parse', side_effect=error):
                self.assertEqual(set(guard.protected_processes()), {81234, 81235})

    def test_other_path_is_not_exempt(self):
        argv = command()
        argv[3] = argv[3].replace(str(HERE.parent / 'pool-llm' / 'pool_llm.py'), '/elsewhere/pool_llm.py')
        self.assertTrue(guard._backend_argv(' '.join(argv)))

    def test_discovery_and_terminal_hook_preserve_pidfile(self):
        ps = '81234 1 81234 ' + ' '.join(command()) + '\n81235 1 81235 hermes serve\n'
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'HERMES_HOME': home}), patch.object(
                guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, ps)):
            self.assertEqual(set(guard.protected_processes()), {81235})
            self.assertFalse(guard._pre_tool_call('terminal', {'command': 'kill -TERM 81234'}))
            self.assertTrue(guard._pre_tool_call('terminal', {'command': 'kill -TERM 81235'}))
            Path(home, 'gateway.pid').write_text('81234')
            self.assertEqual(set(guard.protected_processes()), {81234, 81235})
            self.assertTrue(guard._pre_tool_call('terminal', {'command': 'kill -TERM 81234'}))
