"""PR-only policy: exercising reads, refusal and the actual CAS write boundary."""
import copy
import importlib.util
from pathlib import Path

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


land = load('land')
guard = load('guard')
HEAD = 'a' * 40


class GitHub:
    def __init__(self):
        self.pr = {'number': 7, 'state': 'open', 'draft': False, 'merged': False,
                   'head': {'sha': HEAD, 'ref': 'topic'},
                   'base': {'ref': 'main', 'sha': 'b' * 40, 'repo': {'full_name': land.REPO}},
                   'user': {'login': 'author'}}
        self.runs = [{'id': 1, 'name': 'tests', 'head_sha': HEAD, 'app': {'id': 9},
                      'check_suite': {'id': 101}, 'status': 'completed', 'conclusion': 'success'}]
        self.statuses = []
        self.reviews = [{'id': 1, 'state': 'APPROVED', 'commit_id': HEAD,
                         'user': {'login': 'reviewer', 'type': 'User'},
                         'submitted_at': '2026-01-01T00:00:00Z'}]
        self.required = []
        self.rules = []
        self.writes = []
        self.reads = []
        self.error = ''
        self.move = False

    def __call__(self, *args):
        endpoint = args[1]
        self.reads.append(endpoint)
        if self.error and self.error in endpoint:
            raise land.Refused('unreadable')
        if '--method' in args:
            self.writes.append(args)
            self.pr['merged'] = True
            self.pr['state'] = 'closed'
            return {'merged': True}
        if '/pulls/7/reviews?' in endpoint:
            return copy.deepcopy(self.reviews)
        if endpoint.endswith('/pulls/7'):
            result = copy.deepcopy(self.pr)
            if self.move and self.reads.count(endpoint) > 1:
                result['head']['sha'] = 'c' * 40
            return result
        if '/rules/branches/' in endpoint:
            return self.rules
        if '/branches/main' in endpoint:
            return {'protection': {'required_status_checks': {'contexts': self.required, 'checks': []}}}
        if '/check-runs?' in endpoint:
            return {'check_runs': copy.deepcopy(self.runs)}
        if '/statuses?' in endpoint:
            return self.statuses
        if endpoint == 'user':
            return {'login': 'lander'}
        raise AssertionError(args)


def test_green_independent_exact_head_writes_cas_and_reads_back():
    api = GitHub()
    result = land.land(7, HEAD, read=api)
    assert result['merged'] and result['checks']['mode'] == 'all'
    assert len(api.writes) == 1 and f'sha={HEAD}' in api.writes[0]
    assert api.reads[-1].endswith('/pulls/7')


@pytest.mark.parametrize('state', ['failure', 'cancelled', 'neutral', 'skipped', None])
def test_non_success_held(state):
    api = GitHub()
    api.runs[0]['conclusion'] = state
    with pytest.raises(land.Refused):
        land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('case', ['pending', 'empty', 'no-review', 'stale', 'self', 'bot', 'dismissed',
                                  'changes-requested', 'head-move', 'wrong-head', 'red-status'])
def test_other_holds(case):
    api = GitHub()
    if case == 'pending': api.runs[0]['status'] = 'in_progress'
    if case == 'empty': api.runs = []
    if case == 'no-review': api.reviews = []
    if case == 'stale': api.reviews[0]['commit_id'] = 'c' * 40
    if case == 'self': api.reviews[0]['user']['login'] = 'author'
    if case == 'bot': api.reviews[0]['user']['type'] = 'Bot'
    if case == 'dismissed': api.reviews[0]['state'] = 'DISMISSED'
    if case == 'changes-requested': api.reviews[0]['state'] = 'CHANGES_REQUESTED'
    if case == 'head-move': api.move = True
    if case == 'wrong-head': api.runs[0]['head_sha'] = 'c' * 40
    if case == 'red-status': api.statuses = [{'id': 2, 'context': 'external', 'state': 'failure'}]
    with pytest.raises(land.Refused):
        land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('endpoint', ['/branches/', '/rules/', '/check-runs', '/statuses', '/reviews', 'user'])
