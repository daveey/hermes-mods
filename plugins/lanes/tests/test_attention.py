"""Same-snapshot card coverage including automation asks with no chat link."""
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
import test_api as fixtures

api = fixtures.api


def test_red_card_partition_matches_lane_total(api):
    asks = pytest.importorskip('hermes_cli.kanban_open_asks')
    if not hasattr(asks, 'needs_you'):
        pytest.skip('requires companion shared attention core')
    from hermes_cli.kanban_lanes_flow import classify_reason, classify_step
    hr = {'action': 'Choose the route', 'why_human': 'Only you know', 'interaction': 'reply', 'scope': 'This card', 'evidence': 'Choice needed'}
    cards = [{'id': name, 'title': name, 'status': status, 'human_request': hr if human else None,
              'block_kind': 'capability', 'lane': 'work'}
             for name, status, human in [('linked', 'blocked', True), ('headless', 'blocked', True),
                                         ('released', 'blocked', True), ('stale-kind', 'scheduled', False)]]
    raw = [{**card, 'reason': classify_reason(card, classify_step(card)), 'attention': card} for card in cards]
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE task_chat_links(task_id,chat_gateway,chat_profile,chat_session,released)')
        db.executemany('INSERT INTO task_chat_links VALUES (?,?,?,?,?)',
                       [('linked', 'edge', 'default', 'chat', 0), ('released', 'edge', 'default', 'old', 1)])
        attention = api._attention_module().project(raw, SimpleNamespace(connection=lambda _: db))
    assert attention['available']
    rows = attention['rows']
    assert {row['id'] for row in rows} == {'linked', 'headless', 'released'}
    assert {row['id'] for row in rows if row['unlinked']} == {'headless', 'released'}
    snap = {'lanes': {'work': {}}}
    for expanded in (False, True):
        view = api._view_module().project(snap, {'cards': raw, 'attention': attention}, expanded)
        assert view['status']['you'] == len(view['attention']['rows']) == 3
        assert sum(bool(r['links']) for r in rows) + sum(r['unlinked'] for r in rows) == 3


def test_board_and_owner_identity_survive_attention_projection(api):
    hr = {'action': 'Choose', 'why_human': 'Only you', 'interaction': 'reply',
          'scope': 'This card', 'evidence': 'Choice needed'}
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE task_chat_links(task_id,chat_gateway,chat_profile,chat_session,released)')
        db.executemany('INSERT INTO task_chat_links VALUES (?,?,?,?,?)',
                       [('ask', 'edge-a', None, 'kanban_owner_terminal', 0),
                        ('ask', 'edge-b', None, 'kanban_owner_terminal', 0)])
        result = api._attention_module().project(
            [{'id': 'ask', 'reason': 'you', 'attention': {
                'id': 'ask', 'status': 'blocked', 'human_request': hr}, 'lane': 'work'}],
            SimpleNamespace(connection=lambda _: db), board='bb')
    row, = result['rows']
    assert row['target']['board'] == 'bb'
    assert row['key'] == ':default:bb:ask'
    assert [link['chat_gateway'] for link in row['links']] == ['edge-a', 'edge-b']
    assert all(link['chat_session'] == 'kanban_owner_terminal' for link in row['links'])


def test_missing_membership_never_invents_unlinked_rows(api):
    with closing(sqlite3.connect(':memory:')) as db:
        row = {'id': 'ask', 'status': 'blocked', 'human_request': {
            'action': 'Choose', 'why_human': 'Only you', 'interaction': 'reply', 'scope': 'This card', 'evidence': 'Choice needed'}}
        result = api._attention_module().project(
            [{'id': 'ask', 'reason': 'you', 'attention': row, 'lane': 'work'}],
            SimpleNamespace(connection=lambda _: db))
    assert not result['available'] and result['rows'] == []
