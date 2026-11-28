"""Overdue-triage cards (no human ask) get Release / Archive / Keep parked through real core, never a live board."""

import json
import time

import pytest
from core_runtime import require_isolated_core
from test_withdrawal import ROOT, OWNER, core, load_module

SESSION = 'session-a'


@pytest.fixture
def real(tmp_path, monkeypatch):
    """(kb, connection, board path, action store, mirror module) on an isolated core home."""
    require_isolated_core()
    attention = pytest.importorskip('hermes_cli.kanban_triage_attention')
    if not hasattr(attention, 'PARK_ACK'):
        pytest.skip('this core has no Keep-parked acknowledgement (triage_park_acknowledged)')
    from pathlib import Path
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.delegation_context import kanban_path_is_fenced

    path = tmp_path / 'kanban.db'
    if kanban_path_is_fenced(path):
        pytest.skip('Run this integration in the top-level worker')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    try:
        from hermes_cli import kanban_spend_reviewer as reviewer
        monkeypatch.setattr(reviewer, 'queue', lambda *a, **kw: False)
    except ImportError:
        pass
    token = set_hermes_home_override(str(tmp_path))
    try:
        kb.init_db(path)
        c = connect(path)
        try:
            mod = load_module('overdue_mirror', ROOT / 'kanban_mirror.py')
            monkeypatch.setattr(mod._overdue, '_gateway', lambda mirror: 'test-gateway')
            yield kb, c, path, core.ActionStore(tmp_path / 'actions.sqlite3'), mod
        finally:
            c.close()
    finally:
        reset_hermes_home_override(token)


def parked(kb, c, kind, payload=None, *, block_kind=None, age=7200, session=SESSION, title='Fix the flaky sync test'):
    """A card that entered triage ``age`` seconds ago through the real lifecycle event ``kind``."""
    task = kb.create_task(c, title=title, assignee='default', session_id=session)
    with kb.write_txn(c):
        c.execute("UPDATE tasks SET status='triage', block_kind=? WHERE id=?", (block_kind, task))
        kb._append_event(c, task, kind, payload or {})
        c.execute('UPDATE task_events SET created_at=? WHERE task_id=?', (int(time.time()) - age, task))
    return task


def mirror_for(mod, store, path):
    return mod.Mirror(core, store, boards=[('default', path)],
                      session_exists=lambda sid: sid == SESSION, tip=lambda sid: sid)


def open_requests(store):
    return [r for r in store.requests(OWNER) if r['state'] in ('open', 'needs_context') and not r.get('resolution')]


def choose(store, request, name, key='op'):
    choice = next(ch for ch in request['choices'] if ch['label'].split(':', 1)[1].strip().startswith(name))
    return store.decide(OWNER, dict(requestId=request['requestId'], expectedRevision=request['revision'],
                                    choiceId=choice['id'], revisionText=choice['label'],
                                    idempotencyKey=key))['operation']


def status(c, task):
    return c.execute('SELECT status FROM tasks WHERE id=?', (task,)).fetchone()[0]


def published(mod, store, path):
    mirror = mirror_for(mod, store, path)
    mirror.reconcile(OWNER)
    return mirror, open_requests(store)


def test_retained_hold_gets_a_plain_card_and_release_moves_it(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained',
                  {'summary': 'Keep stopped: no further approach for t_01a2205a8d32f06e until comment 262804 is answered'})
    mirror, (request,) = published(mod, store, path)
    labels = [ch['label'] for ch in request['choices']]
    assert labels == ['A: Release (recommended)', 'B: Archive', 'C: Keep parked']
    assert all(ch.get('explain') for ch in request['choices'])
    scope = request['scope']['markdown']
    # What and why in plain words: no card id or comment number to type.
    assert '“Fix the flaky sync test”' in scope and 'Keep stopped: no further approach for another card' in scope
    assert 't_' not in scope.replace(task, '') and 'comment 262804' not in scope
    assert request['scope']['sourceRefs'][0]['id'] == task  # the link the panel opens
    shown = mirror.present(request)
    assert shown['presentation']['kind'] == 'kanban-overdue' and shown['presentation']['other'] is False
    receipt = mirror.deliver(choose(store, request, 'Release'))
    assert receipt['value'] == 'release' and receipt['outcome'] in ('promoted', 'released')
    assert status(c, task) in ('ready', 'todo')
    assert mirror.readback(choose(store, request, 'Release'), receipt)


