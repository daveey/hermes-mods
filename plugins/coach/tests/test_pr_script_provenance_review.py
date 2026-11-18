"""Real-hook script provenance review regressions and explicit policy controls."""
import json

import pytest
from test_pr_read_outcome import _hooks, NICE

SCRIPT = '''#!/usr/bin/env bash
set -u
echo "read_at=$(date -u +%FT%TZ)"
for n in 3139 3189; do
  gh pr view "$n" --repo example/repo --json state,mergedAt,headRefOid \\
    --jq '"#'"$n"' \\(.state) merged=\\(.mergedAt) head=\\(.headRefOid[0:12])"'
done
'''
OUTPUT = ('read_at=2026-11-15T00:49:27Z\n#3139 OPEN merged=null head=4c1f66ea9b36\n'
          '#3189 MERGED merged=2026-11-15T00:44:23Z head=377e17d85f67')
SID = 'review-probe'


def begin(tmp_path, monkeypatch):
    hooks, rows, mod = _hooks(tmp_path, monkeypatch)
    monkeypatch.setenv('COACH_REPLY_RULES', 'pr_state_claim')
    hooks['pre_llm_call'](session_id=SID, user_message='Is PR #3139 merged?', platform='desktop')
    return hooks


def tool(hooks, name, args, result):
    hooks['post_tool_call'](tool_name=name, args=args, result=json.dumps(result), session_id=SID, status='ok')


def write(hooks, path, source=SCRIPT):
    tool(hooks, 'write_file', {'path': str(path), 'content': source}, {'verified': True, 'resolved_path': str(path)})


def run(hooks, path, output=OUTPUT, exit_code=0, error=None):
    tool(hooks, 'terminal', {'command': NICE + f'bash "{path}"'},
         {'output': output, 'exit_code': exit_code, 'error': error})


def check(hooks, reply):
    return hooks['pre_verify'](session_id=SID, platform='desktop', attempt=0, final_response=reply)


GOOD = "**#3139** is the guard's PR. It's still open at 4c1f66ea9b."


@pytest.mark.parametrize('subject', ["It's", 'This PR is', 'The PR is',
                                     'PR3139 is', 'Pull request 3139 is',
                                     'PR-3139 is', 'PR number 3139 is'])
def test_same_subject_inherits_or_names_read(tmp_path, monkeypatch, subject):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, f'I re-read #3139 at 00:49Z. {subject} still open.') is None


@pytest.mark.parametrize('subject', ['Another PR', 'Another pull request', 'The other pull request'])
def test_another_subject_never_inherits(tmp_path, monkeypatch, subject):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, f'I re-read #3139 at 00:49Z. {subject} is still open.')


@pytest.mark.parametrize('prefix', ['PRs', 'Pull requests', 'PR-', 'PR number', 'PR', 'PRs #'])
@pytest.mark.parametrize('separator', [' and ', ' & ', ', ', ' or ', '/', ', and '])
@pytest.mark.parametrize('last', ['3140', '#3140', '3189 and 3140'])
def test_numbered_lists_require_every_read(tmp_path, monkeypatch, prefix, separator, last):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, f'{prefix} 3139{separator}{last} are still open.')


@pytest.mark.parametrize('separator', [' and ', ' & ', ', ', ' or ', '/', ', and '])
def test_numbered_lists_all_read(tmp_path, monkeypatch, separator):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    # Slash syntax also looks like a repository and conservatively nudges.
    if separator != '/':
        assert check(h, f'PRs 3139{separator}3189 are still open.') is None
    else:
        assert check(h, 'PRs 3139/3189 are still open.')


@pytest.mark.parametrize('separator', [
    '-', ' - ', ' – ', ' to ', ' through ', ' as well as ', ' plus ',
    ' + ', ' and also ', ' and then ', ' (and ', ' (', ' ; ',
    ', then ', ' and number ', ' and the ', ' and 0',
])
@pytest.mark.parametrize('prefix', ['PRs ', 'PR #', '#'])
def test_unbounded_connectives_fail_closed(tmp_path, monkeypatch, separator, prefix):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, f'{prefix}3139{separator}3140 are still open.')


