"""Command/data controls for every consumer of the shared shell classifier."""
import re
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

    'echo "first\n{cmd}\nlast"',
    "echo 'literal ;' {cmd}",
    'cat <<"EOF"\n{cmd}\nEOF\necho done',
    # A definition that is only text.
    "echo 'echo() {{ \"$@\"; }}' {cmd}",
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
    assert ('comment', ['t_X', 'hi']) in hits(command)
    assert hits('printf %s ' + shlex.quote(command)) == []


@pytest.mark.parametrize('command', [
    'bash -c "hermes kanban comment t_X hi"',
    'echo "$(hermes kanban comment t_X hi)"',
    'echo `hermes kanban comment t_X hi`',
    "bash <<'EOF'\nhermes kanban comment t_X hi\nEOF",
    'cat <<EOF\n$(hermes kanban comment t_X hi)\nEOF',
])
def test_explicit_nested_execution(command):
    assert ('comment', ['t_X', 'hi']) in hits(command)
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
    assert ('comment', ['t_X', 'hi']) in hits(command)


@pytest.mark.parametrize('command', [
    "cat <<'EOF'\ndon't ) hermes kanban comment t_X hi\nEOF",
    'git commit -m "$(cat <<\'EOF\'\nhermes kanban comment t_X hi\ndon\'t (\nEOF\n)"',
    "bash -c cat <<'EOF'\nhermes kanban comment t_X hi\nEOF",
    "bash --rcfile 'hermes kanban comment t_X hi'",
    "echo '$(hermes kanban comment t_X hi)'",
    "command -v hermes kanban comment t_X hi",
])
def test_nested_data(command):
    # Round 5 deliberately revokes exemptions outside G, even for inert shells.
    if command.startswith(('cat ', "echo '")):
        assert hits(command) == []
    else:
        assert hits(command)


@pytest.mark.parametrize('suffix', [';', '; echo done', ' | cat', ' && echo done'])
@pytest.mark.parametrize('consumer', ['bash', 'env A=1 bash', 'cat', 'bash -', 'sh -', 'bash -e -'])
def test_heredoc_consumer_survives_separator(suffix, consumer):
    # A lone `-` ends options like `--`; with no script operand the shell reads stdin.
    command = f"{consumer} <<'EOF'{suffix}\nhermes kanban comment t_X hi\nEOF"
    expected = [] if consumer == 'cat' else [('comment', ['t_X', 'hi'])]
    assert (not hits(command)) if not expected else expected[0] in hits(command)


