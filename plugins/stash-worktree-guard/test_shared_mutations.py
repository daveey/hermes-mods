"""Shared-ref protection through the actual registered pre-tool callback."""
from pathlib import Path
import shlex
import subprocess

import pytest
from tests_guard import plugin, guard, multi, git, init_repo, GIT_ENV

MUTATIONS = ['-q', 'push', 'push -m note', '-m note', 'save note',
             'store HEAD', 'drop -q stash@{0}', 'clear', 'pop',
             'branch recovered', 'import HEAD']


@pytest.mark.parametrize('operation', MUTATIONS)
@pytest.mark.parametrize('entry', ['terminal', 'execute_code', 'argv', 'chain'])
def test_shared_mutations_block(multi, operation, entry):
    main, wt = multi
    before = subprocess.check_output(['git', 'stash', 'list'], cwd=main)
    cmd = f'git -C {shlex.quote(str(wt))} stash {operation}'
    if entry == 'chain':
        cmd = f'git -C {main} status --short; {cmd}; git -C {wt} stash list'
    if entry in ('terminal', 'chain'):
        result = plugin._pre_tool_call('terminal', {'command': cmd})
    elif entry == 'argv':
        result = plugin._pre_tool_call('execute_code', {
            'code': f'import subprocess; subprocess.run({shlex.split(cmd)!r})'})
    else:
        result = plugin._pre_tool_call('execute_code', {
            'code': f'from hermes_tools import terminal; terminal({cmd!r})'})
    assert result and result['action'] == 'block', (entry, cmd)
    assert subprocess.check_output(['git', 'stash', 'list'], cwd=main) == before
    assert (wt / 'f.txt').read_text() == 'base\n'


@pytest.mark.parametrize('operation', ['list', 'show', 'show -p', 'create', '--help'])
def test_inventory_allowed(multi, operation):
    main, wt = multi
    cmd = f'git -C {wt} stash {operation}'
    assert plugin._pre_tool_call('terminal', {'command': cmd}) is None
    assert plugin._pre_tool_call('execute_code', {'code': f'terminal({cmd!r})'}) is None


@pytest.mark.parametrize('operation', MUTATIONS)
def test_single_worktree_mutations_allowed(tmp_path, operation):
    repo = tmp_path / 'single'
    init_repo(repo)
    assert guard.refuse_reason(f'git stash {operation}', str(repo)) is None


def test_same_branch_apply_allowed(multi):
    main, _ = multi
    assert guard.refuse_reason('git stash apply', str(main)) is None


def test_reported_operations_really_mutate_shared_ref(multi):
    main, wt = multi
    before = subprocess.check_output(['git', 'rev-parse', 'refs/stash'], cwd=main)
    (wt / 'f.txt').write_text('worker change\n')
    # This execution is confined to the synthetic repository, not a tool dispatch.
    git('stash', '-q', cwd=wt)
    assert subprocess.check_output(['git', 'rev-parse', 'refs/stash'], cwd=main) != before
    git('stash', 'drop', '-q', 'stash@{0}', cwd=wt)
    assert subprocess.check_output(['git', 'rev-parse', 'refs/stash'], cwd=main) == before


def test_supported_retirement_helper_is_not_blanket_blocked(multi):
    main, _ = multi
    helper = Path(__file__).resolve().parents[1] / 'live-checkout-guard/reconcile.py'
    cmd = (f'python3 {helper} '
           f'--root {main} --archive /scratch/archive --retire-stashes --oid ' + 'a' * 40)
    assert plugin._pre_tool_call('terminal', {'command': cmd}) is None
    assert plugin._pre_tool_call('execute_code', {'code': f'terminal({cmd!r})'}) is None
