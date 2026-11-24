"""Real composed canonical-authority regressions for the unactivated source cutover.

Canonical request authority (ActionStore + SessionDB through CoreReaders) stays
separate from board acquisition. No production route is activated: the disposable
composition, a temporary HERMES_HOME and synthetic ids only. Barriers are state
transitions (drain/join), never sleeps.
"""
import os
import uuid

from test_core_readers import api, board, kanban_db
from test_feed import feedmod
from test_partial_projection_core import action_core
from test_source_coordinator_core import compose, drain, readers_for, strict_empty


def make_request(core, actions, session, title):
    owner, rid = core.server_owner(session), str(uuid.uuid4())
    actions.create(owner, dict(schemaVersion=2, owner=dict(owner), requestId=rid, revision=1,
        kind='approval', title=title,
        scope=dict(markdown='Only change example.py.', complete=True, sourceRefs=[]),
        limits=[dict(key='cost', value='0', permissionRequired=True)], exclusions=['No network changes'],
        outcomes=dict(approve='Authorize only this scope', revise='Keep blocked', decline='Cancel'),
        choices=[dict(id=e, label=e.title(), effect=e) for e in ('approve', 'revise', 'decline')],
        aliases=[], state='open'))
    return owner, rid


def identity(rows):
    """Exact request identity: id, revision, owning stored session, choice ids, state."""
    return sorted((r['requestId'], r['revision'], r['owner']['storedSessionId'],
                   tuple(c['id'] for c in r['choices']), r['state']) for r in rows)


def asked(bundle):
    return {row.get('request_id') for row in bundle['model']['sections']['needs_you']} - {None}


def test_real_sessiondb_reparent_retires_old_ancestor_consent_and_keeps_actionstore_unchanged(api, board, tmp_path, monkeypatch):
    from hermes_state import SessionDB
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    db = SessionDB(db_path=tmp_path / 'state.db')
    for sid, parent in (('P', None), ('Q', None), ('C', 'P')):
        db.create_session(sid, 'desktop', parent_session_id=parent)
    core = action_core()
    actions = core.ActionStore()
    (op, rp), (oq, rq), (oc, rc) = (make_request(core, actions, s, f'Authorize scope {s}') for s in ('P', 'Q', 'C'))
    task = kanban_db.create_task(conn, title='Board work', session_id='C')
    f = feedmod.Feed(r, emit=lambda _: None)
    f.watch(['C'])
    lane = compose(f, r)
    try:
        f.changes(['C']); drain(lane)
        old = f.bundles['C']['bundle']
        assert old['lineage'] == ['C', 'P']
        assert identity(old['requests']) == identity(actions.views(oc) + actions.views(op))
        assert {rc, rp} == {x['requestId'] for x in old['requests']} == asked(old)
        stored = {rid: actions.get(o, rid) for o, rid in ((op, rp), (oq, rq), (oc, rc))}

        # A genuine SessionDB write moves C from parent P to parent Q.
        db._execute_write(lambda c: c.execute('UPDATE sessions SET parent_session_id=? WHERE id=?', ('Q', 'C')))
        f._publish(f._compute_all(['C'], refresh=True))
        now = f.bundles['C']['bundle']
        expected = identity(actions.views(oc) + actions.views(oq))
        assert now['lineage'] == ['C', 'Q'] and identity(now['requests']) == expected
        assert {rc, rq} == asked(now) and rp not in asked(now)
        # The old ancestor request is merely out of lineage: its ActionStore record is untouched.
        assert {rid: actions.get(o, rid) for o, rid in ((op, rp), (oq, rq), (oc, rc))} == stored
        # The fast canonical lane, which reads with the held lineage, agrees.
        f.refresh_decision('C', readers=r)
        assert identity(f.bundles['C']['bundle']['requests']) == expected

        # A board-only publication from the held pre-reparent base cannot restore P's ask.
        with lane.lock:
            lane.bases['C'] = old
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('After reparent', task)); conn.commit()
        drain(lane)
        value = f.bundles['C']['bundle']
        assert value['tasks'][0]['title'] == 'After reparent'
        assert value['lineage'] == ['C', 'Q'] and identity(value['requests']) == expected
        assert asked(value) == {rc, rq}

        # Real consent on the NEW ancestor's exact request, then another stale board publication.
        operation = actions.decide(oq, dict(requestId=rq, expectedRevision=1, choiceId='approve',
            idempotencyKey=str(uuid.uuid4())))['operation']
        assert operation['intent']['executionAuthorized'] and operation['dispatchState'] == 'pending'
        f.refresh_decision('C', readers=r)
        decided = identity(actions.views(oc) + actions.views(oq))
        assert identity(f.bundles['C']['bundle']['requests']) == decided and asked(f.bundles['C']['bundle']) == {rc}
        before_operation = actions.operation(oq, oid=operation['operationId'])['operation']
        with lane.lock:
            lane.bases['C'] = old
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('After consent', task)); conn.commit()
        drain(lane)
        value = f.bundles['C']['bundle']
        assert value['tasks'][0]['title'] == 'After consent'
        assert identity(value['requests']) == decided and asked(value) == {rc}
        assert not value['model']['sections']['running'], 'board acquisition manufactured execution'
        assert actions.operation(oq, oid=operation['operationId'])['operation'] == before_operation
        assert actions.get(oc, rc) == stored[rc] and actions.get(op, rp) == stored[rp]
    finally:
        f.stopped.set(); lane.close(); r.release(); db.close(); conn.close()


