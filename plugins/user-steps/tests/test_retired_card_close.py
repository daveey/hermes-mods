"""Retiring a recorded decision closes its kanban decision card, and stale_watch backfills that close for earlier
retirements (t_01a19a11276edc29: operation aded39df was retired, but its card stayed blocked with a human_request,
so the board kept asking David for a decision he had already made). Isolated SQLite contract tests."""
import json
import sqlite3

import pytest

from test_card_delivery import SQLiteCards
from test_decisions import OWNER, decision, request
from test_undelivered_watch import Failing, core, load, sw

cards = load('cards')
REF = dict(gatewayId=OWNER['gatewayId'], profile=OWNER['profile'], type='kanban', board='default', id='card')


class FlakyArchive(SQLiteCards):
    archive_fails = False

    def archive_task(self, c, task_id):
        if self.archive_fails: raise RuntimeError('board locked')
        return super().archive_task(c, task_id)


def setup(tmp_path, *, dependents=False):
    path = tmp_path / 'kanban.db'
    s = core.ActionStore(tmp_path / 'actions.sqlite3')
    r = s.create(OWNER, request(aliases=[REF]))
    backend = FlakyArchive()
    adapter = cards.QualifiedCardDelivery(home=tmp_path, backend=backend)
    with sqlite3.connect(path) as c:
        c.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,body TEXT,session_id TEXT,'
                        'idempotency_key TEXT,claim_lock TEXT,current_run_id INTEGER); '
                        'CREATE TABLE task_comments(id INTEGER PRIMARY KEY,task_id TEXT,author TEXT,body TEXT); '
                        'CREATE TABLE task_links(parent_id TEXT,child_id TEXT);')
        c.execute("INSERT INTO tasks VALUES ('card','blocked','scope',?,?,NULL,NULL)",
                  (OWNER['storedSessionId'], adapter.creation_key(r)))
        if dependents:
            c.execute("INSERT INTO tasks VALUES ('child','todo','waits',NULL,NULL,NULL,NULL)")
            c.execute("INSERT INTO task_links VALUES ('card','child')")
    adapter.bind(r, REF)
    op = s.decide(OWNER, decision(r, choice='approve'))['operation']
    s.dispatch(OWNER, op['operationId'], Failing())  # recorded, never delivered
    return s, adapter, backend, path, op


def board(path):
    with sqlite3.connect(path) as c:
        return (c.execute("SELECT status FROM tasks WHERE id='card'").fetchone()[0],
                c.execute("SELECT state,operation FROM user_steps_card_bindings WHERE task='card'").fetchone(),
                [r[0] for r in c.execute("SELECT body FROM task_comments WHERE task_id='card' ORDER BY id")])


def retire(s, op, adapter):
    return sw.retire_operation(core, s, op['operationId'], reason='owner chat deleted', evidence='no state.db row',
                               local_gateway=OWNER['gatewayId'], cards=adapter)


def test_retire_closes_the_alias_card(tmp_path):
    s, adapter, _b, path, op = setup(tmp_path)
    got = retire(s, op, adapter)
    assert got['operation']['dispatchState'] == 'cancelled' and got['request']['state'] == 'cancelled'
    assert [(g['ref']['id'], g['status'], g['action']) for g in got['cards']] == [('card', 'archived', 'retired+archived')]
    status, binding, comments = board(path)
    assert status == 'archived' and binding == ('retired', op['operationId'])
    assert len(comments) == 1
    assert comments[0].startswith(f"Decided ({op['decision']['choiceId']}) and retired by hygiene: owner chat deleted. "
                                  "Nothing re-asks.")


def test_a_card_with_dependents_stays_blocked(tmp_path):
    s, adapter, _b, path, op = setup(tmp_path, dependents=True)
    got = retire(s, op, adapter)
    assert [(g['status'], g['action']) for g in got['cards']] == [('blocked', 'retired')]
    status, binding, comments = board(path)
    assert status == 'blocked' and binding == ('retired', op['operationId']) and len(comments) == 1
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM tasks WHERE id='child'").fetchone()[0] == 'todo'
    # Backfill leaves it alone: no new comment, still blocked.
    assert sw.close_retired_cards(s, adapter) == []
    assert board(path) == (status, binding, comments)


