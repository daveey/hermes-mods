"""Disposable core board input shared by Python and actual-renderer tests."""
import json
from hermes_cli import kanban_db
from test_model import task, bundle, NOW


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
    # Production shape: negative local id for a remote retained event, with a
    # later spend escalation. Core, not the display tail, orders the receipt.
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
    return bundle(row, cards={tid: {'board': 'default', 'detail': detail}}, gateway='hub', profile='default')
