"""A dependency explains a wait; only its own needs-you row owns the ask."""
import json

import pytest

from test_hierarchy import outline_case, reduce, rows
from test_model import human, request


@pytest.mark.parametrize('status', ['todo', 'scheduled'])
@pytest.mark.parametrize('display_parent', [False, True])
@pytest.mark.parametrize('wire', [False, True])
def test_waiting_dependency_names_human_parent_without_quoting_ask(status, display_parent, wire):
    b = outline_case({'t_a': ['t_b']}, displayed=['t_a', 't_b'] if display_parent else ['t_a'])
    b['tasks'][0]['status'] = status
    h = human()
    imperative = 'On your computer, unlock the device and confirm the pairing dialog.'
    h['action'] = imperative
    parent = b['cards']['t_b']['detail']['task']
    parent.update(status='blocked', title='Check device pairing', block_kind='needs_input',
                  human_request=h, block_reason=imperative, wait={'wait_state': 'human'})
    b['cards']['t_b']['detail']['events'] = [
        {'kind': 'blocked', 'payload': {'reason': imperative, 'human_request': h}}]
    if display_parent:
        b['tasks'][1] = dict(parent)
    # Real diagnostic carriage repeats the same ask in the instruction and
    # dependency recovery trigger, independently of the ordinary block reason.
    b['gateway'] = 'remote'
    b['cards']['t_a']['detail'].update(source='live', task=dict(b['tasks'][0]), actionability={
        'version': 1, 'task_id': 't_a', 'status': status, 'evidence_state': 'known',
        'owner': {'kind': 'assignee', 'refs': ['worker']},
        'action': 'satisfy_dependency', 'instruction': 'Wait until: ' + imperative,
        'contract_key': 'source-contract', 'escalation': {'at': None},
        'violations': [], 'source_refs': ['source-event'],
        'blocker': {'prerequisite': 'Parent is unfinished', 'trigger': imperative},
        'dependencies': [{'task_id': 't_b', 'trigger': imperative, 'restricted': True}]})
    original = json.dumps(b, sort_keys=True)
    m = reduce(b, wire=wire)
    assert json.dumps(b, sort_keys=True) == original
    child = rows(m)['t_a']
    assert child in m['sections']['waiting']
    assert 'waiting on: Check device pairing (your check)' in child['detail']
    assert 't_b' in '\n'.join(child['details'])
    rendered = json.dumps(child)
    for field in ('action', 'why_human', 'scope', 'evidence'):
        assert h[field] not in rendered
    assert imperative not in rendered
    assert child['actionability']['contract_key'] == 'source-contract'
    assert child['actionability']['dependencies'][0]['restricted'] is True
    assert child['actionability']['instruction'].startswith('waiting on: Check device pairing')
    assert child['actions'][-1]['target']['id'] == 't_b'
    if display_parent:
        assert m['sections']['needs_you'][0]['id'] == 't_b'
        assert imperative in json.dumps(m['sections']['needs_you'])


@pytest.mark.parametrize('request_state', ['open', 'dismissed', 'answered'])
def test_canonical_request_controls_check_label_without_lending_ask_text(request_state):
    b = outline_case({'t_a': ['t_b']}, displayed=['t_a'])
    parent = b['cards']['t_b']['detail']['task']
    parent.update(status='blocked', human_request=human(), block_kind='needs_input',
                  block_reason=human()['action'])
    r = request(request_state)
    r['scope']['sourceRefs'][0]['id'] = 't_b'
    b['requests'] = [r]
    child = rows(reduce(b))['t_a']
    assert ('(your check)' in child['detail']) == (request_state == 'open')
    assert human()['action'] not in json.dumps(child)


def test_canonical_only_ask_is_not_repeated_as_agent_hold_reason():
    b = outline_case({'t_a': ['t_b']}, displayed=['t_a'])
    parent = b['cards']['t_b']['detail']['task']
    parent.update(status='triage', block_reason=human()['action'])
    r = request()
    r['scope']['sourceRefs'][0]['id'] = 't_b'
    b['requests'] = [r]
    child = rows(reduce(b))['t_a']
    assert '(your check)' in child['detail']
    assert human()['action'] not in json.dumps(child)


def test_nonhuman_dependency_keeps_its_operational_reason():
    b = outline_case({'t_a': ['t_b']}, displayed=['t_a'])
    b['cards']['t_b']['detail']['task'].update(status='blocked', block_reason='Cooldown window')
    child = rows(reduce(b))['t_a']
    assert 'Cooldown window' in child['detail']
    assert 'Blocking reason: Cooldown window' in child['details']
