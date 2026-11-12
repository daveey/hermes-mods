"""One decision across both producers: session_outcome(decide) then action_request is one request.

Replays agent-d5fb2ec9c4 (sanitized): a chat declared a Post/Edit choice with session_outcome(decide), then
published the exact-text approval with action_request two seconds later. The decide call returned bare step ids
only, so the approval cited nothing and became a second request: the user clicked Post twice (the first click
answered the choice and correctly authorized nothing). Now decide returns each step's qualified sourceRef and
canonical requestId/revision, citing that ref upgrades the declared request in place, and a publication that
cites none of the chat's open declared decisions says so (a hint only: nothing is merged by wording).
All persistence is under pytest's tmp_path.
"""
import importlib.util
import json
import sqlite3
import sys
import types
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUTCOME = ROOT.parent / 'session-outcome' / '__init__.py'
OWNER = {'gatewayId': 'test-gateway', 'profile': 'default', 'storedSessionId': 'S1'}
TEXT = 'Instrumental convergence never needed self-regard. Almost any objective goes better if you keep running.'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeOutcomeApi(types.ModuleType):
    def __init__(self):
        super().__init__('hermes_state_outcome')
        self.declared = []

    def declare_outcome(self, sid, outcome):
        self.declared.append((sid, outcome))

    def register_outcome_provider(self, name, fn):
        pass

    def register_outcome_judge(self, fn):
        pass


def _owner(sid):
    return dict(OWNER, storedSessionId=sid)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    for k in ('HERMES_KANBAN_TASK', 'HERMES_SESSION_OUTCOME_OFF', 'HERMES_USER_STEPS_OFF', 'HERMES_PLATFORM',
              'HERMES_SESSION_PLATFORM'):
        monkeypatch.delenv(k, raising=False)
    db = sqlite3.connect(tmp_path / 'state.db')
    db.execute('create table sessions (id text primary key, parent_session_id text, end_reason text, source text)')
    db.executemany('insert into sessions values (?,?,?,?)', [('S1', None, None, 'desktop'), ('S2', None, None, 'desktop')])
    db.commit()
    db.close()
    monkeypatch.setitem(sys.modules, 'hermes_state_outcome', FakeOutcomeApi())
    # Both producers, as installed side by side: session-outcome loads its own copy of user-steps/core.py.
    for key in ('session_outcome_user_steps_core',):
        sys.modules.pop(key, None)
    outcome = load('xprod_session_outcome', OUTCOME)
    outcome.PLUGINS = ROOT.parent
    outcome._interactive = lambda platform='', session_id='': True
    steps = load('xprod_user_steps', ROOT / '__init__.py')
    us_core = outcome._user_steps()
    assert us_core is not None and us_core is not steps.core
    for core in (steps.core, us_core):
        monkeypatch.setattr(core, 'home', lambda: tmp_path)
        monkeypatch.setattr(core, 'server_owner', _owner)
    return types.SimpleNamespace(outcome=outcome, steps=steps, us_core=us_core, tmp=tmp_path)


def decide(env, items, sid='S1'):
    return json.loads(env.outcome.handle({'kind': 'decide', 'items': items}, session_id=sid))


def post_choice(question='Post this reply?'):
    return {'question': question, 'choices': [{'label': 'Post', 'value': 'post'}, {'label': 'Edit', 'value': 'edit'}]}


def approval_body(title='Post the Bluesky reply', markdown=TEXT, source_refs=()):
    return dict(schemaVersion=2, kind='approval', title=title,
                scope=dict(markdown=markdown, complete=True, sourceRefs=list(source_refs)), limits=[], exclusions=[],
                outcomes=dict(approve='Post exactly this text', revise='Keep it unposted', decline='Discard'),
                choices=[dict(id=e, label=l, effect=e) for e, l in
                         (('approve', 'Post'), ('revise', 'Revise'), ('decline', 'Decline'))],
                aliases=[], state='open')


def publish(env, args, sid='S1'):
    return json.loads(env.steps.publish_action_request(args, session_id=sid))


def live(env, sid='S1'):
    return [r for r in env.steps.core.ActionStore().requests(_owner(sid))
            if r['state'] in ('open', 'needs_context') and not r.get('resolution')]


def test_decide_returns_qualified_source_ref_and_request_identity(env):
    out = decide(env, [post_choice()])
    assert out['ok'] and out['declared'], out
    [ident] = out['requests']
    assert ident['stepId'] == out['steps'][0]
    assert ident['sourceRef'] == dict(gatewayId='test-gateway', profile='default', type='user_step', id=ident['stepId'])
    [req] = live(env)
    assert ident['requestId'] == req['requestId'] and ident['revision'] == req['revision'] == 1
    assert ident['sourceRef'] in req['aliases']
    assert 'sourceRefs' in out['upgrade'] and 'second' in out['upgrade']


