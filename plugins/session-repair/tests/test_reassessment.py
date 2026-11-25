"""Scanner/real replica integration with an explicitly simulated Board transport.

These tests exercise certification and query-only scanner reads, NOT a live Board
or private owner admission. Queue/update real-lifecycle FP proof remains separate.
"""
import copy
import json
from types import SimpleNamespace

import pytest
pytest.importorskip('hermes_cli.kanban_db')
replica = pytest.importorskip('hermes_cli.kanban_owner_reassessment_replica')
from hermes_cli.kanban_db_sync import uuid7
from sr_helpers import Env, fixtures
F = fixtures()
URL = 'https://github.com/example/project/actions/runs/123'
SHA = 'a' * 40


@pytest.fixture
def world(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    env.now = int(env.now)
    tid = F.long_wait(env.conn, env.now, waited_s=40000)
    from hermes_cli.kanban_db_sync import apply_sync_schema_steps
    apply_sync_schema_steps(env.conn)
    for (eid,) in env.conn.execute('SELECT id FROM task_events').fetchall():
        env.conn.execute('UPDATE task_events SET uid=? WHERE id=?', (uuid7(), eid))
    event = env.conn.execute("SELECT id,uid FROM task_events WHERE task_id=? AND kind='scheduled' ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    event_id, uid = event
    ep, run, admitted, bound = (uuid7() for _ in range(4))
    awaiting = {'kind': 'ci', 'ref': URL}
    payload = json.dumps({'reason': 'bounded reassessment', 'awaiting': awaiting,
                          'wake_at': env.now + 600, 'owner_reassessment_episode_uid': ep})
    at = env.now - 40000
    env.conn.execute('UPDATE task_events SET payload=?,created_at=?,origin_gateway=? WHERE id=?', (payload, at, 'fixture-gateway', event_id))
    if env.scan_mod._col(env.conn, 'tasks', 'wake_at'):
        env.conn.execute('UPDATE tasks SET wake_at=? WHERE id=?', (env.now+600, tid))
    F.event(env.conn, tid, 'worker_bound', {}, at=at-1)
    evidence_uid = uuid7()
    env.conn.execute("UPDATE task_events SET uid=? WHERE task_id=? AND kind='worker_bound'", (evidence_uid, tid))
    r = {'version': 1, 'board': 'default', 'task_id': tid, 'gateway': 'fixture-gateway', 'episode_uid': ep,
         'park_event': {'uid': uid, 'run_uid': run, 'kind': 'scheduled', 'created_at': at, 'payload': payload},
         'issuer': {'owner_admitted_uid': admitted, 'profile': 'default', 'run_uid': run,
                    'session_id': F.CHAT, 'worker_bound_uid': bound},
         'producer': awaiting, 'target': {'kind': 'external', 'identity': URL, 'revision': SHA},
         'issued_at': at, 'reassess_at': env.now+600, 'backstop_at': env.now+1200,
         'evidence_refs': [{'table': 'task_events', 'uid': evidence_uid}],
         'suppresses': ['too_long'], 'execution_authority': False}
    ev = {'board': 'default', 'table': 'task_events', 'uid': uid, 'state': 'live',
          'row': {'uid': uid, 'task_id': tid, 'kind': 'scheduled', 'payload': payload,
                  'created_at': at, 'run_uid': run, 'origin_gateway': 'fixture-gateway', 'deleted_at': None},
          'owner_reassessment_v1': r}
    # Explicit transport fixture: certification is real, authentication is not claimed.
    env.conn.execute('CREATE TABLE IF NOT EXISTS kanban_sync_state(board_id TEXT)')
    if not env.conn.execute('SELECT 1 FROM kanban_sync_state').fetchone():
        env.conn.execute("INSERT INTO kanban_sync_state(board_id,gateway_id,hlc_last,mode) VALUES ('default','fixture-gateway','0','off')")
    else:
        env.conn.execute("UPDATE kanban_sync_state SET board_id='default'")
    env.conn.commit()
    hub = SimpleNamespace(row_evidence=lambda *a, **kw: copy.deepcopy(ev))
    replica.certify(env.conn, hub, 'default', task=tid, uid=uid)
    live = {'state': {'url': URL, 'headSha': SHA, 'status': 'in_progress'}, 'happened': False}
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: copy.deepcopy(live))
    return env, tid, event_id, r, live


def hits(world, **kwargs):
    env, tid, *_ = world
    return env.findings(rule='BS-long-wait', card=tid, **kwargs)


def test_pr_target_is_independently_derived_and_failed_checks_veto():
    from sr_helpers import PLUGIN, load
    scanner = load('_sr_reassessment_target_test', PLUGIN / 'scan.py')
    url = 'https://github.com/example/project/pull/123'
    state = {'url': url, 'headRefOid': SHA, 'state': 'OPEN', 'statusCheckRollup': []}
    awaited = {'kind': 'deploy', 'ref': url}
    assert scanner.reassessment_target(awaited, {'state': state}) == {
        'kind': 'external', 'identity': url, 'revision': SHA}
    for patch in ({'state': 'MERGED'}, {'headRefOid': 'short'}, {'reviewDecision': 'CHANGES_REQUESTED'},
                  {'statusCheckRollup': [{'conclusion': 'FAILURE'}]}, {'statusCheckRollup': None}):
        assert scanner.reassessment_target(awaited, {'state': {**state, **patch}}) is None
    assert scanner.reassessment_target({'kind': 'ci', 'ref': URL}, {'state': {
        'url': URL, 'headSha': SHA, 'status': 'in_progress', 'conclusion': 'failure'}}) is None


def test_current_reader_works_on_real_local_schema_without_tombstones(world):
    env, tid, _, r, _ = world
    assert replica.latest_park_uid(env.conn, tid) == r['park_event']['uid']
    assert hits(world) == []


def test_unavailable_verifier_is_not_authority(world, monkeypatch):
    env, *_ = world
    def unavailable(*a, **kw):
        raise RuntimeError('fixture unavailable proof')
    monkeypatch.setattr(replica, 'verified_for_event', unavailable)
    assert hits(world)[0]['variant'] == 'too_long'
    assert any('fixture unavailable proof' in str(e) for e in env.last_sources.errors)
    assert not env.last_sources.conn('default').in_transaction


def test_borrowed_read_transaction_is_preserved(world):
    env, tid, _, r, live = world
    src = env.sources()
    card = env.scan_mod.Card(tid, 'default', None, src.task('default', tid)[0], env.conn, None)
    park = env.scan_mod.current_park(card)
    env.conn.execute('BEGIN')
    try:
        assert env.scan_mod.canonical_age_reassessment(card, src, park, r['producer'], live)
        assert env.conn.in_transaction
    finally:
        env.conn.rollback()


def test_real_replica_query_only_scan_and_exact_deadline(world):
    env, tid, _, r, _ = world
    result = hits(world)
    def get(sql):
        cur = env.conn.execute(sql, (tid,))
        return dict(zip([d[0] for d in cur.description], cur.fetchone()))
    event = get("SELECT * FROM task_events WHERE task_id=? AND kind='scheduled'")
    task = get('SELECT * FROM tasks WHERE id=?')
    from hermes_cli import kanban_owner_reassessment_canonical as canonical
    assert replica.verified_receipt(env.conn, event['uid']) == r
    assert canonical.event_matches(event, r), json.dumps(event)
    assert canonical.current_matches(r, task, latest_park_uid=replica.latest_park_uid(env.conn, tid), now=env.now), json.dumps(task)
    assert replica.verified_for_event(env.conn, event, task, now=env.now)
    assert result == [], (result, env.last_sources.errors)
    assert hits(world, now=r['reassess_at'])[0]['variant'] == 'too_long'
    assert hits(world, now=r['backstop_at'])[0]['variant'] == 'too_long'
    c = env.last_sources.conn('default')
    assert c.execute('PRAGMA query_only').fetchone()[0] == 1
    assert not c.in_transaction
    assert replica.verified_receipt(env.conn, r['park_event']['uid']) == r


@pytest.mark.parametrize('bad', ['revision', 'url', 'missing_revision', 'failed', 'offline', 'unavailable', 'completed'])
def test_independent_producer_evidence_veto(world, bad):
    _, _, _, _, live = world
    if bad == 'revision': live['state']['headSha'] = 'b'*40
    elif bad == 'url': live['state']['url'] = URL+'0'
    elif bad == 'missing_revision': live['state'].pop('headSha')
    elif bad == 'failed': live['state'].update(status='completed', conclusion='failure')
    elif bad in ('offline', 'unavailable'): live['state'] = bad
    else: live.update(happened=True, happened_at=world[0].now-601)
    assert hits(world)[0]['variant'] == ('event_happened' if bad == 'completed' else 'too_long')


@pytest.mark.parametrize('kind', ['wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met', 'external_watch'])
def test_adverse_episode_event_cannot_be_overridden(world, kind):
    env, tid, *_ = world
    F.event(env.conn, tid, kind, {'health': 'offline', 'state': 'external_unknown'}, at=env.now-1)
    assert hits(world)[0]['variant'] == 'too_long'


@pytest.mark.parametrize('kind', ['wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met', 'external_watch'])
@pytest.mark.parametrize('remapped', [False, True])
def test_adverse_chronology_survives_replica_id_remap(world, kind, remapped):
    env, tid, eid, *_ = world
    assert hits(world) == []
    F.event(env.conn, tid, kind, {'health': 'offline', 'state': 'external_unknown'}, at=env.now-1)
    if remapped:
        env.conn.execute('UPDATE task_events SET id=id+10000 WHERE id=?', (eid,))
    env.conn.commit()
    assert hits(world)[0]['variant'] == 'too_long'


@pytest.mark.parametrize('kind', ['wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met', 'external_watch'])
def test_same_second_adverse_event_with_backfilled_uid_is_ambiguous(world, kind):
    env, tid, eid, receipt, _ = world
    F.event(env.conn, tid, kind, {}, at=receipt['park_event']['created_at'])
    # UUIDv7 minted now is a backfill clock, not the old event's chronology.
    env.conn.execute('UPDATE task_events SET uid=? WHERE task_id=? AND kind=?', (uuid7(), tid, kind))
    env.conn.execute('UPDATE task_events SET id=id+10000 WHERE id=?', (eid,))
    env.conn.commit()
    assert hits(world)[0]['variant'] == 'too_long'


HEALTHY_WATCH = {'health': 'ok', 'state': 'external_running', 'consecutive_failures': 0}


@pytest.mark.parametrize('later_healthy', [False, True])
@pytest.mark.parametrize('tie', [False, True])
def test_competing_watchers_use_origin_chronology_not_arrival(world, later_healthy, tie):
    env, tid, *_ = world
    older, newer = env.now-10, env.now-1
    # Older observation arrives last, with the larger replica-local id.
    F.event(env.conn, tid, 'external_watch', HEALTHY_WATCH if later_healthy else {}, at=newer)
    F.event(env.conn, tid, 'external_watch', {} if later_healthy else HEALTHY_WATCH,
            at=newer if tie else older)
    result = hits(world)
    if later_healthy and not tie:
        assert result == []
    else:
        assert result and result[0]['variant'] == 'too_long'


@pytest.mark.parametrize('kind', ['wake_check_broken', 'wake_check_lost', 'wake_check_stale', 'condition_met', 'external_watch'])
def test_proven_prior_adverse_event_does_not_veto_current_episode(world, kind):
    env, tid, _, receipt, _ = world
    F.event(env.conn, tid, kind, {}, at=receipt['park_event']['created_at']-1)
    assert hits(world) == []


@pytest.mark.parametrize('masked', [False, True])
def test_future_dated_healthy_observation_never_exempts(world, masked):
    env, tid, *_ = world
    if masked:  # a future healthy stamp must not outrank a current unhealthy watcher
        F.event(env.conn, tid, 'external_watch', {}, at=env.now-1)
    F.event(env.conn, tid, 'external_watch', HEALTHY_WATCH, at=env.now+1)
    assert hits(world)[0]['variant'] == 'too_long'


@pytest.mark.parametrize('kind', ['wake_check_broken', 'condition_met'])
def test_future_dated_adverse_observation_fails_closed(world, kind):
    env, tid, *_ = world
    F.event(env.conn, tid, kind, {}, at=env.now+1)
    assert hits(world)[0]['variant'] == 'too_long'


@pytest.mark.parametrize('kind', ['wake_check_broken', 'external_watch'])
@pytest.mark.parametrize('stamp', [-1, '', 'invalid', 1.5])
def test_invalid_observation_chronology_fails_closed(world, kind, stamp):
    env, tid, eid, *_ = world
    F.event(env.conn, tid, kind, HEALTHY_WATCH, at=env.now-1)
    env.conn.execute('UPDATE task_events SET created_at=? WHERE task_id=? AND kind=?', (stamp, tid, kind))
    env.conn.execute('UPDATE task_events SET id=id+10000 WHERE id=?', (eid,))
    env.conn.commit()
    assert hits(world)[0]['variant'] == 'too_long'


@pytest.mark.parametrize('bad', ['missing_receipt', 'missing_evidence', 'changed_payload', 'new_park', 'new_run', 'expired_lease'])
def test_current_episode_and_required_evidence(world, bad):
    env, tid, eid, r, _ = world
    if bad == 'missing_receipt':
        # Drop fixture store, not immutable rows via forbidden production writes.
        env.conn.execute(f'DROP TABLE {replica.TABLE}')
    elif bad == 'missing_evidence':
        env.conn.execute('DELETE FROM task_events WHERE uid=?', (r['evidence_refs'][0]['uid'],))
    elif bad == 'changed_payload':
        env.conn.execute("UPDATE task_events SET payload=payload||' ' WHERE id=?", (eid,))
    elif bad == 'new_park':
        F.event(env.conn, tid, 'scheduled', json.loads(r['park_event']['payload']), at=env.now-3000)
    elif bad == 'new_run':
        F._run(env.conn, tid, env.now, started_s=10)
        env.conn.execute("UPDATE task_runs SET status='completed' WHERE task_id=?", (tid,))
        env.conn.execute('UPDATE tasks SET current_run_id=NULL WHERE id=?', (tid,))
    else:
        F.synced_columns(env.conn)
        env.conn.execute('UPDATE tasks SET lease_holder=?,lease_expires=? WHERE id=?', ('fixture-worker', (env.now-1)*1000, tid))
    env.conn.commit()
    if bad == 'new_run':
        assert hits(world) == []  # prior park is not a current wait after a successor run
        src = env.sources()
        card = env.scan_mod.Card(tid, 'default', None, src.task('default', tid)[0], env.conn, None)
        park = {'id': eid, 'at': r['park_event']['created_at'], 'payload': json.loads(r['park_event']['payload'])}
        assert not env.scan_mod.canonical_age_reassessment(card, src, park, r['producer'], world[4])
    else:
        assert hits(world)[0]['variant'] == 'too_long'


def test_untrusted_prose_pending_json_and_future_wake_never_authorize(world):
    env, tid, _, r, _ = world
    env.conn.execute(f'DROP TABLE {replica.TABLE}')
    env.conn.execute('CREATE TABLE kanban_owner_reassessment_pending(receipt TEXT)')
    env.conn.execute('INSERT INTO kanban_owner_reassessment_pending VALUES (?)', (json.dumps(r),))
    F.event(env.conn, tid, 'comment', {'text': 'healthy queue, future wake, reassessment approved'}, at=env.now-1)
    env.conn.commit()
    assert hits(world)[0]['variant'] == 'too_long'


@pytest.mark.parametrize('guard', ['containment', 'spend', 'execution_refusal'])
def test_canonical_receipt_does_not_release_holds(world, monkeypatch, guard):
    env, tid, *_ = world
    if guard == 'containment':
        monkeypatch.setattr(env.scan_mod, '_promotion_held', lambda *a: 'containment')
    else:
        from hermes_cli import kanban_spend_decision
        monkeypatch.setattr(kanban_spend_decision, 'pending' if guard == 'spend' else 'execution_refusal', lambda *a: True)
    assert hits(world) == []  # same hold exclusion; never converts it to an execution nudge


@pytest.mark.parametrize('target', ['queue', 'update', 'opaque_external', 'unknown_coverage'])
def test_unplumbed_target_readers_fail_closed(world, target):
    env, _, _, _, live = world
    live['state'] = {'coverage': 'unknown', 'target': {'kind': target, 'identity': URL, 'revision': SHA}}
    assert hits(world)[0]['variant'] == 'too_long'
