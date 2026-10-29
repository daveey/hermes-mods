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


def test_deciding_a_real_core_decision_card_comments_on_it(real):
    plugin, adapter, path, kb = real
    r = _publish(plugin, producer_payload())['request']
    s = plugin.core.ActionStore()
    op = s.decide(OWNER, decision(r, choice='decline'))['operation']
    receiver = cards.DecisionDelivery(path.parent / 'inbox.db', runs=Runs(), card_receiver=adapter)
    assert s.dispatch(OWNER, op['operationId'], receiver)['operation']['dispatchState'] == 'delivered'
    with sqlite3.connect(path) as c:
        assert c.execute('SELECT state FROM user_steps_card_bindings').fetchone()[0] == 'decline'
        body = c.execute('SELECT body FROM task_comments').fetchone()[0]
    assert body.startswith('Declined;')
    assert _task(path)['status'] == 'blocked'


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
