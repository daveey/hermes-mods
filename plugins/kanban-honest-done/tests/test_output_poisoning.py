"""Input-written executables are not ambient PATH state."""
import shlex
import shutil
import subprocess

import pytest

from test_command_position import hits, _isolated_env
from test_honest_done import core, plugin, OWN, make_board


@pytest.mark.parametrize('writer', ['echo', 'printf', 'heredoc'])
@pytest.mark.parametrize('op', ['>', '>>'])
def test_output_poisoning_real_shell_and_hook(tmp_path, monkeypatch, writer, op):
    env = _isolated_env(tmp_path)
    target = tmp_path / 'bin' / ('grep' if writer == 'heredoc' else 'cat')
    if op == '>':
        shutil.copyfile(shutil.which(target.name), target)
    else:
        target.write_text('#!/bin/sh\n# originally harmless\n')
    target.chmod(0o755)
    cmd = f'hermes kanban comment {OWN} "IF FAIL x: y"'
    if writer == 'echo':
        command = f'echo {cmd} {op} bin/{target.name}; {target.name}'
    elif writer == 'printf':
        command = f"printf '%s\\n' {shlex.quote(cmd)} {op} bin/{target.name}; {target.name}"
    else:
        command = f"cat <<'EOF' {op} bin/{target.name}\n{cmd}\nEOF\n{target.name}"
    run = subprocess.run(['bash', '-c', command], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert run.stderr == ''
    assert (tmp_path / 'ran').read_text() == f'kanban comment {OWN} IF FAIL x: y\n'
    # Execute first: RED proves a real fake-Hermes call, not just disagreement
    # with a new membership API or a broken fixture setup.
    assert hits(command)
    assert not core._shell_commands().within_inert_grammar(command)
    assert plugin.consent_mod.write_texts('terminal', {'command': command})
    db = make_board(tmp_path, tasks=[(OWN, 'test', 'running')])
    monkeypatch.setattr(plugin.comment_branches, 'target_paths', lambda board: [db])
    monkeypatch.setattr(plugin, 'classify_consent', lambda *a, **kw: None)
    monkeypatch.setattr(plugin, '_in_worker', lambda: False)
    assert plugin._pre_tool_call('terminal', {'command': command})['action'] == 'block'


@pytest.mark.parametrize('op', ['>', '>>'])
@pytest.mark.parametrize('suffix', ['', '; cat', ' && grep x', ' || head', ' | tail', '\nwc', '; cd x'])
def test_output_anywhere_revokes_exemption(op, suffix):
    command = f'echo hermes kanban comment t_X hi {op} notes' + suffix
    assert hits(command)
    assert not core._shell_commands().within_inert_grammar(command)
    assert plugin.consent_mod.write_texts('terminal', {'command': command})


@pytest.mark.parametrize('command', [
    'cat; echo hermes kanban comment t_X hi > notes',
    'cat | echo hermes kanban comment t_X hi > notes',
    'echo hi > notes; grep "hermes kanban comment t_X hi" f',
    'cat > notes <<\'EOF\'\nhermes kanban comment t_X hi\nEOF',
])
def test_output_order_and_pipeline_membership(command):
    assert hits(command)
    assert not core._shell_commands().within_inert_grammar(command)


@pytest.mark.parametrize('command', [
    "echo 'hermes kanban comment t_X hi > notes'",
    "printf '%s\\n' 'hermes kanban comment t_X hi >> notes'",
    "cat <<'EOF'\nhermes kanban comment t_X hi > notes\nEOF",
    'grep "hermes kanban comment t_X hi" < notes',
])
def test_output_characters_in_data_are_not_operators(command):
    assert not hits(command)
    assert core._shell_commands().within_inert_grammar(command)
