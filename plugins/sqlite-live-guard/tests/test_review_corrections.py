"""Classification-only corrections from independent review; never run commands."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('review_correction_guard', Path(__file__).resolve().parents[1] / 'guard.py')
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / 'user'
    home = user / '.hermes'
    (home / 'kanban' / 'boards').mkdir(parents=True)
    (home / 'cache').mkdir()
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('HERMES_HOME', str(home))
    return home


@pytest.mark.parametrize('option', ['-t /unrelated', '--target-directory /unrelated', '--target-directory=/unrelated', '-t/unrelated', '-ft /unrelated', '-ft/unrelated'])
@pytest.mark.parametrize('remote', [False, True])
def test_mv_target_directory_ancestor(home, option, remote):
    command = f'mv {option} ~/.hermes'
    if remote:
        command = f'ssh example.com "{command}"'
    assert guard.check_shell(command)


@pytest.mark.parametrize('command,relative_cwd', [
    ('rm -rf .hermes', '..'),
    ('rm -rf ../..', 'kanban'),
    ('cd ~ && rm -rf .hermes', 'cache'),
    ('cd .. && rm -rf .hermes', '.'),
    ('bash -c "rm -rf .hermes"', '..'),
    ('D=.hermes; rm -rf "$D"', '..'),
    ('mv .hermes /unrelated', '..'),
    ('if test -d ~; then cd ~; fi; D=.hermes; rm -rf "$D"', 'cache'),
    ('cd ~ && D=.hermes; rm -rf "$D"', 'cache'),
    ('if test -d ~; then cd ~; fi; bash -c \'rm -rf .hermes\'', 'cache'),
    ('D=~; cd "$D"; rm -rf .hermes', 'cache'),
])
def test_relative_ancestor_known_cwd(home, command, relative_cwd):
    assert guard.check_shell(command, cwd=str(home / relative_cwd))


@pytest.mark.parametrize('command', [
    'rm -rf cache', 'cd cache && rm -rf .',
    'mv -t /unrelated cache', 'mv --target-directory=/unrelated cache',
])
def test_relative_cache_controls(home, command):
    assert guard.check_shell(command, cwd=str(home)) is None


def test_conditional_cwd_bound(home):
    command = '; '.join(f'if test -d /unrelated/{i}; then cd /unrelated/{i}; fi' for i in range(34))
    assert 'bounded' in guard.check_shell(command, cwd=str(home))


def test_cd_alias_variants_union(home, monkeypatch):
    guard.check_shell('echo ready')
    context = guard._SHELL_CONTEXT
    values = context._values
    # Make the harmless alternative last: no hash-order-dependent safety.
    class OrderedValues(frozenset):
        def __iter__(self):
            return iter(sorted(super().__iter__(), key=lambda p: p == '/unrelated'))
    monkeypatch.setattr(context, '_values', lambda *a: OrderedValues(values(*a)))
    command = 'if test -d ~; then D=~; else D=/unrelated; fi; cd "$D"; rm -rf .hermes'
    assert guard.check_shell(command, cwd=str(home / 'cache'))


@pytest.mark.parametrize('code', [
    'from os import rename; rename(src="copy", dst={db!r})',
    'import os as x; x.replace("copy", dst={db!r})',
    'from shutil import move as m; m(src="copy", dst={db!r})',
    'from shutil import copyfile; copyfile(src="copy", dst={db!r})',
])
def test_python_destination_keyword(home, code):
    assert guard.check_python(code.format(db=str(home / 'state.db')))


@pytest.mark.parametrize('code', [
    'import shutil; shutil.rmtree({path!r})',
    'from shutil import rmtree as r; r(path={path!r})',
])
def test_python_recursive_ancestor(home, code):
    assert guard.check_python(code.format(path=str(home)))
    assert guard.check_python(code.format(path=str(home / 'cache'))) is None
