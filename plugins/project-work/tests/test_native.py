import copy
import importlib
import uuid
import pytest
from conftest import PACKAGE, start

NativeBridge = importlib.import_module(PACKAGE + '.native').NativeBridge


def bridge(env, **overrides):
    service, _, ports = env
    args = dict(rpc=lambda *args: None, owner_tool=lambda *args: None,
        sessions=lambda query: [], cards=ports.get_card,
        slack=lambda *args: None, evidence=lambda row: {}, bot_user_id='bot')
    args.update(overrides)
    return NativeBridge(service.policy, **args)


def test_native_session_rpc_and_persisted_prompt(env):
    service, _, _ = env
    requests = []
    stored = {'id': 'stored', 'source': 'desktop', 'cwd': service.policy.cwd,
        'profile': service.policy.builder_profile, 'parent_session_id': None,
        'title': 'Project work wid', 'messages': []}
    def rpc(method, params):
        requests.append((method, params))
        if method == 'session.create':
            return {'session_id': 'live', 'stored_session_id': 'stored'}
        if method == 'prompt.submit':
            stored['messages'].append({'role': 'user', 'content': params['text']})
            return {}
    native = bridge(env, rpc=rpc, sessions=lambda query: [stored])
    native.create_session('wid', 'Project work wid', service.policy.cwd, service.policy.builder_profile)
    native.prompt('live', 'planning only', 'key')
    assert native.recover_prompt('live', 'key')
    assert [r[0] for r in requests] == ['session.create', 'prompt.submit']
    assert requests[0][1]['source'] == 'desktop'
    assert requests[0][1]['hidden'] is False
    assert 'parent_session_id' not in requests[0][1]
    native.archive('stored', service.policy.builder_profile, True)
    assert requests[-1] == ('session.archive', {'session_key': 'stored', 'profile': service.policy.builder_profile, 'archived': True})


def test_native_card_uses_tool_not_private_database(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    params = row['cards']['implement']['params']
    called = []
    def owner_tool(name, args, sid):
        called.append((name, args, sid))
        return {'task_id': 'native-task'}
    def cards(cid):
        return {'id': cid, 'body': called[0][1]['body']}
    native = bridge(env, owner_tool=owner_tool, cards=cards,
                    sessions=lambda query: [ports.get_session(row['stored_session_id'])])
    native.live[row['session_id']] = row['stored_session_id']
    assert native.create_card(row['workflow_id'], 'implement', params) == 'native-task'
    name, args, sid = called[0]
    assert name == 'kanban_create' and sid == row['session_id']
    assert args['project'] == service.policy.project and 'project_id' not in args
    assert args['parents'] == []
    with pytest.raises(PermissionError):
        native.create_card(row['workflow_id'], 'implement', dict(params, repo='elsewhere/repo'))


def valid_facts(row):
    sha = 'a' * 40
    return dict(repo=row['policy']['repo'], workflow_id=row['workflow_id'], generation=row['generation'],
        head_sha=sha, implementation_run_id=1, review_run_id=2, review_head_sha=sha, review_approved=True,
        tests=[{'sha': sha, 'ok': True, 'command': 'pytest'}], required_checks=['unit'],
        checks=[{'name': 'unit', 'sha': sha, 'status': 'completed', 'conclusion': 'success'}],
        delivery={'sha': sha, 'verified': True, 'receipt': 'release-verified'}, pending_followups=False, live_writer=False)


@pytest.mark.parametrize('mutation', ['none', 'stale_review', 'same_run', 'no_checks', 'pending_check', 'stale_test', 'no_delivery', 'writer'])
def test_exact_head_evidence(env, mutation):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    ports.done()
    row['evidence'] = {}
    for stage, entry in row['cards'].items():
        card = ports.get_card(entry['id'])
        card['body'] = f"project-work:{row['workflow_id']}:{row['generation']}:{stage}"
        card['parents'] = card['depends_on']
        row['evidence'][stage] = card
    facts = valid_facts(row)
    if mutation == 'stale_review': facts['review_head_sha'] = 'b' * 40
    if mutation == 'same_run': facts['review_run_id'] = 1
    if mutation == 'no_checks': facts['required_checks'] = []
    if mutation == 'pending_check': facts['checks'][0]['status'] = 'in_progress'
    if mutation == 'stale_test': facts['tests'][0]['sha'] = 'b' * 40
    if mutation == 'no_delivery': facts['delivery']['receipt'] = ''
    if mutation == 'writer': facts['live_writer'] = True
    assert bridge(env, evidence=lambda row: facts).verify_evidence(row) is (mutation == 'none')


def test_slack_pagination_readback_and_recovery(env):
    row = {'channel_id': 'channel', 'thread_id': '1.0', 'message_id': '1.0'}
    key, text = 'stable-key', 'Completed verified work'
    outbound = {'ts': '2.0', 'thread_ts': '1.0', 'user': 'bot', 'text': text,
                'client_msg_id': str(uuid.uuid5(uuid.NAMESPACE_URL, key))}
    messages = [{'ts': '1.0', 'user': 'owner', 'text': 'work'}, outbound]
    calls = []
    def slack(method, payload):
        calls.append((method, payload))
        if method == 'chat.postMessage':
            return {'ok': True, 'channel': 'channel', 'ts': '2.0'}
        if not payload['cursor']:
            return {'ok': True, 'messages': messages[:1], 'has_more': True,
                    'response_metadata': {'next_cursor': 'page2'}}
        return {'ok': True, 'messages': messages[1:], 'has_more': False}
    native = bridge(env, slack=slack)
    receipt = native.post_result(row, text, key)
    assert native.read_result(row, receipt, text)
    assert native.recover_result(row, text, key) == receipt
    assert calls[0][1]['thread_ts'] == '1.0'
    assert calls[0][1]['client_msg_id'] == outbound['client_msg_id']
    messages.append({'ts': '3.0', 'user': 'member', 'text': 'required followup'})
    assert not native.read_result(row, receipt, text)


def test_native_missing_session_not_fabricated(env):
    native = bridge(env)
    with pytest.raises(ValueError): native.get_session('missing')
    assert native.recover_session('unknown') is None
