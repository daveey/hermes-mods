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
        return native_card(service, row, params, cid, called[0][1]['body'])
    native = bridge(env, owner_tool=owner_tool, cards=cards,
                    projects=lambda query: native_project(service), card_keys=lambda query: [],
                    sessions=lambda query: [ports.get_session(row['stored_session_id'])])
    native.live[row['session_id']] = row['stored_session_id']
    assert native.create_card(row['workflow_id'], 'implement', params) == 'native-task'
    name, args, sid = called[0]
    assert name == 'kanban_create' and sid == row['session_id']
    assert args['project'] == 'project-id' and 'project_id' not in args
    assert args['parents'] == []
    with pytest.raises(PermissionError):
        native.create_card(row['workflow_id'], 'implement', dict(params, repo='elsewhere/repo'))


def native_project(service):
    p = service.policy
    # Native Project.to_dict fields plus trusted reader profile/git-remote facts.
    return dict(id='project-id', slug=p.project, name='Project', created_at=1,
                board_slug=p.board, primary_path=p.cwd, archived=False, folders=[],
                profile=p.builder_profile, repo=p.repo)


def native_card(service, row, params, cid, body):
    # Native Task fields; board and parents are added by the scoped reader.
    return dict(id=cid, body=body, project_id='project-id', board=service.policy.board,
                assignee=params['profile'], session_id=row['stored_session_id'],
                workspace_kind='worktree', workspace_path=service.policy.cwd + '/.worktrees/' + cid,
                parents=params['depends_on'], idempotency_key=params['idempotency_key'],
                completion_contract=service.policy.repo, status='running')


@pytest.mark.parametrize('field', ['profile', 'repo', 'primary_path', 'board_slug', 'slug', 'archived'])
def test_project_preflight_blocks_create(env, field):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    project = native_project(service)
    project[field] = 'wrong'
    calls = []
    native = bridge(env, projects=lambda q: project, card_keys=lambda q: [],
                    owner_tool=lambda *a: calls.append(a),
                    sessions=lambda q: [ports.get_session(row['stored_session_id'])])
    native.live[row['session_id']] = row['stored_session_id']
    with pytest.raises(PermissionError):
        native.create_card(row['workflow_id'], 'implement', row['cards']['implement']['params'])
    assert calls == []


@pytest.mark.parametrize('field', ['project_id', 'board', 'assignee', 'session_id', 'workspace_path',
                                   'workspace_kind', 'parents', 'idempotency_key', 'completion_contract', 'body'])
def test_full_card_scope_readback(env, field):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    params = row['cards']['implement']['params']
    card = native_card(service, row, params, 'task', f"project-work:{row['workflow_id']}:{row['generation']}:implement\n")
    card[field] = 'wrong'
    native = bridge(env, projects=lambda q: native_project(service), card_keys=lambda q: ['task'],
                    cards=lambda cid: card, sessions=lambda q: [ports.get_session(row['stored_session_id'])])
    native.live[row['session_id']] = row['stored_session_id']
    with pytest.raises(RuntimeError):
        native.recover_card(row['workflow_id'], 'implement', params)


def test_recovery_includes_archived_and_never_creates(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    params = row['cards']['implement']['params']
    card = native_card(service, row, params, 'task', f"project-work:{row['workflow_id']}:{row['generation']}:implement\n")
    card['status'] = 'archived'
    ids, queries, creates = ['task'], [], []
    def keys(query):
        queries.append(query)
        return ids
    native = bridge(env, projects=lambda q: native_project(service), card_keys=keys,
                    cards=lambda cid: card, owner_tool=lambda *a: creates.append(a),
                    sessions=lambda q: [ports.get_session(row['stored_session_id'])])
    native.live[row['session_id']] = row['stored_session_id']
    assert native.recover_card(row['workflow_id'], 'implement', params) == 'task'
    assert native.create_card(row['workflow_id'], 'implement', params) == 'task'
    ids.clear()
    assert native.recover_card(row['workflow_id'], 'implement', params) is None
    ids.extend(['task', 'duplicate'])
    with pytest.raises(RuntimeError):
        native.recover_card(row['workflow_id'], 'implement', params)
    assert creates == []
    assert all(q['include_archived'] is True and q['profile'] == service.policy.builder_profile for q in queries)


def test_missing_trusted_callbacks_fail_closed(env):
    native = bridge(env)
    with pytest.raises(RuntimeError):
        native.create_card('wid', 'implement', {})
    with pytest.raises(RuntimeError):
        native.recover_card('wid', 'implement', {})


@pytest.mark.parametrize('mutation', ['none', 'missing', 'duplicate', 'edited', 'user', 'thread_ts', 'text', 'bot_id', 'subtype'])
def test_exact_owner_approval_required(env, mutation):
    from conftest import AuthContext
    row = dict(channel_id='channel', thread_id='1.0', message_id='1.0', workflow_id='wid',
               plan_hash='hash', approved_message_id='1.5', approved_by='owner')
    approval = dict(ts='1.5', thread_ts='1.0', user='owner', text='!project approve wid hash')
    messages = [dict(ts='1.0', user='owner', text='work'), approval,
                dict(ts='2.0', thread_ts='1.0', user='bot', text='done')]
    if mutation == 'missing': messages.remove(approval)
    elif mutation == 'duplicate': messages.append(dict(approval))
    elif mutation != 'none': approval[mutation] = 'wrong'
    native = bridge(env, slack=lambda *a: dict(ok=True, messages=messages))
    ctx = AuthContext('slack', 'team', 'channel', '1.5', '1.0', 'owner', 'ingress')
    assert native.verify_approval(ctx, row, 'hash') is (mutation == 'none')
    assert native.read_result(row, dict(channel='channel', ts='2.0'), 'done') is (mutation == 'none')


@pytest.mark.parametrize('subtype', [None, 'file_share', 'thread_broadcast'])
def test_thread_events_preserve_human_and_approval_attempts(env, subtype):
    row = dict(channel_id='channel', thread_id='1.0', message_id='1.0')
    messages = [dict(ts='2.0', thread_ts='1.0', user='member', subtype=subtype,
                     text='!project approve invalid hash'),
                dict(ts='1.0', user='owner', text='work'),
                dict(ts='3.0', thread_ts='1.0', user='bot', text='ignore')]
    native = bridge(env, slack=lambda *a: dict(ok=True, messages=messages))
    events = native.thread_events(row)
    assert [ctx.message_id for ctx, _ in events] == ['1.0', '2.0']
    assert events[1][1] == '!project approve invalid hash'
    assert events[1][0].user_id == 'member'
    assert events[1][0].team_id == 'team'
    assert events[1][0].profile == 'ingress'
    messages[0]['edited'] = {'ts': '4.0'}
    with pytest.raises(RuntimeError): native.thread_events(row)


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
    row = {'channel_id': 'channel', 'thread_id': '1.0', 'message_id': '1.0',
           'workflow_id': 'wid', 'plan_hash': 'hash', 'approved_message_id': '1.5', 'approved_by': 'owner'}
    key, text = 'stable-key', 'Completed verified work'
    outbound = {'ts': '2.0', 'thread_ts': '1.0', 'user': 'bot', 'text': text,
                'client_msg_id': str(uuid.uuid5(uuid.NAMESPACE_URL, key))}
    messages = [{'ts': '1.0', 'user': 'owner', 'text': 'work'},
                {'ts': '1.5', 'thread_ts': '1.0', 'user': 'owner', 'text': '!project approve wid hash'}, outbound]
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