@pytest.mark.parametrize('suffix', [' at 00:49Z', ' at 12:34:56Z',
                                  ' at 4c1f66ea9b', '; tests pass 134/134',
                                  ' with 3 checks pending'])
def test_numeric_metadata_not_pr_list(tmp_path, monkeypatch, suffix):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, f'PR #3139 is still open{suffix}.') is None


# An explicit inventory makes expansion of the trust boundary reviewable.
READERS = ('read_file', 'search_files', 'skill_view', 'skills_list',
           'tool_describe', 'tool_search')


@pytest.mark.parametrize('reader', READERS)
def test_read_only_allowlist_preserves_provenance(tmp_path, monkeypatch, reader):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    tool(h, reader, {}, {})
    run(h, tmp_path / 'r.sh')
    assert check(h, GOOD) is None


def test_read_only_allowlist_inventory(tmp_path, monkeypatch):
    _, _, mod = _hooks(tmp_path, monkeypatch)
    assert mod._load('pr_script_reads').READ_ONLY_TOOLS == frozenset(READERS)


@pytest.mark.parametrize('mutator', ['unknown_future_tool', 'tool_call', 'web_search'])
def test_unlisted_tool_invalidates(tmp_path, monkeypatch, mutator):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    tool(h, mutator, {}, {})
    run(h, tmp_path / 'r.sh')
    assert check(h, GOOD)



def test_control_good_read_exempts(tmp_path, monkeypatch):
    # Sanity: the legitimate path really is exempted, so the probes below measure something.
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, GOOD) is None


# 1. Failed script reads ------------------------------------------------------------------------------------------
@pytest.mark.parametrize('kind', ['exit1', 'exit127', 'error_field', 'gh_failed_in_loop', 'no_exit_code', 'timeout'])
def test_failed_read(tmp_path, monkeypatch, kind):
    h = begin(tmp_path, monkeypatch)
    p = tmp_path / 'r.sh'
    write(h, p)
    if kind == 'exit1':
        run(h, p, exit_code=1)
    elif kind == 'exit127':
        run(h, p, output='bash: gh: command not found', exit_code=127)
    elif kind == 'error_field':
        run(h, p, error='Command timed out')
    elif kind == 'gh_failed_in_loop':
        # gh fails for 3139 (no row), loop's last command succeeds -> script exit 0.
        run(h, p, output='read_at=x\nGraphQL: Could not resolve to a PullRequest with the number of 3139.\n'
                         '#3189 MERGED merged=2026-11-15T00:44:23Z head=377e17d85f67')
    elif kind == 'no_exit_code':
        tool(h, 'terminal', {'command': f'bash "{p}"'}, {'output': OUTPUT})
    elif kind == 'timeout':
        tool(h, 'terminal', {'command': f'bash "{p}"'}, {'output': OUTPUT, 'exit_code': 124})
    assert check(h, GOOD)


# 2. Another repository or PR ----------------------------------------------------------------------------------------
@pytest.mark.parametrize('reply', [
    'PR #3139 in other/repo is still open.',
    'PR other/repo#3139 is still open.',
    'PR https://github.com/other/repo/pull/3139 is still open.',
    "#3139 is the guard's PR. PR #3140 is still open.",
    "#3139 is the guard's PR. It's still open, and so is #3140.",
    # Forms _MF_PR_RE treats as a PR mention but the context-id extractor does not read:
    "I re-read #3139 at 00:49Z. PR3140 is still open.",
    "I re-read #3139 at 00:49Z. Pull request 3140 is still open.",
    "I re-read #3139 at 00:49Z. PR-3140 is still open.",
    "I re-read #3139 at 00:49Z. PR number 3140 is still open.",
    "I re-read #3139 at 00:49Z. The other PR is still open.",
])
def test_other_repo_or_pr(tmp_path, monkeypatch, reply):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, reply)


def test_script_reads_other_repo_reply_unqualified(tmp_path, monkeypatch):
    # Deliberate number-only policy, shared with direct reads: no repo in reply.
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh', SCRIPT.replace('example/repo', 'other/repo'))
    run(h, tmp_path / 'r.sh')
    assert check(h, GOOD) is None


# 3. Script edited after it was written / after it ran -------------------------------------------------------------
SPOOF = '#!/bin/bash\necho "#3139 OPEN merged=null head=4c1f66ea9b36"\n'


