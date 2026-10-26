"""Third-pass adversarial repros for PR10 29fb5bd (reviewer-only, not part of PR).

Assertions describe the DESIRED invariant; they FAIL while the defect exists.
"""
from dataclasses import asdict, replace
import importlib
import pytest
from conftest import PACKAGE, start
from test_native import bridge, native_card, native_project

runtime = importlib.import_module(PACKAGE + '.runtime')
client = importlib.import_module(PACKAGE + '.client')


def _native_for(env, service, row, stage_card, owner_calls, live_ids):
    stored = {'id': row['stored_session_id'], 'source': 'desktop', 'cwd': service.policy.cwd,
              'profile': service.policy.builder_profile, 'parent_session_id': None,
              'title': row['title'], 'messages': [], 'archived': False}
    def rpc(method, params):
        # Native session.resume reuses a live session in-process, otherwise cold
        # resumes under a NEW live id (tui_gateway/methods_session.py:1085).
        return {'session_id': live_ids.pop(0), 'stored_session_id': stored['id']}
    def owner_tool(name, args, live):
        owner_calls.append(live)
        if live != 'L2':
            raise RuntimeError('session not found')  # L1 died with the old gateway
        return {'task_id': stage_card['id']}
    return bridge(env, rpc=rpc, owner_tool=owner_tool,
                  sessions=lambda q: [stored], projects=lambda q: native_project(service),
                  card_keys=lambda q: [], cards=lambda cid: stage_card)


# HIGH-ish (regression at 29fb5bd): _card_target ignores params['session_id'] and picks
# the FIRST live id ever mapped to the stored session. self.live is never pruned, so after
# a gateway restart (plugin runtime survives, recover_session adds L2) create_card invokes
# kanban_create through the dead L1. The create_intent is already persisted, so the next
# reconcile only recover_card()s, misses, and wedges on "operator recovery required".
def test_create_card_uses_current_live_owner_after_resume(env):
    service, ctx, ports = env
    row = start(service, ctx, 'work')
    params = dict(row['cards']['implement']['params'], session_id='L2')
    card = native_card(service, row, params, 'native-task',
                       f"project-work:{row['workflow_id']}:1:implement\n")
    calls = []
    native = _native_for(env, service, row, card, calls, ['L2'])
    native.live['L1'] = row['stored_session_id']           # created before gateway restart
    assert native.recover_session(row['workflow_id'])['session_id'] == 'L2'  # _advance resume
    assert native.create_card(row['workflow_id'], 'implement', params) == 'native-task'
    assert calls == ['L2']


# LOW-MEDIUM: Controller dead-letters ANY PermissionError/ValueError from request,
# including environmental ones (configured cwd briefly missing/replaced during a
# re-clone). Root requests have no backfill, so the owner's request is silently lost.
def test_transient_path_failure_is_not_dead_lettered(env, tmp_path, monkeypatch):
    service, ctx, _ = env
    ctl = runtime.Controller(service)
    class Direct:
        def call(self, op, **kw):
            return ctl.call(op, kw)
    queue = client.DurableIngressQueue(tmp_path / 'queue' / 'q.db', Direct())
    real = service._authorize
    def flaky(c):
        raise PermissionError('Configured path changed')
    monkeypatch.setattr(service, '_authorize', flaky)
    queue.append('request', context=asdict(ctx), text='work')
    try:
        queue.drain_once()
    except Exception:
        pass
    monkeypatch.setattr(service, '_authorize', real)
    with queue.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM rejected').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM pending').fetchone()[0] == 1
