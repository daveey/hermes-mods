"""F3/F4 and N3: every operand and indirect argv producer is gated."""
import os
import subprocess

import pytest

from test_source_review_regressions import OWN, OTHER, board, consent, core, stop


@pytest.mark.parametrize('target', [OWN, '$(cat id)', '`cat id`'])
@pytest.mark.parametrize('verb', ['complete', 'done'])
def test_every_completion_operand(tmp_path, target, verb):
    db = board(tmp_path, '')
    command = f"hermes kanban {verb} {OTHER} {target} --result 'Deferred the tests; not verified.'"
    assert core.check_shell(command, OWN, lambda: [db])


@pytest.mark.parametrize('verb,flags', [
    ('edit', '--title narrowed'), ('edit', '--body narrowed'),
    ('complete', "--result 'Deferred the tests; not verified.'"),
    ('request-review', "--summary 'check 4 failed; waived'"),
])
@pytest.mark.parametrize('template', ['xargs -a ids -I@ hermes kanban {verb} @ {flags}',
                                      'xargs -a ids hermes kanban {verb} {flags}',
                                      'xargs -a ids -I@ sh -c "hermes kanban {verb} @ {flags}"'])
def test_xargs_unknown_own_target(tmp_path, verb, flags, template):
    db = board(tmp_path, 'DONE: check 4 requires >= 25% free disk.')
    assert core.check_shell(template.format(verb=verb, flags=flags), OWN, lambda: [db])


@pytest.mark.parametrize('command', [
    'xargs -a ids -I@ hermes kanban comment @ retry',
    'xargs -a ids hermes kanban comment',
    "printf '%s' t_12345678 | xargs -I@ hermes kanban comment @ retry",
])
def test_xargs_consent_stop(tmp_path, command):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    assert consent.check_relay('terminal', {'command': command}, ['chat-a'], env)
    args = {'calls': [{'name': 'terminal', 'arguments': {'command': command}}]}
    assert consent.check_relay('tool_call', args, ['chat-a'], env)


@pytest.mark.parametrize('verb,option', [('complete', '--summary'), ('complete', '--result'),
                                         ('request-review', '--summary')])
def test_dynamic_handoff_held(verb, option):
    assert core.check_shell(f'hermes kanban {verb} {OWN} {option} "$(cat s)"', OWN, lambda: [])


def test_literal_other_and_inert_controls(tmp_path):
    env = {'HERMES_HOME': str(tmp_path)}
    stop(env)
    assert core.check_shell(f'hermes kanban complete {OTHER} --result "all verified"', OWN, lambda: []) is None
    command = "printf '%s' 'xargs -I@ hermes kanban edit @ --title x'"
    assert core.check_shell(command, OWN, lambda: []) is None
    assert consent.check_relay('terminal', {'command': command}, ['chat-a'], env) is None
    assert core.check_shell('xargs hermes kanban show', OWN, lambda: []) is None


def test_real_xargs_appends_target(tmp_path):
    fake, capture = tmp_path / 'hermes', tmp_path / 'argv'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    fake.chmod(0o700)
    (tmp_path / 'ids').write_text(OWN + '\n')
    subprocess.run(['/bin/bash', '-c', 'xargs -a ids hermes kanban edit --title x'],
                   cwd=tmp_path, check=True,
                   env={'PATH': str(tmp_path) + os.pathsep + '/usr/bin:/bin', 'CAPTURE': str(capture)})
    assert capture.read_text().splitlines() == ['kanban', 'edit', '--title', 'x', OWN]
    assert core.check_shell('xargs -a ids hermes kanban edit --title x', OWN, lambda: [])
