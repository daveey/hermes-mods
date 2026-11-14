"""Apply deferral protects revision-bound proofs, never authorizes changes."""
from copy import deepcopy
import pytest
from plugins.runners.scripts.reconcile_scopes import _apply_hold, _owner
from plugins.runners.tests.test_maintenance_schedule import fixture


def setup():
    c = fixture(2)
    runtime = {'credentials': {c['scopes'][0]['credential_ref']: {'key': 'shared'}}}
    evidence = {s['id']: dict(at=100000, identity=dict(owner=_owner(c), scope=deepcopy(s), credential_key='shared')) for s in c['scopes']}
    return c, runtime, evidence, {}


def check(c, runtime, evidence, state, now=100001, removed=()):
    candidate = deepcopy(c)
    candidate['scopes'] = [s for s in c['scopes'] if s['id'] not in removed]
    return _apply_hold(c, candidate, runtime, evidence, state,
                       {'removals': [{'id': sid} for sid in removed]}, now, 3600)


def test_all_pending_proofs_block_even_if_one_removed():
    c, r, e, state = setup()
    result = check(c, r, e, state, removed=[c['scopes'][0]['id']])
    assert result['deferred'] and result['blockers'] == [c['scopes'][1]['id']]
    assert not check(c, r, e, state, removed=list(e))['deferred']


def test_deadline_survives_refresh_restart_and_absent_blockers():
    c, r, e, state = setup()
    assert check(c, r, e, state)['deferred']
    anchor = deepcopy(state)
    assert not check(c, r, {}, state, now=110000)['deferred']
    assert state == anchor
    for row in e.values(): row['at'] = 180000
    assert check(c, r, e, state, now=186400)['deferred']
    assert not check(c, r, e, state, now=186401)['deferred']
    assert not check(c, r, e, state, now=186402)['deferred']
    assert state == anchor


@pytest.mark.parametrize('change', ['revision', 'scope', 'credential', 'owner', 'expired', 'bool', 'nan'])
def test_changed_identity_or_invalid_proof_does_not_hold(change):
    c, r, e, state = setup()
    for row in e.values():
        if change == 'revision': row['identity']['owner']['config_revision'] += 1
        if change == 'owner': row['identity']['owner']['owner_epoch'] += 1
        if change == 'scope': row['identity']['scope']['name'] = 'example/elsewhere'
        if change == 'credential': row['identity']['credential_key'] = 'different'
        if change == 'expired': row['at'] = 1
        if change == 'bool': row['at'] = True
        if change == 'nan': row['at'] = float('nan')
    assert not check(c, r, e, state)['deferred']
    assert not state


def test_revision_change_starts_new_bounded_hold():
    c, r, e, state = setup()
    check(c, r, e, state)
    c['config_revision'] += 1
    for row in e.values(): row['identity']['owner'] = _owner(c)
    assert check(c, r, e, state, now=110000)['deadline'] == 196400