def test_rerunning_is_a_no_op(tmp_path):
    s, adapter, _b, path, op = setup(tmp_path)
    retire(s, op, adapter)
    before = board(path)
    with pytest.raises(core.ActionError):  # the operation is settled; retiring again is refused
        retire(s, op, adapter)
    [stored] = sw.retired_with_cards(s)
    assert [g['action'] for g in adapter.close_retired(stored)] == ['none']
    assert sw.close_retired_cards(s, adapter) == []
    assert board(path) == before


def test_a_failed_archive_keeps_the_retirement_and_the_next_pass_retries(tmp_path):
    s, adapter, backend, path, op = setup(tmp_path)
    backend.archive_fails = True
    got = retire(s, op, adapter)
    assert got['operation']['dispatchState'] == 'cancelled' and 'board locked' in got['cardsError']
    status, binding, comments = board(path)
    assert status == 'blocked' and binding == ('retired', op['operationId']) and len(comments) == 1
    backend.archive_fails = False
    opened = sw.run(core, load('chat'), s, db=tmp_path / 'missing-state.db', create=lambda c: None, cards=adapter,
                    local_gateway=OWNER['gatewayId'])
    assert 'retired-card:' + op['operationId'] in opened
    assert board(path) == ('archived', binding, comments)  # archived, no second comment


def test_backfill_closes_a_pre_existing_stale_card(tmp_path):
    s, adapter, _b, path, op = setup(tmp_path)
    # Retired before this fix: the store settled it, nothing touched the card.
    sw.retire_operation(core, s, op['operationId'], reason='replay would act on stale intent', evidence='chat moved on',
                        local_gateway=OWNER['gatewayId'])
    assert board(path)[:2] == ('blocked', ('bound', None))
    out = sw.close_retired_cards(s, adapter, local_gateway=OWNER['gatewayId'])
    assert out == [{'operationId': op['operationId'],
                    'cards': [{'id': 'card', 'status': 'archived', 'action': 'retired+archived'}]}]
    status, binding, comments = board(path)
    assert status == 'archived' and binding == ('retired', op['operationId'])
    assert comments == [adapter.retired_comment(json.loads(_op_body(s, op['operationId'])))]
    assert sw.close_retired_cards(s, adapter, local_gateway=OWNER['gatewayId']) == []  # second pass: nothing


def test_backfill_skips_other_gateways_and_reports_errors_without_raising(tmp_path):
    s, adapter, _b, path, op = setup(tmp_path)
    sw.retire_operation(core, s, op['operationId'], reason='r', evidence='e', local_gateway=OWNER['gatewayId'])
    assert sw.close_retired_cards(s, adapter, local_gateway='install:elsewhere') == []
    assert board(path)[0] == 'blocked'

    class Broken:
        def close_retired(self, op): raise RuntimeError('Unknown qualified board')
    [err] = sw.close_retired_cards(s, Broken())
    assert err['operationId'] == op['operationId'] and 'Unknown qualified board' in err['error']


def test_a_delivered_or_withdrawn_binding_is_left_alone(tmp_path):
    s, adapter, _b, path, op = setup(tmp_path)
    sw.retire_operation(core, s, op['operationId'], reason='r', evidence='e', local_gateway=OWNER['gatewayId'])
    with sqlite3.connect(path) as c:
        c.execute("UPDATE user_steps_card_bindings SET state='withdrawn'")
    assert sw.close_retired_cards(s, adapter) == []
    assert board(path)[:2] == ('blocked', ('withdrawn', None))


def _op_body(s, oid):
    with s._read() as c:
        return c.execute('SELECT body FROM operations WHERE id=?', (oid,)).fetchone()[0]
