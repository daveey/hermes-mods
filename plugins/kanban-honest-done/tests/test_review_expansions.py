"""Review F1/F2 and N1/N2: executable bash operands, not shell spelling."""
import builtins
import importlib.util
import os
import subprocess

import pytest

from test_source_review_regressions import HERE, OWN, OTHER, board, consent, core, stop


@pytest.mark.parametrize('target', ['t_1234567{8..8}', 't_1234567{8,8}', '$(cat id)', '$(printf t_12345678)'])
@pytest.mark.parametrize('verb,flags', [
    ('comment', 'retry'), ('edit', '--title narrowed'), ('edit', '--body narrowed'),
    ('complete', '--summary "Deferred the tests; not verified."'),
    ('done', '--summary "Deferred the tests; not verified."'),
    ('request-review', '--summary "check 4 failed; waived"'),
])
def test_bash_expansion_gate(tmp_path, target, verb, flags):
    fake, capture = tmp_path / 'hermes', tmp_path / 'argv'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    fake.chmod(0o700)
    (tmp_path / 'id').write_text(OWN)
    command = f'hermes kanban {verb} {target} {flags}'
    subprocess.run(['/bin/bash', '-c', command], cwd=tmp_path, check=True,
                   env={'PATH': str(tmp_path) + os.pathsep + '/usr/bin:/bin', 'CAPTURE': str(capture)})
    assert capture.read_text().splitlines()[2] == OWN
    if verb == 'comment':
        env = {'HERMES_HOME': str(tmp_path)}
        stop(env)
        assert consent.check_relay('terminal', {'command': command}, ['chat-a'], env)
    else:
        db = board(tmp_path, 'DONE: check 4 requires >= 25% free disk.')
        assert core.check_shell(command, OWN, lambda: [db])


@pytest.mark.parametrize('target', ['$(cat id)', '$(printf x; cat id)', '$((1 + 2))'])
def test_split_substitution_is_held_even_without_recovered_flags(target):
    assert core.check_shell(f'hermes kanban edit {target} --title narrowed', OWN, lambda: [])


def test_end_of_options_target():
    assert core.check_shell(f'hermes kanban edit --title narrowed -- {OWN}', OWN, lambda: [])
    assert core.check_shell(f'hermes kanban edit --title narrowed -- {OTHER}', OWN, lambda: []) is None


def test_literal_inert_expansion_mentions_still_allowed(tmp_path):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    command = "printf '%s' 'hermes kanban edit t_1234567{8..8} --title narrowed'"
    assert consent.check_relay('terminal', {'command': command}, ['chat-a'], env) is None
    assert core.check_shell(command, OWN, lambda: []) is None


@pytest.mark.parametrize('tool,key', [('terminal', 'command'), ('process_manage', 'data'),
                                     ('run_in_user_terminal', 'command')])
@pytest.mark.parametrize('target', ['t_1234567{8..8}', 't_1234567?', 't_1234567[8]'])
def test_nested_shell_expansions_and_structured_literal_controls(tmp_path, tool, key, target):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    args = {'calls': [{'name': tool, 'arguments': {key: f'hermes kanban comment {target} retry'}}]}
    assert consent.check_relay('tool_call', args, ['chat-a'], env)
    assert consent.check_relay('kanban_comment', {'task_id': OTHER, 'body': 'literal {braces}'}, ['chat-a'], env) is None
    assert consent.check_relay('terminal', {'command': f'hermes kanban comment {OTHER} retry'}, [], env) is None


def test_missing_fcntl_import_is_safe_and_writes_fail_closed(tmp_path, monkeypatch):
    original = builtins.__import__
    def without_fcntl(name, *args, **kwargs):
        if name == 'fcntl':
            raise ImportError('native Windows has no fcntl')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_fcntl)
    spec = importlib.util.spec_from_file_location('consent_without_fcntl', HERE / 'consent.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = {'HERMES_HOME': str(tmp_path)}
    assert module.check_relay('kanban_comment', {'task_id': OWN}, ['chat-a'], env)
    assert module.check_relay('kanban_comment', {'task_id': OWN}, [], env)
    assert module.check_relay('terminal', {'command': 'hermes kanban list'}, ['chat-a'], env) is None
    with pytest.raises(RuntimeError, match='locking'):
        with module._transaction(env):
            pytest.fail('unlocked transaction entered')