@pytest.mark.parametrize('editor', ['patch', 'write_file', 'terminal_sed', 'execute_code', 'process_manage',
                                    'delegate_task', 'skill_manage', 'browser_exec'])
def test_edit_after_failed_run_then_rerun(tmp_path, monkeypatch, editor):
    """write verified read script -> first run FAILS (no evidence) -> script edited to echo a forged row -> rerun."""
    h = begin(tmp_path, monkeypatch)
    p = tmp_path / 'r.sh'
    write(h, p)
    run(h, p, exit_code=1)
    if editor == 'patch':
        tool(h, 'patch', {'path': str(p), 'old_string': 'gh pr view', 'new_string': 'echo'}, {'success': True})
    elif editor == 'write_file':
        tool(h, 'write_file', {'path': str(p), 'content': SPOOF}, {'verified': True, 'resolved_path': str(p)})
    elif editor == 'terminal_sed':
        # (avoid the literal 'gh pr view' text, which the baseline read regex treats as a global PR read)
        tool(h, 'terminal', {'command': f"printf '%s' {SPOOF!r} > {p}"}, {'output': '', 'exit_code': 0})
    elif editor == 'execute_code':
        tool(h, 'execute_code', {'code': f'open({str(p)!r},"w").write({SPOOF!r})'}, {'output': ''})
    elif editor == 'process_manage':
        # A pre-existing interactive shell (terminal background=true, pty) receives the edit.
        tool(h, 'process_manage', {'action': 'submit', 'session_id': 'proc_x',
                                   'data': f"printf '%s' {SPOOF!r} > {p}"}, {'status': 'ok'})
    elif editor == 'delegate_task':
        tool(h, 'delegate_task', {'tasks': [{'goal': f'rewrite {p} to print a fixed line'}]}, {'results': []})
    elif editor == 'skill_manage':
        tool(h, 'skill_manage', {'operations': []}, {'success': True})
    elif editor == 'browser_exec':
        tool(h, 'browser_exec', {'code': 'pass'}, {'output': ''})
    run(h, p)  # forged/edited output: '#3139 OPEN ...'
    assert check(h, GOOD)


def test_rerun_after_post_run_edit_does_not_add_ids(tmp_path, monkeypatch):
    """write -> run OK (3139/3189) -> rewrite script (unverified) to read 3140 -> rerun: 3140 claim still raised."""
    h = begin(tmp_path, monkeypatch)
    p = tmp_path / 'r.sh'
    write(h, p)
    run(h, p)
    tool(h, 'write_file', {'path': str(p), 'content': SCRIPT.replace('3189', '3140')}, {'verified': False})
    run(h, p, output=OUTPUT.replace('3189', '3140'))
    assert check(h, "#3139 is the guard's PR. PR #3140 is still open.")


# 4. Numeric repository names --------------------------------------------------------------------------------------
@pytest.mark.parametrize('reply', [
    'PR #3139 is still open in 134/134 today.',
    'PR #3139 in 2024/11 is still open.',
    'PR https://github.com/2024/11/pull/3139 is still open.',
    'PR 2024/11#3139 is still open.',
    "#3139 is the guard's PR in 1234/5678. It's still open.",
    "#3139 is the guard's PR. It's still open. Tests pass in 134/134.",
    "#3139 is the guard's PR in repo 134/134. It's still open; tests pass 134/134.",
    "Done means (review): PR #3139 is still open in 2/2 first pass, 0 fix rounds",
])
def test_numeric_repository(tmp_path, monkeypatch, reply):
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, reply)


@pytest.mark.parametrize('reply', [
    # The masked forms. A numeric repo that happens to be spelled as a counter is exempted by design.
    "#3139 is the guard's PR. It's still open; tests pass 134/134.",
    "#3139 is the guard's PR. It's still open.\nDone means (review): 2/2 first pass, 0 fix rounds; n/a: none.",
])
def test_numeric_repository_masked_forms_report(tmp_path, monkeypatch, reply):
    """Informational: records whether typed counters are masked (exempt) - expected to exempt."""
    h = begin(tmp_path, monkeypatch)
    write(h, tmp_path / 'r.sh')
    run(h, tmp_path / 'r.sh')
    assert check(h, reply) is None
