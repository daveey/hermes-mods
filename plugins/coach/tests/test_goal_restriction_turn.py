"""Whole-turn scope veto in the discovered complete text-stop path."""
import pytest
from test_goal_manager_acceptance import environment, run_turn, verdict, ORDERS


@pytest.mark.parametrize('restriction', ['Stop.', 'Cancel.', 'Status only.', 'Analysis only.', 'Plan first.'])
def test_restriction_never_restarts_work_on_wrong_verdict(environment, monkeypatch, restriction):
    manager, load_order, configure, events = environment
    gd = load_order(ORDERS[0])
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: verdict())
    reply = 'Acknowledged. No further actions were taken.'
    out = run_turn(monkeypatch, request=restriction, texts=[reply])
    assert out[0]['final_response'] == reply
    assert len(out[1]) == 1
    assert out[5]._pre_final_gate_attempts == 0
    assert all(not result or not result.get('hold') for _, _, result in events)
    assert not any('Continue only already-authorized' in str(row) for row in out[4])