def test_real_state_outage_is_not_cleared_or_trusted_by_board_and_recovers_without_board_write(api, board, tmp_path, monkeypatch):
    from hermes_state import SessionDB
    path, conn = board
    r = readers_for(api, path, monkeypatch)
    monkeypatch.setattr(r, 'external_capture', strict_empty)
    state = tmp_path / 'state.db'
    aside = tmp_path / 'state.db.outage-aside'
    db = SessionDB(db_path=state)
    db.create_session('S', 'desktop')
    core = action_core()
    actions = core.ActionStore()
    owner, rid = make_request(core, actions, 'S', 'Authorize the outage scope')
    task = kanban_db.create_task(conn, title='Board work', session_id='S')
    now, pushes = [1800000000.], []
    f = feedmod.Feed(r, emit=pushes.append, clock=lambda: now[0])
    f.watch(['S'])
    lane = compose(f, r)
    try:
        f.changes(['S']); drain(lane)
        held = f.bundles['S']['bundle']
        assert identity(held['requests']) == identity(actions.views(owner)) and asked(held) == {rid}
        assert not f._decision_failures
        stored = actions.get(owner, rid)

        # The real reader boundary fails: CoreReaders cannot open SessionDB's state file.
        os.replace(state, aside)
        assert f.refresh_decisions() == 0
        assert 'S' in f._decision_failures and f._decision_retry_at is not None
        # Genuine consent is recorded while the panel cannot read it. Held rows stay held.
        operation = actions.decide(owner, dict(requestId=rid, expectedRevision=1, choiceId='approve',
            idempotencyKey=str(uuid.uuid4())))['operation']
        assert operation['intent']['executionAuthorized'] and operation['dispatchState'] == 'pending'
        pending = actions.operation(owner, oid=operation['operationId'])['operation']
        assert f.bundles['S']['bundle']['requests'] == held['requests']

        # Board publication (even from a held old base) and heartbeats cannot clear the failure,
        # revive/erase consent from the held projection, or renew execution trust.
        with lane.lock:
            lane.bases['S'] = held
        conn.execute('UPDATE tasks SET title=? WHERE id=?', ('Board change during outage', task)); conn.commit()
        drain(lane)
        for _ in range(2):
            now[0] += 30
            f.beat()
            assert 'S' not in pushes[-1]['fresh']
        value = f.bundles['S']['bundle']
        assert value['tasks'][0]['title'] == 'Board change during outage'
        assert 'S' in f._decision_failures, 'board publication cleared actual canonical failure'
        assert value['requests'] == held['requests'] and asked(value) == {rid}
        assert not value['model']['sections']['running'], 'board acquisition renewed execution trust'
        assert actions.operation(owner, oid=operation['operationId'])['operation'] == pending

        # Recovery is a canonical read only: no board source or projection moves.
        os.replace(aside, state)
        board_stamps, board_seq = r.board_stamps(), value['computed_seq']
        assert f.refresh_decisions() == 1
        assert 'S' not in f._decision_failures
        recovered = f.bundles['S']['bundle']
        assert identity(recovered['requests']) == identity(actions.views(owner))
        assert asked(recovered) == set() and not recovered['model']['sections']['running']
        assert recovered['computed_seq'] == board_seq and recovered['tasks'] == value['tasks']
        assert r.board_stamps() == board_stamps and not lane.pending and not lane.acquiring
        assert actions.operation(owner, oid=operation['operationId'])['operation'] == pending
        assert actions.get(owner, rid)['revision'] == stored['revision'] == 1
    finally:
        if aside.exists():
            os.replace(aside, state)
        f.stopped.set(); lane.close(); r.release(); db.close(); conn.close()