def test_unreadable_is_not_no_requirements(endpoint):
    api = GitHub()
    api.error = endpoint
    with pytest.raises(land.Refused):
        land.land(7, HEAD, read=api)
    assert not api.writes


def test_required_checks_select_and_require_matching_app():
    api = GitHub()
    api.required = ['tests']
    api.statuses = [{'id': 2, 'context': 'optional', 'state': 'failure'}]
    assert land.land(7, HEAD, read=api)['checks']['mode'] == 'required'
    api = GitHub()
    api.rules = [{'type': 'required_status_checks', 'parameters': {
        'required_status_checks': [{'context': 'tests', 'integration_id': 10}]}}]
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


def test_same_named_jobs_in_distinct_workflows_do_not_hide_failure():
    api = GitHub()
    api.runs[0]['conclusion'] = 'failure'
    api.runs.append({**api.runs[0], 'id': 2, 'check_suite': {'id': 102}, 'conclusion': 'success'})
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


def test_newer_failure_wins_over_old_success():
    api = GitHub()
    api.runs.append({**api.runs[0], 'id': 2, 'conclusion': 'failure'})
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('command', [
    'gh pr merge 7 -R daveey/hermes-mods --rebase',
    'gh pr merge 7 -R daveey/hermes-mods --auto',
    'gh -R daveey/hermes-mods pr merge 7',
    'GH_REPO=daveey/hermes-mods gh pr merge 7',
    'env GH_REPO=daveey/hermes-mods gh pr merge 7',
    'export GH_REPO=daveey/hermes-mods; gh pr merge 7',
    'bash -c "gh pr merge 7 -R daveey/hermes-mods"',
    'gh api -X PUT repos/daveey/hermes-mods/pulls/7/merge',
    'curl -X PUT https://api.github.com/repos/daveey/hermes-mods/pulls/7/merge',
])
def test_raw_merges_held_without_config(command):
    assert 'land.py' in guard.check_shell(command, '/', guards=[])


def test_python_rest_held_without_config():
    assert 'land.py' in guard.check_python(
        'requests.put("https://api.github.com/repos/daveey/hermes-mods/pulls/7/merge")', '/', guards=[])


def test_direct_push_and_other_repo_unaffected():
    assert guard.check_shell('git push origin HEAD:main', '/', guards=[]) is None
    assert guard.check_shell('gh pr merge 7 -R example/other', '/', guards=[]) is None


def test_kanban_adapter_requires_validator_and_exact_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(land.Path, 'home', lambda: tmp_path)
    api = GitHub()
    with pytest.raises(land.Refused, match='unavailable'):
        land.land(7, HEAD, 'review', 'implementation', read=api)
    assert not api.writes
    path = tmp_path / '.hermes/lib/core-tools/hermes-core-wt-land'
    path.parent.mkdir(parents=True)
    path.write_text('# fixture validator')
    calls = []

    def validate(reader, repo, pr, head, branch, base, task, **kwargs):
        calls.append((repo, pr, head, branch, base, task, kwargs))
        assert reader.json('api', 'user') == ({'login': 'lander'}, None)
        return {'verdict': 'approve', 'reviewed_sha': head,
                'reviewed_pr': f'https://github.com/{repo}/pull/7'}

    monkeypatch.setattr(land.runpy, 'run_path', lambda *a, **k: {'review_gate': validate})
    assert land.land(7, HEAD, 'review', 'implementation', read=api)['merged']
    assert calls[0][-1] == {'impl_task': 'implementation', 'approved_sha': '', 'pr_reuse_s': 0.0}
    api = GitHub()
    monkeypatch.setattr(land.runpy, 'run_path', lambda *a, **k: {'review_gate': lambda *a, **k: {
        'verdict': 'approve', 'reviewed_sha': 'c' * 40}})
    with pytest.raises(land.Refused): land.land(7, HEAD, 'review', 'implementation', read=api)
    assert not api.writes


