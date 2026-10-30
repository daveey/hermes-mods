"""Regression: ordinary merges by admin credentials also need exact-head CI."""
import pytest
from tests_guard import block, core, run, CHECK


@pytest.mark.parametrize('state', ['failure', 'cancelled', 'queued', 'missing', 'unreadable', 'success'])
@pytest.mark.parametrize('entry', ['terminal', 'execute_code', 'tool_call'])
def test_plain_merge_checks_exact_pr_head(core, state, entry):
    work, gh = core
    sha = '0123456789abcdef0123456789abcdef01234567'
    gh.prs['42'] = {'headRefOid': sha, 'baseRefName': 'main'}
    gh.runs[sha] = [] if state == 'missing' else [run('queued') if state == 'queued' else run('completed', state)]
    gh.fail = state == 'unreadable'
    command = ('token-broker exec --scope user/github --reason "land checked PR" -- '
               f'gh pr merge 42 -R acme/core --rebase --match-head-commit {sha}')
    args = {'command': command, 'workdir': str(work)}
    if entry == 'execute_code':
        args = {'code': f'terminal({command!r}, workdir={str(work)!r})'}
    elif entry == 'tool_call':
        args = {'calls': [{'name': 'terminal', 'arguments': args}]}
    got = block(entry, args)
    if state == 'success':
        assert got is None
        assert any('/commits/' + sha + '/' in str(c) for c in gh.calls)
    else:
        assert got and got['action'] == 'block'
        assert CHECK in got['message']


def test_plain_merge_to_other_base_is_not_gated(core):
    work, gh = core
    gh.prs['42'] = {'headRefOid': 'a' * 40, 'baseRefName': 'release'}
    assert block('terminal', {'command': 'gh pr merge 42 --rebase', 'workdir': str(work)}) is None
    assert len(gh.calls) == 1


def test_disabling_auto_merge_does_not_require_green_ci(core):
    work, gh = core
    assert block('terminal', {'command': 'gh pr merge 42 --disable-auto', 'workdir': str(work)}) is None
    assert not gh.calls


def test_current_branch_selector_is_explicit_with_repo_flag(core):
    work, gh = core
    sha = 'b' * 40
    gh.prs['feat'] = {'headRefOid': sha, 'baseRefName': 'main'}
    gh.runs[sha] = [run('completed', 'success')]
    assert block('terminal', {'command': 'gh pr merge --rebase', 'workdir': str(work)}) is None
    assert gh.calls[0][:3] == ['pr', 'view', 'feat']


def test_unguarded_plain_merge_does_not_read_ci(core):
    work, gh = core
    assert block('terminal', {'command': 'gh pr merge 42 -R acme/other', 'workdir': str(work)}) is None
    assert not gh.calls
