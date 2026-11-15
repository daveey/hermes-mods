"""Command/data controls for every consumer of the shared shell classifier."""
import shlex

import pytest

from test_honest_done import core, plugin, OWN


def hits(command):
    return [hit for seg in core._segments(command) if (hit := core._kanban_verb(seg))]


@pytest.mark.parametrize('verb', ['comment', 'create', 'edit', 'complete'])
@pytest.mark.parametrize('wrap', [
    'echo {cmd}',
    "printf '%s\\n' {quoted}",
    'grep {quoted} f',
    'git commit -m {quoted}',
    "cat <<'EOF'\n{cmd}\nEOF",
    'cat <<EOF\n{cmd}\nEOF',
    "cat <<-'EOF'\n\t{cmd}\n\tEOF",
    'echo "first\n{cmd}\nlast"',
    "echo 'literal ;' {cmd}",
    'cat <<"EOF"\n{cmd}\nEOF\necho done',
])
def test_data_is_not_a_write(verb, wrap):
    cmd = f'hermes kanban {verb} t_X "IF FAIL x: y"'
    command = wrap.format(cmd=cmd, quoted=shlex.quote(cmd))
    assert hits(command) == []
    assert plugin.consent_mod.write_texts('terminal', {'command': command}) == []
    assert list(plugin.comment_branches.writes('terminal', {'command': command}, core)) == []
    assert core.check_shell(command, OWN, lambda: []) is None
    assert core.check_shell_acceptance(command, 'test', lambda: []) is None
    assert plugin.classify_create('terminal', {'command': command}) is None
    # A real call after the same data must remain visible.
    assert hits(command + '\n' + cmd) == [(verb, ['t_X', 'IF FAIL x: y'])]


@pytest.mark.parametrize('prefix', [
    '', 'A=1 ', 'env A=1 ', 'env -i A=1 ', 'command ', 'exec ', 'nohup ',
    'timeout 30 ', 'time ', 'A=1 env -i B=2 command timeout 30 ',
    'cd x && ', '/usr/bin/env A=1 ',
])
def test_command_prefixes(prefix):
    assert hits(prefix + 'hermes kanban --board test comment t_X hi') == [('comment', ['t_X', 'hi'])]
    if '&&' not in prefix:
        assert hits('echo ' + prefix + 'hermes kanban comment t_X hi') == []


@pytest.mark.parametrize('command', [
    '(hermes kanban comment t_X hi)',
    'echo $(hermes kanban comment t_X hi)',
    'x=$(hermes kanban comment t_X hi)',
    'python -m hermes_cli.main kanban comment t_X hi',
    '/usr/bin/python3 -m hermes_cli.main kanban comment t_X hi',
])
def test_existing_executable_forms(command):
    assert hits(command) == [('comment', ['t_X', 'hi'])]
    assert hits('printf %s ' + shlex.quote(command)) == []


@pytest.mark.parametrize('command', [
    'bash -c "hermes kanban comment t_X hi"',
    'echo "$(hermes kanban comment t_X hi)"',
    'echo `hermes kanban comment t_X hi`',
    "bash <<'EOF'\nhermes kanban comment t_X hi\nEOF",
    'cat <<EOF\n$(hermes kanban comment t_X hi)\nEOF',
])
def test_explicit_nested_execution(command):
    assert hits(command) == [('comment', ['t_X', 'hi'])]
    assert hits('printf %s ' + shlex.quote(command)) == []


@pytest.mark.parametrize('command', [
    'if true; then hermes kanban comment t_X hi; fi',
    'if hermes kanban comment t_X hi; then :; fi',
    'if false; then :; else hermes kanban comment t_X hi; fi',
    'for x in a; do hermes kanban comment t_X hi; done',
    'while hermes kanban comment t_X hi; do :; done',
    '>/dev/null hermes kanban comment t_X hi',
    '2>&1 hermes kanban comment t_X hi',
    '> out hermes kanban comment t_X hi',
    '! hermes kanban comment t_X hi',
    '{ hermes kanban comment t_X hi; }',
    'git commit -m "$(cat <<\'EOF\'\ndon\'t ( parse this\nEOF\n)" && hermes kanban comment t_X hi',
])
def test_reserved_words_and_nested_heredoc(command):
    assert hits(command) == [('comment', ['t_X', 'hi'])]


@pytest.mark.parametrize('command', [
    "cat <<'EOF'\ndon't ) hermes kanban comment t_X hi\nEOF",
    'git commit -m "$(cat <<\'EOF\'\nhermes kanban comment t_X hi\ndon\'t (\nEOF\n)"',
    "bash -c cat <<'EOF'\nhermes kanban comment t_X hi\nEOF",
    "bash --rcfile 'hermes kanban comment t_X hi'",
    "echo '$(hermes kanban comment t_X hi)'",
    "command -v hermes kanban comment t_X hi",
])
def test_nested_data(command):
    assert hits(command) == []


@pytest.mark.parametrize('wrap', ['{cmd}', 'env A=1 {cmd}', 'timeout 30 {cmd}',
                                 'echo $({cmd})', 'bash -c {quoted}'])
def test_comment_hook_still_refuses_real_calls(tmp_path, monkeypatch, wrap):
    from test_honest_done import make_board
    db = make_board(tmp_path, tasks=[(OWN, 'test', 'running')])
    monkeypatch.setattr(plugin.comment_branches, 'target_paths', lambda board: [db])
    monkeypatch.setattr(plugin, 'classify_consent', lambda *a, **kw: None)
    monkeypatch.setattr(plugin, '_in_worker', lambda: False)
    cmd = f'hermes kanban comment {OWN} "IF FAIL x: y"'
    command = wrap.format(cmd=cmd, quoted=shlex.quote(cmd))
    assert plugin._pre_tool_call('terminal', {'command': command})['action'] == 'block'
    assert plugin._pre_tool_call('terminal', {'command': 'echo ' + shlex.quote(command)}) is None