def test_registered_hook_holds_classifier_errors(monkeypatch):
    spec = importlib.util.spec_from_file_location('landing_plugin', Path(__file__).with_name('__init__.py'))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    monkeypatch.setattr(plugin.guard, '_gql_repo', lambda *_: (_ for _ in ()).throw(ValueError('bad JSON')))
    result = plugin._pre_tool_call('terminal', {'command': 'gh api graphql -f query=mergePullRequest'})
    assert result['action'] == 'block'


def test_lander_cannot_approve_and_later_dismissal_wins():
    api = GitHub()
    api.reviews[0]['user']['login'] = 'lander'
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    api = GitHub()
    api.reviews.append({**api.reviews[0], 'id': 2, 'state': 'DISMISSED'})
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('payload', [{}, {'contexts': [], 'checks': {}},
    {'contexts': [], 'checks': [None]}, {'contexts': [], 'checks': [{'context': 'tests', 'app_id': '9'}]}])
def test_malformed_classic_inventory_never_writes(payload):
    api = GitHub()
    def read(*args):
        if '/branches/main' in args[1]:
            return {'protection': {'required_status_checks': payload}}
        return api(*args)
    with pytest.raises(land.Refused): land.land(7, HEAD, read=read)
    assert not api.writes


@pytest.mark.parametrize('rule', [{}, {'type': 'required_status_checks', 'parameters': {'required_status_checks': {}}}])
def test_malformed_rules_never_write(rule):
    api = GitHub()
    api.rules = [rule]
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('app', [None, {}, {'id': None}, {'id': True}, {'id': '9'}])
def test_malformed_check_app_never_writes(app):
    api = GitHub()
    api.runs[0]['app'] = app
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('stamp', [None, '', 'zzzz', '2026-01-01', '2026-01-01T00:00:00'])
def test_invalid_review_time_cannot_mask_dismissal(stamp):
    api = GitHub()
    api.reviews[0]['submitted_at'] = stamp
    api.reviews.append({**api.reviews[0], 'id': 2, 'state': 'DISMISSED',
                        'submitted_at': '2026-01-02T00:00:00Z'})
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('exe', ['gh', '/usr/bin/gh'])
@pytest.mark.parametrize('call', ['subprocess.run({argv})', 'subprocess.Popen({argv})',
                                  'asyncio.create_subprocess_exec(*{argv})'])
def test_python_literal_argv_raw_merge_held(exe, call):
    argv = repr([exe, 'pr', 'merge', '7', '-R', land.REPO])
    code = call.format(argv=argv)
    assert 'land.py' in guard.check_python(code, '/', guards=[])
    spec = importlib.util.spec_from_file_location('argv_plugin', Path(__file__).with_name('__init__.py'))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    assert plugin._pre_tool_call('execute_code', {'code': code})['action'] == 'block'


@pytest.mark.parametrize('value', [True, False, -1, 0, '1', None, 1.5])
@pytest.mark.parametrize('field', ['run', 'suite', 'status', 'review'])
def test_malformed_evidence_ids_never_write(field, value):
    api = GitHub()
    if field == 'run': api.runs[0]['id'] = value
    if field == 'suite': api.runs[0]['check_suite']['id'] = value
    if field == 'status': api.statuses = [{'id': value, 'context': 'external', 'state': 'success'}]
    if field == 'review': api.reviews[0]['id'] = value
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


@pytest.mark.parametrize('value', [True, 123, None, '', '  ', [], {}])
@pytest.mark.parametrize('field', ['run', 'status', 'review'])
def test_malformed_evidence_names_never_write(field, value):
    api = GitHub()
    if field == 'run': api.runs[0]['name'] = value
    if field == 'status': api.statuses = [{'id': 2, 'context': value, 'state': 'success'}]
    if field == 'review': api.reviews[0]['user']['login'] = value
    with pytest.raises(land.Refused): land.land(7, HEAD, read=api)
    assert not api.writes


def test_graphql_held_without_config(monkeypatch):
    monkeypatch.setattr(guard, '_gql_repo', lambda *_: land.REPO)
    assert guard.check_shell("gh api graphql -f 'query=mutation{mergePullRequest(input:{pullRequestId: \"PR_test123\"}){clientMutationId}}'", '/', guards=[])
