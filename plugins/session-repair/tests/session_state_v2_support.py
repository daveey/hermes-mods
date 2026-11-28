"""Shared by the SessionState v2 P0 golden of session-repair: core's scenario JSON and one real isolated board per scenario.

Nothing here classifies. It writes the facts a scenario states (card row, events, run, chat session) with the same
builders as the pattern tests (``fixtures/patterns.py``, core's ``create_task``); ``scan.scan`` then judges them.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from sr_helpers import fixtures

SCENARIOS = 'tests/fixtures/session-state-v2/scenarios.json'
GOLDEN = Path(__file__).with_name('session_state_v2_goldens.json')
UPDATE = 'SESSION_STATE_V2_UPDATE_GOLDENS'
AGE = 3600

# Share source materialization by file identity, never a same-basename sys.modules import.
_spec = importlib.util.spec_from_file_location('_repair_lanes_v2_sources',
    Path(__file__).resolve().parents[2] / 'lanes/tests/session_state_v2_support.py')
assert _spec is not None and _spec.loader is not None
_sources = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sources)
replica_case = _sources.replica_case
canonical_case = _sources.canonical_case


def seed_runtime(home, data, scenario, monkeypatch):
    """Real core envelope, persisted chat, and real runtime collectors over isolated source IO."""
    from hermes_state import SessionDB
    from hermes_cli import process_session_activity as psa, subagent_session_activity as ssa
    from tools.process_registry import process_registry
    from types import SimpleNamespace
    canonical = canonical_case(data, scenario)
    sid, now = data['identity']['session_id'], data['now']
    with SessionDB(db_path=home / 'state.db') as db:
        db.create_session(sid, source='desktop')
        db.set_session_title(sid, 'Fixture chat')
        db.append_message(sid, role='user', content='Complete the fixture work.')
        display_kind = None
        if scenario.get('turn') == 'failed':
            from agent.turn_failure_copy import FAILED_TURN_DISPLAY_KIND
            display_kind = FAILED_TURN_DISPLAY_KIND
        db.append_message(sid, role='assistant', content='The turn failed.' if display_kind else
                          'Implemented the fixture work; verified all checks pass.', display_kind=display_kind)
        db._conn.execute('UPDATE sessions SET outcome_json=? WHERE id=?', (json.dumps(canonical['outcome']), sid))
        # Quiet enough for the scanner to consult real runtime obligations, not just exit on message age.
        db._conn.execute('UPDATE messages SET timestamp=? WHERE session_id=?', (now - 7200, sid))
        db._conn.commit()
        runtime = scenario.get('runtime', {})
        if runtime.get('kind') == 'subagent':
            db.create_session(runtime['id'], source='subagent', parent_session_id=sid)
            db.set_session_title(runtime['id'], runtime['title'])
    from hermes_cli import kanban_execution_observations as execution
    monkeypatch.setattr(execution, 'local_scope', lambda: (data['identity']['gateway'], 'default'))
    monkeypatch.setattr(ssa, '_profile_paths', lambda: [('default', home / 'state.db')])
    monkeypatch.setattr(ssa, 'local_scope', lambda: (data['identity']['gateway'], 'default'))
    monkeypatch.setattr(psa, '_checkpoint_paths', lambda: [])
    monkeypatch.setattr(psa, '_cache', None)
    monkeypatch.setattr(psa, '_details', {})
    monkeypatch.setattr(psa, '_evidence', {})
    process = runtime.get('kind') == 'process'
    proc = SimpleNamespace(id=runtime.get('id'), exited=False, pid_scope='remote', pid=None,
        host_start_time=None, session_key=sid, parent_session_id=sid, owner_task_id='default',
        task_id='default', command=runtime.get('title'), started_at=now - 60)
    monkeypatch.setattr(process_registry, '_running', {proc.id: proc} if process else {})
    monkeypatch.setattr(process_registry, 'list_sessions', lambda: [
        {'session_id': proc.id, 'status': 'running'}] if process else [])
    return canonical


def load_scenarios():
    """(scenarios dict, skip reason). The path is always <HERMES_CORE_SOURCE>/tests/fixtures/session-state-v2."""
    root = os.environ.get('HERMES_CORE_SOURCE')
    if not root:
        return None, 'HERMES_CORE_SOURCE is not set'
    path = Path(root) / SCENARIOS
    if not path.is_file():
        return None, f'core at {root} has no {SCENARIOS}'
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1 or not isinstance(data.get('scenarios'), list):
        return None, f'incompatible session-state-v2 scenarios schema {data.get("schema_version")!r}'
    if not (Path(root) / 'hermes_cli' / 'kanban_db.py').is_file():
        return None, f'{root} is not a Hermes core checkout'
    try:
        import hermes_cli  # noqa: F401
    except ImportError:
        sys.path.insert(0, root)
    return data, None


def scenario_params(data):
    return [pytest.param(s, id=s['id']) for s in (data or {}).get('scenarios', [])]


def merged(data, scenario):
    card = scenario.get('card')
    if card is None:
        return None
    card = {**data['defaults'], **card}
    donor = scenario.get('human_request_from')
    if donor:
        card['human_request'] = next(s for s in data['scenarios'] if s['id'] == donor)['card']['human_request']
    return card


def materialize(conn, state, data, scenario, now):
    """Write local card facts only; remote cards live solely in ``replica_case``'s replica."""
    F = fixtures()
    card = merged(data, scenario)
    ids = {'card': None, 'producer': None}
    chat = data['identity']['session_id']
    state.execute('INSERT OR IGNORE INTO sessions (id, title, started_at) VALUES (?, ?, ?)', (chat, 'Fixture chat', now - 86400))
    state.commit()
    if card is None or card.get('remote'):
        return ids
    age = scenario.get('triage_age_s', AGE)
    producer = scenario.get('producer')
    if producer:
        if producer['status'] == 'running':
            ids['producer'] = F.running_healthy(conn, now)
        else:
            ids['producer'] = F.card(conn, producer['title'], status=producer['status'], session=None)
        conn.execute('UPDATE tasks SET session_id = NULL WHERE id = ?', (ids['producer'],))
    status = card['status']
    cols = {}
    if status == 'running':
        cols = dict(claim_lock='host:1', claim_expires=int(now + 3600), started_at=int(now - 600))
    if card.get('human_request') is not None:
        cols['human_request'] = json.dumps(card['human_request'])
    if card.get('block_kind'):
        cols['block_kind'] = card['block_kind']
    if status == 'done':
        cols['completed_at'] = int(now - 600)
    if status == 'ready':
        cols['ready_since'] = int((now - age) * 1000)
    tid = ids['card'] = F.card(conn, card['title'], status=status, session=chat, **cols)
    F.age_all_events(conn, tid, now - age)
    if status == 'running':
        F._run(conn, tid, now, started_s=600, heartbeat_s=30)
        F.event(conn, tid, 'heartbeat', {}, at=now - 30)
    aw = card.get('awaiting')
    if aw:
        aw = dict(aw)
        if aw.get('kind') == 'card':
            aw['ref'] = ids['producer']
            conn.execute('INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)', (ids['producer'], tid))
        aw.setdefault('why', 'fixture')
        F.event(conn, tid, 'scheduled', {'awaiting': aw, 'reason': aw['why'], 'wake_at': int(now + 86400)}, at=now - age)
    if status == 'blocked' and not scenario.get('episodes'):
        F.event(conn, tid, 'blocked', {'reason': 'fixture'}, at=now - age)
    steps = scenario.get('episodes') or []
    for n, kind in enumerate(steps):
        F.event(conn, tid, kind, {'reason': 'fixture episode'}, at=now - (len(steps) - n) * 600)
    conn.commit()
    return ids
