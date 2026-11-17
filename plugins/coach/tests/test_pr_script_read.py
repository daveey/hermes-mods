"""Successful PR reads via a saved script must survive the actual hook log path."""
import json

import pytest
from test_pr_read_outcome import _hooks, _outcome, NICE

SCRIPT = '''#!/usr/bin/env bash
# Read-only PR state re-read.
set -u
echo "read_at=$(date -u +%FT%TZ)"
for n in 3139 3189 3051 3149; do
  gh pr view "$n" --repo example/repo --json state,mergedAt,headRefOid \\
    --jq '"#'"$n"' \\(.state) merged=\\(.mergedAt) head=\\(.headRefOid[0:12])"'
done
gh pr view 131 --repo example/other --json state,mergedAt,headRefOid \\
  --jq '"other#131 \\(.state) merged=\\(.mergedAt) head=\\(.headRefOid[0:12])"'
gh api repos/example/repo/commits/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/statuses \\
  --jq '.[] | "#3139 status \\(.context) \\(.state) \\(.created_at)"' | sort -u | head -3
'''
OUTPUT = ('read_at=2026-11-15T00:49:27Z\n#3139 OPEN merged=null head=4c1f66ea9b36\n'
          '#3189 MERGED merged=2026-11-15T00:44:23Z head=377e17d85f67\n'
          '#3051 MERGED merged=2026-11-14T18:03:13Z head=f277edb196eb\n'
          '#3149 MERGED merged=2026-11-14T23:38:42Z head=89901bf4385f\n'
          'other#131 MERGED merged=2026-11-14T23:45:42Z head=e3d5e6464752')
REPLY = "**#3139** is the guard's PR.\n  - It's still open at 4c1f66ea9b and has no ship receipt yet."
SID = 'script-read-fixture'


def replay(hooks, path, source=SCRIPT, output=OUTPUT, exit_code=0, command=None, verified=True):
    hooks['post_tool_call'](tool_name='write_file', args={'path': str(path), 'content': source},
                           result=json.dumps({'verified': verified, 'resolved_path': str(path)}),
                           session_id=SID, status='ok')
    if command is None:
        command = f'chmod +x "{path}"; token-broker exec --scope user/github --reason "read PR states" -- "{path}" 2>&1 | cut -c1-200'
    hooks['post_tool_call'](tool_name='terminal', args={'command': NICE + command},
                           result=json.dumps({'output': output, 'exit_code': exit_code, 'error': None}),
                           session_id=SID, status='ok')


def begin(tmp_path, monkeypatch):
    hooks, rows, mod = _hooks(tmp_path, monkeypatch)
    monkeypatch.setenv('COACH_REPLY_RULES', 'pr_state_claim')
    hooks['pre_llm_call'](session_id=SID, user_message='Is PR #3139 merged?', platform='desktop')
    return hooks, rows, mod


def check(hooks, reply=REPLY, attempt=0):
    return hooks['pre_verify'](session_id=SID, platform='desktop', attempt=attempt, final_response=reply)


def test_script_read_through_actual_hooks(tmp_path, monkeypatch):
    hooks, rows, _ = begin(tmp_path, monkeypatch)
    replay(hooks, tmp_path / 'read.sh')
    assert check(hooks) is None
    assert not [r for r in rows() if r['event'] == 'reply_nudge']


def test_script_read_resolves_nudge(tmp_path, monkeypatch):
    hooks, rows, _ = begin(tmp_path, monkeypatch)
    assert check(hooks)
    replay(hooks, tmp_path / 'read.sh')
    check(hooks, attempt=1)
    assert _outcome(rows)['resolved'] is True


@pytest.mark.parametrize('change', ['failed', 'wrong_pr', 'quoted', 'not_executed', 'unverified', 'fake_script'])
def test_untrusted_or_unrelated_output_does_not_exempt(tmp_path, monkeypatch, change):
    hooks, _, _ = begin(tmp_path, monkeypatch)
    kw = {}
    if change == 'failed': kw['exit_code'] = 1
    if change == 'wrong_pr': kw['output'] = OUTPUT.replace('#3139', '#9999')
    if change == 'quoted': kw['output'] = json.dumps({'quoted': OUTPUT})
    if change == 'not_executed': kw['command'] = f'cat "{tmp_path / "read.sh"}"'
    if change == 'unverified': kw['verified'] = False
    if change == 'fake_script': kw['source'] = SCRIPT + '\necho "#3139 OPEN merged=null head=4c1f66ea9b36"\n'
    replay(hooks, tmp_path / 'read.sh', **kw)
    assert check(hooks)


def test_previous_turn_source_does_not_exempt(tmp_path, monkeypatch):
    hooks, _, _ = begin(tmp_path, monkeypatch)
    replay(hooks, tmp_path / 'read.sh')
    hooks['post_llm_call'](session_id=SID, assistant_response='Checked.', platform='desktop')
    hooks['pre_llm_call'](session_id=SID, user_message='Check again.', platform='desktop')
    hooks['post_tool_call'](tool_name='terminal', args={'command': str(tmp_path / 'read.sh')},
                           result={'output': OUTPUT, 'exit_code': 0}, session_id=SID, status='ok')
    assert check(hooks)


def test_matching_read_does_not_cover_another_claim(tmp_path, monkeypatch):
    hooks, _, _ = begin(tmp_path, monkeypatch)
    replay(hooks, tmp_path / 'read.sh')
    assert check(hooks, REPLY + '\nPR #9999 is still open.')


