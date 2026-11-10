"""Small incremental compute spend is pre-approved, not a fresh budget request."""
import importlib.util
import pytest
from test_compute_envelope import HERE, _hooks, _log


def module():
    spec = importlib.util.spec_from_file_location('ce_topups', HERE / 'compute_envelope.py')
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    return ce


def invoke(hooks, surface, text):
    if surface == 'reply':
        return hooks['pre_final_gate:compute_envelope'](session_id='topup', final_response=text)
    args = {'reason': text} if surface == 'kanban_block' else {
        'body': text, 'initial_status': 'blocked'}
    return hooks['pre_tool_call'](session_id='topup', tool_name=surface, args=args)


@pytest.mark.parametrize('surface', ['reply', 'kanban_block', 'kanban_create'])
@pytest.mark.parametrize('text', [
    'GPU rental: $23.15 spent against the $25 budget. Please approve an additional $6, total $29.15.',
    'Please approve a $6 top-up for the single GPU rental, taking total spend to $29.15.',
    'GPU rental: $100 spent. Please approve a $6 top-up, total $106.',
    'May I rent one GPU for another $19.99 beyond the $25 budget, total $44.99?',
])
def test_small_topup_ask_is_held(tmp_path, monkeypatch, surface, text):
    assert invoke(_hooks(tmp_path, monkeypatch), surface, text)
    assert any(r.get('rule') == 'compute_envelope' for r in _log(tmp_path))


@pytest.mark.parametrize('surface', ['reply', 'kanban_block', 'kanban_create'])
@pytest.mark.parametrize('text', [
    'GPU rental: $23.15 spent. I will add $6 with a total cap of $29.15.',
    'I will rent one GPU for an additional $6, total budget $29.15.',
    'GPU rental: $100 spent. I will add $6, total cap $106.',
    'GPU rental: $23.15 spent + $6 cap = $29.15 total budget.',
    'I will rent one GPU for an additional $6. Total cap $29.15.',
    'I will rent one GPU with a $19.99 top-up, total cap $44.99.',
])
def test_small_topup_declaration_passes(tmp_path, monkeypatch, surface, text):
    assert invoke(_hooks(tmp_path, monkeypatch), surface, text) is None


@pytest.mark.parametrize('increment', ['20', '20.01', '30'])
def test_large_increment_requires_approval_even_below_initial_envelope(increment):
    ce = module()
    assert ce.classify(f'Please approve an additional ${increment} for the GPU rental.') is None
    assert ce.authority(f'I will rent one GPU for an additional ${increment}.')


@pytest.mark.parametrize('machine', ['2 GPUs', 'multi-GPU', 'multi-node', '2 nodes'])
def test_small_increment_does_not_approve_multiple_machines(machine):
    ce = module()
    assert ce.classify(f'Please approve a $6 top-up to rent {machine}.') is None
    assert ce.authority(f'I will rent {machine} with a $6 top-up, total cap $29.15.')


def test_delta_from_user_budget_and_persisted_grant():
    ce = module()
    text = 'I will rent one GPU with a total cap of $119.99.'
    assert ce.authority(text, ['I approve $100 for GPU rental.']) is None
    assert ce.authority(text, ['The last GPU rental cost $100.'])
    assert ce.authority('I will rent one GPU with a total cap of $120.', ['I approve $100 for GPU rental.'])
    assert ce.authority(text, persisted=lambda: [100]) is None
    assert ce.authority('I will rent one GPU with a total cap of $120.', asked=[100])
    assert ce.authority(text, ['# Kanban task: user approved $100 GPU budget.'])


def test_unrelated_small_amount_cannot_excuse_large_cap():
    ce = module()
    assert ce.authority('I will rent one GPU for an additional $6, total cap $3000.')
    assert ce.classify('Please approve an additional $6 for GPU rental, total cap $3000.') is None
    assert ce.authority('I will rent one GPU for an additional $20 and a $6 top-up.')


def test_historical_spend_is_not_an_increment():
    ce = module()
    assert ce.authority('GPU rental: $120 spent. I will rent one GPU with a $150 cap.')
    assert ce.classify('GPU rental: $120 spent. Please approve $150.') is None
