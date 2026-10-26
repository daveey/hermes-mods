"""Native queue persistence -> public owner guard, in an isolated local profile.

Reuse the core queue fixture's real adopt/stage/flush turn body; only the
model/worker launch is replaced. No positive canonical user row is appended
by this test, and neither the owner reader nor consumer health is patched.
This is in-process acceptance, not another serve/WebSocket end-to-end test.
"""
from types import SimpleNamespace

import pytest
from hermes_state import SessionDB
from tui_gateway import server
from tests.tui_gateway.test_tui_gateway_queue_on_busy import _session
from tests.tui_gateway.test_queued_prompt_persistence import _busy, _run_turn
from test_goal_ownership import owner, gd, SID, REQUEST
from goal_consumer_fixture import consumer


@pytest.mark.parametrize('control', ['matching', 'mismatch', 'newer', 'stop', 'consumer_stop'])
def test_native_queue_drain_owner(owner, consumer, monkeypatch, control):
    db = SessionDB(owner[0].parent / 'state.db')
    monkeypatch.setattr(server, '_get_db', lambda: db)
    monkeypatch.setattr(server, '_session_uses_compute_host', lambda _: False)
    events = []
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: owner[1])
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    session = _session(session_key=SID, source='desktop',
                       profile_home=str(owner[0].parent))
    session['agent'].interrupt = lambda *a, **kw: None
    server._sessions[SID] = session
    dispatched = []

    def check(accepted):
        kwargs = dict(session_id=SID, final_response='The existing worker is continuing.',
                      turn_messages=[dict(role='user', content=REQUEST)], attempt=1)
        if accepted:
            assert gate(**kwargs) is None
            assert events[-1]['disposition'] == 'owned_continuing'
        else:
            with pytest.raises(ValueError):
                gate(**kwargs)
            assert events[-1]['event'] == 'goal_disposition_unknown'

    def dispatch(rid, sid, sess, text, **kwargs):
        dispatched.append(text)
        _run_turn(sess, db, SID, text, 'The existing worker is continuing.')

    monkeypatch.setattr(server, '_run_prompt_submit', dispatch)
    try:
        # Establish that the older matching row and still-live owner alone work.
        check(True)
        _busy(session, in_flight='An unrelated active turn')
        text = 'Only explain the unrelated app; do not repair' if control == 'mismatch' else REQUEST
        ack = server._handle_busy_submit('queue', SID, session, text, None,
                                         queued=True, display_kind=None)
        assert ack['result']['status'] == 'queued'
        queued = db.get_messages_as_conversation(SID, include_row_ids=True)[-1]
        assert queued['content'] == text
        assert queued['display_metadata']['_queued_prompt'] is True
        check(False)  # matching queued text cannot borrow the older canonical row
        if control == 'stop':
            server._interrupt_session_turn(SID, session)
        session['running'] = False
        server._clear_inflight_turn(session)
        assert server._drain_queued_prompt('drain', SID, session) is (control != 'stop')
        if control == 'stop':
            assert dispatched == []
            check(False)
            return
        assert dispatched == [text]
        rows = db.get_messages_as_conversation(SID, include_row_ids=True)
        canonical = rows[-2]
        assert canonical['role'] == 'user' and canonical['content'] == text
        assert canonical['_row_id'] != queued['_row_id']
        assert not (canonical.get('display_metadata') or {}).get('_queued_prompt')
        assert queued['_row_id'] not in {row['_row_id'] for row in rows}
        all_rows = db.get_messages_as_conversation(SID, include_inactive=True, include_row_ids=True)
        assert queued['_row_id'] in {row['_row_id'] for row in all_rows}
        check(control != 'mismatch')
        if control == 'newer':
            _busy(session, in_flight=REQUEST)
            ack = server._handle_busy_submit('newer', SID, session,
                'Do not repair; only explain a different app', None,
                queued=True, display_kind=None)
            assert ack['result']['status'] == 'queued'
            check(False)
            session['running'] = False
            server._clear_inflight_turn(session)
            assert server._drain_queued_prompt('newer-drain', SID, session)
            assert len(dispatched) == 2
            check(False)
        elif control == 'consumer_stop':
            consumer[1].set()
            check(False)  # durable subscription is not live scoped return health
    finally:
        server._sessions.pop(SID, None)
        db.close()
