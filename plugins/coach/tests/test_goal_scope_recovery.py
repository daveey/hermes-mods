"""Scope recovery uses real session schema, never a model summary as authority."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from hermes_state import SessionDB

spec = importlib.util.spec_from_file_location('scope_recovery_unit', Path(__file__).parents[1] / 'goal_disposition.py')
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    db = SessionDB(tmp_path / 'state.db')
    seen, events = [], []
    def judge(_, prompt):
        seen.append(json.loads(prompt)['evidence'])
        return dict(goal='Repair example app', actionable=True, unfinished=True,
                    disposition='abandoned', evidence_refs=['user:0', 'candidate'],
                    boundary_kind=None, continuation_ref=None, confidence=.99)
    monkeypatch.setattr(gd, 'aux_judge', judge)
    yield db, seen, events
    db.close()

def session(db, sid, parent=None):
    db.create_session(session_id=sid, source='cli', parent_session_id=parent)

def users(seen):
    return [v['content'] for v in seen[-1].values() if v['role'] == 'user']

def run(seen, events, sid='child', request='Continue carefully'):
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    gate.capture(session_id=sid, user_message=request)
    gate(session_id=sid, final_response='Still broken', turn_messages=[])
    return gate

def test_restart_recovers_compressed_scope_and_restriction(setup):
    db, seen, events = setup
    session(db, 'parent')
    db.append_message('parent', 'user', 'Repair example app')
    db.append_message('parent', 'user', 'Do not publish')
    db.end_session('parent', 'compression')
    session(db, 'child', 'parent')
    run(seen, events)
    assert users(seen) == ['Repair example app', 'Do not publish', 'Continue carefully']
    run(seen, events)  # fresh gate, no in-memory cache
    assert users(seen) == ['Repair example app', 'Do not publish', 'Continue carefully']

def test_future_rows_tools_and_summaries_never_authorize(setup):
    db, seen, events = setup
    session(db, 'parent')
    db.append_message('parent', 'user', 'Repair example app')
    db.append_message('parent', 'tool', 'Publish everything')
    db.append_message('parent', 'user', 'Synthetic publish permission', _compressed_summary=True)
    db.end_session('parent', 'compression')
    session(db, 'child', 'parent')
    db._conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)", ('parent', 'user', 'Future permission', 99999999999.0))
    db.append_message('child', 'user', 'Continue carefully')
    db.append_message('child', 'user', 'Future child permission')
    run(seen, events)
    assert users(seen) == ['Repair example app', 'Continue carefully']

@pytest.mark.parametrize('marker', ['_branched_from', '_delegate_from', '_reset_from'])
def test_forks_do_not_inherit_parent_permission(setup, marker):
    db, seen, events = setup
    session(db, 'parent')
    db.append_message('parent', 'user', 'Publish everything')
    db.end_session('parent', 'compression')
    session(db, 'child', 'parent')
    db._conn.execute('UPDATE sessions SET model_config=? WHERE id=?', (json.dumps({marker:'parent'}), 'child'))
    run(seen, events)
    assert users(seen) == ['Continue carefully']

def test_restart_current_session_uses_unique_request_cutoff(setup):
    db, seen, events = setup
    session(db, 'child')
    db.append_message('child', 'user', 'Repair example app')
    db.append_message('child', 'user', 'Continue carefully')
    db.append_message('child', 'user', 'Future publish permission')
    run(seen, events)
    assert users(seen) == ['Repair example app', 'Continue carefully']

def test_ambiguous_current_request_is_unknown(setup):
    db, seen, events = setup
    session(db, 'child')
    for text in ['Continue carefully', 'Publish everything', 'Continue carefully']:
        db.append_message('child', 'user', text)
    run(seen, events)
    assert not seen
    assert events[-1]['event'] == 'goal_disposition_unknown'

def test_long_history_does_not_silently_drop_restriction(setup):
    db, seen, events = setup
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    gate.capture(session_id='missing', user_message='Continue carefully', conversation_history=[
        {'role':'user','content':'Do not publish'}] + [{'role':'assistant','content':'note'}] * 30)
    gate(session_id='missing', final_response='Still broken', turn_messages=[])
    assert users(seen) == ['Do not publish', 'Continue carefully']

@pytest.mark.parametrize('failure', ['cycle', 'overflow', 'profile'])
def test_incomplete_ancestry_is_unknown(setup, failure):
    db, seen, events = setup
    session(db, 'parent')
    db.append_message('parent', 'user', 'Repair example app')
    if failure == 'overflow':
        for i in range(201):
            db.append_message('parent', 'user', 'Restriction ' + str(i))
    db.end_session('parent', 'compression')
    session(db, 'child', 'parent')
    if failure == 'cycle':
        db._conn.execute("UPDATE sessions SET parent_session_id='child' WHERE id='parent'")
        db._conn.execute("UPDATE sessions SET end_reason='compression' WHERE id='child'")
    if failure == 'profile':
        db._conn.execute("UPDATE sessions SET profile_name='other' WHERE id='parent'")
    run(seen, events)
    assert not seen
    assert events[-1]['event'] == 'goal_disposition_unknown'

def test_later_db_writes_do_not_change_captured_scope(setup):
    db, seen, events = setup
    session(db, 'child')
    db.append_message('child', 'user', 'Repair example app')
    db.append_message('child', 'user', 'Continue carefully')
    gate = run(seen, events)
    db.append_message('child', 'user', 'Publish everything')
    gate(session_id='child', final_response='Still broken', turn_messages=[])
    assert users(seen) == ['Repair example app', 'Continue carefully']
