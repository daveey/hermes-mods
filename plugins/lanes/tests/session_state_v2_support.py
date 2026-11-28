"""Shared by the SessionState v2 P0 golden: the core scenario JSON and a real, isolated lanes board per scenario.

Nothing here classifies. It only writes the facts a scenario states (card rows, events, chat link) into a real board
made by core's ``kanban_db``; lanes flow -> attention -> view then classify them in the test.
"""
import json
import os
import sys
import importlib.util
from contextlib import closing
from pathlib import Path

import pytest

SCENARIOS = 'tests/fixtures/session-state-v2/scenarios.json'
GOLDEN = Path(__file__).with_name('session_state_v2_goldens.json')
UPDATE = 'SESSION_STATE_V2_UPDATE_GOLDENS'
AGE = 3600  # every card was created and last moved this long before the pinned clock, unless a scenario says more


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
    """The scenario's card with the file defaults applied; a ``human_request_from`` borrows another scenario's ask."""
    card = scenario.get('card')
    if card is None:
        return None
    card = {**data['defaults'], **card}
    donor = scenario.get('human_request_from')
    if donor:
        card['human_request'] = next(s for s in data['scenarios'] if s['id'] == donor)['card']['human_request']
    return card


def _ev(conn, tid, kind, payload, at):
    conn.execute('INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, ?)',
                 (tid, kind, json.dumps(payload or {}), int(at)))


