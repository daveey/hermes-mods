"""Capability proofs bind a current paired operation, not arbitrary old errors."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'capability_proof_test', Path(__file__).parents[1] / 'goal_proofs.py')
proofs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proofs)


def tool(call_id='c', args=None, result=None, name='terminal'):
    return {'role': 'tool', 'tool_call_id': call_id,
            'call': {'id': call_id, 'function': {
                'name': name, 'arguments': json.dumps(args or {'command': 'repair example'})}},
            'content': json.dumps(result or {'error': 'missing_credentials'})}


def case():
    return ({'evidence_refs': ['user:0', 'tool:c', 'candidate'],
             'boundary_kind': 'credentials',
             'proof': {'source_ref': 'tool:c', 'action': 'repair example'}},
            {'candidate': {'role': 'assistant', 'content': 'Access is missing.'},
             'user:0': {'role': 'user', 'content': 'Repair example'},
             'tool:c': tool()})


@pytest.mark.parametrize('error', ['missing_credentials', 'permission_denied',
                                 'tool_unavailable', 'authentication_required', 'access_denied'])
def test_current_paired_command_failure(error):
    v, e = case()
    e['tool:c']['content'] = {'error': error}
    assert proofs.capability(v, e) is None


def test_current_paired_url_failure():
    v, e = case()
    v['proof']['action'] = 'https://example.com/manage'
    e['tool:c'] = tool(args={'url': v['proof']['action']}, name='fetch')
    assert proofs.capability(v, e) is None


@pytest.mark.parametrize('proof', [None, {}, {'source_ref': 'tool:c'},
    {'source_ref': 'tool:c', 'action': 'other'},
    {'source_ref': 'tool:c', 'action': 'repair example', 'extra': 'guess'},
    {'source_ref': 'candidate', 'action': 'repair example'},
    {'source_ref': 'user:0', 'action': 'repair example'}])
def test_missing_unsupported_or_inexact_proof_is_unknown(proof):
    v, e = case()
    v['proof'] = proof
    with pytest.raises(ValueError):
        proofs.capability(v, e)


def test_source_must_be_cited():
    v, e = case()
    v['evidence_refs'].remove('tool:c')
    with pytest.raises(ValueError):
        proofs.capability(v, e)


@pytest.mark.parametrize('change', [
    {'call': None}, {'call': {}}, {'call': {'id': 'other'}},
    {'tool_call_id': 'other'}, {'role': 'assistant'},
    {'call': {'id': 'c', 'function': None}},
    {'call': {'id': 'c', 'function': {'name': 'terminal', 'arguments': '{bad'}}},
    {'call': {'id': 'c', 'function': {'name': '', 'arguments': {'command': 'repair example'}}}},
])
def test_unpaired_or_malformed_source_is_unknown(change):
    v, e = case()
    e['tool:c'].update(copy.deepcopy(change))
    with pytest.raises(ValueError):
        proofs.capability(v, e)


@pytest.mark.parametrize('args', [{'code': 'repair example'}, {'commands': ['repair example']},
    {'command': 'repair example '}, {'command': 'repair example', 'url': 'repair example'},
    {'url': 'repair example'}, {'command': ['repair example']}])
def test_unsupported_action_formats_are_unknown(args):
    v, e = case()
    e['tool:c'] = tool(args=args)
    with pytest.raises(ValueError):
        proofs.capability(v, e)


@pytest.mark.parametrize('result', [{'status': 503}, {'output': 'missing_credentials'},
    {'error': 'outage'}, {'error': {'code': 'missing_credentials'}},
    {'error': 'missing_credentials', 'success': True},
    {'error': 'missing_credentials', 'exit_code': 0},
    {'error': 'missing_credentials', 'status': 200}])
def test_non_capability_or_conflicting_result_is_unknown(result):
    v, e = case()
    e['tool:c'] = tool(result=result)
    with pytest.raises(ValueError):
        proofs.capability(v, e)


def test_later_user_invalidates_even_uncited_source():
    v, e = case()
    e['user:1'] = {'role': 'user', 'content': 'Credentials are available now; continue.'}
    with pytest.raises(ValueError, match='superseded'):
        proofs.capability(v, e)


@pytest.mark.parametrize('result', [{'exit_code': 0}, {'error': 'missing_credentials'}, {'error': 'outage'}])
def test_later_same_command_invalidates_with_changed_options(result):
    v, e = case()
    e['tool:later'] = tool('later', args={'command': 'repair example', 'timeout': 90}, result=result)
    with pytest.raises(ValueError, match='superseded'):
        proofs.capability(v, e)


def test_later_same_url_invalidates_across_tool_names():
    v, e = case()
    target = 'https://example.com/manage'
    v['proof']['action'] = target
    e['tool:c'] = tool(args={'url': target}, name='fetch')
    e['tool:later'] = tool('later', args={'url': target}, name='other_fetch', result={'status': 200})
    with pytest.raises(ValueError, match='superseded'):
        proofs.capability(v, e)


def test_unrelated_later_operation_does_not_invalidate():
    v, e = case()
    e['tool:later'] = tool('later', args={'command': 'inspect other'}, result={'exit_code': 0})
    assert proofs.capability(v, e) is None


def test_unsupported_later_tool_is_unknown_not_guessed_unrelated():
    v, e = case()
    e['tool:later'] = tool('later', args={'code': 'repair example'})
    with pytest.raises(ValueError, match='unsupported'):
        proofs.capability(v, e)


def test_real_evidence_builder_pairs_source_and_retains_steering():
    gd_spec = importlib.util.spec_from_file_location(
        'capability_evidence_test', Path(__file__).parents[1] / 'goal_disposition.py')
    gd = importlib.util.module_from_spec(gd_spec)
    gd_spec.loader.exec_module(gd)
    v, _ = case()
    result = tool()
    rows = [{'role': 'user', 'content': 'Repair example'},
            {'role': 'assistant', 'tool_calls': [result.pop('call')]}, result]
    assert proofs.capability(v, gd.evidence_for(rows, 'Missing access')) is None
    rows.append({'role': 'user', 'content': 'Access fixed; continue'})
    with pytest.raises(ValueError, match='superseded'):
        proofs.capability(v, gd.evidence_for(rows, 'Missing access'))


def test_new_failure_after_steering_can_be_current():
    v, e = case()
    e['user:1'] = {'role': 'user', 'content': 'Try again'}
    e['tool:new'] = tool('new')
    v['proof']['source_ref'] = 'tool:new'
    v['evidence_refs'].append('tool:new')
    assert proofs.capability(v, e) is None
