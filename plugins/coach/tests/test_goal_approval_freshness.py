import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
from test_goal_proofs import gd, verdict

def rows(new_timeout):
    action = 'systemctl restart example'
    return [dict(role='user', content='Repair app'),
        dict(role='assistant',tool_calls=[dict(id='old',function=dict(name='terminal',arguments={'command':action,'timeout':30}))]),
        dict(role='tool',tool_call_id='old',content={'status':'pending_approval','approval_pending':True,'command':action}),
        dict(role='assistant',tool_calls=[dict(id='new',function=dict(name='terminal',arguments={'command':action,'timeout':new_timeout}))]),
        dict(role='tool',tool_call_id='new',content={'exit_code':0,'output':'Restart completed'})]

def value():
    return verdict(evidence_refs=['user:0','tool:old','candidate'],proof={'action':'systemctl restart example','source_ref':'tool:old','approval_path':'/approve'})

@pytest.mark.parametrize('new_timeout',[30,60])
def test_successful_same_command_invalidates_pending_approval(new_timeout):
    e=gd.evidence_for(rows(new_timeout),'systemctl restart example awaits /approve')
    with pytest.raises(ValueError,match='superseded_approval_evidence'):
        gd.validate_disposition(gd.validate(value(),e),e)

def test_public_recheck_cannot_clear_pending_with_stale_approval(monkeypatch,tmp_path):
    from agent.pre_final_gate import evaluate, UnresolvedFallback
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    monkeypatch.setattr(gd,'settings',lambda:{'mode':'enforce'})
    monkeypatch.setattr(gd,'aux_judge',lambda *_:value())
    events=[]
    gate=gd.build_gate(SimpleNamespace(log=events.append))
    monkeypatch.setattr('agent.pre_final_gate.eligible_callbacks',lambda agent:[gate])
    pending=UnresolvedFallback('Repair app',('user:0','candidate'),recheck_callback=gate)
    d=evaluate(SimpleNamespace(session_id='',platform='cli',model='test'),
               'systemctl restart example awaits /approve',rows(60),1,pending)
    assert not d.recheck_accepted, events
    assert d.unresolved_fallback is not None


def test_other_command_does_not_supersede_approval():
    messages = rows(60)
    messages[3]['tool_calls'][0]['function']['arguments']['command'] = 'systemctl status other'
    evidence = gd.evidence_for(messages, 'systemctl restart example awaits /approve')
    gd.validate_disposition(gd.validate(value(), evidence), evidence)
