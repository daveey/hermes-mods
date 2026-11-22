"""Observation carriage through the canonical source-less hub row producer."""
import copy
import json
import sqlite3

import pytest
from test_source_coverage import api_module


def test_ledger_pull_clock_is_not_semantic_and_bindings_do_not_collide(tmp_path):
    ledger = pytest.importorskip('hermes_cli.kanban_origin_ledger')
    adapter = api_module().CoreReaders
    replica, path = tmp_path / 'hub.db', tmp_path / 'origin-work.db'
    binding = {
        'binding_id': 'binding-0001', 'version': 1,
        'owner': {'gateway': 'owner', 'profile': 'work', 'board': 'build', 'task_id': 't_one'},
        'origin': {'gateway': 'origin', 'profile': 'default', 'session': 's_old'},
        'task_record_key': 'work/build/t_one', 'publication_version': 3,
    }
    task = {**binding['owner'], 'id': 't_one', 'title': 'work', 'status': 'running',
            'origin_bindings': [binding]}
    with sqlite3.connect(replica) as db:
        db.execute('CREATE TABLE replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, data TEXT)')
        db.execute('CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)')
        db.execute("INSERT INTO meta VALUES ('last_ok', '1000')")
        db.execute("INSERT INTO replica VALUES ('owner', 'tasks', ?, 3, ?)",
                   (binding['task_record_key'], json.dumps(task)))
    receipt = ledger.accept_binding(
        path=path, replica=replica, binding=binding, request_id='request-0001',
        sender='gw:owner', gateway='origin', profile='default', resolve_session=lambda sid: 'S1')
    assert receipt['status'] == 'accepted'

    def project():
        rows = ledger.project(path, replica=replica, gateway='origin', profile='default', now=1010)
        assert len(rows) == 1
        return rows[0]

    def capture(*rows):
        return tuple(json.dumps(r, sort_keys=True) for r in rows)

    old = project()
    with sqlite3.connect(replica) as db:
        db.execute("UPDATE meta SET v='1010' WHERE k='last_ok'")
    new = project()
    assert old['via'] == 'origin' and 'source' not in old
    assert (old['synced_at'], new['synced_at']) == (1000, 1010)
    assert adapter.external_semantic(capture(old)) == adapter.external_semantic(capture(new))
    # No owner liveness proof was published: carrying a pull must preserve unknown.
    assert old['stale'] and new['stale'] and new['status'] == 'unknown'
    other = {**old, 'binding_id': 'binding-0002', 'synced_at': 900}
    carried = adapter.advance_external({'tasks': [old, other], 'done': [old]}, capture(new))
    assert [r['synced_at'] for r in carried['tasks']] == [1010, 900]
    assert carried['done'][0]['synced_at'] == 1010
    assert old['synced_at'] == 1000
    for change in ({'observation_version': 4}, {'stale': False}, {'binding_id': 'binding-0002'}):
        assert adapter.external_semantic(capture(new)) != adapter.external_semantic(capture({**new, **change}))
    task['title'] = 'changed'
    with sqlite3.connect(replica) as db:
        db.execute('UPDATE replica SET version=4,data=?', (json.dumps(task),))
    changed = project()
    assert changed['title'] == 'changed' and changed['observation_version'] == 4
    assert adapter.external_semantic(capture(new)) != adapter.external_semantic(capture(changed))

def test_canonical_projection_clock_carriage_is_source_qualified(monkeypatch):
    projection = pytest.importorskip('hermes_cli.kanban_replica_projection')
    adapter = api_module().CoreReaders

    def row(gateway, synced, **task_fields):
        task = {'id': 'card1', 'status': 'running', 'title': 'work', **task_fields}
        return projection.card_rows(
            (gateway, 'default', 'default', 'card1'), task, sids=['S1'],
            closed=False, synced=synced, stale=False, me='local',
            origin_profile='default', legacy_record=None, historical={})[0][1]

    def capture(*rows):
        return tuple(json.dumps(r, sort_keys=True) for r in rows)

    old = row('owner-a', 1000)
    new = row('owner-a', 1010)
    other = row('owner-b', 999)
    assert 'source' not in old  # real canonical projection, not a replica_cards imitation
    assert adapter.external_semantic(capture(old)) == adapter.external_semantic(capture(new))
    bundle = {'tasks': [old, other], 'done': [copy.deepcopy(old)], 'computed_at': 1000, 'computed_seq': 3}
    carried = adapter.advance_external(bundle, capture(new))
    assert [r['synced_at'] for r in carried['tasks']] == [1010, 999]
    assert carried['done'][0]['synced_at'] == 1010
    assert carried['computed_at'] == 1000 and carried['computed_seq'] == 3
    assert bundle['tasks'][0]['synced_at'] == 1000
    # Source reset may legitimately regress the observation; never take max(time).
    reset = adapter.advance_external(carried, capture(row('owner-a', 900)))
    assert reset['tasks'][0]['synced_at'] == 900
    for change in ({'title': 'changed'}, {'stale': True}, {'reported_status': 'blocked'}):
        changed = {**new, **change}
        assert adapter.external_semantic(capture(new)) != adapter.external_semantic(capture(changed))
    # Distinct producers can publish the same owner/card/session with different
    # observation clocks; do not let their capture ordering choose the clock.
    replica = {**new, 'source': 'replica', 'synced_at': 1200}
    split = adapter.advance_external({'tasks': [old, {**old, 'source': 'replica'}], 'done': []},
                                     capture(new, replica))
    assert [r['synced_at'] for r in split['tasks']] == [1010, 1200]
    # The real selection adapter adds a source label without changing producer.
    from hermes_cli import kanban_db_notify
    from hermes_cli.kanban_origin_obligations import preserve_origin_obligations
    monkeypatch.setattr(kanban_db_notify, 'is_machine_session', lambda *a, **kw: False)
    selected = preserve_origin_obligations([], [old], gateway='local', profile='default',
                                          ids=['S1'], statuses=('running',))
    assert selected[0]['source'] == 'origin-obligation'
    carried = adapter.advance_external({'tasks': selected, 'done': []}, capture(new))
    assert carried['tasks'][0]['synced_at'] == 1010
    # A local/unknown producer's same-named field is not known pull bookkeeping.
    assert adapter.external_semantic(capture({'id': 'card1', 'synced_at': 1})) != adapter.external_semantic(
        capture({'id': 'card1', 'synced_at': 2}))
