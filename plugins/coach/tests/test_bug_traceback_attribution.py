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


@pytest.mark.parametrize('shape', ['dict', 'json', 'hinted'])
@pytest.mark.parametrize('output', [CORE, '[pid 123] ' + CORE.replace('\n', '\n[pid 123] '),
                                   CORE.rsplit('json.decoder.', 1)[0]])
def test_successful_terminal_log_reads_are_not_bugs(shape, output):
    d = {'output': output, 'exit_code': 0, 'error': None}
    result = d if shape == 'dict' else json.dumps(d)
    if shape == 'hinted':
        result = json.dumps(d) + '\n[retry guidance]'
    assert rules.bug_signature('terminal', result, 'ok', None, None) is None


@pytest.mark.parametrize('output', [CORE.rsplit('json.decoder.', 1)[0],
                                   '[pid 123] ' + CORE.replace('\n', '\n[pid 123] '),
                                   repr(CORE)])
def test_terminal_frames_without_exception_identity_do_not_form_generic_bug(output):
    assert rules.bug_signature('terminal', envelope(output, 'json'), 'error', None, 'exit 1') is None


@pytest.mark.parametrize('shape', ['raw', 'dict', 'json', 'hinted'])
def test_failed_terminal_core_traceback_keeps_identity(shape):
    assert rules.bug_signature('terminal', envelope(CORE, shape), 'error', None, None) == (
        'terminal: json.decoder.JSONDecodeError: Extra data: line <n> column <n> (char <n>)')


@pytest.mark.parametrize('field', ['error', 'hint'])
def test_terminal_zero_exit_with_explicit_failure_is_not_hidden(field):
    d = {'output': CORE, 'exit_code': 0, field: 'pipeline failed'}
    assert rules.bug_signature('terminal', d, 'error', None, None)


def test_terminal_explicit_infrastructure_error_survives_zero_exit():
    assert rules.bug_signature('terminal', {'exit_code': 0, 'output': ''},
                               'error', 'RuntimeError', 'backend failed') == (
        'terminal: RuntimeError: backend failed')


def test_terminal_observer_ignores_repeated_log_reads_but_records_failure():
    class Sink:
        def __init__(self): self.bugs = []
        def bug(self, *args): self.bugs.append(args)
        def log(self, *args): pass
    sink = Sink()
    observer = rules.AgentCoach(signals=sink)
    for i in range(3):
        observer.observe(str(i), 'terminal', {'command': 'read logs'},
                         json.dumps({'output': CORE, 'exit_code': 0, 'error': None}), status='ok')
    assert sink.bugs == []
    observer.observe('core', 'terminal', {}, envelope(CORE, 'json'), status='error')
    assert len(sink.bugs) == 1


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