@pytest.mark.parametrize('prefix', [
    'bash /dev/null -c', 'bash -- /dev/null -c',
    'bash -o posix /dev/null -c', 'bash --rcfile /dev/null /dev/null -c',
    'bash -O extglob /dev/null -c', 'bash - /dev/null -c',
])
def test_shell_script_arguments_are_data(prefix):
    assert hits(prefix + " 'hermes kanban comment t_X hi'")


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
    command = f"{consumer} <<'EOF';\nhermes kanban comment t_X hi\nEOF"
    assert bool(hits(command)) == (consumer != 'cat')


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

    Built from an allowlist, not from the host environment, so no BASH_ENV, ENV,
    BASH_FUNC_*, SHELLOPTS, PS4 or other variable can run startup code (review
    5914765330 finding 3). Both shells must resolve the fake before any row runs, so a
    test can never exec the host's real hermes. Every shell these tests start, the
    availability probes included, gets this env.
    """
    import os
    import subprocess
    bin_dir = tmp_path / 'bin'
    fake = bin_dir / 'hermes'
    home = tmp_path / 'home'
    if not fake.exists():
        bin_dir.mkdir(parents=True, exist_ok=True)
        # `command` skips functions: a row may export one named echo or printf.
        fake.write_text('#!/bin/sh\ncommand printf \'%s\\n\' "$*" >> "$HERMES_FAKE_MARKER"\n')
        fake.chmod(0o755)
        home.mkdir(exist_ok=True)
    env = {'PATH': f'{bin_dir}{os.pathsep}{os.environ.get("PATH") or os.defpath}', 'HOME': str(home),
           'HERMES_HOME': str(home / '.hermes'), 'HERMES_FAKE_MARKER': str(tmp_path / 'ran'),
           'LC_ALL': 'C'}
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
    run = tmp_path / 'run.sh'  # a program that runs its arguments, whatever it is called
    run.write_text('#!/bin/sh\nexec "$@"\n')
    run.chmod(0o755)
    subprocess.run(['bash', '-c', command], cwd=tmp_path, env=env, timeout=30,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    marker = tmp_path / 'ran'
    return marker.exists() and 'kanban comment t_X hi' in marker.read_text()


@pytest.mark.parametrize('shape', _REAL_SHELL_SHAPES)
def test_classifier_agrees_with_real_shells(tmp_path, shape):
    command = _shape(shape)
    ran = _shell_ran(tmp_path, command)
    # G narrows exemptions: real calls must be detected, as must non-G mentions.
    if ran or not command.startswith('cat '):
        assert ('comment', ['t_X', 'hi']) in hits(command), (command, ran)
    else:
        assert not hits(command)


def test_real_shell_rows_never_use_a_login_shell():
    # A login shell reads /etc/profile and ~/.profile (macOS path_helper reorders PATH),
    # which can put the host's real hermes ahead of the fake (review 5912225322). No row
    # that runs may use one; `bash -lc` stays a classifier-only row above.
    rows = (_REAL_SHELL_SHAPES + [s for s, _ in _WRAPPED_CALLS + _REDEFINED_CALLS] + _PRINTED_CALLS
            + _CONTINUATION_CALLS + _STILL_DATA + _UNUSED_REDEFINITIONS)
    login = re.compile(r'\b(?:bash|sh|dash|zsh|ksh)\s+(?:[-+][A-Za-z]+\s+)*(?:-[A-Za-z]*l[A-Za-z]*|--login)\b')
    assert [row for row in rows if login.search(row)] == []


def test_every_test_shell_gets_the_isolated_env():
    # Review 5914765330 finding 3: the coproc probe ran bash with the host environment
    # (BASH_ENV and the rest) before the isolation existed. Every subprocess call in this
    # file must pass env=, and _isolated_env must build that env from an allowlist.
    import ast
    import inspect
    from pathlib import Path
    tree = ast.parse(Path(__file__).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr in {'run', 'Popen', 'call',
                                                                              'check_call', 'check_output'}
             and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess']
    assert calls and [node.lineno for node in calls if not any(k.arg == 'env' for k in node.keywords)] == []
    assert 'os.environ.items()' not in inspect.getsource(_isolated_env)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    allowed_keys = {'PATH', 'HOME', 'HERMES_HOME', 'HERMES_FAKE_MARKER', 'LC_ALL'}
    for name, function in functions.items():
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or node not in calls:
                continue
            value = next(k.value for k in node.keywords if k.arg == 'env')
            assert isinstance(value, ast.Name)
            if name == '_runnable':
                assert value.id == 'env'  # its callers below must pass the factory
                continue
            definitions = [a for a in ast.walk(function) if isinstance(a, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == value.id for t in a.targets)]
            assert len(definitions) == 1
            source = definitions[0].value
            if name == '_isolated_env':
                assert isinstance(source, ast.Dict)
                assert all(isinstance(k, ast.Constant) for k in source.keys)
                assert {k.value for k in source.keys if isinstance(k, ast.Constant)} == allowed_keys
            else:
                assert isinstance(source, ast.Call) and isinstance(source.func, ast.Name)
                assert source.func.id == '_isolated_env'
            mutations = [a for a in ast.walk(function) if isinstance(a, ast.Assign)
                         for t in a.targets if isinstance(t, ast.Subscript)
                         and isinstance(t.value, ast.Name) and t.value.id == value.id]
            assert len(mutations) == (1 if value.id == 'positive_env' else 0)
            for mutation in mutations:
                target = mutation.targets[0]
                assert isinstance(target, ast.Subscript)
                assert isinstance(target.slice, ast.Constant) and target.slice.value == 'BASH_ENV'
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_runnable':
            value = node.args[1]
            assert ((isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                     and value.func.id == '_isolated_env') or
                    (isinstance(value, ast.Name) and value.id == 'env'))


def test_isolated_shells_run_no_host_startup_code(tmp_path, monkeypatch):
    # The reviewer's repro as a test: startup code in the host environment must not run in
    # any shell these tests start, availability probes included.
    import subprocess
    marker = tmp_path / 'startup-ran'
    startup = tmp_path / 'startup.sh'
    ambient = tmp_path / 'ambient-ran'
    monkeypatch.setenv('BASH_FUNC_printf%%',
                      f'() {{ command printf AMBIENT >> {shlex.quote(str(ambient))}; command printf "$@"; }}')
    startup.write_text(f'printf STARTUP_RAN >> {shlex.quote(str(marker))}\n')
    for name, value in [('BASH_ENV', str(startup)), ('ENV', str(startup)),
                        ('BASH_FUNC_echo%%', f'() {{ printf STARTUP_RAN >> {shlex.quote(str(marker))}; }}'),
                        ('PS4', f'$(printf STARTUP_RAN >> {shlex.quote(str(marker))})'),
                        ('SHELLOPTS', 'xtrace')]:
        monkeypatch.setenv(name, value)
    # Positive control: add ONLY the deliberate startup fixture to the allowlist.
    positive_env = _isolated_env(tmp_path / 'positive')
    positive_env['BASH_ENV'] = str(startup)
    subprocess.run(['bash', '-c', 'true'], env=positive_env, timeout=30,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert marker.exists()
    marker.unlink()
    rows = tmp_path / 'rows'
    env = _isolated_env(rows)
    for tool in [*_PROBES, None, 'nice']:
        _runnable(tool, env)
    _shell_ran(rows, 'echo x; true')
    assert not marker.exists()
    assert not ambient.exists()


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
# Rows a given shell cannot run: sudo is classifier only; bash 3.2 (macOS /bin/bash)
# sources nothing from a process substitution.
_PRINTED_TOOLS = {"sudo -n bash <<'EOF'\n{cmd}\nEOF": '', 'source <(echo {cmd})': 'bash4'}

# Review 5914765330 finding 1: a data command the input redefines is not data. Main
# refused each row (a `hermes kanban` pair in any argv) and the shells run each one.
_REDEFINED_CALLS = [
    ('echo() {{ "$@"; }}; echo {cmd}', None), ('cat() {{ "$@"; }}; cat {cmd}', None),
    ('function echo {{ "$@"; }}; echo {cmd}', None), ('function cat {{ "$@"; }}; cat {cmd}', None),
    ('function grep() {{ "$@"; }}; grep {cmd}', None), ('printf () {{ "$@"; }}; printf {cmd}', None),
    # Names split by a backslash-newline, in the definition and in the call.
    ('ec\\\nho() {{ "$@"; }}; echo {cmd}', None), ('echo() {{ "$@"; }}; ec\\\nho {cmd}', None),
    ('function c\\\nat {{ "$@"; }}; cat {cmd}', None),
    # Definitions in code the scanner reads: eval text, a -c script, a heredoc to a shell.
    ("eval 'echo() {{ \"$@\"; }}'; echo {cmd}", None), ("bash -c 'echo() {{ \"$@\"; }}; echo {cmd}'", None),
    ("bash <<'EOF'\necho() {{ \"$@\"; }}\necho {cmd}\nEOF", None),
    ('echo() {{ eval "$*"; }}; echo {quoted}', None),
    # Input the redefinition reads: heredoc, here-string, pipeline stages.
    ("cat() {{ bash; }}; cat <<'EOF'\n{cmd}\nEOF", None), ('cat() {{ bash; }}; cat <<< {quoted}', None),
    ('grep() {{ bash; }}; echo {cmd} | grep x', None), ("rg() {{ bash; }}; cat <<'EOF' | rg x\n{cmd}\nEOF", None),
    # Other ways the input changes what a data command name runs.
    # run.sh is the test's own `exec "$@"` (a busybox env would act on its argv[0]).
    ('shopt -s expand_aliases\nalias echo=env\necho {cmd}', None), ('hash -p "$PWD/run.sh" cat; cat {cmd}', None),
    ('mkdir -p d && ln -s "$PWD/run.sh" d/cat && PATH="$PWD/d:$PATH" cat {cmd}', None),
    ('mkdir -p d && ln -s "$PWD/run.sh" d/cat && ./d/cat {cmd}', None),
    ("env 'BASH_FUNC_echo%%=() {{ \"$@\"; }}' bash -c 'echo {cmd}'", 'bash_func'),
]

# Review 5914765330 finding 2: an operator split by a backslash-newline is the operator.
_CONTINUATION_CALLS = [
    '$\\\n(echo {cmd})', '$\\\n\\\n(echo {cmd})', 'nice $\\\n(echo {cmd})', 'echo "$\\\n({cmd})"',
    'bash <\\\n(echo {cmd})', "bash <\\\n<'EOF'\n{cmd}\nEOF", "bash <\\\n<-'EOF'\n{cmd}\nEOF",
    'bash <\\\n<< {quoted}', 'echo {cmd} \\\n| bash', "cat <<EOF > notes.txt\n$\\\n({cmd})\nEOF",
    '# a comment does not continue \\\necho {cmd} | bash',
]

# Fail closed: a redefinition counts even where its body ignores the arguments.
_UNUSED_REDEFINITIONS = ['echo() {{ :; }}; echo {cmd}', 'alias cat=true; cat {cmd}',
                         'PATH=/usr/bin:/bin; echo {cmd} | grep x']


# Probes for rows that need more than plain bash, asked of the isolated shells only.
_PROBES = {
    'coproc': '[ "${BASH_VERSINFO[0]}" -ge 4 ]',  # coproc came with bash 4
    'bash4': '[ "${BASH_VERSINFO[0]}" -ge 4 ]',
    'bash_func': "[ \"$(env 'BASH_FUNC_f%%=() { echo ok; }' bash -c f 2>/dev/null)\" = ok ]",
}


def _runnable(tool, env):
    import shutil
    import subprocess
    if tool == '':
        return False
    if tool in _PROBES:
        return subprocess.run(['bash', '-c', _PROBES[tool]], env=env, timeout=30, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    return tool is None or shutil.which(tool, path=env['PATH']) is not None


@pytest.mark.parametrize('shape,tool', _WRAPPED_CALLS + _REDEFINED_CALLS
                         + [(s, _PRINTED_TOOLS.get(s)) for s in _PRINTED_CALLS]
                         + [(s, None) for s in _CONTINUATION_CALLS])
def test_main_parity_calls_are_refused(tmp_path, shape, tool):
    command = _shape(shape)
    assert ('comment', ['t_X', 'hi']) in hits(command), command
    assert plugin.consent_mod.write_texts('terminal', {'command': command}) != []
    if tool == '':
        return  # classifier only: sudo and the real hermes_cli never run here
    if not _runnable(tool, _isolated_env(tmp_path)):
        pytest.skip(f'{tool} is not available here; the classifier still refuses {command!r}')
    # The row is a real call: the shell runs it (the classifier must never be laxer).
    assert _shell_ran(tmp_path, command), command


@pytest.mark.parametrize('shape', _UNUSED_REDEFINITIONS)
def test_redefinitions_fail_closed(shape):
    command = _shape(shape)
    assert ('comment', ['t_X', 'hi']) in hits(command), command


# The card's own bug must stay fixed: printed text that nothing runs is data.
_STILL_DATA = [
    'echo {cmd}', 'echo {cmd} | grep kanban', 'echo {cmd} | cat',
    "cat <<'EOF' | grep hermes\n{cmd}\nEOF", 'printf %s {quoted}', 'grep -r {quoted} .',
    'echo {cmd} || echo done', 'echo {cmd} && echo done',
    "echo 'echo() {{ \"$@\"; }}'; echo {cmd}",
    'grep -c "alias echo=env" notes.txt; echo {cmd}',
    "echo '$\\\n(echo {cmd})'",
]

# Output writes can change executable state, even across separate invocations.
_OUTPUT_DATA = [
    'echo {cmd} > notes.txt', "cat <<'EOF' > notes.txt\n{cmd}\nEOF",
    "cat <<'EOF' > defs.sh\necho() {{ \"$@\"; }}\nEOF\necho {cmd}",
    "cat <<'EOF' > notes.txt\n$\\\n(echo {cmd})\nEOF",
]

# Explicit contract narrowing, not deletion of controls. These remain inert in
# real shells, but their syntax is outside G and therefore gets no exemption.
_FORMER_DATA = [
    'cat <<< {quoted}', 'echo "$(echo {cmd})"', "bash -c cat <<'EOF'\n{cmd}\nEOF",
    "bash /dev/null <<'EOF'\n{cmd}\nEOF", 'x=$(echo {cmd})',
    'myecho() {{ "$@"; }}; echo {cmd}', 'function catalog {{ "$@"; }}; cat {quoted}',
    "bash -c 'myecho() {{ \"$@\"; }}; echo {cmd}'",
    '/bin/echo {cmd} | /usr/bin/grep kanban',
    "cat <<-'EOF'\n\t{cmd}\n\tEOF",
]


@pytest.mark.parametrize('shape', _FORMER_DATA + _OUTPUT_DATA)
def test_former_data_outside_g_is_detected(tmp_path, shape):
    command = _shape(shape)
    assert not core._shell_commands().within_inert_grammar(command)
    assert hits(command)
    assert plugin.consent_mod.write_texts('terminal', {'command': command})
    assert not _shell_ran(tmp_path, command)


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
                                 '$(echo {cmd})',
                                 # Review 5914765330 findings 1-2, through the real hook.
                                 'echo() {{ "$@"; }}; echo {cmd}', 'function cat {{ "$@"; }}; cat {cmd}',
                                 'ec\\\nho() {{ "$@"; }}; echo {cmd}', '$\\\n(echo {cmd})'])
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
        'command': 'bash /dev/null -c ' + shlex.quote(cmd)})['action'] == 'block'
    # The card's original bug: unquoted echo of the call is text, not a call.
    assert plugin._pre_tool_call('terminal', {'command': 'echo ' + cmd}) is None
