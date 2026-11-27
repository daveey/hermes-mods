"""Canonical decide lifecycle through the real store and status publication."""
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

import test_real_core as helpers

real = helpers.real
ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def decide(real, tmp_path, monkeypatch):
    from hermes_state import SessionDB
    so, plugin = real
    us = plugin._user_steps()
    owner = dict(gatewayId='example-install', profile='default', storedSessionId='chat')
    monkeypatch.setattr(us, 'server_owner', lambda sid: dict(owner, storedSessionId=sid))
    monkeypatch.setattr(us, 'lineage', lambda sid, *a: [sid])
    step = us.add_step('chat', 'Choose a configuration', ['One', 'Two'])
    store = us.ActionStore()
    request = store.migrate_step(owner, step)
    # Finish setup's committed-change refresh before opening the assertion writer.
    # Concurrent last-close/reopen can otherwise invalidate its WAL generation.
    helpers._drain(plugin)
    db = SessionDB(db_path=tmp_path / 'state.db')
    db.append_message('chat', role='assistant', content=helpers.SUMMARY)
    plugin._llm = lambda *a: 'YES'
    yield so, plugin, us, owner, store, request, db
    db.close()


def answer(store, owner, request):
    return store.decide(owner, dict(requestId=request['requestId'], expectedRevision=1,
        choiceId=request['choices'][0]['id'], revisionText=request['choices'][0]['label'],
        idempotencyKey=str(uuid.uuid4())))['operation']


@pytest.mark.parametrize('entry', ['sidebar', 'panel', 'idle'])
@pytest.mark.parametrize('answered', [False, True])
def test_decide_never_done_per_entry(decide, entry, answered):
    so, plugin, us, owner, store, request, db = decide
    if answered:
        op = answer(store, owner, request)
        assert op['dispatchState'] == 'pending'
        assert not us.open_steps('chat')  # reproduces the old provider's blind spot
    out = so.refresh_outcome('chat', 'manual' if entry == 'idle' else 'turn_end', db=db)
    expected = 'waiting' if answered else 'decision'
    assert out['status']['kind'] == expected
    assert out['status']['presentation']['stripe'] != 'done'
    if answered:
        assert out['status']['on'][0]['state'] == 'pending'
    if entry == 'sidebar':
        from hermes_cli.sessions_cmd_outcome import sidebar_rows
        row = next(row for row in sidebar_rows(db) if row['id'] == 'chat')
        assert row['outcome']['status'] == out['status']
    elif entry == 'panel':
        model = load('decide_panel_model', ROOT / 'session-panel' / 'model.py')
        view = model.reduce_panel(dict(outcome_json=out, requests=[store.view(owner, request['requestId'])]), now=1)
        assert view['header']['state'] == expected
        assert view['header']['source'] == 'canonical'
    else:
        from hermes_cli import sessions_cmd_outcome as audit
        row = next(row for row in audit.audit_rows(db, refresh=True) if row['id'] == 'chat')
        assert row['kind'] == expected
        assert so.get_outcome('chat', db=db)['status']['kind'] == expected


def test_failed_delivery_is_agent_pending_not_another_human_ask(decide):
    so, plugin, us, owner, store, request, db = decide
    op = answer(store, owner, request)
    class Busy:
        def deliver(self, operation):
            raise RuntimeError('exact session is busy or has pending input; retry later with the same operation')
    store.dispatch(owner, op['operationId'], Busy())
    status = so.refresh_outcome('chat', 'manual', db=db)['status']
    assert status['kind'] == 'waiting'
    assert status['on'][0]['state'] == 'pending'


def test_consumed_answer_can_finish_and_foreign_answer_cannot_block(decide):
    so, plugin, us, owner, store, request, db = decide
    op = answer(store, owner, request)
    class Receiver:
        def deliver(self, operation):
            return {'deliveryId': operation['operationId']}
        def readback(self, operation, receipt):
            return receipt['deliveryId'] == operation['operationId']
    store.dispatch(owner, op['operationId'], Receiver())
    assert so.refresh_outcome('chat', 'manual', db=db)['status']['kind'] == 'done'


def test_unreadable_decisions_are_not_successful_empty_controls(decide, monkeypatch):
    so, plugin, us, owner, store, request, db = decide
    def unavailable(*a, **kw):
        raise OSError('decision source unavailable')
    monkeypatch.setattr(us.ActionStore, '_read', unavailable)
    status = so.refresh_outcome('chat', 'manual', db=db)['status']
    assert status['coverage']['state'] == 'unknown'
    assert status['kind'] != 'done'
