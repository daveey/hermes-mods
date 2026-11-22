"""Permanent regressions for the three PR347 independent review probes.

Exercise settlement after a same-generation return and the actual panel reducer,
not merely Mirror.present's delivery metadata.
"""
import json
from pathlib import Path

import pytest
from test_remote_card_state_machine import setup, depart, operation, Courier, age, opens
from test_withdrawal import OWNER, load_module


@pytest.mark.parametrize('state', ['done', 'rejected', 'expired', 'failed'])
def test_same_generation_return_settles_retiring_custody(tmp_path, state):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row)

    class Counting(Courier):
        calls = 0

        def read(self, *args):
            self.calls += 1
            return super().read(*args)

    courier = Counting(state)
    mirror.remote.courier = courier
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    actual = operation(store, op)
    assert courier.calls > 0
    if state == 'done':
        assert actual['dispatchState'] == 'delivered'
        assert 'retired' not in actual
        assert opens(store) == []
    else:
        assert actual['dispatchState'] == 'cancelled'
        assert actual['retired']['evidence'] != 'remote-retiring'
        returned = opens(store)
        assert len(returned) == 1
        assert returned[0]['requestId'] != old['requestId']
        mirror.remote.reconcile(OWNER)
        assert opens(store) == returned  # returned-generation remint is stable


def test_return_without_terminal_fact_holds_same_generation(tmp_path):
    mirror, store, row, old, op = setup(tmp_path, local='sent')
    depart(mirror, row)
    mirror.remote.cards = lambda owner: [row]
    mirror.remote.reconcile(OWNER)
    assert operation(store, op)['retired']['evidence'] == 'remote-retiring'
    assert opens(store) == []


@pytest.mark.parametrize('departure', [False, True])
@pytest.mark.parametrize('seconds', [0, 7200])
def test_retiring_panel_presentation(tmp_path, departure, seconds):
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row, departure=departure)
    age(store, op, seconds)
    shown = mirror.present(store.get(OWNER, old['requestId']))
    model = load_module('return_panel', Path(__file__).resolve().parents[2] / 'session-panel/model.py')
    changed = dict(row, card_answer_generation='b' * 64, remote=True, block_kind='needs_input')
    result = model.reduce_panel(dict(tasks=[] if departure else [changed], requests=[shown],
                                    gateway='fixture-sync', profile=OWNER['profile']), now=1795323600)
    waiting = result['sections']['waiting']
    assert len(waiting) == 1
    assert waiting[0]['decision']['text'] == shown['delivery']['text']
    assert waiting[0]['detail'] == ''
    assert not waiting[0].get('options')
    assert not any(a['kind'] == 'decide' for a in waiting[0]['actions'])
    assert not result['sections']['needs_you']
    assert 'request cancelled' not in json.dumps(result)
    assert shown['delivery']['unconfirmed'] == bool(seconds)


@pytest.mark.parametrize('status', ['blocked', 'running', 'stuck', 'unknown', 'stale'])
def test_retiring_mirror_to_panel_preserves_owner_detail(tmp_path, status):
    """Permanent form of review probe_e2e: real Mirror.present -> reduce_panel."""
    mirror, store, row, old, op = setup(tmp_path)
    depart(mirror, row, departure=(status != 'blocked'))
    shown = mirror.present(store.get(OWNER, old['requestId']))
    model = load_module('retiring_owner_panel', Path(__file__).resolve().parents[2] / 'session-panel/model.py')
    now = 1795323600
    changed = dict(row, card_answer_generation='b' * 64, remote=True, status=status)
    if status != 'blocked':
        changed.pop('human_request', None)
        changed.pop('block_kind', None)
    if status == 'running':
        changed.update(run_model='m', started_at=now - 600,
                       execution_observations=[{'state': 'running'}])
    elif status == 'stuck':
        changed.update(status='scheduled', wait={'wait_state': 'stalled'})
    elif status in ('unknown', 'stale'):
        changed.update(status='running', stale=status == 'stale')
    b = dict(tasks=[changed], requests=[shown], computed_at=now,
             gateway='fixture-sync', profile=OWNER['profile'])
    project = lambda records, **kwargs: {'state': records[0]['state'], 'sourced': True}
    result = model.reduce_panel(b, now=now, projector=project)
    baseline = model.reduce_panel({**b, 'requests': []}, now=now, projector=project)
    section, actual = next((s, rows[0]) for s, rows in result['sections'].items() if rows)
    assert actual['decision']['text'] == shown['delivery']['text']
    if status == 'blocked':
        assert actual['detail'] == ''  # only the answered-ask placeholder is redundant
    else:
        plain = baseline['sections'][section][0]
        assert plain['detail']
        assert actual['detail'] == plain['detail']
        assert actual['details'] == plain['details']
    assert not result['sections']['needs_you']
    assert 'request cancelled' not in json.dumps(result)
