"""Queued receipts are not executed user turns or recovery anchors."""
from types import SimpleNamespace
from test_goal_scope_recovery import setup, session, run, users, gd


def test_queued_receipt_does_not_duplicate_current_anchor(setup):
    db, seen, events = setup
    session(db, 'child')
    db.append_message('child', 'user', 'Repair example app')
    db.append_message('child', 'user', 'Continue carefully', display_metadata={'_queued_prompt': True})
    db.append_message('child', 'user', 'Continue carefully')
    run(seen, events)
    assert seen, events
    assert users(seen) == ['Repair example app', 'Continue carefully']


def test_unexecuted_queue_cannot_add_authority(setup):
    db, seen, events = setup
    session(db, 'child')
    db.append_message('child', 'user', 'Do not publish')
    db.append_message('child', 'user', 'Publish everything', display_metadata={'_queued_prompt': True})
    db.append_message('child', 'user', 'Continue carefully')
    run(seen, events)
    assert users(seen) == ['Do not publish', 'Continue carefully']


def test_false_queue_marker_retains_real_scope(setup):
    db, seen, events = setup
    session(db, 'child')
    db.append_message('child', 'user', 'Do not publish', display_metadata={'_queued_prompt': False})
    db.append_message('child', 'user', 'Continue carefully')
    run(seen, events)
    assert users(seen) == ['Do not publish', 'Continue carefully']


def test_in_memory_queue_receipt_is_not_scope(setup):
    db, seen, events = setup
    session(db, 'child')
    rows = [
        {'role': 'user', 'content': 'Do not publish'},
        {'role': 'user', 'content': 'Publish everything', 'display_metadata': {'_queued_prompt': True}},
    ]
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    gate.capture(session_id='child', user_message='Continue carefully', conversation_history=rows)
    gate(session_id='child', final_response='Still broken', turn_messages=[])
    assert users(seen) == ['Do not publish', 'Continue carefully']
