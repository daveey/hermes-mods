"""GNU env split-string grammar: real Bash/Git oracle, not shell shlex."""
import os
import shlex
import subprocess

import pytest
from tests_scoped_uncertainty import guard, _blocked, wt

CMD = r"launcher exec --unknown -- env -S 'git\_stash\_pop'"


def launcher(wt):
    bindir = wt.parent / 'bin'
    bindir.mkdir()
    path = bindir / 'launcher'
    path.write_text('#!/bin/sh\nwhile [ "$1" != -- ]; do shift; done\nshift\nexec "$@"\n')
    path.chmod(0o755)
    return dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'])


def gnu_env():
    p = subprocess.run(['env', '--version'], capture_output=True, text=True)
    if 'GNU coreutils' not in p.stdout:
        pytest.skip('requires actual GNU env')


def snapshot(wt):
    return ((wt / 'f.txt').read_bytes(), subprocess.check_output(
        ['git', 'rev-parse', 'refs/stash'], cwd=wt))


DOUBLE_COMMANDS = [
    prefix + 'env ' + flag + r'"git\_stash\_pop"'
    for prefix in ['', 'launcher exec --unknown -- ']
    for flag in ['-S ', '-S', '--split-string ', '--split-string=']
]
DOUBLE_COMMANDS += [
    shell + ' ' + shlex.quote(r'env -S "git\_stash\_pop"')
    for shell in ['bash -c', 'sh -c', 'eval']
]
DOUBLE_COMMANDS += [r'''env -S "git\_stash\_'pop'"''']

FOLLOWUP_COMMANDS = [
    'env ' + flags + " 'git\\_stash\\_pop'"
    for flags in ['-i -S', '-iS', '-vS', '-u UNUSED -S', '-C . -S',
                  '--unset=UNUSED -S', '--ignore-environment -S']
] + [
    r"env -S 'git\_stash\_pop${UNUSED}'",
    r"UNUSED= env -S 'git\_stash\_pop${UNUSED}'",
    r'''env -S "$(printf '%s' 'git\_stash\_pop')"''',
]
DOUBLE_COMMANDS += FOLLOWUP_COMMANDS
DOUBLE_COMMANDS += ['launcher exec --unknown -- ' + cmd for cmd in FOLLOWUP_COMMANDS
                    if cmd.startswith('env ')]


@pytest.mark.parametrize('cmd', [CMD, *DOUBLE_COMMANDS])
def test_escaped_separator_refusal_before_execution(wt, cmd):
    gnu_env()
    before = snapshot(wt)
    reason = guard.refuse_reason(cmd, str(wt))
    blocked = _blocked(cmd, wt)
    assert snapshot(wt) == before
    assert reason and blocked


@pytest.mark.parametrize('cmd', [CMD, *DOUBLE_COMMANDS])
def test_escaped_separator_independent_real_restore(wt, cmd):
    gnu_env()
    subprocess.run(['bash', '-c', cmd], cwd=wt, env=launcher(wt),
                   check=True, capture_output=True, timeout=10)
    assert (wt / 'f.txt').read_text() == 'stashed\n'
    assert subprocess.check_output(['git', 'stash', 'list'], cwd=wt) == b''


@pytest.mark.parametrize('text', [
    r'git\_stash\_pop', r'git\tstash\tpop', r'"git\_stash\_pop"',
    r"'git\_stash\_pop'", r'a\nb', r'a\fb', r'a\rb', r'a\vb',
    r'a\#b \$x', r'a\c ignored', 'a # ignored', 'a#b',
    r'''"a\"b" 'a\'b' 'a\\b' '' ""''',
    r'"a;b" "$(ignored)"',
])
def test_literal_split_matches_gnu_env(text):
    gnu_env()
    # printf is an external program: its NUL-delimited output preserves argv exactly.
    if '$(' in text:
        with pytest.raises(guard._ParseError):
            guard._env_split(text)
        return
    result = subprocess.run(['env', '-S', "printf '%s\\0' " + text],
                            check=True, capture_output=True)
    assert guard._env_split(text) == result.stdout.decode().split('\0')[:-1]


@pytest.mark.parametrize('flag', ['-S ', '-S', '--split-string ', '--split-string='])
@pytest.mark.parametrize('prefix', ['', 'command ', 'launcher exec --unknown -- '])
def test_option_forms_refused(wt, flag, prefix):
    cmd = prefix + 'env ' + flag + shlex.quote(r'git\_stash\_pop')
    assert guard.refuse_reason(cmd, str(wt))
    assert _blocked(cmd, wt)


@pytest.mark.parametrize('text', [
    r'printf "%s" "git\_stash\_pop"',
    r"printf '%s' 'git\_stash\_pop'",
    r'git\_push\_origin\_HEAD:fix/stash-topic',
    r'git\_push\_origin\_HEAD:stash-topic',
])
@pytest.mark.parametrize('double', [False, True])
@pytest.mark.parametrize('flags', ['-S', '-i -S', '-iS', '-vS', '-u UNUSED -S',
                                   '-C . -S', '--unset=UNUSED -S',
                                   '--ignore-environment -S'])
def test_safe_quoted_data_and_literal_refspecs(wt, text, double, flags):
    quoted = shlex.quote(text)
    if double:
        quoted = '"' + text.replace('"', '\\"') + '"'
    cmd = 'launcher exec --unknown -- env ' + flags + ' ' + quoted
    before = snapshot(wt)
    assert guard.refuse_reason(cmd, str(wt)) is None
    assert not _blocked(cmd, wt)
    assert snapshot(wt) == before


@pytest.mark.parametrize('argv, expected', [
    (['env', '-u', '-S', 'printf'], None),
    (['env', '-CuS', 'printf'], None),
    (['env', '--unset=-S', '-vSgit'], (2, 3)),
    (['env', '--', '-S', 'git'], None),
    (['env', 'printf', '-S', 'git'], None),
    (['env', 'NAME=value', '-S', 'git'], None),
    (['env', '--split-string='], (1, 15)),
])
def test_env_option_operand_boundaries(argv, expected):
    assert guard._env_split_spec(argv) == expected


@pytest.mark.parametrize('cmd', [
    r'''env -iS'git\_stash\_drop' ''',
    r'''env -u UNUSED --split-string='git\_stash\_clear' ''',
    r'''env -S"$(printf '%s' 'git\_stash\_drop')"''',
    r'''env --split-string="$(printf '%s' 'git\_stash\_clear')"''',
    r'''env -S 'git\_stash\_drop${UNUSED}' ''',
])
def test_mutation_refused_and_real_gnu_mutates(wt, cmd):
    gnu_env()
    before = snapshot(wt)
    assert guard.refuse_reason(cmd, str(wt))
    assert _blocked(cmd, wt)
    assert snapshot(wt) == before
    subprocess.run(['bash', '-c', cmd], cwd=wt, check=True,
                   capture_output=True, timeout=10)
    assert (wt / 'f.txt').read_bytes() == before[0]
    assert subprocess.check_output(['git', 'stash', 'list'], cwd=wt) == b''


def test_env_child_options_do_not_taint_caller(wt):
    cmd = "P=printf; env -iS 'true'; $P '%s' hello"
    assert guard.refuse_reason(cmd, str(wt)) is None
    assert not _blocked(cmd, wt)
