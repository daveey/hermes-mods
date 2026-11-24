"""Rollout audit regressions: real shell receipts and producer/condition boundaries."""
import json
import pytest
pytest.importorskip('hermes_cli.kanban_db')
from sr_helpers import Env, fixtures
F = fixtures()

@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    monkeypatch.setattr(e.scan_mod.Sources, 'producer_state', lambda *a: {'state':'not_resolvable','happened':False})
    monkeypatch.setattr(e.scan_mod.Sources, 'outcome_rows', lambda *a: [])
    return e

@pytest.mark.parametrize('kind', ['external', 'deploy', 'ci'])
@pytest.mark.parametrize('bad', [None, 'stale', 'broken', 'overdue', 'old_episode', 'wrong_command'])
def test_shell_listener_with_future_backstop(env, kind, bad):
    tid = F.legacy_probe_wait(env.conn, env.now, waited_s=40000)
    env.conn.execute("UPDATE task_events SET payload=json_set(payload,'$.awaiting',json(?)) WHERE task_id=? AND kind='scheduled'", (json.dumps({'kind':kind,'ref':'listener'}),tid))
    at = env.now-(50000 if bad=='old_episode' else 30000 if bad=='stale' else 20)
    payload={'adapter':'shell','health':'ok','state':'external_unknown','observed_at':at,'next_due_at':at+900,'every':900,'consecutive_failures':0}
    if bad=='wrong_command': payload['wake_check']='other command'
    F.event(env.conn,tid,'external_watch',payload,at=at)
    if bad=='broken': F.event(env.conn,tid,'wake_check_broken',{},at=env.now-10)
    if bad=='overdue': env.conn.execute("UPDATE task_events SET payload=json_set(payload,'$.wake_at',?) WHERE task_id=? AND kind='scheduled'",(env.now-1,tid))
    env.conn.commit()
    assert bool(env.findings(rule='BS-long-wait',card=tid)) == bool(bad)

@pytest.mark.parametrize('kind', ['deploy','external'])
def test_pr_terminal_is_not_a_custom_probe_result(env,monkeypatch,kind):
    tid=F.legacy_probe_wait(env.conn,env.now,waited_s=1200)
    env.conn.execute("UPDATE task_events SET payload=json_set(payload,'$.awaiting',json(?)) WHERE task_id=? AND kind='scheduled'",(json.dumps({'kind':kind,'ref':'https://github.com/example/project/pull/12'}),tid));env.conn.commit()
    monkeypatch.setattr(env.scan_mod.Sources,'producer_state',lambda *a: {'state':{'state':'MERGED','url':'https://github.com/example/project/pull/12'},'happened':True,'happened_at':env.now-1000})
    assert not env.findings(rule='BS-long-wait',card=tid)
    # No healthy receipt: still diagnose after the actual wait limit.
    env.conn.execute("UPDATE task_events SET created_at=? WHERE task_id=?",(env.now-40000,tid));env.conn.commit()
    assert env.findings(rule='BS-long-wait',card=tid)[0]['variant']=='too_long'

@pytest.mark.parametrize('settled',[True,False])
def test_canonical_settled_chat_overrides_legacy_unresolved(env,monkeypatch,settled):
    row={'id':'quiet','kind':'unresolved','status':{'kind':'done' if settled else 'unresolved','coverage':{'state':'complete'}}}
    env.state.execute('INSERT INTO sessions (id,title,started_at,outcome_json) VALUES (?,?,?,?)',('quiet','Quiet',env.now-7200,json.dumps(row)))
    env.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',('quiet','assistant','Waiting',env.now-3700));env.state.commit()
    monkeypatch.setattr(env.scan_mod.Sources,'outcome_rows',lambda *a:[row])
    monkeypatch.setattr(env.scan_mod.Sources,'chat_obligations',lambda *a:False)
    assert bool(env.findings(rule='BS-quiet-chat')) != settled