def test_upgrade_with_returned_provenance_is_one_request_with_exact_scope(env):
    # The incident sequence: declare the choice, then publish the exact-text approval citing what decide returned.
    ident = decide(env, [post_choice()])['requests'][0]
    out = publish(env, {'request': approval_body(source_refs=[ident['sourceRef']])})
    assert 'error' not in out, out
    req = out['request']
    assert req['requestId'] == ident['requestId'] and req['revision'] == ident['revision'] + 1
    assert req['kind'] == 'approval' and req['scope']['markdown'] == TEXT and req['scope']['complete']
    assert ident['sourceRef'] in req['aliases']
    [only] = live(env)
    assert only['requestId'] == ident['requestId']
    # Consent semantics unchanged: publishing/upgrading records no decision.
    assert not only.get('resolution')


def test_citing_a_declared_step_not_yet_canonical_still_upgrades_it(env, monkeypatch):
    # An older session-outcome (or a failed early migration) leaves only the ledger step until next turn's
    # pre_llm_call. Citing its exact ref must migrate and fold it now, not mint a request the next turn duplicates.
    monkeypatch.setattr(env.outcome, '_step_identities', lambda us, sid, steps: [], raising=False)
    step_id = decide(env, [post_choice()])['steps'][0]
    assert live(env) == []
    ref = dict(gatewayId='test-gateway', profile='default', type='user_step', id=step_id)
    out = publish(env, {'request': approval_body(source_refs=[ref])})
    assert 'error' not in out, out
    for step in env.steps.core.Ledger().read('S1')['steps']:  # what next turn's pre_llm_call does
        if step.get('status') == 'open':
            env.steps.core.ActionStore().migrate_step(_owner('S1'), step)
    [only] = live(env)
    assert only['requestId'] == out['request']['requestId'] and ref in only['aliases']
    assert only['kind'] == 'approval' and only['scope']['markdown'] == TEXT


def test_uncited_publication_names_the_open_declared_decision_and_merges_nothing(env):
    # The live failure: action_request with empty aliases/sourceRefs after a decide. Still published separately
    # (wording never establishes identity), but the reply names the declared decision to upgrade or withdraw.
    ident = decide(env, [post_choice()])['requests'][0]
    out = publish(env, {'request': approval_body()})
    assert out['request']['requestId'] != ident['requestId']
    assert ident['requestId'] in out['note'] and ident['stepId'] in out['note'] and 'sourceRefs' in out['note']
    assert 'authorizes nothing' in out['note']
    assert len(live(env)) == 2  # nothing merged or withdrawn


def test_concurrent_decisions_fold_only_the_cited_one(env):
    out = decide(env, [post_choice('Post the reply to thread A?'), post_choice('Post the reply to thread B?')])
    a, b = out['requests']
    assert a['requestId'] != b['requestId'] and a['sourceRef'] != b['sourceRef']
    up = publish(env, {'request': approval_body(source_refs=[b['sourceRef']])})
    assert up['request']['requestId'] == b['requestId']
    reqs = {r['requestId']: r for r in live(env)}
    assert set(reqs) == {a['requestId'], b['requestId']}
    assert reqs[a['requestId']]['revision'] == 1 and reqs[a['requestId']]['kind'] != 'approval'
    # The other open declared decision is still named, so the agent can settle it too.
    hint = up.get('note', '').split('does not cite:')[-1]
    assert a['requestId'] in hint and b['requestId'] not in hint and a['stepId'] in hint


def test_missing_identity_returns_no_fabricated_ref(env, monkeypatch):
    def no_identity(sid):
        raise env.us_core.ActionError('Server gateway identity or stored session missing', 503)
    monkeypatch.setattr(env.us_core, 'server_owner', no_identity)
    out = decide(env, [post_choice()])
    assert out['ok'] and out['steps']
    [ident] = out['requests']
    assert ident == {'stepId': out['steps'][0], 'identity': 'unavailable'}
    assert 'do not invent' in out['upgrade']


def test_unrelated_asks_stay_separate_and_other_chats_are_not_named(env):
    other = decide(env, [post_choice()], sid='S2')
    out = publish(env, {'request': approval_body(title='Deploy build 9', markdown='Deploy build 9 to staging.')})
    assert other['steps'][0] not in json.dumps(out)
    assert 'note' not in out, out
    assert len(live(env)) == 1
