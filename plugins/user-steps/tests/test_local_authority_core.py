"""Real producer -> ActionStore -> local Mirror -> core authority adapter."""
import json
import time
import sys

import pytest
from test_kanban_mirror_release_core import real
from test_withdrawal import core


@pytest.fixture
def local_world(real, tmp_path, monkeypatch):
    sx = pytest.importorskip('hermes_cli.kanban_selfwork_exception')
    adapter = pytest.importorskip('hermes_cli.kanban_selfwork_local')
    from hermes_cli import kanban_card_answer_runtime as runtime, kanban_card_answer_transport as transport
    from hermes_cli import install_identity
    kb, connect, path, _, mod = real
    owner = dict(gatewayId='install:test-install', profile='default', storedSessionId='session-a')
    store = core.ActionStore(tmp_path / 'user-steps/actions.sqlite3')
    monkeypatch.setattr(install_identity, 'get_install_id', lambda: 'test-install')
    monkeypatch.setattr(runtime, '_profile', lambda: ('default', tmp_path))
    monkeypatch.setattr(runtime, '_board_db', lambda board: path)
    # This composition tests actual source/store and delivery. Home/envelope policy
    # is covered through real receiver fixtures in core, not reproduced here.
    monkeypatch.setattr(sx, '_home', lambda conn: 'home')
    monkeypatch.setattr(sx, 'original', lambda records: {})
    monkeypatch.setattr(transport, 'Records', lambda: object())
    with connect(path) as c:
        tid = kb.create_task(c, title='Synthetic exact exception', session_id='session-a', assignee='default')
        monkeypatch.setattr(sx, 'TARGET', sx.Target('synthetic-request', 'a' * 64, 'b' * 64, tid, 'synthetic/1'))
        stamp = dict(version=1, task=tid, request_id=sx.TARGET.request_id, intent_sha256=sx.TARGET.intent_sha256,
                     rejection_sha256=sx.TARGET.rejection_sha256, tenant=None, scope='accounting-recovery-only',
                     approval=sx.TARGET.approval, expires_at=int(time.time()) + 3600, offer_uid='synthetic-offer')
        ask = dict(action='One exact exception. Unblock = allow; archive = decline.', interaction='approval',
                   why_human='User decision', scope='Synthetic scope', evidence='Fixture', **{sx.STAMP: stamp})
        with kb.write_txn(c):
            c.execute("UPDATE tasks SET status='blocked',human_request=? WHERE id=?", (json.dumps(ask), tid))
            kb._append_event(c, tid, 'blocked', {})
            kb._append_event(c, tid, sx.OFFERED, {sx.STAMP: stamp, 'ask_sha256': adapter.source.digest(ask)})
    mirror = mod.Mirror(core, store, boards=[('default', path)], session_exists=lambda sid: sid == 'session-a',
                        tip=lambda sid: sid)
    mirror.reconcile(owner)
    request = store.requests(owner)[0]
    op = store.decide(owner, dict(requestId=request['requestId'], expectedRevision=request['revision'],
                                  choiceId='card-0', revisionText='Unblock', idempotencyKey='one'))['operation']
    return kb, connect, path, store, mirror, op, sx, adapter, owner


def test_real_pending_click_grants_and_acknowledges(local_world):
    kb, connect, path, store, mirror, op, sx, adapter, owner = local_world
    assert op['dispatchState'] == 'pending'
    receipt = mirror.deliver(op)
    with connect(path) as c:
        grant = adapter.recorded(c, 'synthetic-offer')
        assert grant['operationId'] == op['operationId']
        assert sx.status(c)['local_authority']['usable'] is False  # source not yet acknowledged
        assert kb.get_task(c, sx.TARGET.owner_task).status == 'ready'
    # Crash-after-board-commit replay while ActionStore remains pending.
    assert mirror.deliver(op) == receipt
    store.dispatch(owner, op['operationId'], mirror)
    saved = store.operation(owner, op['operationId'])['operation']
    assert saved['dispatchState'] == 'delivered' and saved['receipt'] == receipt
    with connect(path) as c:
        assert sx.status(c)['local_authority']['usable'] is True
        assert len(sx._own(c, 'unblocked')) == 1
        assert len(sx._own(c, 'commented')) == 1
    assert mirror.deliver(saved) == receipt


def test_stamped_offer_old_core_refuses_not_plain_unblock(local_world, monkeypatch):
    kb, connect, path, _, mirror, op, sx, _, _ = local_world
    import hermes_cli
    monkeypatch.delattr(hermes_cli, 'kanban_selfwork_local', raising=False)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_selfwork_local', None)
    with pytest.raises(core.ActionError, match='unavailable'):
        mirror.deliver(op)
    with connect(path) as c:
        assert kb.get_task(c, sx.TARGET.owner_task).status == 'blocked'
        assert not sx._own(c, 'unblocked')


def test_old_plugin_plain_unblock_is_refused_by_new_core(local_world):
    kb, connect, path, _, _, op, sx, adapter, _ = local_world
    receipt = dict(destination='kanban_ask', board='default', task=sx.TARGET.owner_task,
                   operationId=op['operationId'], value='unblock', executionStarted=False,
                   fingerprint=adapter.source.digest([op['intent'], op['decision']]))
    with connect(path) as c:
        assert kb.unblock_task(c, sx.TARGET.owner_task,
                               event_extra={'user_steps_projection': receipt}) is False
        assert kb.get_task(c, sx.TARGET.owner_task).status == 'blocked'
        assert not sx._own(c, 'unblocked')


def test_stamped_offer_wrong_adapter_version_refuses(local_world, monkeypatch):
    kb, connect, path, _, mirror, op, sx, adapter, _ = local_world
    monkeypatch.setattr(adapter, 'VERSION', 2)
    with pytest.raises(core.ActionError, match='version mismatch'):
        mirror.deliver(op)
    with connect(path) as c:
        assert kb.get_task(c, sx.TARGET.owner_task).status == 'blocked'
