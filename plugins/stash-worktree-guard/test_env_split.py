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


def test_escaped_separator_refusal_before_execution(wt):
    gnu_env()
    before = snapshot(wt)
    reason = guard.refuse_reason(CMD, str(wt))
    blocked = _blocked(CMD, wt)
    assert snapshot(wt) == before
    assert reason and blocked


def test_escaped_separator_independent_real_restore(wt):
    gnu_env()
    subprocess.run(['bash', '-c', CMD], cwd=wt, env=launcher(wt),
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
def test_safe_quoted_data_and_literal_refspecs(wt, text):
    cmd = 'launcher exec --unknown -- env -S ' + shlex.quote(text)
    before = snapshot(wt)
    assert guard.refuse_reason(cmd, str(wt)) is None
    assert not _blocked(cmd, wt)
    assert snapshot(wt) == before

