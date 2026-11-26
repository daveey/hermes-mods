"""A committed write to what the outcome's `controls` read refreshes the chat's stored outcome, with no turn.

session-outcome registers the `user-steps` provider: open ledger steps plus the action store's open requests
and undelivered answers (decision_obligations.py). Core calls refresh_outcome only on turn, notice, card,
reap and mute events, so before this nothing refreshed the stored outcome when an ask opened, was revised,
answered, delivered or withdrawn outside a turn. Each writer below is exercised through its entry point
against the REAL core outcome store, with the real session-outcome provider registered.
"""
import copy
import importlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[2]
CORE = Path(os.environ.get('HERMES_CORE_CHECKOUT') or Path.home() / '.hermes' / 'hermes-agent').expanduser()
SID = 'chat-controls'


@pytest.fixture
def real(tmp_path, monkeypatch):
    if not (CORE / 'hermes_state_outcome.py').is_file():
        pytest.skip(f'needs Hermes core (none at {CORE})')
    pytest.importorskip('psutil', reason="core's card reads need psutil (run with --with psutil)")
    monkeypatch.setenv('HERMES_DISABLE_LAZY_INSTALLS', '1')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_SESSION_SOURCE', 'desktop')
    monkeypatch.syspath_prepend(str(CORE))
    for name in [m for m in sys.modules if m.startswith('hermes_state') or m == 'hermes_constants']:
        monkeypatch.delitem(sys.modules, name, raising=False)
    so = importlib.import_module('hermes_state_outcome')
    import hermes_state
    monkeypatch.setattr(hermes_state, 'DEFAULT_DB_PATH', tmp_path / 'state.db', raising=False)
    so._reset_hooks_for_tests()
    # Controls are real; unrelated fleet/card readers are not part of this disposable chat.
    monkeypatch.setattr(so, '_card_items', lambda *a, **k: ([], []))
    db = hermes_state.SessionDB(db_path=tmp_path / 'state.db')
    try:
        db.create_session(SID, source='desktop')
        db.append_message(SID, role='user', content='set up the deploy')
        db.append_message(SID, role='assistant', content='Which configuration should I use?')
    finally:
        db.close()
    spec = importlib.util.spec_from_file_location('session_outcome_controls_refresh', PLUGINS / 'session-outcome' / '__init__.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin.register(type('Ctx', (), {'register_tool': lambda *a, **k: None, 'register_hook': lambda *a, **k: None})())
    so.register_outcome_judge(None)  # no LLM: the heuristic decides the reply part, controls decide this test
    us = plugin._user_steps()
    monkeypatch.setattr(us, 'server_owner', lambda sid, **kw: dict(
        gatewayId='install:example', profile='default', storedSessionId=sid))
    # Keep real compression/fork lineage; this suite must exercise provider attribution.
    calls = []
    quietly = so.refresh_outcomes_quietly

    def counted(sids, trigger, *, db=None):
        calls.append((list(sids), trigger))
        return quietly(sids, trigger, db=db)

    monkeypatch.setattr(so, 'refresh_outcomes_quietly', counted)
    yield so, plugin, us, calls
    assert drain(us)
    so._reset_hooks_for_tests()


def drain(us):
    # Original synchronous implementation has no worker to drain (for behavioral RED controls).
    fn = getattr(us, 'flush_controls_refresh', None)
    return fn(60) if fn else True


def stored(so, sid=SID):
    worker = sys.modules.get('_user_steps_controls_refresher')
    if worker:
        assert worker.instance.flush(10)
    return so.get_outcome(sid) or {}


def items(so):
    return [(i['id'], i.get('question')) for i in stored(so).get('items') or ()]


def pending(so):
    on = stored(so).get('on') or ()
    return [i['id'] for i in on if str(i.get('id', '')).startswith('decision-answer:')]


def canonical(us):
    owner = us.server_owner(SID)
    step = us.add_step(SID, 'Select a configuration', ['One', 'Two'])
    store = us.ActionStore()
    return owner, step, store, store.migrate_step(owner, step)


def choose(store, owner, request):
    return store.decide(owner, dict(requestId=request['requestId'], expectedRevision=request['revision'],
        choiceId=request['choices'][0]['id'], revisionText=request['choices'][0]['label'],
        idempotencyKey=str(uuid.uuid4())))['operation']


def test_ledger_add_step_refreshes_the_stored_outcome(real):
    so, plugin, us, calls = real
    assert stored(so) == {}
    step = us.add_step(SID, 'Ship it now?', ['Yes', 'Later'])
    assert items(so) == [(step['id'], 'Ship it now?')]
    assert calls and calls[-1] == ([us.lineage_root(SID)], 'notice')


def test_ledger_resolve_refreshes_the_stored_outcome(real):
    so, plugin, us, calls = real
    step = us.add_step(SID, 'Ship it now?', ['Yes', 'Later'])
    assert stored(so).get('kind') == 'decision'
    lg = us.Ledger()
    assert us.resolve(lg, lg.root(SID), [step['id']], 'Yes', 'user') == [step['id']]
    assert items(so) == []
    assert stored(so).get('kind') != 'decision'


def test_ledger_write_that_leaves_open_asks_unchanged_does_not_refresh(real):
    so, plugin, us, calls = real
    us.add_step(SID, 'Ship it now?', ['Yes', 'Later'])
    assert drain(us)
    before = len(calls)
    lg = us.Ledger()
    with lg.edit(lg.root(SID)) as d:
        d['user_msg'] = 'unrelated bookkeeping'
    assert len(calls) == before


def test_action_store_revision_refreshes_the_question(real):
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    replacement = copy.deepcopy(request)
    replacement['title'] = 'Current revised question'
    store.revise(owner, request['requestId'], request['revision'], replacement)
    assert items(so) == [(step['id'], 'Current revised question')]


def test_action_store_decision_refreshes_to_the_pending_answer(real):
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    op = choose(store, owner, request)
    assert items(so) == []
    assert pending(so) == ['decision-answer:' + op['operationId']]


def test_outbox_delivery_state_refreshes_away_the_pending_answer(real):
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    op = choose(store, owner, request)
    assert pending(so)
    with store._tx() as c:  # the outbox's own settlement goes through this same transaction
        op['dispatchState'] = 'delivered'
        c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), op['operationId']))
    assert pending(so) == [] and items(so) == []


