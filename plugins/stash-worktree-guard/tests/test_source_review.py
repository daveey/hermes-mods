"""Source-review regressions: only disposable repositories and injected failures."""
import importlib.util
from pathlib import Path
import shlex
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_review_plugin', ROOT / '__init__.py')
assert spec is not None and spec.loader is not None
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
guard = plugin.guard


def refused(command, repo):
    assert guard.refuse_reason(command, str(repo))
    result = plugin._pre_tool_call('terminal', {'command': command, 'workdir': str(repo)})
    assert result and result['action'] == 'block'


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def shared(tmp_path):
    repo, wt = tmp_path / 'repo', tmp_path / 'wt'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'Fixture')
    git(repo, 'config', 'user.email', 'fixture@example.com')
    (repo / 'file').write_text('base\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    git(repo, 'worktree', 'add', '-qb', 'other', str(wt))
    (wt / 'file').write_text('foreign\n')
    git(wt, 'stash', 'push', '-qm', 'foreign')
    foreign = git(repo, 'rev-parse', 'refs/stash')
    git(repo, 'update-ref', 'refs/heads/saved-stash', foreign)
    (repo / 'file').write_text('local\n')
    git(repo, 'stash', 'push', '-qm', 'local')
    return repo, foreign


@pytest.mark.parametrize('args', ["push -m '--help'", "push -m '-h'", "push -- --help",
                                  "-m '--help'", "push --message=--help", "push -qm '--help'",
                                  "push --mess --help", "push --me --help", "--mess --help",
                                  "push --m --help", "push --pathspec-from-file --help",
                                  "push --pathspec-from-f --help"])
def test_help_is_data(shared, args):
    repo, _ = shared
    if '--pathspec' in args:
        (repo / '--help').write_text('file\n')
        (repo / 'file').write_text('changed\n')
    else:
        (repo / '--help').write_text('changed\n')
        git(repo, 'add', '--', '--help')
    before = git(repo, 'rev-parse', 'refs/stash')
    refused(f'git stash {args}', repo)
    assert git(repo, 'rev-parse', 'refs/stash') == before
    # These help-looking tokens really mutate the stack when run as Git data.
    git(repo, 'stash', *shlex.split(args))
    assert git(repo, 'rev-parse', 'refs/stash') != before


@pytest.mark.parametrize('operation', ['push', ''])
def test_end_of_options_makes_help_a_pathspec(shared, operation):
    repo, _ = shared
    (repo / '--help').write_text('changed\n')
    git(repo, 'add', '--', '--help')
    before = git(repo, 'rev-parse', 'refs/stash')
    args = [*([operation] if operation else []), '--end-of-options', '--help']
    refused('git stash ' + shlex.join(args), repo)
    assert git(repo, 'rev-parse', 'refs/stash') == before
    result = subprocess.run(['git', '-C', str(repo), 'stash', *args], capture_output=True)
    version = tuple(int(n) for n in git(repo, '--version').split()[2].split('.')[:2])
    if operation == 'push' and version >= (2, 44):
        assert result.returncode == 0, result.stderr
        assert git(repo, 'rev-parse', 'refs/stash') != before
    else:
        # Older Git treats --end-of-options itself as a pathspec; implicit push
        # rejects the following --help token even on newer Git. The refusal
        # assertion above remains version-independent and is never skipped.
        assert result.returncode != 0
        assert git(repo, 'rev-parse', 'refs/stash') == before


@pytest.mark.parametrize('ref', ['refs/heads/saved-stash', 'oid'])
@pytest.mark.parametrize('options', ['', '--index -q ', '-- '])
def test_apply_checks_actual_ref(shared, ref, options):
    repo, oid = shared
    ref = oid if ref == 'oid' else ref
    refused(f'git stash apply {options}{ref}', repo)
    # Git really applies the foreign stash, not the safe top-of-stack entry.
    git(repo, 'stash', 'apply', ref)
    assert (repo / 'file').read_text() == 'foreign\n'


@pytest.mark.parametrize('options', ['', '--index -q ', '-- '])
def test_numeric_ref_uses_stash_reflog(shared, options):
    repo, _ = shared
    # A real branch named 1 must not shadow Git stash's numeric shorthand.
    git(repo, 'update-ref', 'refs/heads/1', git(repo, 'rev-parse', 'refs/stash'))
    refused(f'git stash apply {options}1', repo)
    git(repo, 'stash', 'apply', *shlex.split(options), '1')
    assert (repo / 'file').read_text() == 'foreign\n'


@pytest.mark.parametrize('ref', ['0', '00'])
def test_numeric_same_branch_control(shared, ref):
    repo, _ = shared
    command = f'git stash apply {ref}'
    assert guard.refuse_reason(command, str(repo)) is None
    assert plugin._pre_tool_call('terminal', {'command': command, 'workdir': str(repo)}) is None
    git(repo, 'stash', 'apply', ref)
    assert (repo / 'file').read_text() == 'local\n'


@pytest.mark.parametrize('failure', ['timeout', 'error', 'empty'])
def test_metadata_failure_refuses(shared, monkeypatch, failure):
    repo, _ = shared
    original = guard._run_git
    def query(path, args):
        if args[0] == 'log':
            if failure == 'timeout':
                return None
            return subprocess.CompletedProcess(args, 128 if failure == 'error' else 0, '', 'failure')
        return original(path, args)
    monkeypatch.setattr(guard, '_run_git', query)
    refused('git stash apply', repo)


def test_computed_suffix_after_budget_refuses(shared):
    repo, _ = shared
    command = ':;' * 5000 + '$(printf gi)t $(printf sta)sh clear'
    before = git(repo, 'rev-parse', 'refs/stash')
    refused(command, repo)
    assert git(repo, 'rev-parse', 'refs/stash') == before
    subprocess.run(['bash', '-c', command], cwd=repo, check=True, timeout=10)
    assert not git(repo, 'stash', 'list')


@pytest.mark.parametrize('command', ['git stash --help', 'git stash push --help',
                                    'git stash list', 'git stash apply',
                                    'git stash apply --index -q stash@{0}'])
def test_safe_controls(shared, command):
    repo, _ = shared
    assert guard.refuse_reason(command, str(repo)) is None
