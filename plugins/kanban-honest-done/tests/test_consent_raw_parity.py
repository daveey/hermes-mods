"""Consent retains its raw-string detector outside the inert grammar."""
import ast
from pathlib import Path
import re
import shlex
import subprocess

import pytest

from test_honest_done import core, plugin
from test_command_position import _isolated_env

consent = plugin.consent_mod
# Frozen main detector: deliberately independent of the implementation regex.
MAIN_WRITE_RE = re.compile(
    r"\b(?:kanban|hermes-hub)\b[^\n;|&]{0,80}?\b(?:comment|create|create-task|edit|unblock|move|supervisor\w*|"
    r"operator\w*|notify|send)\b", re.I)
PAYLOAD = 'hermes-hub request example tasks default/default/t_12345678 comment --arg text=hello'
Q = shlex.quote(PAYLOAD)
EXECUTED = [
    f'bash -c {Q}', f'sh -c {Q}', f'eval {Q}',
    f'env X=1 bash -c {Q}', f'nohup bash -c {Q}',
    'bash -c ' + shlex.quote('eval ' + Q),
    f'bash <<< {Q}', f'echo {Q} | bash',
    f"printf '%s\\n' {Q} | sh", f"echo {Q} | xargs -I{{}} sh -c '{{}}'",
    PAYLOAD.replace('hermes-hub', '$(echo hermes-hub)', 1),
    PAYLOAD.replace('hermes-hub', '`echo hermes-hub`', 1),
]
CONTROLS = [f'echo {Q}', f"printf '%s\\n' {Q}"]


@pytest.mark.parametrize('command', EXECUTED + [f'ssh somehost {Q}', PAYLOAD])
@pytest.mark.parametrize('field', ['command', 'input', 'data'])
def test_raw_main_refusals_preserved(command, field, tmp_path):
    assert MAIN_WRITE_RE.search(command)
    assert not core._shell_commands().within_inert_grammar(command)
    assert consent.shell_write_texts(command)
    env = {'HERMES_HOME': str(tmp_path)}
    consent.record('kanban_comment', {'task_id': 't_12345678'},
                   'BLOCKED: User denied', 'test-session', env=env, now=100)
    assert consent.check_relay('terminal', {field: command}, ['test-session'], env=env, now=101)


@pytest.mark.parametrize('command', EXECUTED + [PAYLOAD] + CONTROLS)
def test_real_bash_hub_marker(command, tmp_path):
    env = _isolated_env(tmp_path)
    fake = tmp_path / 'bin' / 'hermes-hub'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/hub-marker"\n')
    fake.chmod(0o755)
    result = subprocess.run(['bash', '-c', command], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    marker = Path(env['HOME']) / 'hub-marker'
    if command in CONTROLS:
        assert not marker.exists()
        assert core._shell_commands().within_inert_grammar(command)
        assert consent.shell_write_texts(command) == []
    else:
        assert marker.read_text().splitlines() == [PAYLOAD.split(' ', 1)[1]]
        assert consent.shell_write_texts(command)


def test_whole_test_corpus_preserves_main_consent_detector():
    # Every literal string in every plugin test, plus instantiated format strings.
    # Read source rather than importing test modules (imports can mutate fixtures).
    corpus = set(EXECUTED + CONTROLS + [PAYLOAD, f'ssh somehost {Q}'])
    for path in Path(__file__).parent.glob('test_*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                corpus.add(node.value)
                for payload in [PAYLOAD, 'hermes kanban comment t_12345678 hi']:
                    try:
                        corpus.add(node.value.format(cmd=payload, quoted=shlex.quote(payload)))
                    except (ValueError, KeyError, IndexError, AttributeError):
                        pass
    compared = 0
    for command in sorted(corpus):
        if not core._shell_commands().within_inert_grammar(command) and MAIN_WRITE_RE.search(command):
            compared += 1
            assert consent.shell_write_texts(command), repr(command)
    assert compared >= 140
    print(f'consent corpus: {len(corpus)} strings; {compared} outside-G main refusals')


def test_shell_fields_keep_main_whole_input_scope():
    args = {'command': 'hermes kanban comment t_87654321 hi',
            'data': f'bash -c {Q}'}
    texts = consent.write_texts('process_manage', args)
    assert 't_12345678' in '\n'.join(texts)
    assert 't_87654321' in '\n'.join(texts)
    assert consent.write_texts('terminal', {'command': CONTROLS[0], 'data': CONTROLS[1]})


def test_raw_match_keeps_targets_even_when_segment_found_another_write():
    command = "hermes kanban comment t_87654321 hi; " + f'bash -c {Q}'
    texts = consent.shell_write_texts(command)
    assert 't_12345678' in '\n'.join(texts)
    assert 't_87654321' in '\n'.join(texts)
