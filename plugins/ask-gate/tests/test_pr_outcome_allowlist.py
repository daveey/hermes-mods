"""Lifecycle text is bounded PR scope, not a second authorization channel."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('outcome_parser', Path(__file__).parents[1] / 'pr_approval_request.py')
assert spec is not None and spec.loader is not None
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def row(unblock='merge', archive='close the PR'):
    return {'human_request': {'interaction': 'approval', 'scope': 'example/widget PR #1950',
            'action': f'Approve PR #1950 at head 21cb9c1c2. Unblock = {unblock}; archive = {archive}.'}}


@pytest.mark.parametrize('clause', ['wipe the prod bucket', 'transfer funds', 'email the credentials',
    'send the secrets', 'publish the report', 'revoke access', 'invent an unknown operation'])
@pytest.mark.parametrize('outcome', ['Unblock', 'archive'])
def test_unknown_verbs_fail_closed_and_name_clause(clause, outcome):
    r = row('merge and ' + clause) if outcome == 'Unblock' else row(archive='close the PR then ' + clause)
    assert parser.request_target(r) is None
    reason = parser.parse_failure_reason(r)
    assert outcome in reason
    assert clause in reason


@pytest.mark.parametrize('unblock', [
    'queue', 'queue merges', 'queue the merge train', 'merge train', 'merge', 'merges', 'merge the PR',
    'agent merges via normal gates', 'deploy', 'deploys', 'release', 'ship', 'I verify',
    'I check CI', 're-run CI', 'I re-run CI', 'watch CI', 'I watch CI', 'continue the task',
    'resume the task', 'queue merges+deploys and I verify', 'merge then deploy, I check CI',
])
def test_supported_unblock_atoms_and_combinations(unblock):
    assert parser.request_target(row(unblock)) == ('example/widget', 1950, '21cb9c1c2')
    assert parser.parse_failure_reason(row(unblock)) == ''


@pytest.mark.parametrize('archive', ['close', 'close the PR', 'drop the change', 'abandon the change',
    'findings stay open', 'card stays open', 'leave it unmerged', 'PR left open',
    'close the PR, findings stay open', 'drop the change and card stays open',
    'leave it unmerged + findings stay open'])
def test_supported_archive_atoms_and_combinations(archive):
    assert parser.request_target(row(archive=archive)) == ('example/widget', 1950, '21cb9c1c2')


@pytest.mark.parametrize('unblock,archive', [
    ('merge the PR into an unrelated repo', 'close'), ('I verify and wipe the bucket', 'close'),
    ('I verify secrets', 'close'), ('merge', 'close the PR and send mail'),
    ('merge or send mail', 'close'), ('merge; send mail', 'close'),
    ('merge. send mail', 'close'), ('merge', 'drop the database'),
    ('merge and', 'close'), ('+merge', 'close'), ('merge,,deploy', 'close'),
    ('merge', 'close the PR then'), ('merge', 'close..'), ('merge', '…'),
    ('close the PR', 'merge'), ('merge\nemail credentials', 'close'),
])
def test_complete_clause_matching_not_verb_prefixes(unblock, archive):
    assert parser.request_target(row(unblock, archive)) is None


def test_canonical_action_with_concrete_lifecycle():
    r = row()
    r['human_request']['action'] = parser.canonical_wording(r).replace('Unblock = …; archive = ….',
        'Unblock = merge then watch CI; archive = leave it unmerged.')
    assert parser.request_target(r) == ('example/widget', 1950, '21cb9c1c2')


def test_archive_without_sentence_period():
    r = row()
    r['human_request']['action'] = r['human_request']['action'][:-1]
    assert parser.request_target(r) == ('example/widget', 1950, '21cb9c1c2')


def test_malformed_lifecycle_diagnostic():
    r = row()
    r['human_request']['action'] = r['human_request']['action'].replace('; archive', ', archive')
    assert parser.request_target(r) is None
    assert 'Malformed Unblock/archive section' in parser.parse_failure_reason(r)


def test_multiline_unknown_clause_diagnostic():
    r = row('merge and\nemail credentials')
    assert parser.request_target(r) is None
    assert "Unrecognised Unblock outcome clause: 'email credentials'" in parser.parse_failure_reason(r)


def test_long_whitespace_is_not_backtracking_search():
    # Bound the old cubic extraction path in a child so regression cannot hang pytest.
    import subprocess
    import sys
    code = ('import importlib.util; s=importlib.util.spec_from_file_location("p", ' + repr(parser.__file__) + '); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            'r={"human_request":{"interaction":"approval","action":"Approve PR #1950. " + " "*20000 + "Unblock = " + " "*20000}}; '
            'assert m.request_target(r) is None; assert "Malformed" in m.parse_failure_reason(r)')
    subprocess.run([sys.executable, '-c', code], check=True, timeout=5)
