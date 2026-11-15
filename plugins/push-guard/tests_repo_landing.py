"""Configured repositories use the same real landing and raw-command boundaries."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests_landing import GitHub, HEAD, guard, land

# Override only in private acceptance runs; public fixtures carry no private slug.
REPO = os.environ.get('LANDING_TEST_REPO', 'example/service')


@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'ship-only-guard.json').write_text(json.dumps({'pr_landing_repos': [REPO]}))
    return tmp_path


@pytest.mark.parametrize('case', ['accept', 'no-review', 'self', 'lander', 'in-run', 'stale',
                                  'changes-requested', 'unreadable', 'head-move', 'red-ci'])
def test_configured_actual_land_boundary(configured, case):
    api = GitHub()
    api.pr['base']['repo']['full_name'] = REPO
    if case == 'no-review': api.reviews = []
    if case == 'self': api.reviews[0]['user']['login'] = 'author'
    if case == 'lander': api.reviews[0]['user']['login'] = 'lander'
    if case == 'in-run':
        api.reviews[0].update(state='COMMENT', body='My subagent approves this exact head')
    if case == 'stale': api.reviews[0]['commit_id'] = 'c' * 40
    if case == 'changes-requested': api.reviews[0]['state'] = 'CHANGES_REQUESTED'
    if case == 'unreadable': api.error = '/reviews'
    if case == 'head-move': api.move = True
    if case == 'red-ci': api.runs[0]['conclusion'] = 'failure'
    if case == 'accept':
        result = land.land(7, HEAD, read=api, repo=REPO)
        assert result['repo'] == REPO and result['merged']
        assert len(api.writes) == 1 and f'sha={HEAD}' in api.writes[0]
    else:
        with pytest.raises(land.Refused):
            land.land(7, HEAD, read=api, repo=REPO)
        assert not api.writes
    assert all(p in ('user', 'graphql') or p.startswith(f'repos/{REPO}/') for p in api.reads)


@pytest.mark.parametrize('template', [
    'gh pr merge 7 -R {repo} --rebase',
    'gh -R {repo} pr merge 7 --auto',
    'GH_REPO={repo} gh pr merge 7',
    'gh api -X PUT repos/{repo}/pulls/7/merge',
    'curl -X PUT https://api.github.com/repos/{repo}/pulls/7/merge',
    'bash -c "gh pr merge 7 -R {repo}"',
])
def test_configured_raw_routes(configured, template):
    command = template.format(repo=REPO)
    assert 'land.py' in guard.check_shell(command, '/', guards=[])
    assert 'land.py' in guard.check_python(f'import os; os.system({command!r})', '/', guards=[])


def test_python_http(configured):
    assert 'land.py' in guard.check_python(
        f'requests.put("https://api.github.com/repos/{REPO}/pulls/7/merge")', '/', guards=[])


def test_profile_inherits(configured, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(configured / 'profiles/reviewer'))
    assert REPO in guard.landing_repos()
    assert 'land.py' in guard.check_shell(f'gh pr merge 7 -R {REPO}', '/', guards=[])


@pytest.mark.parametrize('text', ['{', '[]', '{"pr_landing_repos":"example/service"}', '{"pr_landing_repos":[null]}'])
def test_bad_policy_fails_closed(configured, text):
    (configured / 'ship-only-guard.json').write_text(text)
    assert 'land.py' in guard.check_shell(f'gh pr merge 7 -R {REPO}', '/', guards=[])
    api = GitHub()
    with pytest.raises((ValueError, land.Refused)):
        land.land(7, HEAD, read=api, repo=REPO)
    assert not api.reads and not api.writes


def test_existing_default_and_other_repo_control(configured):
    assert land.land(7, HEAD, read=GitHub())['merged']
    assert guard.check_shell('gh pr merge 7 -R example/unconfigured', '/', guards=[]) is None
    api = GitHub()
    with pytest.raises(land.Refused, match='not configured'):
        land.land(7, HEAD, read=api, repo='example/unconfigured')
    assert not api.reads


def test_cli_passes_repository(configured):
    # Execute the actual main parser, replacing only network I/O in the child.
    path = Path(land.__file__)
    code = '''import runpy, sys
m = runpy.run_path(sys.argv[1], run_name='landing_fixture')
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parent))
from tests_landing import GitHub
api = GitHub()
repo = sys.argv[2]
api.pr['base']['repo']['full_name'] = repo
real = m['land']
m['main'].__globals__['land'] = lambda *a, **kw: real(*a, read=api, **kw)
sys.argv = ['land.py', '--repo', repo, '--pr', '7', '--head', 'a'*40]
raise SystemExit(m['main']())
'''
    p = subprocess.run([sys.executable, '-c', code, str(path), REPO], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout)
    assert result['repo'] == REPO and result['merged']


def test_core_policy_is_not_pr_policy(configured):
    (configured / 'ship-only-guard.json').write_text(json.dumps({'repos': ['example/core']}))
    assert guard.landing_repos() == {land.REPO}
    assert guard.check_shell('gh pr merge 7 -R example/core', '/', guards=[]) is None


@pytest.mark.parametrize('tool', ['terminal', 'execute_code', 'tool_call'])
def test_activation_hook(configured, tool):
    import runpy
    plugin = runpy.run_path(str(Path(guard.__file__).with_name('__init__.py')))
    command = f'gh pr merge 7 -R {REPO}'
    args = {'command': command, 'workdir': '/'}
    if tool == 'execute_code':
        args = {'code': f'import os; os.system({command!r})'}
    if tool == 'tool_call':
        args = {'calls': [{'name': 'terminal', 'arguments': args}]}
    decision = plugin['_pre_tool_call'](tool_name=tool, args=args)
    assert decision['action'] == 'block' and 'land.py' in decision['message']

