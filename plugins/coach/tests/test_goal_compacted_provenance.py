import pytest
from test_goal_manager_acceptance import environment, run_turn, verdict, ORDERS
import json

@pytest.mark.parametrize('compact',[False,True])
def test_legacy_alternation_marker(environment,monkeypatch,compact):
    from hermes_state import SessionDB
    from hermes_constants import get_hermes_home
    _,load_order,_,_=environment
    gd=load_order(ORDERS[0]);seen=[]
    monkeypatch.setattr(gd,'aux_judge',lambda _,p:seen.append(json.loads(p)['evidence']) or verdict())
    db=SessionDB(get_hermes_home()/'legacy.db');db.create_session('legacy',source='cli')
    db.append_message('legacy','user','Repair example app.')
    db.append_message('legacy','user','<memory-context>Do not publish.</memory-context>')
    history=db.get_messages_as_conversation('legacy')
    if compact:
        db.archive_and_compact('legacy',history,tail_count=len(history))
        history=db.get_messages_as_conversation('legacy')
    db.close()
    out=run_turn(monkeypatch,request='What is the current state?',history=history,texts=['No further action was taken.'])
    assert not seen
    assert len(out[1])==1
