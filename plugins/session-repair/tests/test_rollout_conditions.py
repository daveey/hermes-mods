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

@pytest.mark.parametrize('bad', [None, 'stale', 'broken', 'overdue', 'wrong_ref', 'shell_without_command'])
def test_native_ci_receipt_does_not_require_shell_command(env, bad):
    tid = F.legacy_probe_wait(env.conn, env.now, waited_s=10000)
    ref = 'https://github.com/example/project/actions/runs/123'
    env.conn.execute("UPDATE task_events SET payload=json_set(json_remove(payload,'$.wake_check'),'$.awaiting',json(?)) WHERE task_id=? AND kind='scheduled'", (json.dumps({'kind':'ci','ref':ref}),tid))
    at = env.now-(30000 if bad=='stale' else 20)
    F.event(env.conn,tid,'external_watch',{'adapter':'shell' if bad=='shell_without_command' else 'github_run','url':'other' if bad=='wrong_ref' else ref,'health':'ok','state':'external_running','observed_at':at,'next_due_at':at+300,'every':300,'consecutive_failures':0},at=at)
    if bad=='broken': F.event(env.conn,tid,'wake_check_broken',{},at=env.now-10)
    if bad=='overdue': env.conn.execute("UPDATE task_events SET payload=json_set(payload,'$.wake_at',?) WHERE task_id=? AND kind='scheduled'",(env.now-1,tid))
    env.conn.commit()
    assert bool(env.findings(rule='BS-long-wait',card=tid)) == bool(bad)

@pytest.mark.parametrize('bad', [None, 'no_widen', 'wrong_run', 'bool_run', 'wrong_head',
    'wrong_repo', 'empty_pending', 'bad_pending', 'old_widen', 'future_watch',
    'old_watch', 'stale', 'failed', 'broken', 'lost', 'condition_met', 'overdue',
    'ambiguous_watch', 'ambiguous_widen', 'history_overflow', 'arrival_remap',
    'old_park_remap', 'ambiguous_park', 'future_park'])
