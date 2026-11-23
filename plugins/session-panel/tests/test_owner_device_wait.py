"""Scheduled owner steps use the existing keyed attention row, not repeated questions."""
from test_model import NOW, bundle, model, task


STEP = 'Confirm paired-device access, or report that the phone availability check is wrong.'


def test_scheduled_phone_visibility_wait_is_one_needs_you_row_each_probe_tick():
    row = task(status='scheduled', title='Press the ring three times', wake_check='python3 probe_phone_available.py',
               wait={'wait_state': 'human', 'waiting_on': 'you: ' + STEP, 'owner_step': STEP})
    before = bundle(row)
    first = model.reduce_panel(before, now=NOW)
    later = model.reduce_panel(before, now=NOW + 900)
    assert first['header']['label'] == 'needs you'
    assert first['header']['counts']['needs_you'] == 1
    assert first['sections']['waiting'] == []
    ask = first['sections']['needs_you'][0]
    assert ask['title'] == STEP
    assert 'Press the ring' not in ask['title']
    assert ask['only_you']
    assert ask['request_id'] is None
    assert all(a['kind'] != 'decide' for a in ask['actions'])
    assert later['sections']['needs_you'][0]['key'] == ask['key']
    assert len(later['sections']['needs_you']) == 1
    assert 'human_request' not in row


def test_core_phone_visibility_wait_flows_into_single_attention_row():
    import pytest
    waits = pytest.importorskip('hermes_cli.kanban_wait_info')
    if not hasattr(waits, 'owner_device_step'):
        pytest.skip('requires owner-device wait classifier in core')
    park = {'wake_check': '/usr/bin/python3 -S /workspace/probe_phone_available.py',
            'awaiting': {'kind': 'external', 'ref': 'paired phone', 'why': 'natural visibility'}}
    row = task(status='scheduled', title='Press the ring three times',
               wait=waits.describe('scheduled', park=park))
    first = model.reduce_panel(bundle(row), now=NOW)
    assert first['header']['label'] == 'needs you'
    attention = first['sections']['needs_you']
    assert len(attention) == 1
    assert 'paired-device access' in attention[0]['title']
    assert first['sections']['waiting'] == []
    for tick in (1, 2, 3):
        row['wait'] = waits.describe('scheduled', park=park, now=NOW + tick * 900)
        later = model.reduce_panel(bundle(row), now=NOW + tick * 900)
        assert [ask['key'] for ask in later['sections']['needs_you']] == [attention[0]['key']]
    row.update(status='running', wait=waits.describe('running', park=park))
    assert model.reduce_panel(bundle(row), now=NOW)['sections']['needs_you'] == []


def test_canonical_ask_is_not_replaced_by_legacy_owner_step():
    from test_model import human, request
    row = task(status='scheduled', human_request=human(),
               wait={'wait_state': 'human', 'owner_step': STEP})
    result = model.reduce_panel(bundle(row, requests=[request()]), now=NOW)
    assert len(result['sections']['needs_you']) == 1
    assert result['sections']['needs_you'][0]['request_id'] == 'req-1'