def test_spend_hold_release_is_a_persons_release(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'spend_budget_triaged', {'reason': 'run budget exhausted', 'cause': 'scope_stopped'})
    from hermes_cli import kanban_release
    assert kanban_release.held(c, task) == task
    mirror, (request,) = published(mod, store, path)
    assert 'more agent runs' in request['scope']['markdown']
    op = choose(store, request, 'Release')
    receipt = mirror.deliver(op)
    assert receipt['released'] == task and receipt['outcome'] == 'released'
    assert status(c, task) == 'ready'
    renewed = json.loads(c.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='scope_renewed'",
                                   (task,)).fetchone()[0])
    assert renewed['authority_kind'] == 'human'
    assert mirror.deliver(op) == receipt  # a retry writes nothing more


def test_lane_gate_park_releases_by_promotion(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'blocked', {'status': 'triage', 'reason': 'lane admission pending'}, block_kind='lane_gate')
    mirror, (request,) = published(mod, store, path)
    assert 'lane planner' in request['scope']['markdown']
    receipt = mirror.deliver(choose(store, request, 'Release'))
    assert receipt['outcome'] == 'promoted' and status(c, task) == 'ready'
    assert c.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='promoted_manual'", (task,)).fetchone()


def test_safety_hold_offers_no_release_and_says_why(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'spend_budget_triaged', {'reason': 'refused', 'cause': 'safety_refusal'})
    mirror, (request,) = published(mod, store, path)
    assert [ch['label'] for ch in request['choices']] == ['A: Archive', 'B: Keep parked (recommended)']
    assert 'provider safety refusal' in request['scope']['markdown']
    receipt = mirror.deliver(choose(store, request, 'Keep parked'))
    assert receipt['value'] == 'keep_parked' and status(c, task) == 'triage'


def test_needs_input_asked_elsewhere_still_gets_a_card(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'blocked', {'status': 'triage', 'reason': 'asked in the other chat'}, block_kind='needs_input')
    _, (request,) = published(mod, store, path)
    assert 'asked for input in another chat' in request['scope']['markdown']
    assert request['scope']['sourceRefs'][0]['id'] == task


def test_archive_stops_the_card(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'})
    mirror, (request,) = published(mod, store, path)
    receipt = mirror.deliver(choose(store, request, 'Archive'))
    assert receipt['value'] == 'archive' and status(c, task) == 'archived'


def test_keep_parked_silences_until_the_next_lifecycle_event_or_a_day(real, monkeypatch):
    kb, c, path, store, mod = real
    from hermes_cli import kanban_triage_attention as attention
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'})
    mirror, (request,) = published(mod, store, path)
    receipt = mirror.deliver(choose(store, request, 'Keep parked'))
    assert receipt['value'] == 'keep_parked' and status(c, task) == 'triage'
    assert c.execute('SELECT 1 FROM task_events WHERE task_id=? AND kind=?', (task, attention.PARK_ACK)).fetchone()
    row = attention.annotate(c, [{'id': task, 'status': 'triage'}])[0]
    assert not attention.overdue(row, now=time.time())
    mirror.reconcile(OWNER)
    assert open_requests(store) == []  # silent while acknowledged
    later = time.time() + attention.PARK_QUIET_S + 1
    monkeypatch.setattr(mod.time, 'time', lambda: later)
    monkeypatch.setattr(mod._overdue.time, 'time', lambda: later)
    mod._MEMO.clear()
    mirror.reconcile(OWNER)
    (again,) = open_requests(store)  # the red comes back as a new card after 24 h
    assert again['requestId'] != request['requestId']


def test_keep_parked_ends_at_the_next_lifecycle_event(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'})
    mirror, (request,) = published(mod, store, path)
    mirror.deliver(choose(store, request, 'Keep parked'))
    with kb.write_txn(c):
        kb._append_event(c, task, 'decision_retained', {'summary': 'stopped again'})
    mod._MEMO.clear()
    mirror.reconcile(OWNER)
    assert len(open_requests(store)) == 1


def test_not_yet_overdue_or_a_human_ask_gets_no_overdue_card(real):
    kb, c, path, store, mod = real
    parked(kb, c, 'decision_retained', {'summary': 'fresh'}, age=60)
    _, requests = published(mod, store, path)
    assert requests == []


def test_a_changed_card_refuses_a_stale_click(real):
    kb, c, path, store, mod = real
    task = parked(kb, c, 'decision_retained', {'summary': 'stopped'})
    mirror, (request,) = published(mod, store, path)
    op = choose(store, request, 'Archive')
    with kb.write_txn(c):
        c.execute("UPDATE tasks SET status='ready' WHERE id=?", (task,))
        kb._append_event(c, task, 'unblocked', {})
    with pytest.raises(core.ActionError):
        mirror.deliver(op)
    assert status(c, task) == 'ready'
