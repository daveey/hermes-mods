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


@pytest.mark.parametrize('suffix', [';', '; echo done', ' | cat', ' && echo done'])
@pytest.mark.parametrize('consumer', ['bash', 'env A=1 bash', 'cat', 'bash -', 'sh -', 'bash -e -'])
def test_heredoc_consumer_survives_separator(suffix, consumer):
    # A lone `-` ends options like `--`; with no script operand the shell reads stdin.
    command = f"{consumer} <<'EOF'{suffix}\nhermes kanban comment t_X hi\nEOF"
    expected = [] if consumer == 'cat' else [('comment', ['t_X', 'hi'])]
    assert hits(command) == expected


@pytest.mark.parametrize('prefix', [
    'bash /dev/null -c', 'bash -- /dev/null -c',
    'bash -o posix /dev/null -c', 'bash --rcfile /dev/null /dev/null -c',
    'bash -O extglob /dev/null -c', 'bash - /dev/null -c',
])
def test_shell_script_arguments_are_data(prefix):
    assert hits(prefix + " 'hermes kanban comment t_X hi'") == []


@pytest.mark.parametrize('prefix', ['bash -o posix -c', 'bash --rcfile /dev/null -c',
                                    'bash -O extglob -c', 'bash -lc',
                                    # -c takes the first operand after *all* options.
                                    'bash -c -e', 'bash -c --', 'bash -c -', 'bash -c -o posix',
                                    'bash -c +x', 'bash -c -O extglob', 'sh -c -e', 'bash -ec -x'])
def test_shell_options_before_command(prefix):
    assert hits(prefix + " 'hermes kanban comment t_X hi'") == [('comment', ['t_X', 'hi'])]


@pytest.mark.parametrize('consumer', ['bash /dev/null', 'bash -- /dev/null', 'cat',
                                      'bash - /dev/null', 'sh - /dev/null'])
def test_heredoc_with_script_operand_is_data(consumer):
    assert hits(f"{consumer} <<'EOF';\nhermes kanban comment t_X hi\nEOF") == []


# Every shape above, checked against the shells themselves: a fake `hermes` on PATH
# records whether the shell really ran the call, and the classifier must agree. No row
# uses a login shell (`bash -lc` stays a classifier-only row above): a login shell reads
# ~/.profile, which can put the host's real hermes ahead of the fake (review 5912225322).
_REAL_SHELL_SHAPES = [
    "bash <<'EOF';\n{cmd}\nEOF", "bash <<'EOF' | cat\n{cmd}\nEOF",
    "bash - <<'EOF'\n{cmd}\nEOF", "sh - <<'EOF';\n{cmd}\nEOF", "bash -e - <<'EOF'\n{cmd}\nEOF",
    "bash - /dev/null <<'EOF'\n{cmd}\nEOF", "sh - /dev/null <<'EOF';\n{cmd}\nEOF",
    "bash /dev/null <<'EOF'\n{cmd}\nEOF", "cat <<'EOF'; echo done\n{cmd}\nEOF",
    'bash -c {quoted}', 'bash -c -e {quoted}', 'bash -c -- {quoted}',
    'bash -c - {quoted}', 'bash -c -o posix {quoted}', 'bash -c +x {quoted}',
    'bash -c -O extglob {quoted}', 'bash -ec -x {quoted}', 'sh -c -e {quoted}',
    'bash -o posix -c {quoted}', 'bash /dev/null -c {quoted}', 'bash -- /dev/null -c {quoted}',
    'bash - /dev/null -c {quoted}', 'bash -o posix /dev/null -c {quoted}',
    # Review 5912225322 finding 1: both shells read `c` whatever its sign, bash reads `s`
    # whatever its sign. Finding 2: every o/O letter in a cluster consumes one name.
    'bash +c {quoted}', 'sh +c {quoted}', 'bash +ec {quoted}', 'bash +c -e {quoted}',
    "bash +s /dev/null <<'EOF'\n{cmd}\nEOF",
    'bash -coO posix extglob {quoted}', 'bash -coo posix errexit {quoted}',
    'sh -coo errexit nounset {quoted}', 'bash -oo posix errexit -c {quoted}',
]

_CMD = 'hermes kanban comment t_X hi'


def _shape(shape, cmd=_CMD):
    return shape.format(cmd=cmd, quoted=shlex.quote(cmd))


