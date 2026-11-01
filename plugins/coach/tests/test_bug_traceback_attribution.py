"""Tool envelopes and bundled Python frames must not manufacture core defects."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('bug_rules', Path(__file__).parents[1] / 'agent_rules.py')
assert spec is not None and spec.loader is not None
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)

CALLER = '''Traceback (most recent call last):
  File "/home/test/.hermes/cache/scratch/hermes_kernel_xyz/hermes_kernel_runner.py", line 84, in run_cell
    exec(compile(request["code"], "<cell>", "exec"), GLOBALS)
  File "<cell>", line 4, in <module>
  File "/home/test/.hermes/tools/python-3.14/lib/python3.14/json/decoder.py", line 348, in decode
    raise JSONDecodeError("Extra data", s, end)
json.decoder.JSONDecodeError: Extra data: line 1 column 2 (char 1)
'''
CORE = '''Traceback (most recent call last):
  File "/home/test/.hermes/hermes-agent/tools/code_kernel.py", line 20, in run
  File "/home/test/.hermes/tools/python-3.14/lib/python3.14/json/decoder.py", line 348, in decode
json.decoder.JSONDecodeError: Extra data: line 1 column 2 (char 1)
'''


def envelope(tb, shape):
    d = {'status': 'error', 'output': '\n--- stderr ---\n' + tb, 'error': tb, 'exit_code': 1}
    return {'raw': tb, 'dict': d, 'json': json.dumps(d), 'hinted': json.dumps(d) + '\n[retry guidance]'}[shape]


@pytest.mark.parametrize('shape', ['raw', 'dict', 'json', 'hinted'])
@pytest.mark.parametrize('tail', ['Extra data: line 1 column 2 (char 1)',
                                  'Extra data: line 2367 column 1 (char 115599)',
                                  'Invalid control character at: line 128 column 461 (char 19970)'])
def test_caller_json_errors_are_not_hermes_bugs(shape, tail):
    tb = CALLER.replace('Extra data: line 1 column 2 (char 1)', tail)
    assert rules.bug_signature('execute_code', envelope(tb, shape), 'error', 'JSONDecodeError', tb) is None


@pytest.mark.parametrize('shape', ['raw', 'dict', 'json', 'hinted'])
def test_core_exception_keeps_qualified_tail(shape):
    sig = rules.bug_signature('execute_code', envelope(CORE, shape), 'error', None, None)
    assert sig == 'execute_code: json.decoder.JSONDecodeError: Extra data: line <n> column <n> (char <n>)'


def test_core_invoked_by_cell_is_still_detected():
    tb = CALLER.replace('  File "/home/test/.hermes/tools/python-3.14',
                        '  File "/home/test/.hermes/hermes-agent/tools/x.py", line 9, in broken\n'
                        '  File "/home/test/.hermes/tools/python-3.14')
    assert rules.bug_signature('execute_code', envelope(tb, 'json'), 'error', None, None)


def test_outer_core_executor_does_not_own_cell_exception():
    tb = CALLER.replace('Traceback (most recent call last):',
                        'Traceback (most recent call last):\n'
                        '  File "/home/test/.hermes/hermes-agent/tools/code_kernel.py", line 1, in run')
    assert rules.bug_signature('execute_code', envelope(tb, 'json'), 'error', 'JSONDecodeError', tb) is None


def test_successful_output_quoting_traceback_is_not_a_failure():
    result = {'status': 'success', 'exit_code': 0, 'output': CORE}
    assert rules.bug_signature('execute_code', json.dumps(result), 'success', None, None) is None


def test_observer_does_not_record_three_caller_errors():
    class Sink:
        def __init__(self): self.bugs = []
        def bug(self, *args): self.bugs.append(args)
        def log(self, *args): pass
    sink = Sink()
    observer = rules.AgentCoach(signals=sink)
    for i in range(3):
        observer.observe(str(i), 'execute_code', {'code': 'json.loads(text)'},
                         envelope(CALLER, 'json'), status='error', error_message=CALLER)
    assert sink.bugs == []
    observer.observe('core', 'execute_code', {}, envelope(CORE, 'json'), status='error')
    assert len(sink.bugs) == 1
