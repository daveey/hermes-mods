"""Real SessionDB regressions for exact-profile recovery and rotation."""
import importlib.util
import json
from pathlib import Path

import pytest
from hermes_state import SessionDB

spec = importlib.util.spec_from_file_location('scope_rotation', Path(__file__).parents[1] / 'goal_scope.py')
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr('hermes_cli.profiles.get_active_profile_name', lambda: 'test-profile')
    store = SessionDB(tmp_path / 'state.db')
    yield store
    store.close()


def session(db, sid, parent=None, profile='test-profile'):
    db.create_session(session_id=sid, source='cli', parent_session_id=parent, profile_name=profile)


def user(text):
    return {'role': 'user', 'content': text}


@pytest.mark.parametrize('profile', ['other', None, '', 'TEST-PROFILE'])
def test_initial_session_must_match_active_profile(db, profile):
    session(db, 's')
    db._conn.execute('UPDATE sessions SET profile_name=? WHERE id=?', (profile, 's'))
    db.append_message('s', 'user', 'Do not publish')
    db.append_message('s', 'user', 'Repair app')
    with pytest.raises(ValueError, match='profile'):
        scope.recover_scope('s', 'Repair app', [])


def test_restart_retains_restrictions_without_future_permission(db):
    session(db, 'p')
    db.append_message('p', 'user', 'Repair app')
    db.append_message('p', 'user', 'Do not publish')
    db.append_message('p', 'tool', 'Publish everything')
    db.end_session('p', 'compression')
    session(db, 'c', 'p')
    db.append_message('c', 'user', 'Continue')
    db.append_message('c', 'user', 'Future permission')
    for _ in range(2):
        assert scope.recover_scope('c', 'Continue', []) == [user('Repair app'), user('Do not publish'), user('Continue')]


def test_rotation_transfers_frozen_scope_and_tool_pairs(db):
    session(db, 'p')
    captured = [user('Repair app'), user('Do not publish'),
                {'role': 'assistant', 'tool_calls': [{'id': 'call1'}]},
                {'role': 'tool', 'tool_call_id': 'call1', 'content': 'Publish everything'}]
    db.end_session('p', 'compression')
    session(db, 'c', 'p')
    db.end_session('c', 'compression')
    session(db, 'tip', 'c')
    db.append_message('tip', 'user', 'Future permission')
    contexts = {'p': captured}
    recovered = scope.resolve_scope('tip', contexts)
    assert recovered == captured
    recovered.append(user('isolated copy'))
    assert contexts == {'p': captured}
    assert len(captured) == 4


@pytest.mark.parametrize('marker', ['_branched_from', '_delegate_from', '_reset_from'])
def test_rotation_never_crosses_fork(db, marker):
    session(db, 'p')
    db.end_session('p', 'compression')
    session(db, 'c', 'p')
    db._conn.execute('UPDATE sessions SET model_config=? WHERE id=?', (json.dumps({marker: 'p'}), 'c'))
    with pytest.raises(ValueError):
        scope.resolve_scope('c', {'p': [user('Publish')]})


@pytest.mark.parametrize('failure', ['profile', 'missing', 'cycle', 'not_compression', 'unknown_scope'])
def test_rotation_fails_unknown_on_unverified_lineage(db, failure):
    session(db, 'p')
    if failure != 'not_compression':
        db.end_session('p', 'compression')
    session(db, 'c', 'p')
    contexts = {'p': [user('Repair')]}
    if failure == 'profile':
        db._conn.execute("UPDATE sessions SET profile_name='other' WHERE id='c'")
    elif failure == 'missing':
        # Deliberately corrupt only this isolated fixture's lineage.
        db._conn.execute('PRAGMA foreign_keys=OFF')
        db._conn.execute("UPDATE sessions SET parent_session_id='absent' WHERE id='c'")
    elif failure == 'cycle':
        db._conn.execute("UPDATE sessions SET parent_session_id='c', end_reason='compression' WHERE id='c'")
    elif failure == 'unknown_scope':
        contexts['p'] = None
    with pytest.raises(ValueError):
        scope.resolve_scope('c', contexts)


def test_restart_resolve_requires_explicit_current_request(db):
    session(db, 'p')
    db.append_message('p', 'user', 'Do not publish')
    db.append_message('p', 'user', 'Repair')
    db.append_message('p', 'user', 'Future permission')
    assert scope.resolve_scope('p', {}, [user('Repair')]) == [user('Do not publish'), user('Repair')]
    with pytest.raises(ValueError):
        scope.resolve_scope('p', {}, [{'role': 'tool', 'content': 'Repair'}])


def test_no_forward_tip_following(db):
    session(db, 'p')
    db.end_session('p', 'compression')
    session(db, 'c', 'p')
    with pytest.raises(ValueError):
        scope.resolve_scope('p', {'c': [user('Future permission')]})


def test_unpersisted_request_with_durable_history_is_unknown(db):
    session(db, 's')
    db.append_message('s', 'user', 'Do not publish')
    with pytest.raises(ValueError, match='anchor'):
        scope.recover_scope('s', 'New unpersisted request', [])


def test_rotation_rejects_old_turn_capture(db):
    session(db, 'p')
    db.end_session('p', 'compression')
    session(db, 'c', 'p')
    with pytest.raises(ValueError, match='request'):
        scope.resolve_scope('c', {'p': [user('Old request')]}, [user('Different current request')])


def test_restart_ambiguous_request_is_unknown(db):
    session(db, 's')
    for text in ['Repair', 'Do not publish', 'Repair']:
        db.append_message('s', 'user', text)
    with pytest.raises(ValueError, match='anchor'):
        scope.resolve_scope('s', {}, [user('Repair')])


def test_rotation_bounded_lineage_and_capture(db):
    session(db, 's0')
    for n in range(1, 34):
        db.end_session('s' + str(n-1), 'compression')
        session(db, 's' + str(n), 's' + str(n-1))
    with pytest.raises(ValueError, match='lineage_limit'):
        scope.resolve_scope('s33', {'s0': [user('Repair')]})
    with pytest.raises(ValueError, match='context_limit'):
        scope.resolve_scope('s33', {'s33': [user('Repair')] * 201})