def test_withdraw_request_refreshes_the_stored_outcome(real):
    """The REST withdraw route and stale_watch.withdraw_old both call core.withdraw_request."""
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    assert items(so) == [(step['id'], 'Select a configuration')]
    us.withdraw_request(store, us.Ledger(), owner, request['requestId'], by='user', reason='not needed')
    assert items(so) == []


def test_direct_request_state_change_refreshes(real):
    """chat.py's chat retirement and the resolve route update requests inside ActionStore._tx."""
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    with store._tx() as c:
        c.execute("UPDATE requests SET state='superseded' WHERE id=?", (request['requestId'],))
    assert items(so) == []
    assert calls[-1] == ([SID], 'notice')


def test_unrelated_or_rolled_back_transactions_do_not_refresh(real):
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    assert drain(us)
    before = len(calls)
    store.mark_notified(owner, [request['requestId']])  # lifecycle bookkeeping, not controls
    with pytest.raises(RuntimeError):
        with store._tx() as c:
            c.execute("UPDATE requests SET state='superseded' WHERE id=?", (request['requestId'],))
            raise RuntimeError('abort')
    assert len(calls) == before
    assert items(so) == [(step['id'], 'Select a configuration')]


def test_no_refresh_in_a_process_without_the_provider(real):
    """A refresh there would store an outcome without its open asks."""
    so, plugin, us, calls = real
    so._providers.pop('user-steps')
    us.add_step(SID, 'Ship it now?', ['Yes', 'Later'])
    assert drain(us)
    assert calls == [] and stored(so) == {}


@pytest.mark.parametrize('writer', ['ledger', 'requests'])
def test_compression_root_write_refreshes_tip(real, tmp_path, writer):
    from hermes_state import SessionDB
    so, plugin, us, calls = real
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.end_session(SID, end_reason='compression')
        db.create_session('tip', source='desktop', parent_session_id=SID)
        db.append_message('tip', role='user', content='Choose the setup')
        db.append_message('tip', role='assistant', content='Which configuration?')
    assert us.lineage('tip') == ['tip', SID]
    step = us.add_step(SID, 'Compressed ask', ['One', 'Two'])
    if writer == 'requests':
        store = us.ActionStore()
        request = store.migrate_step(us.server_owner(SID), step)
        replacement = copy.deepcopy(request)
        replacement['title'] = 'Revised compressed ask'
        store.revise(us.server_owner(SID), request['requestId'], request['revision'], replacement)
    outcome = stored(so, 'tip')
    expected = 'Revised compressed ask' if writer == 'requests' else 'Compressed ask'
    assert outcome['items'][0]['question'] == expected


@pytest.mark.parametrize('marker', ['_reset_from', '_branched_from', '_delegate_from'])
def test_fork_write_targets_its_own_outcome(real, tmp_path, marker):
    from hermes_state import SessionDB
    so, plugin, us, calls = real
    with SessionDB(db_path=tmp_path / 'state.db') as db:
        db.end_session(SID, end_reason='compression')
        db.create_session('tip', source='desktop', parent_session_id=SID)
        db.create_session('fork', source='desktop', parent_session_id=SID, model_config={marker: SID})
        for sid in ['tip', 'fork']:
            db.append_message(sid, role='user', content='Choose')
            db.append_message(sid, role='assistant', content='Which configuration?')
        assert db.get_compression_chain(SID) == [SID, 'tip']
    assert us.lineage('fork') == ['fork']
    step = us.add_step('fork', 'Fork ask', ['One', 'Two'])
    assert stored(so, 'fork')['items'][0]['id'] == step['id']
    assert step['id'] not in json.dumps(stored(so, 'tip'))


def test_stale_watch_registers_missing_providers_before_withdraw(real, monkeypatch):
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    assert items(so)
    so._reset_hooks_for_tests()
    from hermes_cli import plugins_cmd
    monkeypatch.setattr(plugins_cmd, '_get_enabled_set', lambda: {'session-outcome'})
    monkeypatch.setattr(plugins_cmd, '_get_disabled_set', lambda: set())
    spec = importlib.util.spec_from_file_location('controls_stale_watch', PLUGINS / 'user-steps' / 'stale_watch.py')
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    assert watch.register_controls_providers()
    assert set(so._providers) >= {'user-steps', 'person-dependencies'}
    us.withdraw_request(store, us.Ledger(), owner, request['requestId'], by='agent', reason='stale')
    assert items(so) == []


def test_retry_bookkeeping_does_not_refresh_controls(real):
    so, plugin, us, calls = real
    owner, step, store, request = canonical(us)
    op = choose(store, owner, request)
    assert pending(so)
    before = len(calls)
    for attempt in range(3):
        with store._tx() as c:
            op.update(attempts=attempt, lastError='temporary', nextAttemptAt=attempt + 1)
            c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), op['operationId']))
    assert drain(us)
    assert len(calls) == before
    with store._tx() as c:
        op['dispatchState'] = 'delivered'
        c.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), op['operationId']))
    assert pending(so) == []
