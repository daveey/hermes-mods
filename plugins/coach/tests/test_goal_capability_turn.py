"""Freshness must preserve a rejected candidate through the complete turn."""
from test_goal_manager_acceptance import environment, run_turn, assert_private, verdict, ORDERS


def test_stale_capability_cannot_release_held_text(environment, monkeypatch):
    manager, load_order, configure, events = environment
    gd = load_order(ORDERS[0])
    owner = next(h for h in manager.iter_hook_callbacks('pre_final_gate')
                 if h.acceptance_plugin == 'coach')
    seen = []
    def judge(*args):
        seen.append(True)
        if len(seen) == 1:
            return verdict()
        return dict(verdict('capability_boundary'), boundary_kind='credentials',
                    evidence_refs=['user:0', 'candidate', 'tool:blocked'],
                    proof={'source_ref': 'tool:blocked', 'action': 'https://example.com/manage'})
    monkeypatch.setattr(gd, 'aux_judge', judge)
    def observe(count, agent):
        if count != 2:
            return
        for call_id, result in [('blocked', {'error': 'missing_credentials'}),
                                ('recovered', {'status': 200})]:
            owner.observe(session_id=agent.session_id, tool_name='fetch',
                          args={'url': 'https://example.com/manage'},
                          result=result, status='success', tool_call_id=call_id)
    out = run_turn(monkeypatch, before_call=observe)
    assert_private(out)
    assert len(seen) == 2
    assert 'continuation unavailable' in out[0]['final_response']
