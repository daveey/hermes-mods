"""Observation carriage through the canonical source-less hub row producer."""
import copy
import json

import pytest
from test_source_coverage import api_module


def test_canonical_projection_clock_carriage_is_source_qualified():
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
    # A local/unknown producer's same-named field is not known pull bookkeeping.
    assert adapter.external_semantic(capture({'id': 'card1', 'synced_at': 1})) != adapter.external_semantic(
        capture({'id': 'card1', 'synced_at': 2}))