def _isolated_env(tmp_path):
    """Env whose shells resolve `hermes` to a fake that only records its argv.

    HOME, HERMES_HOME, BASH_ENV and ENV are isolated, and both shells must resolve the
    fake before any row runs, so a test can never exec the host's real hermes.
    """
    import os
    import subprocess
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'hermes'
    fake.write_text('#!/bin/sh\necho "$@" >> "$HERMES_FAKE_MARKER"\n')
    fake.chmod(0o755)
    home = tmp_path / 'home'
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in {'BASH_ENV', 'ENV', 'CDPATH'}}
    env.update(PATH=f'{bin_dir}{os.pathsep}{os.environ.get("PATH", "")}', HOME=str(home),
               HERMES_HOME=str(home / '.hermes'), HERMES_FAKE_MARKER=str(tmp_path / 'ran'))
    for shell in ('bash', 'sh'):
        found = subprocess.run([shell, '-c', 'command -v hermes'], env=env, capture_output=True,
                               text=True, timeout=30).stdout.strip()
        assert found == str(fake), f'{shell} resolves hermes to {found!r}, not the fake'
    return env


def _shell_ran(tmp_path, command):
    import shutil
    import subprocess
    if not (shutil.which('bash') and shutil.which('sh')):
        pytest.skip('needs bash and sh')
    env = _isolated_env(tmp_path)
    (tmp_path / 'script.sh').write_text('. /dev/stdin\n')  # a script that runs its input
    subprocess.run(['bash', '-c', command], cwd=tmp_path, env=env, timeout=30,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    marker = tmp_path / 'ran'
    return marker.exists() and 'kanban comment t_X hi' in marker.read_text()


@pytest.mark.parametrize('shape', _REAL_SHELL_SHAPES)
def test_classifier_agrees_with_real_shells(tmp_path, shape):
    command = _shape(shape)
    ran = _shell_ran(tmp_path, command)
    assert (hits(command) == [('comment', ['t_X', 'hi'])]) == ran, (command, ran)


def test_real_shell_rows_never_run_the_host_hermes(tmp_path):
    # The isolation itself: even a login shell, whose startup files may prepend
    # ~/.local/bin, must not reach anything but the fake.
    import subprocess
    env = _isolated_env(tmp_path)
    found = subprocess.run(['bash', '-lc', 'command -v hermes'], env=env, capture_output=True,
                           text=True, timeout=30).stdout.strip()
    assert found == str(tmp_path / 'bin' / 'hermes')


# Review 5912225322 finding 3: main refused each of these and the shells run each one. A
# command word that is not a known data command keeps main's rule (fail closed), so a
# wrapper outside any list cannot hide a call. `None` = run it with plain bash; a name =
# run it only where that tool exists; '' = classifier only (sudo, the real hermes_cli).
_WRAPPED_CALLS = [
    ('nice {cmd}', 'nice'), ('echo x | xargs -I{{}} {cmd}', 'xargs'), ('setsid {cmd}', 'setsid'),
    ('stdbuf -oL {cmd}', 'stdbuf'), ('function f {{ {cmd}; }}; f', None),
    (". /dev/stdin <<'EOF'\n{cmd}\nEOF", None), ('ionice -c3 {cmd}', 'ionice'),
    ('builtin command {cmd}', None), ("source /dev/stdin <<'EOF'\n{cmd}\nEOF", None),
    ('sudo -n {cmd}', ''), ('coproc {cmd}; wait', 'coproc'),
    ('python3 -X dev -m hermes_cli.main kanban comment t_X hi', ''),
]

# Printed text that reaches something that runs it. Fail closed beyond main too: main
# missed the quoted forms, since a quoted payload was one token to it.
_PRINTED_CALLS = [
    'echo {cmd} | bash', 'echo {quoted} | sh', "printf '%s\\n' {quoted} | bash",
    "cat <<'EOF' | bash\n{cmd}\nEOF", 'echo {cmd} | grep hermes | bash', 'echo {cmd} |\nbash',
    "bash -c 'source /dev/stdin' <<'EOF'\n{cmd}\nEOF", "bash ./script.sh <<'EOF'\n{cmd}\nEOF",
    '$(echo {cmd})', '`echo {cmd}`', 'env $(echo {cmd})', 'nice $(echo {cmd})',
    'eval {quoted}', 'eval "$(echo {cmd})"', 'bash -c "$(echo {cmd})"',
    'bash <(echo {cmd})', 'source <(echo {cmd})', '(echo {cmd}) | bash',
    '{{ echo {cmd}; }} | bash', 'bash <<< {quoted}', 'nice bash -c {quoted}',
    "nice bash <<'EOF'\n{cmd}\nEOF", "sudo -n bash <<'EOF'\n{cmd}\nEOF",
]


def _runnable(tool):
    import shutil
    import subprocess
    if tool == '':
        return False
    if tool == 'coproc':
        out = subprocess.run(['bash', '-c', 'echo "${BASH_VERSINFO[0]}"'], capture_output=True, text=True)
        return out.stdout.strip().isdigit() and int(out.stdout) >= 4
    return tool is None or shutil.which(tool) is not None


@pytest.mark.parametrize('shape,tool', _WRAPPED_CALLS + [(s, None if 'sudo' not in s else '') for s in _PRINTED_CALLS])
def test_main_parity_calls_are_refused(tmp_path, shape, tool):
    command = _shape(shape)
    assert ('comment', ['t_X', 'hi']) in hits(command), command
    assert plugin.consent_mod.write_texts('terminal', {'command': command}) != []
    if not _runnable(tool):
        return
    # The row is a real call: the shell runs it (the classifier must never be laxer).
    if not _shell_ran(tmp_path, command):
        pytest.skip(f'this host does not run {command!r}; the classifier still refuses it')


# The card's own bug must stay fixed: printed text that nothing runs is data.
_STILL_DATA = [
    'echo {cmd}', 'echo {cmd} > notes.txt', 'echo {cmd} | grep kanban', 'echo {cmd} | cat',
    "cat <<'EOF' | grep hermes\n{cmd}\nEOF", 'printf %s {quoted}', 'grep -r {quoted} .',
    'cat <<< {quoted}', 'echo "$(echo {cmd})"', "bash -c cat <<'EOF'\n{cmd}\nEOF",
    "bash /dev/null <<'EOF'\n{cmd}\nEOF", 'x=$(echo {cmd})', 'echo {cmd} || echo done',
    'echo {cmd} && echo done', "cat <<'EOF' > notes.txt\n{cmd}\nEOF",
]


@pytest.mark.parametrize('shape', _STILL_DATA)
def test_printed_text_nothing_runs_is_data(tmp_path, shape):
    command = _shape(shape)
    assert hits(command) == [], command
    assert plugin.consent_mod.write_texts('terminal', {'command': command}) == []
    assert not _shell_ran(tmp_path, command), command


def test_consent_hub_writes_behind_wrappers():
    hub = 'hermes-hub kanban comment t_X hi'
    for command in (hub, 'nice ' + hub, 'sudo -n ' + hub, 'echo x | xargs ' + hub):
        assert plugin.consent_mod.write_texts('terminal', {'command': command}) != [], command
    for command in ('echo ' + hub, 'grep ' + shlex.quote(hub) + ' notes', 'printf %s ' + shlex.quote(hub)):
        assert plugin.consent_mod.write_texts('terminal', {'command': command}) == [], command


@pytest.mark.parametrize('wrap', ['{cmd}', 'env A=1 {cmd}', 'timeout 30 {cmd}',
                                 'echo $({cmd})', 'bash -c {quoted}',
                                 "bash <<'EOF';\n{cmd}\nEOF",
                                 "bash <<'EOF'; echo done\n{cmd}\nEOF",
                                 "bash <<'EOF' | cat\n{cmd}\nEOF",
                                 "bash - <<'EOF'\n{cmd}\nEOF",
                                 "sh - <<'EOF';\n{cmd}\nEOF",
                                 'bash -c -e {quoted}', 'bash -c -- {quoted}',
                                 'bash -c -o posix {quoted}', 'bash -c +x {quoted}',
                                 'sh -c -e {quoted}',
                                 # Review 5912225322 findings 1-3, through the real hook.
                                 'bash +c {quoted}', 'sh +c {quoted}',
                                 "bash +s /dev/null <<'EOF'\n{cmd}\nEOF",
                                 'bash -coO posix extglob {quoted}', 'sh -coo errexit nounset {quoted}',
                                 'nice {cmd}', 'echo x | xargs -I{{}} {cmd}', 'setsid {cmd}',
                                 'stdbuf -oL {cmd}', 'function f {{ {cmd}; }}; f',
                                 ". /dev/stdin <<'EOF'\n{cmd}\nEOF",
                                 'echo {cmd} | bash', "cat <<'EOF' | bash\n{cmd}\nEOF",
                                 '$(echo {cmd})'])
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
    assert plugin._pre_tool_call('terminal', {
        'command': 'bash /dev/null -c ' + shlex.quote(cmd)}) is None
    # The card's original bug: unquoted echo of the call is text, not a call.
    assert plugin._pre_tool_call('terminal', {'command': 'echo ' + cmd}) is None
