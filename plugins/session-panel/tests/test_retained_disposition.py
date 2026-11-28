"""Settled retained disposition through the real board adapter, not event-tail guesses."""
import json
import pytest
from test_core_readers import api, board, readers_for, kanban_db
from test_model import model, task, bundle, NOW, human

TEXT = 'stopped · incomplete · evidence retained · no action needed'


def retained_bundle(api, conn, readers, *, replica=False, pending=False, newer=False, backfill=False):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(task_events)')}
    for name in ('uid', 'origin_gateway'):
        if name not in columns:
            conn.execute(f'ALTER TABLE task_events ADD COLUMN {name} TEXT')
    remote_id = min(-43, conn.execute('SELECT coalesce(min(id),0)-1 FROM task_events').fetchone()[0])
    tid = kanban_db.create_task(conn, title='Stopped test — incomplete; evidence retained. No action needed.',
                               body='Current disposition: no action needed.\nHistorical acceptance remains UNMET.', initial_status='running')
    conn.execute("UPDATE tasks SET status='triage' WHERE id=?", (tid,))
    conn.execute('UPDATE task_events SET created_at=? WHERE task_id=?', (NOW - 9000, tid))
    conn.execute('UPDATE tasks SET created_at=? WHERE id=?', (NOW - 9000, tid))
    # Same production shape: remote retained receipt is backfilled with a negative
    # local SQL id, then spend-review escalation arrives after the settled stop.
    payload = {'episode_uid': 'episode-fixture', 'event_uid': 'retained-fixture',
               'disposition': {'reason': 'decision_pending' if pending else 'scope_stopped',
                               'summary': 'Recovery withdrawn; original acceptance UNMET', 'evidence': ['Retain evidence']}}
    conn.execute('INSERT INTO task_events(id,task_id,kind,created_at,payload,uid,origin_gateway) VALUES (?,?,?,?,?,?,?)',
                 (remote_id, tid, 'decision_retained', NOW - 8000, json.dumps(payload), 'retained-fixture', 'remote'))
    conn.execute('INSERT INTO task_events(task_id,kind,created_at,payload) VALUES (?,?,?,?)',
                 (tid, 'spend_review_escalated', NOW - 7000, json.dumps({'episode': 'episode-fixture', 'reason': 'Original acceptance unmet'})))
    if newer:
        conn.execute('INSERT INTO task_events(task_id,kind,created_at,payload) VALUES (?,?,?,?)',
                     (tid, 'claimed', NOW - 6000, '{}'))
    if backfill:
        conn.execute('INSERT INTO task_events(task_id,kind,created_at,payload,origin_gateway) VALUES (?,?,?,?,?)',
                     (tid, 'claimed', NOW - 8500, '{}', 'remote'))
    conn.commit()
    detail = readers._detail(conn, tid, 'default', replica)
    detail['owner'] = 'remote' if replica else None
    row = task(tid, status='triage', gateway='remote' if replica else 'hub')
    row.pop('wait', None)
    b = bundle(row, cards={tid: {'board': 'default', 'detail': detail}}, gateway='hub', profile='default')
    return b


def one(b):
    return next(r for rows in model.reduce_panel(b, now=NOW)['sections'].values() for r in rows)


@pytest.mark.parametrize('replica', [False, True])
@pytest.mark.parametrize('backfill', [False, True])
def test_retained_stop_outranks_later_spend_review(api, board, monkeypatch, replica, backfill):
    path, conn = board
    b = retained_bundle(api, conn, readers_for(api, path, monkeypatch), replica=replica, backfill=backfill)
    row = one(b)
    assert row['detail'].startswith(TEXT)
    assert 'owner has not decided' not in row['detail']
    assert 'waiting on' not in row['detail']
    assert row['retained_disposition']['text'] == TEXT
    assert not model.reduce_panel(b, now=NOW)['sections']['done']


@pytest.mark.parametrize('case', ['pending', 'human', 'newer', 'missing', 'foreign', 'running'])
def test_retained_stop_does_not_mask_live_or_unknown_evidence(api, board, monkeypatch, case):
    path, conn = board
    b = retained_bundle(api, conn, readers_for(api, path, monkeypatch), pending=case == 'pending', newer=case == 'newer')
    raw = b['tasks'][0]; detail = b['cards'][raw['id']]['detail']
    if case == 'human':
        raw.update(human_request=human(), block_kind='needs_input', wait={'wait_state': 'human'})
    if case == 'missing':
        detail['task'].pop('retained_stop', None)
    if case == 'foreign':
        raw['gateway'] = 'another-owner'
    if case == 'running':
        raw['status'] = 'running'
    row = one(b)
    assert 'retained_disposition' not in row
    assert not row['detail'].startswith(TEXT)
    if case == 'human':
        assert model.reduce_panel(b, now=NOW)['sections']['needs_you']
    if case == 'pending':
        assert 'owner has not decided' in row['detail']


def test_stale_retained_stop_keeps_disposition_separate_from_current_trust(api, board, monkeypatch):
    path, conn = board
    b = retained_bundle(api, conn, readers_for(api, path, monkeypatch), replica=True)
    b['tasks'][0].update(stale=True, synced_at=NOW - 400)
    row = one(b)
    assert row['detail'].startswith(TEXT)
    assert row['evidence_state'] == 'unknown'
    assert 'last recorded disposition' in row['detail']
    assert any('freshness' in text.lower() for text in row['details'])
    assert not row.get('wait_kind')
