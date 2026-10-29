"""decisionCard against the REAL core kanban_db (creation lint, human_request validation), not a fake backend.

The fake ProducerCards backend accepts any create_task kwargs, so it never saw core refuse a blocked card
without a human_request (agent_owned_block). These tests import the core checkout (HERMES_CORE_DIR, else
~/.hermes/hermes-agent) and run every board write against a temporary HERMES_HOME.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from test_decisions import OWNER, decision
from test_delivery import cards, Runs
from test_review_regressions import plugin_at, producer_payload


def _core():
    try:
        import hermes_cli.kanban_db  # noqa: F401
    except ImportError:
        root = Path(os.environ.get('HERMES_CORE_DIR') or Path.home() / '.hermes' / 'hermes-agent')
        if (root / 'hermes_cli' / 'kanban_db.py').is_file() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
    kb = pytest.importorskip('hermes_cli.kanban_db', reason='core checkout not importable (set HERMES_CORE_DIR)')
    from hermes_cli.kanban_db_connect import init_db
    return kb, init_db


@pytest.fixture
def real(tmp_path, monkeypatch):
    kb, init_db = _core()
    from agent.delegation_context import kanban_path_is_fenced
    for var in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_BOARD', 'HERMES_KANBAN_TASK', 'HERMES_KANBAN_WORKSPACE'):
        monkeypatch.delenv(var, raising=False)
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    plugin = plugin_at(tmp_path, monkeypatch)
    token = set_hermes_home_override(str(tmp_path))
    path = tmp_path / 'kanban.db'
    if kanban_path_is_fenced(path):
        reset_hermes_home_override(token)
        pytest.skip('Delegated mutation fence: run in a permitted top-level process')
    init_db(path)
    adapter = cards.QualifiedCardDelivery(home=tmp_path)
    monkeypatch.setattr(plugin.cards_mod, 'QualifiedCardDelivery', lambda: adapter)
    try:
        yield plugin, adapter, path, kb
    finally:
        reset_hermes_home_override(token)


def _task(path):
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute('SELECT * FROM tasks').fetchall()
    assert len(rows) == 1, [dict(r) for r in rows]
    return dict(rows[0])


def _publish(plugin, payload):
    return json.loads(plugin.publish_action_request({'request': payload, 'decisionCard': {'board': 'default'}},
                                                    session_id=OWNER['storedSessionId']))


@pytest.mark.parametrize('kind,interaction', [('approval', 'approval'), ('choice', 'reply'),
                                              ('input', 'reply'), ('external_step', 'external')])
def test_decision_card_publishes_through_real_core_with_a_human_request(real, kind, interaction):
    plugin, adapter, path, kb = real
    payload = producer_payload()
    payload['kind'] = kind
    payload['title'] = 'Approve the synthetic change'
    payload['scope']['markdown'] = '  Change example.py and run the tests.  \n\nSecond paragraph is detail.'
    if kind != 'approval':
        payload['choices'] = [dict(id='a', label='A', effect='answer'), dict(id='b', label='B', effect='answer')]
    out = _publish(plugin, payload)
    assert 'error' not in out, out
    r = out['request']
    task = _task(path)
    assert task['status'] == 'blocked'
    assert task['block_kind'] == 'needs_input'
    assert task['idempotency_key'] == adapter.creation_key(r)
    assert task['session_id'] == OWNER['storedSessionId']
    assert task['body'].startswith('Canonical decision metadata only; never execute this card.\n')
    hr = json.loads(task['human_request'])
    assert hr == {
        'action': 'Approve the synthetic change',
        'why_human': hr['why_human'],
        'interaction': interaction,
        'scope': 'Change example.py and run the tests.',
        'evidence': f"ActionRequest {r['requestId']} revision 1 in the For you queue",
    }
    assert 'David' in hr['why_human']
    from hermes_cli.kanban_human_request import needs_human
    assert needs_human(hr, kind=task['block_kind'])
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT request,state FROM user_steps_card_bindings').fetchone() == (
            cards.DecisionInbox._encode(r), 'bound')


def _decide(plugin, adapter, path, r, choice, key='key-1'):
    s = plugin.core.ActionStore()
    op = s.decide(OWNER, decision(r, key=key, choice=choice, text='Change the plan'))['operation']
    receiver = cards.DecisionDelivery(path.parent / 'inbox.db', runs=Runs(), card_receiver=adapter)
    return s.dispatch(OWNER, op['operationId'], receiver)['operation']


def _tasks(path):
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY created_at, rowid')]


@pytest.mark.parametrize('effect,prefix', [('approve', 'Approved'), ('revise', 'Revision requested'),
                                           ('decline', 'Declined;')])
def test_deciding_a_real_core_decision_card_comments_then_archives_it(real, effect, prefix):
    """David answered: the card no longer asks him anything (archived, human_request cleared)."""
    plugin, adapter, path, kb = real
    r = _publish(plugin, producer_payload())['request']
    op = _decide(plugin, adapter, path, r, effect)
    assert op['dispatchState'] == 'delivered'
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT state FROM user_steps_card_bindings').fetchone()[0] == effect
        assert c.execute('SELECT body FROM task_comments').fetchone()[0].startswith(prefix)
        kinds = [k for (k,) in c.execute("SELECT kind FROM task_events WHERE kind IN ('commented','archived') ORDER BY id")]
    task = _task(path)
    assert task['status'] == 'archived'
    assert task['human_request'] is None
    assert kinds[-1] == 'archived'


def test_a_decision_card_with_dependents_stays_blocked_so_nothing_is_released(real):
    """Archiving promotes children; a decision never schedules execution, so a card others wait on is kept."""
    plugin, adapter, path, kb = real
    r = _publish(plugin, producer_payload())['request']
    card = r['aliases'][0]['id']
    from hermes_cli.kanban_db_connect import connect
    c = connect(path)
    try:
        child = kb.create_task(c, title='Implement it', assignee='default', parents=(card,))
    finally:
        c.close()
    assert _decide(plugin, adapter, path, r, 'decline')['dispatchState'] == 'delivered'
    rows = {t['id']: t for t in _tasks(path)}
    assert rows[card]['status'] == 'blocked'
    assert rows[child]['status'] == 'todo'


def test_a_replacement_after_a_decision_gets_a_fresh_card_that_asks_again(real):
    plugin, adapter, path, kb = real
    payload = producer_payload()
    r = _publish(plugin, payload)['request']
    old = r['aliases'][0]
    assert _decide(plugin, adapter, path, r, 'revise')['dispatchState'] == 'delivered'
    payload['aliases'] = [old]; payload['scope']['markdown'] = 'Replacement plan'
    out = json.loads(plugin.publish_action_request({'request': payload, 'requestId': r['requestId'],
                                                    'expectedRevision': 1}, session_id=OWNER['storedSessionId']))
    assert 'error' not in out, out
    revised = out['request']
    kanban = [a for a in revised['aliases'] if a['type'] == 'kanban']
    assert kanban[0] == old and len(kanban) == 2
    rows = {t['id']: t for t in _tasks(path)}
    assert rows[old['id']]['status'] == 'archived'
    fresh = rows[kanban[1]['id']]
    assert fresh['status'] == 'blocked' and fresh['block_kind'] == 'needs_input'
    assert json.loads(fresh['human_request'])['evidence'] == \
        f"ActionRequest {r['requestId']} revision 2 in the For you queue"
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT revision,state FROM user_steps_card_bindings WHERE task=?',
                         (fresh['id'],)).fetchone() == (2, 'bound')
    # Revision 2 is decided on the fresh card alone; the retired card keeps its revision-1 decision.
    assert _decide(plugin, adapter, path, revised, 'approve', key='key-2')['dispatchState'] == 'delivered'
    rows = {t['id']: t for t in _tasks(path)}
    assert rows[fresh['id']]['status'] == 'archived'
    with sqlite3.connect(path) as c:
        states = dict(c.execute('SELECT task,state FROM user_steps_card_bindings'))
    assert states == {old['id']: 'revise', fresh['id']: 'approve'}


def test_an_archive_that_failed_after_the_decision_commit_is_retried(real, monkeypatch):
    plugin, adapter, path, kb = real
    r = _publish(plugin, producer_payload())['request']
    s = plugin.core.ActionStore()
    op = s.decide(OWNER, decision(r, choice='approve'))['operation']
    from types import SimpleNamespace
    api = adapter._api()
    def boom(*a, **k): raise RuntimeError('archive failed')
    adapter._api = lambda: SimpleNamespace(**{**vars(api), 'archive_task': boom})
    with pytest.raises(RuntimeError, match='archive failed'):
        adapter.deliver(op)
    assert _task(path)['status'] == 'blocked'
    del adapter._api
    receipts = adapter.deliver(op)
    assert adapter.readback(op, receipts)
    assert _task(path)['status'] == 'archived'
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT count(*) FROM task_comments').fetchone()[0] == 1


def test_withdrawing_a_card_bound_request_archives_its_decision_card(real):
    plugin, adapter, path, kb = real
    r = _publish(plugin, producer_payload())['request']
    out = json.loads(plugin.publish_action_request({'withdraw': {'requestId': r['requestId'], 'reason': 'Moot now'}},
                                                   session_id=OWNER['storedSessionId']))
    assert 'error' not in out, out
    assert out['request']['state'] == 'withdrawn'
    task = _task(path)
    assert task['status'] == 'archived'
    assert task['human_request'] is None
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT state FROM user_steps_card_bindings').fetchone()[0] == 'withdrawn'
        assert 'Moot now' in c.execute('SELECT body FROM task_comments').fetchone()[0]
    # Repeating the withdrawal is a no-op that keeps the card closed.
    again = json.loads(plugin.publish_action_request({'withdraw': {'requestId': r['requestId'], 'reason': 'Moot now'}},
                                                     session_id=OWNER['storedSessionId']))
    assert again['request']['state'] == 'withdrawn'
    assert _task(path)['status'] == 'archived'