def test_completed_component_does_not_complete_widened_commit_wait(env, monkeypatch, bad):
    tid = F.legacy_probe_wait(env.conn, env.now, waited_s=10000)
    ref = 'https://github.com/example/project/actions/runs/123'
    sha = 'a' * 40
    env.conn.execute("UPDATE task_events SET payload=json_set(json_remove(payload,'$.wake_check'),'$.awaiting',json(?)) WHERE task_id=? AND kind='scheduled'", (json.dumps({'kind':'ci','ref':ref}),tid))
    widen = {'from_run':123,'head_sha':sha,'pending_runs':[124,125],'since':env.now-1000}
    watch = {'adapter':'github_run','url':'https://github.com/example/project/commit/'+sha,'health':'ok','state':'external_running','observed_at':env.now-20,'next_due_at':env.now+280,'every':300,'consecutive_failures':0}
    for case, key, value in [('wrong_run','from_run',999),('bool_run','from_run',True),('wrong_head','head_sha','b'*40),('empty_pending','pending_runs',[]),('bad_pending','pending_runs',[True])]:
        if bad == case: widen[key] = value
    if bad == 'wrong_repo': watch['url'] = 'https://github.com/other/project/commit/'+sha
    if bad == 'future_watch': watch['observed_at'] = env.now+1
    if bad == 'stale': watch.update(observed_at=env.now-2000,next_due_at=env.now-1700)
    widen_at = env.now-(20000 if bad=='old_widen' else 900)
    watch_at = env.now-(1000 if bad=='old_watch' else 10)
    if bad != 'no_widen': F.event(env.conn, tid, 'ci_wait_widened', widen, at=widen_at)
    if bad == 'arrival_remap':
        # An old receipt arriving later cannot hide the newer immutable watch.
        F.event(env.conn, tid, 'external_watch', watch, at=watch_at)
        F.event(env.conn, tid, 'external_watch', {'health':'error'}, at=env.now-500)
    else: F.event(env.conn, tid, 'external_watch', watch, at=watch_at)
    if bad == 'ambiguous_watch': F.event(env.conn, tid, 'external_watch', watch, at=watch_at)
    if bad == 'ambiguous_widen': F.event(env.conn, tid, 'ci_wait_widened', widen, at=widen_at)
    if bad == 'history_overflow':
        for offset in range(100): F.event(env.conn,tid,'external_watch',watch,at=env.now-100-offset)
    if bad in ('broken','lost','condition_met'):
        F.event(env.conn,tid,{'broken':'wake_check_broken','lost':'wake_check_lost','condition_met':'condition_met'}[bad],{},at=env.now-5)
    if bad in ('old_park_remap', 'ambiguous_park', 'future_park'):
        original = env.conn.execute("SELECT id,payload,created_at FROM task_events WHERE task_id=? AND kind='scheduled'", (tid,)).fetchone()
        at = {'old_park_remap':env.now-500, 'ambiguous_park':original[2], 'future_park':env.now+1}[bad]
        F.event(env.conn, tid, 'scheduled', json.loads(original[1]), at=at)
        if bad == 'old_park_remap':
            # Only arrival ID changes: immutable old episode and new park stay intact.
            newest = env.conn.execute('SELECT MAX(id) FROM task_events').fetchone()[0]
            env.conn.execute('UPDATE task_events SET id=? WHERE id=?', (newest+100000,original[0]))
    if bad == 'overdue': env.conn.execute("UPDATE task_events SET payload=json_set(payload,'$.wake_at',?) WHERE task_id=? AND kind='scheduled'",(env.now-1,tid))
    env.conn.commit()
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state':{'url':ref,'status':'completed','conclusion':'failure' if bad=='failed' else 'success','headSha':sha},'happened':True,'happened_at':env.now-1000})
    assert bool(env.findings(rule='BS-long-wait', card=tid)) == (bad not in (None,'arrival_remap'))


@pytest.mark.parametrize('unknown', [True, False])
def test_unknown_coverage_is_not_positive_unfinished_work(env, monkeypatch, unknown):
    row={'id':'quiet','kind':'unresolved','reason':'card_read_failed' if unknown else 'unfinished_work','status':{'kind':'unresolved','coverage':{'state':'unknown' if unknown else 'complete'}}}
    env.state.execute('INSERT INTO sessions (id,title,started_at,outcome_json) VALUES (?,?,?,?)',('quiet','Quiet',env.now-7200,json.dumps(row)))
    env.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',('quiet','assistant','Waiting',env.now-3700));env.state.commit()
    monkeypatch.setattr(env.scan_mod.Sources,'outcome_rows',lambda *a:[row])
    monkeypatch.setattr(env.scan_mod.Sources,'chat_obligations',lambda *a:False)
    assert bool(env.findings(rule='BS-quiet-chat')) != unknown

@pytest.mark.parametrize('placeholder', [True, False])
def test_owner_placeholder_no_summary_is_not_a_persons_stalled_chat(env, monkeypatch, placeholder):
    sid='kanban_owner_service' if placeholder else 'ordinary'
    row={'id':sid,'kind':'unresolved','reason':'no_summary','on':[],'items':[]}
    env.state.execute('INSERT INTO sessions (id,title,started_at,outcome_json) VALUES (?,?,?,?)',(sid,'Quiet',env.now-7200,json.dumps(row)))
    env.state.execute('INSERT INTO messages (session_id,role,content,timestamp) VALUES (?,?,?,?)',(sid,'assistant','Waiting',env.now-3700));env.state.commit()
    monkeypatch.setattr(env.scan_mod.Sources,'outcome_rows',lambda *a:[row])
    monkeypatch.setattr(env.scan_mod.Sources,'chat_obligations',lambda *a:False)
    assert bool(env.findings(rule='BS-quiet-chat')) != placeholder

