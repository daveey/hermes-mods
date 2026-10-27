"""Ownership may not use synthetic or provenance-lost rows as user scope."""
import json
import sqlite3
import pytest
from test_goal_ownership import owner, certify
from goal_consumer_fixture import consumer


@pytest.mark.parametrize('field,value', [
    ('observed', 1),
    ('display_kind', 'internal_event'),
    ('_compressed_summary', 1),
    ('display_metadata', json.dumps({'user_content_sanitized': True})),
    ('display_metadata', '{invalid'),
])
def test_non_user_latest_row_cannot_certify_owner(owner, consumer, field, value):
    with sqlite3.connect(owner[0].parent / 'state.db') as conn:
        conn.execute(f"UPDATE messages SET {field}=? WHERE role='user'", (value,))
    with pytest.raises(ValueError):
        certify(owner)


def test_native_sidecar_is_not_authority_but_clean_user_remains_valid(owner, consumer):
    with sqlite3.connect(owner[0].parent / 'state.db') as conn:
        conn.execute("UPDATE messages SET api_content=?, display_metadata=? WHERE role='user'",
                     ('different wire content', json.dumps({'user_content_sanitized': False})))
    assert certify(owner)['disposition'] == 'owned_continuing'


def test_gate_records_unknown_instead_of_certifying_synthetic_origin(owner, consumer, monkeypatch):
    from types import SimpleNamespace
    from test_goal_ownership import gd, SID, REQUEST
    with sqlite3.connect(owner[0].parent / 'state.db') as conn:
        conn.execute("UPDATE messages SET observed=1 WHERE role='user'")
    events = []
    monkeypatch.setattr(gd, 'settings', lambda: {'mode': 'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: owner[1])
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(session_id=SID, final_response='Worker continuing',
                turn_messages=[{'role': 'user', 'content': REQUEST}]) is None
    assert events[-1]['event'] == 'goal_disposition_unknown'
    assert events[-1]['reason'] == 'owner_current_request_mismatch'
