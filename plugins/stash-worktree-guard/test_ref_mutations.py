"""Raw ref and unresolved-target regressions; only synthetic repositories."""
import shlex
import pytest
from tests_guard import plugin, guard, multi

RAW = ['update-ref -d refs/stash', 'update-ref refs/stash HEAD',
       'reflog delete stash@{0}', 'reflog expire --expire=now refs/stash',
       'reflog expire --expire=now --all',
       'fetch . HEAD:refs/stash', 'symbolic-ref refs/stash refs/heads/main',
       'symbolic-ref --delete refs/stash',
       'symbolic-ref -m message refs/stash refs/heads/main',
       'symbolic-ref -mmessage refs/stash refs/heads/main']

@pytest.mark.parametrize('operation', RAW)
@pytest.mark.parametrize('entry', ['terminal', 'execute_code', 'argv'])
def test_raw_mutation_block(multi, operation, entry):
    main, _ = multi
    cmd = f'git -C {main} {operation}'
    args = {'command': cmd}
    if entry == 'execute_code':
        args = {'code': f'terminal({cmd!r})'}
    if entry == 'argv':
        args = {'code': f'subprocess.run({shlex.split(cmd)!r})'}
    assert plugin._pre_tool_call('terminal' if entry == 'terminal' else 'execute_code', args)

@pytest.mark.parametrize('operation', ['push', 'drop', 'clear'])
@pytest.mark.parametrize('prefix', ['git -C "$UNKNOWN"', 'git --git-dir=/unknown', 'GIT_DIR=/unknown git'])
def test_unknown_mutation_block(operation, prefix):
    assert guard.refuse_reason(f'{prefix} stash {operation}')

@pytest.mark.parametrize('operation', ['reflog show stash', 'log -g refs/stash',
    'rev-parse refs/stash', 'for-each-ref refs/stash', 'symbolic-ref refs/stash'])
def test_raw_inventory_allowed(multi, operation):
    main, _ = multi
    cmd = f'git -C {main} {operation}'
    assert plugin._pre_tool_call('terminal', {'command': cmd}) is None
    assert plugin._pre_tool_call('execute_code', {'code': f'terminal({cmd!r})'}) is None
    assert plugin._pre_tool_call('execute_code', {'code': f'subprocess.run({shlex.split(cmd)!r})'}) is None

@pytest.mark.parametrize('failure', ['budget', 'exception'])
@pytest.mark.parametrize('operation', ['stash push', 'stash drop', 'stash clear', 'stash -q',
    'update-ref -d refs/stash', 'reflog expire --expire=now --all', 'push . HEAD:refs/stash'])
@pytest.mark.parametrize('entry', ['terminal', 'execute_code'])
def test_mutation_analysis_failure(multi, monkeypatch, failure, operation, entry):
    main, _ = multi
    if failure == 'budget':
        monkeypatch.setattr(guard, 'MAX_STEPS', 0)
    else:
        def boom(*args, **kwargs):
            raise RuntimeError('analysis failure')
        monkeypatch.setattr(guard._Analyzer, 'simple', boom)
    cmd = f'git -C {main} {operation}'
    args = {'command': cmd, 'workdir': str(main)} if entry == 'terminal' else {'code': f'terminal({cmd!r})'}
    assert plugin._pre_tool_call(entry, args)


@pytest.mark.parametrize('operation', RAW)
@pytest.mark.parametrize('entry', ['terminal', 'execute_code'])
def test_raw_unknown_chains(operation, entry):
    cmd = f'true; git -C "$UNKNOWN" {operation}; git status'
    args = {'command': cmd} if entry == 'terminal' else {'code': f'terminal({cmd!r})'}
    assert plugin._pre_tool_call(entry, args)


def test_ansi_mutation_uncertainty(multi):
    main, _ = multi
    assert guard.refuse_reason(f"echo $'x'; git -C {main} stash push")


@pytest.mark.parametrize('operation', [
    'reflog delete "$E"; git stash drop', 'fetch . "$S" && git stash clear',
    'symbolic-ref "$X"; git stash pop', 'update-ref -d "$REF"',
    'update-ref --stdin < f', 'push . HEAD:refs/stash', 'push . :refs/stash'])
@pytest.mark.parametrize('entry', ['terminal', 'execute_code'])
def test_review_raw_unknown_fail_closed(multi, operation, entry):
    main, _ = multi
    cmd = f'git -C {main} {operation}'
    args = {'command': cmd, 'workdir': str(main)} if entry == 'terminal' else {'code': f'os.system({cmd!r})'}
    result = plugin._pre_tool_call(entry, args)
    assert result and result['action'] == 'block'


def test_stdin_update_ref(multi):
    main, _ = multi
    assert guard.refuse_reason(f"printf 'delete refs/stash\\n' | git -C {main} update-ref --stdin")
