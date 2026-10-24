"""Read-only owner certification against isolated real Kanban schema."""
import importlib.util
import os
import socket
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('owner_goal_test', Path(__file__).parents[1] / 'goal_disposition.py')
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)
REQUEST = 'Repair example app'
SID = 'example-origin'
TID = 't_1234abcd'


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from hermes_cli import kanban_db as kb
    from hermes_cli import profiles
    monkeypatch.setattr(profiles, 'get_active_profile_name', lambda: 'default')
    from hermes_cli.kanban_db_dispatch import _process_fingerprint
    from hermes_cli.kanban_host_identity import claim_prefix
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_SESSION_KEY', SID)
    for key in ('HERMES_KANBAN_DB', 'HERMES_KANBAN_TASK', 'HERMES_PROFILE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(kb, 'kanban_home', lambda: tmp_path)
    db = tmp_path / 'kanban.db'
    with sqlite3.connect(db) as c:
        c.executescript(kb.SCHEMA_SQL)
        c.execute("INSERT INTO tasks (id,title,body,assignee,status,created_at,session_id,current_run_id) VALUES (?,?,?,'default','running',?,?,1)", (TID,REQUEST,REQUEST,int(time.time()),SID))
        c.execute("INSERT INTO task_runs (id,task_id,profile,status,claim_lock,claim_expires,worker_pid,worker_started_at,started_at) VALUES (1,?,'default','running',?,?,?,?,?)", (TID,claim_prefix() + str(os.getpid()),int(time.time())+300,os.getpid(),_process_fingerprint(os.getpid()),int(time.time())))
        c.execute("INSERT INTO kanban_notify_subs (task_id,platform,chat_id,notifier_profile,created_at) VALUES (?,'tui',?,'default',?)", (TID,SID,int(time.time())))
    with sqlite3.connect(tmp_path / 'state.db') as c:
        c.executescript('CREATE TABLE sessions (id TEXT, profile_name TEXT); CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT);')
        c.execute('INSERT INTO sessions VALUES (?,?)',(SID,'default'))
        c.execute('INSERT INTO messages VALUES (1,?,?,?)',(SID,'user',REQUEST))
    ref = dict(gateway=socket.gethostname(), profile='default', board='default', task_id=TID)
    v = dict(goal=REQUEST, actionable=True, unfinished=True, disposition='owned_continuing', evidence_refs=['user:0','candidate'], boundary_kind=None, continuation_ref=ref, confidence=.99)
    evidence = gd.evidence_for([dict(role='user',content=REQUEST)], 'An existing worker is continuing.')
    return db, v, evidence


def certify(owner):
    _, v, evidence = owner
    return gd.validate_disposition(v, evidence, session_id=SID)


def test_authoritative_running_owner(owner):
    assert certify(owner)['disposition'] == 'owned_continuing'


@pytest.mark.parametrize('sql', [
    "UPDATE tasks SET status='done'", "UPDATE tasks SET status='blocked'",
    "UPDATE tasks SET status='scheduled'", "UPDATE tasks SET assignee=NULL",
    "UPDATE tasks SET assignee='other'", "UPDATE tasks SET body='Unrelated monitoring'",
    "UPDATE tasks SET session_id='another-origin'", "UPDATE tasks SET current_run_id=7",
    "UPDATE task_runs SET status='failed'", "UPDATE task_runs SET ended_at=1",
    "UPDATE task_runs SET claim_expires=1", "UPDATE task_runs SET worker_started_at='unverified'",
    "UPDATE task_runs SET worker_started_at='wrong|123'", "UPDATE task_runs SET worker_pid=99999999",
    "UPDATE task_runs SET profile='other'", "UPDATE task_runs SET task_id='t_deadbeef'",
    "UPDATE task_runs SET claim_lock='foreign-host:123'", "DELETE FROM kanban_notify_subs",
    "UPDATE kanban_notify_subs SET chat_id='other-origin'",
    "UPDATE kanban_notify_subs SET notifier_profile='other'", "DELETE FROM tasks",
])
def test_invalid_owner_cannot_certify(owner, sql):
    with sqlite3.connect(owner[0]) as c:
        c.execute(sql)
    with pytest.raises(ValueError):
        certify(owner)


@pytest.mark.parametrize('field,value', [('gateway','unavailable-remote'),('profile','other'),('board','missing'),('task_id','t_deadbeef'),('task_id',' t_1234abcd'),('board','../default')])
def test_exact_route_no_fallback(owner, field, value):
    owner[1]['continuation_ref'][field] = value
    with pytest.raises(ValueError):
        certify(owner)


def test_cached_success_and_assistant_claim_never_replace_readback(owner):
    owner[2]['tool:cached'] = dict(role='tool',content={'success':True,'status':'running'})
    owner[1]['evidence_refs'].append('tool:cached')
    with sqlite3.connect(owner[0]) as c:
        c.execute('DELETE FROM tasks')
    with pytest.raises(ValueError):
        certify(owner)


def test_independent_current_request_required(owner):
    with sqlite3.connect(owner[0].parent / 'state.db') as c:
        c.execute("UPDATE messages SET content='Only explain; do not repair'")
    with pytest.raises(ValueError):
        certify(owner)


def test_gate_public_entry_certifies_fresh_owner_without_writes(owner, monkeypatch):
    events = []
    monkeypatch.setattr(gd, 'settings', lambda: {'mode':'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: owner[1])
    before = {p: p.read_bytes() for p in owner[0].parent.glob('*.db')}
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(session_id=SID, final_response='Worker continuing', turn_messages=[dict(role='user',content=REQUEST)]) is None
    assert events[-1]['disposition'] == 'owned_continuing'
    assert all(p.read_bytes() == data for p,data in before.items())
    with sqlite3.connect(owner[0]) as c:
        c.execute("UPDATE tasks SET status='done'")
    with pytest.raises(ValueError):
        gate(session_id=SID, final_response='Worker continuing', turn_messages=[dict(role='user',content=REQUEST)], attempt=1)
    assert events[-1]['event'] == 'goal_disposition_unknown'


def test_owned_continuation_through_discovered_agent_text_stop(owner, monkeypatch):
    from unittest.mock import patch
    from run_agent import AIAgent
    from hermes_cli.plugins import get_plugin_manager
    root = Path(__file__).parents[1]
    home = owner[0].parent
    (home / 'coach').mkdir()
    (home / 'coach' / 'goal-disposition.json').write_text('{"mode":"enforce"}')
    (home / 'plugins').mkdir()
    (home / 'plugins' / 'coach').symlink_to(root, target_is_directory=True)
    (home / 'config.yaml').write_text('plugins:\n  enabled: [coach]\n')
    monkeypatch.setenv('HERMES_VERIFY_ON_STOP', '0')
    monkeypatch.setenv('COACH_LESSONS', '0')
    monkeypatch.setenv('COACH_REPLY_RULES', 'none')
    manager = get_plugin_manager()
    manager.discover_and_load(force=True)
    try:
        plugin = manager._plugins['coach'].module
        assert Path(plugin.__file__).resolve().parent == root.resolve()
        loaded_gd = plugin._load('goal_disposition')
        monkeypatch.setattr(loaded_gd, 'aux_judge', lambda *_: owner[1])
        proofs = []
        original = loaded_gd.authoritative_owner
        def proof(*args):
            original(*args)
            proofs.append(True)
        monkeypatch.setattr(loaded_gd, 'authoritative_owner', proof)
        with patch('model_tools.get_tool_definitions', return_value=[]), \
             patch('model_tools.check_toolset_requirements', return_value={}), \
             patch('agent.process_bootstrap.OpenAI'):
            agent = AIAgent(session_id=SID, api_key='test-key', base_url='https://example.invalid/v1',
                            provider='openai-compat', model='test/model', max_iterations=3,
                            quiet_mode=True, skip_context_files=True, skip_memory=True, platform='desktop')
        agent._cached_system_prompt = 'stable test prompt'
        agent._session_db = None
        agent.save_trajectories = False
        agent.compression_enabled = False
        agent._cleanup_task_resources = lambda *_a, **_kw: None
        agent._save_trajectory = lambda *_a, **_kw: None
        calls = []
        def api(kw):
            calls.append(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='The existing worker is continuing.', tool_calls=None), finish_reason='stop')],
                model='test/model', usage=None)
        agent._interruptible_api_call = api
        agent._interruptible_streaming_api_call = lambda kw, on_first_delta=None: api(kw)
        result = agent.run_conversation(REQUEST)
        assert result['final_response'] == 'The existing worker is continuing.'
        assert len(calls) == 1
        assert proofs == [True], 'must certify, not fail open as UNKNOWN'
    finally:
        manager.unload()