def _make(conn, title, status, now, *, session, age=AGE, **cols):
    from hermes_cli import kanban_db
    tid = kanban_db.create_task(conn, title=title, initial_status='running', session_id=session, assignee='default',
                                tenant='work')
    conn.execute('UPDATE task_events SET created_at = ? WHERE task_id = ?', (int(now - age), tid))
    sets = {'status': status, 'created_at': int(now - age), **cols}
    conn.execute(f"UPDATE tasks SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), tid))
    conn.commit()
    return tid


def _run(conn, tid, now, started_s=600):
    cur = conn.execute('INSERT INTO task_runs (task_id, profile, status, started_at, last_heartbeat_at) '
                       "VALUES (?, 'default', 'running', ?, ?)", (tid, int(now - started_s), int(now - 30)))
    conn.execute('UPDATE tasks SET current_run_id = ? WHERE id = ?', (cur.lastrowid, tid))
    _ev(conn, tid, 'heartbeat', {}, now - 30)
    conn.commit()


CHAT_LINKS = '''CREATE TABLE IF NOT EXISTS task_chat_links (
    id INTEGER PRIMARY KEY, uid TEXT, task_id TEXT NOT NULL, chat_gateway TEXT NOT NULL, chat_profile TEXT,
    chat_session TEXT NOT NULL, kind TEXT NOT NULL, released INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)'''


def link_chat(conn, tid, identity, now):
    """The membership row a synced board holds for a chat-created card (core's own DDL, columns unchanged)."""
    conn.execute(CHAT_LINKS)
    conn.execute('INSERT INTO task_chat_links (task_id, chat_gateway, chat_profile, chat_session, kind, created_at) '
                 'VALUES (?, ?, ?, ?, ?, ?)',
                 (tid, identity['gateway'], identity['profile'], identity['session_id'], 'created', int(now - AGE)))


def materialize(conn, data, scenario, now, *, session=None):
    """Write one scenario into ``conn``. Returns {'card': id|None, 'producer': id|None}.

    Facts only. Remote cards are materialized separately in the full replica by ``replica_case``; they must
    never become healthy local workers here.
    """
    card = merged(data, scenario)
    ids = {'card': None, 'producer': None}
    if card is None or card.get('remote'):
        return ids
    session = session or data['identity']['session_id']
    producer = scenario.get('producer')
    if producer:
        ids['producer'] = _make(conn, producer['title'], producer['status'], now, session=None,
                                claim_lock='fixture:1', claim_expires=int(now + 3600), started_at=int(now - 600))
        if producer['status'] == 'running':
            _run(conn, ids['producer'], now)
    status = card['status']
    age = scenario.get('triage_age_s', AGE)
    cols = {}
    if status == 'running':
        cols = dict(claim_lock='fixture:1', claim_expires=int(now + 3600), started_at=int(now - 600))
    if card.get('human_request') is not None:
        cols['human_request'] = json.dumps(card['human_request'])
    if card.get('block_kind'):
        cols['block_kind'] = card['block_kind']
    if status == 'done':
        cols['completed_at'] = int(now - 600)
    tid = ids['card'] = _make(conn, card['title'], status, now, session=session, age=age, **cols)
    if status == 'running':
        _run(conn, tid, now)
    link_chat(conn, tid, data['identity'], now)
    aw = card.get('awaiting')
    if aw:
        aw = dict(aw)
        if aw.get('kind') == 'card':
            aw['ref'] = ids['producer']
            conn.execute('INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)', (ids['producer'], tid))
        aw.setdefault('why', 'fixture')
        _ev(conn, tid, 'scheduled', {'awaiting': aw, 'reason': aw['why'], 'wake_at': int(now + 86400)}, now - age)
    if status == 'blocked' and not scenario.get('episodes'):
        _ev(conn, tid, 'blocked', {'reason': 'fixture'}, now - age)
    steps = scenario.get('episodes') or []
    for n, kind in enumerate(steps):
        _ev(conn, tid, kind, {'reason': 'fixture episode'}, now - (len(steps) - n) * 600)
    conn.commit()
    return ids


def canonical_case(data, scenario):
    # Import-only bootstrap: test-all already provisions dependencies. Do not ask PM
    # to discover/install a runtime beneath the helper's temporary HERMES_HOME.
    from unittest.mock import patch
    with patch.object(sys, 'argv', ['hermes', 'pm', 'repair']):
        import hermes_bootstrap  # noqa: F401
    path = Path(os.environ['HERMES_CORE_SOURCE']) / 'tests/hermes_state/session_state_v2_fixtures.py'
    spec = importlib.util.spec_from_file_location('_lanes_canonical_session_v2', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_case(scenario, data)


def replica_case(data, scenario, monkeypatch, home):
    """Seed the actual sync replica, then read freshness/ownership through core's replica reader."""
    from hermes_cli import kanban_db, kanban_db_connect as kbc, kanban_sync_client as ksc
    from hermes_cli import kanban_db_sync as sync, kanban_chat_links as links
    card = merged(data, scenario)
    if not card or not card.get('remote'):
        return None, []
    path = home / 'replica.db'
    monkeypatch.setattr(ksc, 'shadow_path', lambda slug: path)
    with closing(kbc.connect(path)) as conn:
        sync.apply_sync_schema_steps(conn)
        tid = kanban_db.create_task(conn, title=card['title'], initial_status='running', session_id=None,
                                    tenant='work', assignee='default')
        conn.execute('UPDATE tasks SET status=?,created_on=?,created_at=? WHERE id=?',
                     (card['status'], card['gateway'], int(data['now'] - AGE), tid))
        link_chat(conn, tid, data['identity'], data['now'])
        # A pull clock is source metadata, not a current execution heartbeat.
        conn.execute('INSERT INTO kanban_sync_state(board_id,gateway_id,hlc_last,mode,pulled_at) '
                     'VALUES (?,?,?,?,?)', ('fixture', data['identity']['gateway'], '0', 'primary',
                                           card['replica_synced_at']))
        conn.commit()
    rows = links.replica_cards([data['identity']['session_id']], data['identity']['gateway'],
                              replicas=[('default', path)], profile=data['identity']['profile'],
                              origin_cards=[], now=data['now'])
    assert len(rows) == 1, rows
    row = rows[0]
    for key in ('remote', 'source', 'stale', 'gateway', 'status'):
        assert row[key] == card[key], (key, row)
    assert row['synced_at'] == card['replica_synced_at']
    return tid, rows