@pytest.mark.parametrize('recent', [True, False])
@pytest.mark.parametrize('wake', ['continuation_woken','condition_met'])
def test_ready_age_fenced_by_new_wake_even_when_ready_since_old(env, recent, wake):
    tid=F.card(env.conn,'Ready',status='ready')
    if not env.scan_mod._col(env.conn,'tasks','ready_since'):
        env.conn.execute('ALTER TABLE tasks ADD COLUMN ready_since INTEGER')
    env.conn.execute('UPDATE tasks SET ready_since=? WHERE id=?',((env.now-7200)*1000,tid))
    F.event(env.conn,tid,wake,{},at=env.now-(10 if recent else 4000));env.conn.commit()
    card=env.scan_mod.Card(tid,'default',None,env.sources().task('default',tid)[0],env.conn,None)
    assert env.scan_mod._ready_since(env.conn,card) == pytest.approx(env.now-(10 if recent else 4000))

@pytest.mark.parametrize('new_status', ['running','scheduled'])
def test_stale_ready_object_does_not_describe_current_executor(env,new_status):
    tid=F.card(env.conn,'Ready',status='ready')
    F.age_all_events(env.conn,tid,env.now-7200)
    if not env.scan_mod._col(env.conn,'tasks','ready_since'):
        env.conn.execute('ALTER TABLE tasks ADD COLUMN ready_since INTEGER')
    env.conn.execute('UPDATE tasks SET ready_since=? WHERE id=?',((env.now-7200)*1000,tid));env.conn.commit()
    src=env.sources(); task=src.task('default',tid)[0]
    stale=env.scan_mod.Card(tid,'default',None,task,env.conn,None)
    assert env.scan_mod._current_idle_status(stale,'ready')
    env.conn.execute('UPDATE tasks SET status=? WHERE id=?',(new_status,tid));env.conn.commit()
    assert env.scan_mod.rule_ready_worker_free(stale,src) is None

@pytest.mark.parametrize('new_status', ['scheduled','running'])
def test_stale_review_object_does_not_restart_parked_reviewer(env,new_status):
    tid=F.card(env.conn,'Review',status='review')
    F.event(env.conn,tid,'review_requested',{},at=env.now-7200);env.conn.commit()
    src=env.sources(); stale=env.scan_mod.Card(tid,'default',None,src.task('default',tid)[0],env.conn,None)
    env.conn.execute('UPDATE tasks SET status=? WHERE id=?',(new_status,tid));env.conn.commit()
    assert env.scan_mod.rule_review_no_reviewer(stale,src) is None

@pytest.mark.parametrize('recent', [True,False])
@pytest.mark.parametrize('wake', ['continuation_woken','condition_met'])
def test_review_wake_starts_new_idle_episode(env,recent,wake):
    tid=F.card(env.conn,'Review',status='review')
    F.event(env.conn,tid,'review_requested',{},at=env.now-7200)
    F.event(env.conn,tid,wake,{},at=env.now-(10 if recent else 4000));env.conn.commit()
    assert bool(env.findings(rule='BS-review-no-reviewer',card=tid)) != recent

@pytest.mark.parametrize('rule,status',[('rule_ready_worker_free','ready'),('rule_review_no_reviewer','review')])
def test_running_successor_not_diagnosed_from_idle_task_pointer(env,rule,status):
    tid=F.card(env.conn,'Idle',status=status)
    F._run(env.conn,tid,env.now,started_s=10)
    env.conn.execute('UPDATE tasks SET current_run_id=NULL WHERE id=?',(tid,));env.conn.commit()
    src=env.sources(); card=env.scan_mod.Card(tid,'default',None,src.task('default',tid)[0],env.conn,None)
    assert not env.scan_mod._current_idle_status(card,status)
    assert getattr(env.scan_mod,rule)(card,src) is None
