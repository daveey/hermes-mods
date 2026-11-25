"""Which asks the local authority adapter owns: bound to the card, never to the stamp key alone."""
import json
import time

import pytest
from test_local_authority_core import local_world, real
from test_withdrawal import core

ASK = dict(action='One exact exception. Unblock = allow; archive = decline.', interaction='approval',
           why_human='User decision', scope='Synthetic scope', evidence='Fixture')


def world(real, tmp_path, monkeypatch, *, card, stamp_task):
    """A blocked card whose ask carries ``stamp_task``'s stamp ('' = no stamp); ``card`` is 'target' or 'other'."""
    sx = pytest.importorskip('hermes_cli.kanban_selfwork_exception')
    adapter = pytest.importorskip('hermes_cli.kanban_selfwork_local')
    kb, connect, path, _, mod = real
    owner = dict(gatewayId='install:test-install', profile='default', storedSessionId='session-a')
    store = core.ActionStore(tmp_path / 'user-steps/actions.sqlite3')
    with connect(path) as c:
        target = kb.create_task(c, title='Synthetic target', session_id='session-b', assignee='default')
        other = kb.create_task(c, title='Synthetic other card', session_id='session-b', assignee='default')
        monkeypatch.setattr(sx, 'TARGET', sx.Target('synthetic-request', 'a' * 64, 'b' * 64, target, 'synthetic/1'))
        tid = target if card == 'target' else other
        named = dict(target=target, other=other).get(stamp_task, stamp_task)
        stamp = dict(version=1, task=named, request_id=sx.TARGET.request_id, intent_sha256=sx.TARGET.intent_sha256,
                     rejection_sha256=sx.TARGET.rejection_sha256, tenant=None, scope='accounting-recovery-only',
                     approval=sx.TARGET.approval, expires_at=int(time.time()) + 3600, offer_uid='synthetic-offer')
        ask = dict(ASK, **({sx.STAMP: stamp} if stamp_task else {}))
        with kb.write_txn(c):
            c.execute("UPDATE tasks SET status='blocked',human_request=?,session_id='session-a' WHERE id=?",
                      (json.dumps(ask), tid))
            kb._append_event(c, tid, 'blocked', {})
    mirror = mod.Mirror(core, store, boards=[('default', path)], session_exists=lambda sid: sid == 'session-a',
                        tip=lambda sid: sid)
    mirror.reconcile(owner)
    request = store.requests(owner)[0]
    op = store.decide(owner, dict(requestId=request['requestId'], expectedRevision=request['revision'],
                                  choiceId='card-0', revisionText='Unblock', idempotencyKey='one'))['operation']

    def no_adapter(_):
        pytest.fail('ordinary answer reached the local authority adapter')
    return kb, connect, path, mirror, op, sx, adapter, tid, no_adapter


@pytest.mark.parametrize('stamp_task', ['target', ''], ids=['stamp-names-another-card', 'unstamped'])
def test_ask_without_an_offer_on_this_card_keeps_the_plain_unblock(real, tmp_path, monkeypatch, stamp_task):
    kb, connect, path, mirror, op, sx, adapter, tid, no_adapter = world(
        real, tmp_path, monkeypatch, card='other', stamp_task=stamp_task)
    monkeypatch.setattr(adapter, 'deliver', no_adapter)
    receipt = mirror.deliver(op)
    assert receipt['value'] == 'unblock' and receipt['task'] == tid
    assert mirror.deliver(op) == receipt
    with connect(path) as c:
        assert kb.get_task(c, tid).status == 'ready'
        events = [json.loads(r[0] or '{}') for r in c.execute(
            "SELECT payload FROM task_events WHERE task_id=? AND kind='unblocked'", (tid,))]
        assert len(events) == 1 and sx.GRANT not in events[0]
        assert not sx._own(c, 'unblocked')


def test_target_card_with_a_stamp_naming_another_card_is_refused_by_core(real, tmp_path, monkeypatch):
    kb, connect, path, mirror, op, sx, adapter, tid, no_adapter = world(
        real, tmp_path, monkeypatch, card='target', stamp_task='other')
    monkeypatch.setattr(adapter, 'deliver', no_adapter)
    with pytest.raises(core.ActionError, match='transition refused'):
        mirror.deliver(op)
    with connect(path) as c:
        assert kb.get_task(c, tid).status == 'blocked'
        assert not sx._own(c, 'unblocked') and not sx._own(c, 'commented')


@pytest.mark.parametrize('stamp', ['not-a-stamp', None, {'task': 7}, {}], ids=['text', 'null', 'task-not-text', 'empty'])
def test_malformed_stamp_on_the_target_stays_on_the_adapter(real, tmp_path, monkeypatch, stamp):
    kb, connect, path, mirror, op, sx, adapter, tid, _ = world(
        real, tmp_path, monkeypatch, card='target', stamp_task='')
    binding = dict(mirror._bound(op))
    snapshot = json.loads(binding['snapshot'])
    snapshot['ask'][sx.STAMP] = stamp
    binding['snapshot'] = json.dumps(snapshot)
    monkeypatch.setattr(mirror, '_bound', lambda value: binding)
    calls = []

    def refuse(operation_id):
        calls.append(operation_id)
        raise sx.Refused('local_stamp_invalid')
    monkeypatch.setattr(adapter, 'deliver', refuse)
    with pytest.raises(core.ActionError, match='Local authority delivery refused'):
        mirror.deliver(op)
    assert calls == [op['operationId']]
    with connect(path) as c:
        assert kb.get_task(c, tid).status == 'blocked'
        assert not sx._own(c, 'unblocked')


@pytest.fixture
def stamped_open(real, tmp_path, monkeypatch):
    """``local_world`` up to the open stamped ask, before any decision."""
    original = core.ActionStore.decide
    monkeypatch.setattr(core.ActionStore, 'decide', lambda self, owner, data: dict(operation=None))
    out = local_world.__wrapped__(real, tmp_path, monkeypatch)
    monkeypatch.setattr(core.ActionStore, 'decide', original)
    return out


def test_stamped_other_text_is_refused_by_the_adapter_and_writes_nothing(stamped_open):
    kb, connect, path, store, mirror, _, sx, adapter, owner = stamped_open
    request = store.requests(owner)[0]
    op = mirror.decide_other(owner, dict(requestId=request['requestId'], expectedRevision=request['revision'],
                                         choiceId='answer-text', revisionText='Just allow it',
                                         idempotencyKey='text'))['operation']
    assert op['provenance'] == dict(type='kanban_other', version=1)
    with connect(path) as c:
        before = c.execute('SELECT COUNT(*) FROM task_events').fetchone()[0]
    for _ in range(2):  # a retry refuses the same way
        with pytest.raises(core.ActionError, match='Local authority delivery refused: local_operation_intent_mismatch'):
            mirror.deliver(op)
    with connect(path) as c:
        assert kb.get_task(c, sx.TARGET.owner_task).status == 'blocked'
        assert c.execute('SELECT COUNT(*) FROM task_events').fetchone()[0] == before
        assert c.execute('SELECT COUNT(*) FROM task_comments').fetchone()[0] == 0
        assert adapter.recorded(c, 'synthetic-offer') is None
        state = sx.status(c)
        assert state['grant'] is None and state['local_authority'] is None and state['spent'] == []