@pytest.mark.parametrize('change', ['missing_state','missing_origin','changed_goal','restricted_request','extra_route_field','archived_board'])
def test_missing_or_conflicting_authority(owner, change):
    home = owner[0].parent
    if change == 'missing_state':
        (home / 'state.db').unlink()
    elif change == 'missing_origin':
        with sqlite3.connect(home / 'state.db') as c:
            c.execute('DELETE FROM sessions')
    elif change == 'changed_goal':
        owner[1]['goal'] = 'An unrelated goal'
    elif change == 'restricted_request':
        owner[2]['user:1'] = dict(role='user', content='Do not repair anything')
    elif change == 'extra_route_field':
        owner[1]['continuation_ref']['trusted'] = True
    else:
        (home / 'kanban').mkdir()
        (home / 'kanban' / 'boards').mkdir()
        board = home / 'kanban' / 'boards' / 'archive'
        board.mkdir()
        (board / 'board.json').write_text('{"archived":true}')
        owner[1]['continuation_ref']['board'] = 'archive'
    with pytest.raises(ValueError):
        certify(owner)


def test_unavailable_remote_is_visible_unknown(owner, monkeypatch):
    events = []
    owner[1]['continuation_ref']['gateway'] = 'unavailable-remote'
    monkeypatch.setattr(gd, 'settings', lambda: {'mode':'enforce'})
    monkeypatch.setattr(gd, 'aux_judge', lambda *_: owner[1])
    gate = gd.build_gate(SimpleNamespace(log=events.append))
    assert gate(session_id=SID, turn_messages=[dict(role='user',content=REQUEST)]) is None
    assert events[-1]['reason'] == 'owner_remote_readback_unavailable'
