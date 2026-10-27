"""Gate integration must consume recovered scope, not merely test its helper."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from hermes_state import SessionDB

spec = importlib.util.spec_from_file_location('goal_scope_gate', Path(__file__).parents[1] / 'goal_disposition.py')
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr('hermes_cli.profiles.get_active_profile_name', lambda: 'test-profile')
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    db = SessionDB(tmp_path / 'state.db')
    seen, events = [], []
    def judge(_, prompt):
        seen.append(json.loads(prompt)['evidence'])
        return dict(goal='Repair app', actionable=True, unfinished=True, disposition='abandoned',
                    evidence_refs=['user:0', 'candidate'], boundary_kind=None, continuation_ref=None, confidence=.99)
    monkeypatch.setattr(gd, 'aux_judge', judge)
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    yield db, seen, events, gate
    db.close()

def session(db, sid, parent=None, profile='test-profile'):
    db.create_session(session_id=sid, source='cli', parent_session_id=parent, profile_name=profile)

def user(s):
    return {'role': 'user', 'content': s}

def test_gate_rotation_preserves_scope_restrictions_and_observations(setup):
    db, seen, events, gate = setup
    session(db, 'p')
    gate.capture(session_id='p', user_message='Repair app', conversation_history=[user('Do not publish')])
    gate.observe(session_id='p', tool_name='fetch', args={}, result={'status':503}, tool_call_id='before')
    db.end_session('p', 'compression')
    session(db, 'c', 'p')
    gate.observe(session_id='c', tool_name='fetch', args={}, result={'status':503}, tool_call_id='after')
    assert gate(session_id='c', final_response='Still broken', turn_messages=[user('Repair app')])['hold']
    assert [r['content'] for r in seen[-1].values() if r['role']=='user'] == ['Do not publish', 'Repair app']
    assert {'tool:before','tool:after'} <= seen[-1].keys()

def test_gate_restart_recovers_restriction_without_capture(setup):
    db, seen, events, gate = setup
    session(db, 's')
    for text in ['Do not publish', 'Repair app', 'Future permission']:
        db.append_message('s', 'user', text)
    gate(session_id='s', final_response='Still broken', turn_messages=[user('Repair app')])
    assert [r['content'] for r in seen[-1].values() if r['role']=='user'] == ['Do not publish', 'Repair app']

def test_gate_foreign_initial_profile_is_unknown(setup):
    db, seen, events, gate = setup
    session(db, 's', profile='other')
    gate.capture(session_id='s', user_message='Repair app')
    gate(session_id='s', final_response='Still broken', turn_messages=[])
    assert not seen
    assert events[-1]['event']=='goal_disposition_unknown'

def test_same_session_id_in_other_profile_does_not_reuse_capture(setup, monkeypatch, tmp_path):
    db, seen, events, gate = setup
    gate.capture(session_id='s', user_message='Private old request')
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(other))
    monkeypatch.setattr('hermes_cli.profiles.get_active_profile_name', lambda: 'other')
    gate(session_id='s', final_response='Still broken', turn_messages=[user('Repair app')])
    assert [r['content'] for r in seen[-1].values() if r['role']=='user']==['Repair app']


def test_capture_before_persistence_uses_current_message_timestamp(setup):
    db, seen, events, gate = setup
    session(db, 's')
    db.append_message('s', 'user', 'Do not publish')
    import time
    cutoff = time.time()
    db._conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('s','user','Future permission',?)", (cutoff+100,))
    gate.capture(session_id='s', user_message='Repair app', conversation_history=[
        dict(user('Repair app'), timestamp=cutoff)])
    gate(session_id='s', final_response='Still broken', turn_messages=[])
    assert seen, events
    assert [r['content'] for r in seen[-1].values() if r['role']=='user']==['Do not publish', 'Repair app']


def test_persistence_metadata_is_not_synthetic_authorization(setup):
    db, seen, events, gate = setup
    gate.capture(session_id='s', user_message='Repair app', conversation_history=[
        dict(user('Do not publish'), _row_id=1, message_uid='scope1'),
        dict(user('Publish everything'), _pre_final_gate_synthetic=True)])
    gate(session_id='s', final_response='Still broken', turn_messages=[])
    assert [r['content'] for r in seen[-1].values() if r['role']=='user']==['Do not publish', 'Repair app']
    evidence = gd.evidence_for([
        dict(user('Repair app'), _row_id=2),
        {'role':'assistant','tool_calls':[{'id':'c','function':{'name':'fetch','arguments':'{}'}}], '_tool_call_uids':{'c':'uid'}},
        {'role':'tool','tool_call_id':'c','content':{'status':503},'_tool_call_uid':'uid','_row_id':4}], 'Still broken')
    assert 'user:0' in evidence and 'tool:c' in evidence
