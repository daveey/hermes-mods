"""Historical acknowledgement must preserve ownership and actual turn completion."""
import sqlite3
import pytest
from test_ledger_consistency import core


def proof_db(tmp_path, notice, *, kind=None, finish='stop', tools=None, active=1):
    path = tmp_path / 'state.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages(session_id, role, content, timestamp, display_kind, finish_reason, tool_calls, active)')
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', ('s', 'user', notice, 101, None, None, None, 1))
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)', ('s', 'assistant', 'Result checked.', 102, kind, finish, tools, active))
    return path


@pytest.mark.parametrize('flags', [
    {'stale': True}, {'unfinished': True}, {'via': 'replica'}, {'binding_id': None},
])
def test_untrusted_remote_cannot_be_removed_even_with_proof(flags):
    tid = 'remote/profile/board/t_same'
    card = {'remote': True, 'status': 'done', 'unfinished': False, 'stale': False,
            'via': 'origin', 'binding_id': 'binding', 'completed_at': 100, **flags}
    row = {'batch': [tid], 'covered': []}
    assert not core.is_settled(card)
    assert core.reconcile_done(row, {tid: card}, lambda *_: True) == {}


@pytest.mark.parametrize('tid', ['remote/profile/board/t_same', 'other/t_same', 't_sam'])
def test_wrong_owner_board_or_partial_id_is_not_proof(tmp_path, tid):
    db = proof_db(tmp_path, '✔ [default] @default Kanban t_same done — local')
    assert not core.ack_proven([db], ['s'], tid, 100)


@pytest.mark.parametrize('kwargs', [
    {'kind': 'commentary'}, {'kind': 'failed_turn'}, {'kind': 'hidden'},
    {'finish': None}, {'finish': 'length'}, {'finish': 'interrupted'},
    {'tools': '[{"id":"call"}]'}, {'active': 0},
])
def test_nonfinal_replies_are_not_proof(tmp_path, kwargs):
    db = proof_db(tmp_path, '✔ [default] @default Kanban t_same done — local', **kwargs)
    assert not core.ack_proven([db], ['s'], 't_same', 100)


@pytest.mark.parametrize('tid,notice', [
    ('t_same', '✔ [default] @default Kanban t_same done — local'),
    ('work/t_same', '✔ [work] @default Kanban t_same done — local'),
    ('remote/profile/board/t_same', '✔ [board] @profile Kanban remote/profile/board/t_same done — remote'),
])
def test_exact_owner_completed_final_control(tmp_path, tid, notice):
    db = proof_db(tmp_path, notice)
    assert core.ack_proven([db], ['s'], tid, 100)


def test_review_notice_is_not_final_done_notice(tmp_path):
    db = proof_db(tmp_path, '✔ [default] @default Kanban t_same review — pending review')
    assert not core.ack_proven([db], ['s'], 't_same', 100)
