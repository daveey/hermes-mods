"""Maintenance identity checks; burner strings are classified, never executed."""
import subprocess
from pathlib import Path

import pytest
from tests_guard import plugin, run, BURN_SH, protected


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def trees(tmp_path, monkeypatch):
    main = tmp_path / 'main'
    main.mkdir()
    git(main, 'init', '-q')
    own = main / 'plugins/cpu-load-guard'
    own.mkdir(parents=True)
    (own / 'tests_guard.py').write_text('# fixture\n')
    git(main, 'add', '.')
    git(main, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'fixture')
    wt = tmp_path / 'detached'
    git(main, 'worktree', 'add', '--detach', str(wt), 'HEAD')
    monkeypatch.setattr(plugin, '_OWN_DIR', own.resolve())
    yield main, wt
    git(main, 'worktree', 'remove', '--force', str(wt))


@pytest.mark.parametrize('tool', ['write_file', 'patch', 'v4a', 'deferred'])
def test_own_detached_maintenance(trees, tool):
    _, wt = trees
    path = str(wt / 'plugins/cpu-load-guard/tests_guard.py')
    if tool == 'write_file':
        args = {'path': path, 'content': BURN_SH}
    elif tool == 'v4a':
        tool = 'patch'
        args = {'mode': 'patch', 'patch': f'*** Begin Patch\n*** Update File: {path}\n+' + BURN_SH.replace('\n', '\n+')}
    else:
        args = {'path': path, 'old_string': '# fixture', 'new_string': BURN_SH}
        if tool == 'deferred':
            tool, args = 'tool_call', {'calls': [{'name': 'patch', 'arguments': args}]}
    assert run(tool, args) is None


def test_unrelated_and_untracked_scripts_guarded(trees, tmp_path):
    _, wt = trees
    for path in (wt / 'other.py', wt / 'plugins/cpu-load-guard-extra/tests_guard.py',
                 wt / 'plugins/cpu-load-guard/arbitrary.sh',
                 tmp_path / 'lookalike/plugins/cpu-load-guard/tests_guard.py'):
        assert not plugin._own_file(str(path))
        assert run('write_file', {'path': str(path), 'content': BURN_SH})['action'] == 'block'


def test_other_repository_same_layout_guarded(trees, tmp_path):
    main, _ = trees
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(main), str(clone))
    path = clone / 'plugins/cpu-load-guard/tests_guard.py'
    assert not plugin._own_file(str(path))


def test_symlink_escape_and_git_environment_guarded(trees, tmp_path, monkeypatch):
    main, wt = trees
    path = wt / 'plugins/cpu-load-guard/tests_guard.py'
    path.unlink()
    outside = tmp_path / 'outside.py'
    outside.write_text('# fixture\n')
    path.symlink_to(outside)
    assert not plugin._own_file(str(path))
    monkeypatch.setenv('GIT_DIR', str(main / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(wt))
    fake = tmp_path / 'fake/plugins/cpu-load-guard/tests_guard.py'
    fake.parent.mkdir(parents=True)
    assert not plugin._own_file(str(fake))


def test_forged_git_pointer_guarded(trees, tmp_path):
    _, wt = trees
    fake = tmp_path / 'forged'
    path = fake / 'plugins/cpu-load-guard/tests_guard.py'
    path.parent.mkdir(parents=True)
    (fake / '.git').write_text((wt / '.git').read_text())
    assert not plugin._own_file(str(path))


def test_git_failure_retains_write_guard(trees, monkeypatch):
    _, wt = trees
    def unavailable(*args):
        raise subprocess.TimeoutExpired('git', 2)
    monkeypatch.setattr(plugin, '_maintenance_git', unavailable)
    path = wt / 'plugins/cpu-load-guard/tests_guard.py'
    assert run('write_file', {'path': str(path), 'content': BURN_SH})['action'] == 'block'


def test_existing_fixture_patch_and_relative_alias(trees, tmp_path, monkeypatch):
    _, wt = trees
    path = wt / 'plugins/cpu-load-guard/tests_guard.py'
    path.write_text(BURN_SH + '# old\n')
    monkeypatch.chdir(wt)
    assert run('patch', {'path': 'plugins/cpu-load-guard/tests_guard.py',
                         'old_string': '# old', 'new_string': '# new'}) is None
    alias = tmp_path / 'alias'
    alias.symlink_to(wt, target_is_directory=True)
    assert plugin._own_file(str(alias / 'plugins/cpu-load-guard/tests_guard.py'))


def test_maintenance_does_not_exempt_execution(trees):
    _, wt = trees
    path = wt / 'plugins/cpu-load-guard/tests_guard.py'
    path.write_text(BURN_SH)
    assert plugin._own_file(str(path))
    assert run('terminal', {'command': f'bash {path}'})['action'] == 'block'
    assert run('execute_code', {'code': f'from hermes_tools import terminal\nterminal("bash {path}")'})['action'] == 'block'