@pytest.mark.parametrize('mutation', ['patch', 'write_file', 'terminal', 'execute_code'])
def test_intervening_mutation_invalidates_provenance(tmp_path, monkeypatch, mutation):
    hooks, _, _ = begin(tmp_path, monkeypatch)
    path = tmp_path / 'read.sh'
    hooks['post_tool_call'](tool_name='write_file', args={'path': str(path), 'content': SCRIPT},
                           result={'verified': True}, session_id=SID, status='ok')
    args = {'path': str(path), 'content': 'echo spoof', 'command': f'echo spoof > {path}', 'code': 'pass'}
    hooks['post_tool_call'](tool_name=mutation, args=args, result={'exit_code': 0}, session_id=SID, status='ok')
    hooks['post_tool_call'](tool_name='terminal', args={'command': str(path)},
                           result={'output': OUTPUT, 'exit_code': 0}, session_id=SID, status='ok')
    assert check(hooks)


def test_bounded_source_parser_without_core():
    from test_pr_read_outcome import load, ROOT
    p = load('pr_script_read_unit', ROOT / 'pr_script_reads.py')
    assert set(p.script_ids(SCRIPT)) == {'3139', '3189', '3051', '3149', '131'}
    for source in (SCRIPT + '\necho fake', SCRIPT.replace('gh pr view', 'echo gh pr view'),
                   SCRIPT.replace('#!/usr/bin/env bash', '#!/usr/bin/python3'),
                   SCRIPT.replace(r'\(.state)', 'OPEN'), SCRIPT + 'x' * p.CAP):
        assert not p.script_ids(source)
    assert p.execution_path('echo "/tmp/read.sh"') == ''
    assert p.execution_path('cat /tmp/read.sh') == ''
    assert p.execution_path('/tmp/read.sh; echo fake') == ''
    assert p.execution_path('bash /tmp/read.sh') == '/tmp/read.sh'
    state = {}
    p.observe(state, 'write_file', {'path': '/tmp/read.sh', 'content': SCRIPT}, {'verified': True}, True)
    assert p.observe(state, 'terminal', {'command': '/tmp/read.sh'}, {'output': OUTPUT, 'exit_code': 0}, True)
    for result in ({'output': OUTPUT}, {'output': OUTPUT, 'exit_code': False},
                   {'output': OUTPUT, 'exit_code': 1}, {'output': OUTPUT, 'exit_code': 0, 'error': 'failed'}):
        assert not p.observe(state, 'terminal', {'command': '/tmp/read.sh'}, result, True)
    assert not p.execution_path("bash '$HOME/read.sh'")
    assert not p.execution_path('bash "~/read.sh"')
    assert not p.execution_path('token-broker exec --scope user/github --reason "$(mutate)" -- /tmp/read.sh')
    assert not p.observe(state, 'terminal', {'command': 'bash /unknown.sh'}, {'exit_code': 0}, True)
    assert not state['pr_read_scripts']
    p.observe(state, 'write_file', {'path': '/tmp/read.sh', 'content': SCRIPT}, {'verified': True}, True)
    p.observe(state, 'write_file', {'path': 'read.sh', 'content': 'echo spoof'},
              {'verified': True, 'resolved_path': '/tmp/read.sh'}, True)
    assert not state['pr_read_scripts']


@pytest.mark.parametrize('reply', ['PR #3139 in unrelated/repo is still open.',
                                   'PR unrelated/repo#3139 is still open.',
                                   'PR https://github.com/unrelated/repo/pull/3139 is still open.'])
def test_other_repository_does_not_exempt(tmp_path, monkeypatch, reply):
    hooks, _, _ = begin(tmp_path, monkeypatch)
    replay(hooks, tmp_path / 'read.sh')
    assert check(hooks, reply)


@pytest.mark.parametrize('repo', ['unrelated/repo', 'example/repo'])
@pytest.mark.parametrize('formatting', ['`{repo}`', '**{repo}**', '[{repo}](https://github.com/{repo})'])
def test_formatted_repository_scope(tmp_path, monkeypatch, repo, formatting):
    hooks, _, _ = begin(tmp_path, monkeypatch)
    replay(hooks, tmp_path / 'read.sh')
    result = check(hooks, f'PR #3139 in {formatting.format(repo=repo)} is still open.')
    assert bool(result) == (repo != 'example/repo')


@pytest.mark.parametrize('quote', ['', "'", '"'])
def test_brace_execution_provenance(tmp_path, monkeypatch, quote):
    import subprocess
    hooks, _, _ = begin(tmp_path, monkeypatch)
    path = tmp_path / '{first,second}.sh'
    # The literal file is verified; unquoted Bash invokes first.sh instead.
    path.write_text("printf '%s\\n' '" + OUTPUT + "'\n")
    (tmp_path / 'first.sh').write_text(path.read_text())
    command = f'bash {quote}{path}{quote}'
    result = subprocess.run(['bash', '-c', command], capture_output=True, text=True, check=True)
    assert not result.stderr
    replay(hooks, path, output=result.stdout, command=command)
    assert bool(check(hooks)) == (not quote)


def test_legacy_outcomes_require_explicit_verifier_retirement():
    from test_pr_read_outcome import load, ROOT
    sh = load('pr_script_retirement', ROOT / 'self_heal.py')
    symptom = "agent's revised reply still failed the coach pr_state_claim check: PR #3139 is still open."
    for source in ('signal', 'agent', None):
        payload = {'component': 'agent', 'kind': 'coach', 'symptom': symptom,
                   'details': '#3139 OPEN merged=null head=4c1f66ea9b36'}
        if source:
            payload['source'] = source
        assert sh._detector_exempts_now(payload) is None

