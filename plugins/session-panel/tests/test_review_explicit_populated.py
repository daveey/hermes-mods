"""Independent populated-source acceptance probes (review only)."""
import sqlite3
import time
import pytest
from test_explicit_home_acquisition import api, worlds, tripwires, ctx_for


def test_existing_action_store_does_not_resolve_ambient(api, worlds, tripwires):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    companion = readers._user_step_api()
    core = companion._load('core')
    with tripwires.disarmed():
        core.ActionStore(path=worlds['explicit'] / 'user-steps' / 'actions.sqlite3')
    try:
        requests = readers.user_step_requests('chat', ['chat'])
        assert requests == []
        assert tripwires == []
    finally:
        readers.release()


def test_action_store_with_explicit_boards_control(api, worlds, tripwires, monkeypatch):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    companion = readers._user_step_api()
    core = companion._load('core')
    mirror = companion._load('kanban_mirror')
    original = mirror.Mirror
    with tripwires.disarmed():
        core.ActionStore(path=worlds['explicit'] / 'user-steps' / 'actions.sqlite3')
    monkeypatch.setattr(mirror, 'Mirror', lambda c, s: original(c, s, boards={}))
    try:
        assert readers.user_step_requests('chat', ['chat']) == []
        assert tripwires == []
    finally:
        readers.release()


def test_producer_lane_cannot_certify_skipped_reconciliation(api, worlds, monkeypatch):
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    companion = readers._user_step_api()
    calls = []
    monkeypatch.setattr(companion, 'panel_reconcile', lambda *a, **kw: calls.append((a, kw)) or [])
    try:
        deferred = readers.reconcile_sources(['chat'])
        assert calls or 'chat' in deferred or 'chat' in readers.reconcile_unreached
    finally:
        readers.release()


def test_persisted_subagent_is_not_silently_empty(api, worlds, tripwires):
    home = worlds['explicit']
    now = time.time()
    from hermes_state import SessionDB
    with tripwires.disarmed():
        db = SessionDB(db_path=home / 'state.db')
        try:
            db.create_session('explicit-child', source='subagent', parent_session_id='chat')
        finally:
            db.close()
    with sqlite3.connect(home / 'state.db') as db:
        db.execute('UPDATE sessions SET last_activity_at=?, started_at=?, title=? WHERE id=?',
                   (now, now, 'Real child', 'explicit-child'))
    from hermes_cli import subagent_session_activity as sub
    assert sub._children(home / 'state.db', now, {'chat'})[0][0] == 'explicit-child'
    readers = api.CoreReaders(home_context=ctx_for(home))
    try:
        result = readers.panel_inputs('chat', ['chat'])
        assert tripwires == []
        assert result['subagents'] or result['input_errors'], result
    finally:
        readers.release()


def test_bad_config_is_unavailable_not_disabled(api, worlds, tripwires):
    (worlds['explicit'] / 'config.yaml').write_text('kanban: [unterminated')
    readers = api.CoreReaders(home_context=ctx_for(worlds['explicit']))
    try:
        with pytest.raises(Exception):
            readers.external_capture({'chat': {'lineage':['chat'], 'bundle':{}, 'refs':[]}}, include_inventory=True)
    finally:
        readers.release()
