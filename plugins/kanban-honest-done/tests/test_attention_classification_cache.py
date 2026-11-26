"""Only immutable classification may survive an attention pass; DB verdicts may not."""
import sqlite3
from contextlib import closing

import pytest

from test_honest_done import core, make_board, OWN


@pytest.fixture(autouse=True)
def clear_classification_cache():
    cache = getattr(core, '_attention_classification', None)
    if cache is not None:
        cache.cache_clear()
    yield
    if cache is not None:
        cache.cache_clear()


def test_same_summary_is_classified_once_across_attention_passes(tmp_path, monkeypatch):
    db = make_board(tmp_path, tasks=[(OWN, 'work', 'done')])
    original = core.dropped_work_in_handoff
    calls = []
    def classify(summary, title=''):
        calls.append((summary, title))
        return original(summary, title)
    monkeypatch.setattr(core, 'dropped_work_in_handoff', classify)
    card = {'id': OWN, 'summary': 'Live check was not verified.', 'title': 'work'}
    assert core.needs_attention(card, [db]) is True
    assert core.needs_attention(card, [db]) is True
    assert len(calls) == 1
    card['summary'] = 'Shipped and checked.'
    assert core.needs_attention(card, [db]) is False
    assert len(calls) == 2
    card['title'] = 'other request context'
    assert core.needs_attention(card, [db]) is False
    assert len(calls) == 3  # retain compatibility if a later classifier uses titles


def test_ack_insert_delete_remains_visible_with_cached_summary(tmp_path):
    db = make_board(tmp_path, tasks=[(OWN, 'work', 'done')])
    card = {'id': OWN, 'summary': 'Live check was not verified.'}
    assert core.needs_attention(card, [db]) is True
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('CREATE TABLE task_comments (task_id TEXT, body TEXT)')
        conn.execute('INSERT INTO task_comments VALUES (?, ?)', (OWN, 'honest-done: ack'))
    assert core.needs_attention(card, [db]) is False
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('DELETE FROM task_comments')
    assert core.needs_attention(card, [db]) is True


@pytest.mark.parametrize('relation', ['link', 'creator'])
def test_relation_and_status_changes_remain_fresh(tmp_path, relation):
    import json
    child = 't_11223344'
    db = make_board(tmp_path, tasks=[(OWN, 'work', 'done'), (child, 'child', 'ready')])
    card = {'id': OWN, 'summary': 'Live check was not verified.'}
    assert core.needs_attention(card, [db]) is True
    with closing(sqlite3.connect(db)) as conn, conn:
        if relation == 'link':
            conn.execute('INSERT INTO task_links VALUES (?, ?)', (OWN, child))
        else:
            conn.execute("INSERT INTO task_events(task_id,kind,payload) VALUES (?,'created',?)",
                         (child, json.dumps({'creator_task_id': OWN})))
    assert core.needs_attention(card, [db]) is False
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (child,))
    assert core.needs_attention(card, [db]) is True
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (child,))
    assert core.needs_attention(card, [db]) is False
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute('DELETE FROM task_links')
        conn.execute('DELETE FROM task_events')
    assert core.needs_attention(card, [db]) is True


def test_replica_arrival_and_removal_remain_fresh(tmp_path):
    child = 't_11223344'
    db = make_board(tmp_path, tasks=[(OWN, 'work', 'done')])
    card = {'id': OWN, 'summary': 'Live check was not verified; follow-up ' + child}
    assert core.needs_attention(card, [db]) is True
    shadow = tmp_path / 'kanban' / 'sync-shadow'
    shadow.mkdir(parents=True)
    replica_dir = tmp_path / 'replica-fixture'
    replica_dir.mkdir()
    replica = make_board(replica_dir, tasks=[(child, 'child', 'ready')], links=[(OWN, child)])
    target = shadow / 'board.db'
    replica.rename(target)
    assert core.needs_attention(card, [db]) is False
    target.unlink()
    assert core.needs_attention(card, [db]) is True


def test_classifier_replacement_is_not_hidden_by_cache(tmp_path, monkeypatch):
    db = make_board(tmp_path)
    card = {'id': OWN, 'summary': 'Shipped and checked.'}
    assert core.needs_attention(card, [db]) is False
    monkeypatch.setattr(core, 'dropped_work_in_handoff', lambda *args: [('new policy', 'hit')])
    assert core.needs_attention(card, [db]) is True


def test_oversized_text_bypasses_retention(tmp_path, monkeypatch):
    db = make_board(tmp_path)
    calls = []
    monkeypatch.setattr(core, 'dropped_work_in_handoff', lambda *args: calls.append(args) or [])
    card = {'id': OWN, 'summary': 'x' * 16385}
    for _ in range(2):
        assert core.needs_attention(card, [db]) is False
    assert len(calls) == 2
    assert core._attention_classification.cache_info().currsize == 0


def test_classification_cache_is_bounded(tmp_path, monkeypatch):
    db = make_board(tmp_path)
    monkeypatch.setattr(core, 'dropped_work_in_handoff', lambda *args: [])
    for n in range(1100):
        assert core.needs_attention({'id': OWN, 'summary': str(n)}, [db]) is False
    info = core._attention_classification.cache_info()
    assert info.maxsize == 1024
    assert info.currsize == 1024
